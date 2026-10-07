"""ING-4d (found by the live Aimmune re-ingest): the clause-extraction cache and result reporting of the reference
pack's contract pipeline. A document repeating a provision verbatim (a press release printed twice) made a later run
reuse the OTHER copy's cached record (the key had no position), which the provenance check then rejected -- and the
rejected clause vanished from the KG while `clause_records` still counted it and `clause_failures` stayed empty."""
from __future__ import annotations

from types import SimpleNamespace

from rag_wright.subgraphs.contract_ingestion_pipeline import clause_cache_key, settle_clause_results


def test_the_same_text_at_the_same_index_but_another_position_is_a_different_cache_entry():
    a = clause_cache_key("doc:127:88298454", "doc:45:7bbb#0", "Other", "v3")
    b = clause_cache_key("doc:127:88298454", "doc:50:7bbb#0", "Other", "v3")
    assert a != b
    assert a == clause_cache_key("doc:127:88298454", "doc:45:7bbb#0", "Other", "v3")  # stable for the same provision


def _unit(index, anchor, tags=("Other",)):
    return SimpleNamespace(index=index, anchor=SimpleNamespace(span_id=anchor), tags=list(tags))


def test_a_record_rejected_by_the_contract_check_is_reported_not_counted():
    records = {0: "rec0", 1: "rec1", 2: "rec2"}
    stage = SimpleNamespace(units=[_unit(0, "s0"), _unit(1, "s1", ("Payment",)), _unit(2, "s2")],
                            failures=[{"unit": 1, "anchor": "s1", "reason": "ValueError: no provenance"}])
    kept, failures = settle_clause_results(records, [], stage)
    assert kept == ["rec0", "rec2"]
    assert failures == [{"span_id": "s1", "function": "Payment", "reason": "ValueError: no provenance"}]


def test_an_extractor_failure_is_reported_once():
    hook = [{"span_id": "s1", "function": "Payment", "reason": "timeout"}]  # the hook's own PROD-3 entry
    stage = SimpleNamespace(units=[_unit(0, "s0"), _unit(1, "s1", ("Payment",))],
                            failures=[{"unit": 1, "anchor": "s1", "reason": "RuntimeError: clause extraction failed"}])
    kept, failures = settle_clause_results({0: "rec0"}, hook, stage)
    assert kept == ["rec0"] and failures == hook


def test_the_cache_key_changes_when_the_extraction_method_changes():
    # ING-9b: switching the judge or the residual lane (LLM <-> decision model) must not reuse records the old
    # method produced -- the method is part of the template version the cache key carries.
    from rag_wright.spans.residual_candidates import DecisionResidualExtractor
    from rag_wright.spans.semantic_judge import DecisionJudge
    from rag_wright.subgraphs.contract_ingestion_pipeline import extraction_method

    async def _d(_inputs):
        return {}

    decision = extraction_method(DecisionJudge(_d), DecisionResidualExtractor(_d), judge_model=None, residual_model="m")
    llm_judge = extraction_method(lambda *a: None, DecisionResidualExtractor(_d), judge_model="q", residual_model="m")
    llm_residual = extraction_method(DecisionJudge(_d), None, judge_model=None, residual_model="m")
    assert len({decision, llm_judge, llm_residual}) == 3
    assert extraction_method(lambda *a: None, None, judge_model="q", residual_model="m") != \
        extraction_method(lambda *a: None, None, judge_model="q2", residual_model="m")
