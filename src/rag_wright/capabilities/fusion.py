"""Fusion (FR-Q.4, T27): union and deduplicate the two evidence streams on `chunk_id`, capped.

Combines the reranked retrieval top set (T22) and the graph-cited chunks (T26) into one deduplicated,
capped evidence set for synthesis/generation. This is deliberately NOT a score fusion: the graph
returns an answer with cited chunks, not a comparable ranked list, so there is no common score to fuse.
It is a deterministic union — retrieval chunks first (in their reranked order), then the graph-cited
chunks (in order of appearance), each `chunk_id` kept once with a record of which stream(s) surfaced it.
"""

from __future__ import annotations

from pydantic import BaseModel

from rag_wright.capabilities.graph_query import GraphAnswer
from rag_wright.capabilities.reranking import RerankResult

DEFAULT_UNION_CAP = 20  # the fused evidence set is capped before synthesis (§16.7)

RETRIEVAL = "retrieval"
GRAPH = "graph"


class FusedChunk(BaseModel):
    """One chunk in the fused evidence set: its id and which stream(s) surfaced it (no score — this is
    a union, not a score fusion)."""

    chunk_id: str
    sources: list[str]  # subset of {"retrieval", "graph"}, sorted


class FusionResult(BaseModel):
    """The fused, deduplicated, capped evidence set for synthesis (T28) / generation (T29)."""

    chunks: list[FusedChunk]


def fuse(
    reranked: RerankResult, graph: GraphAnswer, *, cap: int = DEFAULT_UNION_CAP
) -> FusionResult:
    """Union the reranked candidates and the graph-cited chunks on `chunk_id`, capped, deterministic.

    Order is deterministic: reranked candidates first (in their order), then graph-cited chunks (first
    appearance). A chunk surfaced by both streams appears once, tagged with both sources. The union is
    then cut to `cap`.
    """
    sources: dict[str, set[str]] = {}
    order: list[str] = []

    def _add(chunk_id: str, source: str) -> None:
        if chunk_id not in sources:
            sources[chunk_id] = set()
            order.append(chunk_id)
        sources[chunk_id].add(source)

    for candidate in reranked.candidates:
        _add(candidate.chunk_id, RETRIEVAL)
    for evidence in graph.evidence:
        for chunk_id in evidence.chunk_ids:
            _add(chunk_id, GRAPH)

    chunks = [FusedChunk(chunk_id=chunk_id, sources=sorted(sources[chunk_id])) for chunk_id in order[:cap]]
    return FusionResult(chunks=chunks)


