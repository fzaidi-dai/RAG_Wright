"""Issue 0005 (RuleWright): every model-calling INGEST stage names itself in the deadline warning, so the next
timeout is a statement ("cancelled for clause_function_classifier.classify_spans") not an elimination across
candidates. Hermetic -- the structured factory is captured, no model. Covers the path-A stages (build_structured
via the seam) that logged the bare model id; the path-B docling-graph stage carries `dg_extraction.<stage>`."""

from __future__ import annotations

from rag_wright.packs.contracts.schemas.property import PropertyDimension


def test_batch_clause_classifier_labels_its_free_text_call():
    # issue 0005 route (b): the batched classifier now runs CLIENT-SIDE tag-parse; its runnable carries the stage
    from rag_wright.packs.contracts.spans.clause_function_classifier import production_batch_clause_classifier

    clf = production_batch_clause_classifier("granite")
    assert clf._runnable._label == "clause_function_classifier.classify_spans"  # the prime suspect names itself


def test_per_clause_classifier_labels_its_free_text_call():
    from rag_wright.packs.contracts.spans.clause_function_classifier import production_llm_clause_classifier

    clf = production_llm_clause_classifier("granite")
    assert clf._runnable._label == "clause_function_classifier.classify"


async def test_semantic_judge_labels_its_structured_call():
    captured = {}

    class _Runnable:
        async def ainvoke(self, prompt):
            return None

    def factory(model_id, schema, **kw):
        captured["label"] = kw.get("label")
        return _Runnable()

    from rag_wright.packs.contracts.spans.semantic_judge import build_asemantic_judge_fn

    ajudge = build_asemantic_judge_fn("granite", structured_factory=factory)
    await ajudge(next(iter(PropertyDimension)), "some-value", "some clause text")
    assert captured["label"] == "semantic_judge.judge"
