"""ING-9b: the residual (numeric/open) dimensions on a typed-DECISION model instead of an LLM -- deterministic
candidate spans + one `choice` per candidate, ONE call per provision (none when there are no candidates). The role
criteria are authored in contract_bridge.ttl (ADR-0066). Hermetic: a fake decision model, no network."""
from __future__ import annotations

import asyncio

from rag_wright.contracts.identifiers import ChunkId
from rag_wright.spans.property_extractor import RESIDUAL_LLM_DIMS

_TEXT = ("Either party may terminate this Agreement upon thirty (30) days' prior written notice. This Agreement is "
         "governed by the laws of the State of California. Accuray will audit the records once per year. Signed in 2019.")


def test_the_residual_roles_and_their_criteria_come_from_the_ontology():
    from rag_wright.ontology.loader import load_residual_role_criteria

    roles = load_residual_role_criteria()
    assert set(roles) == {d.value for d in RESIDUAL_LLM_DIMS} | {"none"}
    assert all(c.strip() for c in roles.values())
    assert "never a temporal bound" in roles["notice_period"]  # the measured notice-vs-bound distinction


def test_candidates_cover_the_value_shapes_and_never_a_bare_year():
    from rag_wright.spans.residual_candidates import candidates

    spans = [s for _k, s in candidates(_TEXT)]
    assert "thirty (30) days" in spans and "once per year" in spans
    assert any("State of California" in s for s in spans)
    assert "2019" not in spans


def test_the_request_states_the_rubric_then_the_provision_then_one_choice_per_candidate():
    from rag_wright.ontology.loader import load_residual_role_criteria
    from rag_wright.spans.residual_candidates import residual_request

    state, questions = residual_request("A  provision\nbody.", [("duration", "thirty (30) days"), ("frequency", "annually")])
    assert "\n\nProvision:\nA provision body.\n\nCandidates:\n[0] thirty (30) days\n[1] annually" in state
    assert list(questions) == ["c0", "c1"] and questions["c0"]["type"] == "choice"
    assert questions["c1"]["criteria"] == load_residual_role_criteria()


def test_clean_value_trims_fragment_edges():
    from rag_wright.spans.residual_candidates import clean_value

    assert clean_value("the Term of this Agreement and") == "the Term of this Agreement"
    assert clean_value("2004 unless") == "2004"
    assert clean_value("a thirty (30) days") == "thirty (30) days"
    assert clean_value("thirty (30) days") == "thirty (30) days"


def _fake(labels=None, error=None):
    calls = []

    async def decide(inputs):
        calls.append(inputs)
        if error:
            raise error
        return {"answers": {f"c{i}": {"choice": lab, "probabilities": {lab: 0.9}} for i, lab in enumerate(labels)}}

    return decide, calls


def _hybrid(decide):
    from rag_wright.spans.property_extractor import HybridPropertyExtractor
    from rag_wright.spans.residual_candidates import DecisionResidualExtractor

    return HybridPropertyExtractor(classifier_fn=lambda text, functions: [], runnable=object(),
                                   residual=DecisionResidualExtractor(decide))


def _residual(record):
    return sorted((a.dimension.value, a.value) for a in record.assertions if a.dimension in RESIDUAL_LLM_DIMS)


def test_one_decision_call_per_provision_and_none_is_dropped():
    from rag_wright.spans.residual_candidates import candidates

    n = len(candidates(_TEXT))
    labels = ["none"] * n
    spans = [s for _k, s in candidates(_TEXT)]
    labels[spans.index("thirty (30) days")] = "notice_period"
    labels[spans.index("once per year")] = "audit_frequency"
    decide, calls = _fake(labels)
    rec = asyncio.run(_hybrid(decide).aextract(chunk_id=ChunkId.of("d", 0, _TEXT), function="NONE", text=_TEXT))
    assert len(calls) == 1
    assert _residual(rec) == [("audit_frequency", "once per year"), ("notice_period", "thirty (30) days")]


def test_a_provision_without_candidates_makes_no_call():
    decide, calls = _fake([])
    text = "Each party shall act in good faith."
    rec = asyncio.run(_hybrid(decide).aextract(chunk_id=ChunkId.of("d", 0, text), function="NONE", text=text))
    assert calls == [] and _residual(rec) == []


def test_a_decision_error_yields_no_residual_values():
    decide, _ = _fake(error=RuntimeError("decisions API down"))
    rec = asyncio.run(_hybrid(decide).aextract(chunk_id=ChunkId.of("d", 0, _TEXT), function="NONE", text=_TEXT))
    assert _residual(rec) == []


def test_without_a_decision_model_the_residual_lane_falls_back_to_the_llm(monkeypatch):
    from rag_wright.spans.residual_candidates import select_residual_extractor

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("RAG_RESIDUAL_EXTRACTOR", raising=False)
    assert select_residual_extractor() is None  # None -> the HybridPropertyExtractor's LLM residual call


def test_the_decision_model_is_the_default_when_configured(monkeypatch):
    import rag_wright.api as api
    from rag_wright.spans.residual_candidates import DecisionResidualExtractor, select_residual_extractor

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.delenv("RAG_RESIDUAL_EXTRACTOR", raising=False)
    monkeypatch.setattr(api, "capability_index", lambda: {"jev_decision": {}})
    assert isinstance(select_residual_extractor(), DecisionResidualExtractor)
    monkeypatch.setenv("RAG_RESIDUAL_EXTRACTOR", "llm")
    assert select_residual_extractor() is None


def test_a_value_written_twice_is_kept_once_preferring_digits():
    text = ("Aimmune shall pay five million Dollars ($5,000,000) and bear one hundred percent (100%) of the costs, "
            "upon thirty (30) days' notice.")
    from rag_wright.spans.residual_candidates import candidates

    spans = [s for _k, s in candidates(text)]
    roles = {"five million Dollars": "commitment_quantum", "$5,000,000": "commitment_quantum",
             "one hundred percent": "commitment_quantum", "100%": "commitment_quantum",
             "thirty (30) days": "notice_period"}
    labels = [roles.get(s, "none") for s in spans]
    decide, _ = _fake(labels)
    rec = asyncio.run(_hybrid(decide).aextract(chunk_id=ChunkId.of("d", 0, text), function="NONE", text=text))
    assert _residual(rec) == [("commitment_quantum", "$5,000,000"), ("commitment_quantum", "100%"),
                              ("notice_period", "thirty (30) days")]


def test_a_jurisdiction_value_is_the_place_name():
    from rag_wright.spans.residual_candidates import clean_value

    assert clean_value("laws of the State of California", "jurisdiction") == "California"
    assert clean_value("courts located in Tel-Aviv, Israel", "jurisdiction") == "Tel-Aviv, Israel"
    assert clean_value("Court in California", "jurisdiction") == "California"
    assert clean_value("laws of the Commonwealth of Pennsylvania", "jurisdiction") == "Pennsylvania"
    assert clean_value("laws of England", "jurisdiction") == "England"
    assert clean_value("Venue shall be Kansas", "jurisdiction") == "Kansas"
    assert clean_value("laws of the State of Kansas.", "jurisdiction") == "Kansas"


def test_an_overlapping_candidate_keeps_the_contained_value_and_a_place_stops_at_the_sentence():
    from rag_wright.spans.residual_candidates import _one_per_value, clean_value

    flat = "give notice no less than thirty (30) days before the expiration"
    long = ("notice_period", "no less than thirty (30) days before the expiration", 12, len(flat), 0.9)
    core = ("notice_period", "thirty (30) days", 25, 41, 0.9)
    assert [v[1] for v in _one_per_value([long, core], flat)] == ["thirty (30) days"]
    assert clean_value("laws of the State of Kansas. The", "jurisdiction") == "Kansas"
