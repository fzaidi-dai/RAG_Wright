"""Engine issue 0003 / ADR-0056: structured calls use ONE bounded retry layer, not two stacked layers that
multiply the per-attempt timeout into a ~36 min worst case. Hermetic -- exercises `_with_bounded_retry` with
fake runnables, no network or credentials.
"""
from __future__ import annotations

import pytest
from langchain_core.runnables import RunnableLambda

from rag_wright.models import seam


def test_transient_is_retried_up_to_the_bounded_attempt_count(monkeypatch):
    monkeypatch.setattr(seam, "_STRUCTURED_RETRY_ATTEMPTS", 2)  # keep the test's backoff to one short sleep
    calls = {"n": 0}

    def boom(_):
        calls["n"] += 1
        raise ValueError("simulated OpenRouter 504 'operation was aborted'")

    wrapped = seam._with_bounded_retry(RunnableLambda(boom), "test-model")
    with pytest.raises(ValueError):
        wrapped.invoke("x")
    assert calls["n"] == 2  # exactly the attempt budget -- NOT the old SDK-multiplied 6 x 3


def test_a_non_transient_error_is_not_retried():
    calls = {"n": 0}

    def boom(_):
        calls["n"] += 1
        raise KeyError("a programming error, not a transient")

    wrapped = seam._with_bounded_retry(RunnableLambda(boom), "test-model")
    with pytest.raises(KeyError):
        wrapped.invoke("x")
    assert calls["n"] == 1  # not in the transient set -> surfaced immediately, no retry


def test_each_failed_attempt_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(seam, "_STRUCTURED_RETRY_ATTEMPTS", 1)  # single attempt, no backoff sleep

    def boom(_):
        raise ValueError("boom")

    wrapped = seam._with_bounded_retry(RunnableLambda(boom), "modelX")
    with caplog.at_level("WARNING"):
        with pytest.raises(ValueError):
            wrapped.invoke("x")
    # a retrying call is visibly working, not a silent hang (the diagnostic cost the issue flagged)
    assert any("modelX" in r.getMessage() and "failed" in r.getMessage() for r in caplog.records)


def test_a_successful_call_passes_through_without_retrying():
    calls = {"n": 0}

    def ok(x):
        calls["n"] += 1
        return f"ok:{x}"

    wrapped = seam._with_bounded_retry(RunnableLambda(ok), "test-model")
    assert wrapped.invoke("q") == "ok:q" and calls["n"] == 1


def test_structured_worst_case_wall_clock_is_bounded():
    # one bounded layer: worst case = per-request timeout x attempts (a single product, not x the SDK budget).
    # Guard the bound so a future constant bump that reintroduces a multi-minute stall is a conscious change.
    assert seam._STRUCTURED_TIMEOUT_S * seam._STRUCTURED_RETRY_ATTEMPTS <= 200


def test_plain_free_text_worst_case_wall_clock_is_bounded():
    # plain build_model has the single SDK retry layer; worst case for a persistent upstream stall =
    # timeout x (max_retries + 1). Guard the bound so a future bump (it was 120 x 7 = ~14 min) stays conscious.
    assert seam._TIMEOUT_S * (seam._MAX_RETRIES + 1) <= 300
