"""Issue 0005 (RuleWright): every model-calling INGEST stage names itself in the deadline warning, so the next
timeout is a statement ("cancelled for clause_function_classifier.classify_spans") not an elimination across
candidates. Hermetic -- the structured factory is captured, no model. Covers the path-A stages (build_structured
via the seam) that logged the bare model id; the path-B docling-graph stage carries `dg_extraction.<stage>`."""

from __future__ import annotations

import rag_wright.models.seam as seam
from rag_wright.contracts.property import PropertyDimension


def test_batch_clause_classifier_labels_its_structured_call(monkeypatch):
    captured = {}
    monkeypatch.setattr(seam, "build_structured",
                        lambda model_id, schema, **kw: captured.update(label=kw.get("label")) or object())
    from rag_wright.spans.clause_function_classifier import production_batch_clause_classifier

    production_batch_clause_classifier("granite")
    assert captured["label"] == "clause_function_classifier.classify_spans"  # the prime suspect names itself


def test_per_clause_classifier_labels_its_structured_call(monkeypatch):
    captured = {}
    monkeypatch.setattr(seam, "build_structured",
                        lambda model_id, schema, **kw: captured.update(label=kw.get("label")) or object())
    from rag_wright.spans.clause_function_classifier import production_llm_clause_classifier

    production_llm_clause_classifier("granite")
    assert captured["label"] == "clause_function_classifier.classify"


async def test_semantic_judge_labels_its_structured_call():
    captured = {}

    class _Runnable:
        async def ainvoke(self, prompt):
            return None

    def factory(model_id, schema, **kw):
        captured["label"] = kw.get("label")
        return _Runnable()

    from rag_wright.spans.semantic_judge import build_asemantic_judge_fn

    ajudge = build_asemantic_judge_fn("granite", structured_factory=factory)
    await ajudge(next(iter(PropertyDimension)), "some-value", "some clause text")
    assert captured["label"] == "semantic_judge.judge"
