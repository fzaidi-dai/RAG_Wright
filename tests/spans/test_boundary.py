"""Bug-A: provision-boundary decision = deterministic-first + a decision-model fallback for the UNCERTAIN residue
(spans.boundary). Hermetic: an injected fake decider, so no model and no network."""
from __future__ import annotations

from rag_wright.packs.contracts.spans.boundary import adecide_provision_starts

_TEXTS = [
    "Section 8. Limitation of Liability.",                       # deterministic START
    "the parties agree to indemnify each other for any losses",  # deterministic CONTINUE
    "Limitation of Liability:",                                  # UNCERTAIN (colon heading) -> decider
]


async def test_deterministic_only_when_no_decider():
    # No decision model: start/continue decided for free; the uncertain residue degrades to NOT-a-start (fold in).
    assert await adecide_provision_starts(_TEXTS, decider=None) == [True, False, False]


async def test_decider_sees_only_the_uncertain_residue():
    seen: dict = {}

    async def fake(residue_texts):
        seen["texts"] = residue_texts
        return [True] * len(residue_texts)  # the model judges each uncertain line a provision start

    starts = await adecide_provision_starts(_TEXTS, decider=fake)
    assert seen["texts"] == ["Limitation of Liability:"]  # ONLY the uncertain span reached the model
    assert starts == [True, False, True]                   # deterministic start kept; residue resolved to start


async def test_decider_error_degrades_to_deterministic():
    async def boom(_residue):
        raise RuntimeError("decision model unavailable")

    assert await adecide_provision_starts(["Limitation of Liability:"], decider=boom) == [False]


# --- ING-4c: Jev decisions are content-hash cached (repeatable boundaries; never paid for twice) -------------------

def test_the_decision_cache_asks_once_per_batch(tmp_path):
    import asyncio

    from rag_wright.packs.contracts.spans.boundary import cached_decider

    calls = []

    async def decider(texts):
        calls.append(list(texts))
        return [t.startswith("2.") for t in texts]

    cached = cached_decider(decider, tmp_path)
    assert asyncio.run(cached(["2.4 Covenants.", "continued text"])) == [True, False]
    assert asyncio.run(cached(["2.4 Covenants.", "continued text"])) == [True, False]  # served from the cache
    assert asyncio.run(cached(["3.1 Term."])) == [False]  # a different batch is asked
    assert len(calls) == 2


def test_a_failed_decision_is_not_cached(tmp_path):
    import asyncio

    import pytest

    from rag_wright.packs.contracts.spans.boundary import cached_decider

    state = {"fail": True}

    async def decider(texts):
        if state["fail"]:
            raise RuntimeError("endpoint down")
        return [True] * len(texts)

    cached = cached_decider(decider, tmp_path)
    with pytest.raises(RuntimeError):
        asyncio.run(cached(["2.4 Covenants."]))
    state["fail"] = False
    assert asyncio.run(cached(["2.4 Covenants."])) == [True]


def test_no_decider_stays_no_decider(tmp_path):
    from rag_wright.packs.contracts.spans.boundary import cached_decider

    assert cached_decider(None, tmp_path) is None


# --- ING-4d: the residue request -- a STRUCTURAL rubric stated once, one minimal question per line ---------------

def test_the_rubric_is_stated_once_then_one_line_per_item():
    from rag_wright.packs.contracts.spans.boundary import residue_request

    state, questions = residue_request(["  7.2 Delivery.\n", "Source: ACME CORP, 8-K, 1/1/2020"])
    rubric, body = state.split("\n\n[0]", 1)
    assert "STARTS A NEW SECTION" in rubric and body == " 7.2 Delivery.\n[1] Source: ACME CORP, 8-K, 1/1/2020"
    assert list(questions) == ["c0", "c1"]
    assert questions["c1"]["type"] == "noul" and questions["c1"]["instructions"] == "Item [1]"
    assert questions["c0"]["criteria"] == questions["c1"]["criteria"]  # the same full criteria on every item


def test_the_rubric_defines_a_start_by_structure_not_topic():
    # The measured failure modes (ING-4d): same-topic numbered siblings judged "continues", and furniture / lead-ins /
    # TOC lines judged "starts". The rubric must say both, and stay domain-neutral (any long document).
    from rag_wright.packs.contracts.spans.boundary import residue_request

    rubric = residue_request(["x"])[0].lower()
    assert "even when" in rubric and "same topic" in rubric
    for negative in ("page furniture", "table-of-contents", "revision-history", "reference to another section",
                     "lead-in sentence", "signature"):
        assert negative in rubric, negative
    assert "exhibit, annex or appendix" in rubric
    # ING-4d decision: lettered items (even titled ones, '(b) Enforcement of Patents.') fold into their numbered parent
    # -- measured, asking the model to split titled lettered sub-sections left them at ~0.5 and destabilised lead-ins.
    assert "a lettered, roman or bracketed list item" in rubric and "short title" not in rubric
    assert "contract" not in rubric.split("(")[0]  # opens generically ("a long document"), not as a contract task


async def test_the_jev_decider_sends_the_request_and_thresholds_the_scores(monkeypatch):
    import rag_wright.api as api
    from rag_wright.packs.contracts.spans import boundary

    sent: dict = {}

    async def fake_invoke(name, inputs, resources=None):
        sent.update(name=name, **inputs)
        return {"answers": {"c0": {"noul": 0.93}, "c1": {"noul": 0.12}}}

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(api, "ainvoke_model", fake_invoke)
    monkeypatch.setattr(api, "capability_index", lambda: {"jev_decision": {}})
    decide = boundary.jev_boundary_decider()
    texts = ["7.2 Delivery.", "Source: ACME CORP, 8-K, 1/1/2020"]
    assert await decide(texts) == [True, False]
    assert sent["name"] == "jev_decision" and (sent["state"], sent["questions"]) == boundary.residue_request(texts)


def test_a_new_rubric_does_not_reuse_decisions_cached_under_the_old_one(tmp_path, monkeypatch):
    import asyncio

    from rag_wright.packs.contracts.spans import boundary

    calls = []

    async def decider(texts):
        calls.append(texts)
        return [True] * len(texts)

    asyncio.run(boundary.cached_decider(decider, tmp_path)(["7.2 Delivery."]))
    monkeypatch.setattr(boundary, "_RUBRIC", boundary._RUBRIC + " (revised)")
    asyncio.run(boundary.cached_decider(decider, tmp_path)(["7.2 Delivery."]))
    assert len(calls) == 2  # the prompt is part of the cache key
