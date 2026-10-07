"""LG-1: the typed_clause_extraction hardened subgraph -- hermetic (fake record_fn / escalate_fn, no LLM).

Verifies the reference pattern end to end: happy path, RetryPolicy recovers a transient blip, dead-letter on a
genuine no-model result, conditional Flash->Pro escalation, and the optional HITL interrupt.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import RetryPolicy

from rag_wright.packs.contracts.schemas.property import ClausePropertyRecord
from rag_wright.packs.contracts.subgraphs.typed_clause_extraction import (
    TransientExtraction,
    build_typed_clause_extraction,
)

_NO_ESCALATE = lambda record, text: False  # noqa: E731
_ALWAYS_ESCALATE = lambda record, text: True  # noqa: E731
_FAST_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.0)  # no backoff sleeps in tests


def _record(function: str = "Cap On Liability") -> ClausePropertyRecord:
    return ClausePropertyRecord(clause_id="c1", function=function, assertions=[])


def test_happy_path_produces_a_regrounded_record():
    graph = build_typed_clause_extraction(
        lambda text, model: _record(), cheap_model="cheap", strong_model="strong", escalate_fn=_NO_ESCALATE
    )
    out = graph.invoke({"clause_text": "a cap clause"})
    assert isinstance(out["record"], ClausePropertyRecord)
    assert out.get("dead_letter") is None
    assert not out.get("escalated")


def test_retry_policy_recovers_a_transient_blip():
    calls = {"n": 0}

    def flaky(text: str, model: str):
        calls["n"] += 1
        if calls["n"] < 2:
            raise TransientExtraction("provider blip")
        return _record()

    graph = build_typed_clause_extraction(
        flaky, cheap_model="cheap", strong_model="strong", escalate_fn=_NO_ESCALATE, retry_policy=_FAST_RETRY
    )
    out = graph.invoke({"clause_text": "x"})
    assert isinstance(out["record"], ClausePropertyRecord)
    assert calls["n"] == 2  # failed once, retried, succeeded
    assert out.get("dead_letter") is None


def test_persistent_transient_dead_letters_after_retries():
    calls = {"n": 0}

    def always_flaky(text: str, model: str):
        calls["n"] += 1
        raise TransientExtraction("provider down")

    graph = build_typed_clause_extraction(
        always_flaky, cheap_model="cheap", strong_model="strong", escalate_fn=_NO_ESCALATE, retry_policy=_FAST_RETRY
    )
    out = graph.invoke({"clause_text": "x"})
    assert out["dead_letter"]["reason"] == "extraction_failed"  # dropped, not raised
    assert out.get("record") is None
    assert calls["n"] == 3  # retried up to max_attempts, then dead-lettered


def test_genuine_no_model_dead_letters_without_raising():
    graph = build_typed_clause_extraction(
        lambda text, model: None, cheap_model="cheap", strong_model="strong", escalate_fn=_NO_ESCALATE
    )
    out = graph.invoke({"clause_text": "x"})
    assert out["dead_letter"]["reason"] == "extraction_produced_no_models"
    assert out.get("record") is None  # dropped, not raised


def test_escalation_routes_to_the_strong_model_once():
    seen: list[str] = []

    def record_fn(text: str, model: str):
        seen.append(model)
        return _record()

    graph = build_typed_clause_extraction(
        record_fn, cheap_model="cheap", strong_model="strong", escalate_fn=_ALWAYS_ESCALATE
    )
    out = graph.invoke({"clause_text": "x"})
    assert seen == ["cheap", "strong"]  # escalated exactly once (bounded by the `escalated` flag)
    assert out.get("escalated") is True
    assert isinstance(out["record"], ClausePropertyRecord)


def test_human_gate_interrupts_on_low_confidence():
    graph = build_typed_clause_extraction(
        lambda text, model: _record(),
        cheap_model="cheap",
        strong_model="strong",
        escalate_fn=_ALWAYS_ESCALATE,
        enable_human_gate=True,
        checkpointer=InMemorySaver(),
    )
    result = graph.invoke({"clause_text": "x"}, config={"configurable": {"thread_id": "t1"}})
    assert result.get("__interrupt__")  # paused for human review at the gate


def test_registers_as_a_subgraph():
    from rag_wright.capabilities.registry import CapabilityRegistry
    from rag_wright.packs.contracts.subgraphs.typed_clause_extraction import register_typed_clause_extraction

    reg = CapabilityRegistry()
    register_typed_clause_extraction(reg)
    registration = reg.get("typed_clause_extraction")
    assert registration.kind == "subgraph"
    assert registration.contract is ClausePropertyRecord
