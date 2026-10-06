"""Capability discovery: rank the live ARD catalog by semantic match to a task (embedding-based).

A product-side agent that needs to PLAN over the engine — when invoking a known capability by name through its seam
is not enough and it must find what's available for a task — calls `discover(task, resources=ws)`, then orchestrates
the `ainvoke_*` calls over the top matches. Ranking is by BGE-M3 embedding similarity of the task against each
capability's `representative_queries` + description (the signal manifests carry for exactly this), using the
workspace's query embedder — the same embedder and vector space retrieval uses. Domain-free: it ranks whatever is in
the LIVE catalog (`MANIFEST_SPECS`), so it covers the product's OWN registered capabilities, not just the reference
pack. Complements `capability_index` (the flat listing) with task-ranked selection.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from rag_wright.api.workspace import WorkspaceHandle


@dataclass(frozen=True)
class Discovered:
    """One ranked capability match from `discover` — enough for an agent to pick and invoke it by `slug`."""

    slug: str
    kind: str
    description: str
    representative_queries: tuple[str, ...]
    score: float  # cosine similarity in [-1, 1]; higher is a better task match


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0.0 or nb == 0.0 else dot / (na * nb)


def _match_text(spec) -> str:
    rq = " ".join(spec.representative_queries or ())
    return f"{spec.display_name}. {spec.description} {rq}".strip()


def discover(query: str, *, resources: WorkspaceHandle, kind: Optional[str] = None, k: int = 8) -> list[Discovered]:
    """Rank the live ARD catalog by semantic match to `query`; return the top `k` (optionally filtered to one
    `kind`: `subgraph` / `model` / `function` / `agent_skill` / `mcp_tool`). Embedding-based, via the workspace's
    query embedder (BGE-M3, the same space retrieval uses). Returns `[]` when the (filtered) catalog is empty; raises
    `RuntimeError` if the workspace has no query embedder available (discovery needs one)."""
    from rag_wright.capabilities.manifests import MANIFEST_SPECS

    specs = [s for s in MANIFEST_SPECS.values() if kind is None or s.kind == kind]
    if not specs:
        return []
    embedder = getattr(resources, "_embedder", None)
    if embedder is None:
        raise RuntimeError("discover() needs a query embedder; none is available on this workspace")
    dense, _sparse = embedder.encode_batch([query] + [_match_text(s) for s in specs])
    query_vec, cand_vecs = dense[0], dense[1:]
    ranked = sorted(
        zip(specs, cand_vecs), key=lambda sv: _cosine(query_vec, sv[1]), reverse=True
    )
    return [
        Discovered(
            slug=s.slug,
            kind=s.kind,
            description=s.description,
            representative_queries=tuple(s.representative_queries or ()),
            score=round(_cosine(query_vec, v), 4),
        )
        for s, v in ranked[:k]
    ]
