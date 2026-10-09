"""PS-4 (G19): grounded answer generation and span relevance judgment on the engine API surface.

A product that composes its own question answering over engine retrieval calls these instead of the engine's
capability modules. The model comes from the workspace's roles (`EngineConfig.models`, else the profile default):
generation uses `ModelRole.GENERAL`, the relevance judge `ModelRole.STRUCTURED_REASONING`.

    answer = await agenerate_answer(question, [EvidenceItem(chunk_id=s.chunk_id, text=s.text) for s in spans], ws=ws)
    verdicts = await ajudge_spans([(s.text, []) for s in spans], Condition(category="safety requirement"), ws=ws)

Both methods are domain-neutral; pass `guidance=` (prose: what your documents, categories and questions look like)
to add your domain's wording to the method, as the engine's reference pack does.
"""
from __future__ import annotations

from typing import Any, Optional

from rag_wright.capabilities.answer_generator import AnswerKind, EvidenceItem, GeneratedAnswer
from rag_wright.capabilities.span_relevance_judgment import Condition, Relevance, RelevanceVerdict
from rag_wright.models.profiles import ModelRole

__all__ = ["agenerate_answer", "ajudge_spans", "EvidenceItem", "GeneratedAnswer", "AnswerKind", "RelevanceVerdict",
           "Relevance", "Condition"]


async def agenerate_answer(query: str, evidence: list[EvidenceItem], *, ws: Any,
                           guidance: Optional[str] = None) -> GeneratedAnswer:
    """A grounded, cited answer to `query` over `evidence`, or an abstention. Empty evidence abstains without a
    model call; a citation not in the evidence is dropped, and an answer left with no valid citation becomes an
    abstention (no claim without a citation). `answer_kind` says whether the evidence fully supported the answer
    (`answered`), only partly (`partial`) or not at all (`abstained`). `guidance` adds your domain's wording to the
    domain-neutral method."""
    from rag_wright.capabilities import answer_generator as gen

    model = gen.answer_model_for(ws.model_id(ModelRole.GENERAL))
    return await gen.agenerate_answer(query, evidence, model=model, guidance=guidance)


async def ajudge_spans(spans: list[tuple[str, list[tuple[str, str]]]], condition: Condition, *, ws: Any,
                       max_concurrency: int = 8, guidance: Optional[str] = None) -> list[RelevanceVerdict]:
    """Judge whether each span addresses `condition`, concurrently, one verdict per span in order. Each span is
    `(text, matched)`, where `matched` lists `(property, value)` pairs already detected on the span (context for the
    judge, not proof; pass `[]` when there are none). Every verdict is in the closed `Relevance` vocabulary: an
    unreadable judgment, or one that timed out, is `uncertain` (never a fabricated `not_relevant`). `guidance` adds
    your domain's wording (what its categories look like) to the domain-neutral method."""
    from rag_wright.capabilities import span_relevance_judgment as rel

    judge = rel.build_arelevance_judge_fn(ws.model_id(ModelRole.STRUCTURED_REASONING), guidance=guidance)
    raw = await rel.ajudge_spans(spans, condition, ajudge_fn=judge, max_concurrency=max_concurrency)
    return [rel.finalize_verdict(v) for v in raw]
