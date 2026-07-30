"""Reranking (FR-C.4, FR-Q.2): a cross-encoder precision gate over the retrieved candidates.

A BGE-reranker cross-encoder re-scores each retrieved candidate against the query and cuts the list to
a top-k set, the precision gate before any expensive downstream work (synthesis). Unlike the bi-encoder
retrieval legs (T19/T21), a cross-encoder reads the query and the passage together, so it is more
precise but too costly to run over the whole index — it runs only over the already-fused candidate list
(T21).

This capability is a pure function of `(query, passage)` pairs: it is given the candidates *with their
passage text* and returns them reranked and cut. It deliberately does not fetch the text itself — the
passage a candidate is scored on (the chunk summary, or the full chunk text from the parse manifest,
FR-I.1) is a wiring choice the compiled query graph makes, not this capability's, so reranking stays
decoupled from the store and the manifest. Grounded on `FlagEmbedding.FlagAutoReranker` (the public
auto-reranker; `.compute_score` over sentence pairs).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from rag_wright.capabilities.registry import CapabilityRegistry

DEFAULT_TOP_K = 5  # the precision-gate cut before synthesis; the eval and caller can override
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"  # the BGE-M3 companion cross-encoder


@runtime_checkable
class Reranker(Protocol):
    """The cross-encoder seam: score each passage against the query (higher is more relevant)."""

    def score(self, query: str, passages: list[str]) -> list[float]: ...


class Passage(BaseModel):
    """A retrieved candidate with the passage text to score (the reranker's input unit)."""

    model_config = {"frozen": True}

    chunk_id: str
    source_doc_id: str
    text: str


class ScoredCandidate(BaseModel):
    """One reranked candidate: the relevance score the cross-encoder gave it."""

    model_config = {"frozen": True}

    chunk_id: str
    source_doc_id: str
    score: float


class RerankResult(BaseModel):
    """The reranking capability's output: the top-k candidates by cross-encoder score (FR-C.4)."""

    model_config = {"frozen": True}

    query: str
    candidates: list[ScoredCandidate]  # cross-encoder order, best first, cut to top_k


class BGEReranker:
    """The real reranker: `FlagEmbedding.FlagAutoReranker` (model loaded lazily)."""

    def __init__(self, model_name: str = DEFAULT_RERANKER_MODEL, *, use_fp16: bool = False) -> None:
        from FlagEmbedding import FlagAutoReranker

        self._model = FlagAutoReranker.from_finetuned(model_name, use_fp16=use_fp16)

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        scores = self._model.compute_score([(query, passage) for passage in passages])
        # compute_score returns a scalar for a single pair; normalize to a list of floats.
        if not isinstance(scores, (list, tuple)):
            scores = [scores]
        return [float(s) for s in scores]


def rerank(
    query: str,
    passages: list[Passage],
    *,
    reranker: Reranker,
    top_k: int = DEFAULT_TOP_K,
) -> RerankResult:
    """Rerank the candidate passages by cross-encoder relevance and cut to the top-`k` set.

    The cross-encoder scores each `(query, passage.text)` pair; the passages are sorted by score
    descending (stable, so equal scores keep their incoming fused order) and cut to `top_k`. An empty
    candidate list reranks to an empty result.
    """
    if not passages:
        return RerankResult(query=query, candidates=[])

    scores = reranker.score(query, [p.text for p in passages])
    if len(scores) != len(passages):
        raise ValueError(
            f"reranker returned {len(scores)} scores for {len(passages)} passages (must be 1:1)"
        )

    scored = [
        ScoredCandidate(chunk_id=p.chunk_id, source_doc_id=p.source_doc_id, score=score)
        for p, score in zip(passages, scores)
    ]
    scored.sort(key=lambda c: c.score, reverse=True)  # stable: ties keep the incoming fused order
    return RerankResult(query=query, candidates=scored[:top_k])


def register_reranking(registry: CapabilityRegistry) -> None:
    """Register reranking under FR-C.4 (`reranking`, an in-process `function`)."""
    registry.register(
        "reranking",
        contract=RerankResult,
        kind="model",  # BGE cross-encoder inference (CAP-REG-1)
        display_name="Reranking (BGE cross-encoder precision gate)",
    )
