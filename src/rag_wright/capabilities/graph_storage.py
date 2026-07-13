"""Graph storage (FR-I.4, FR-I.5, T25): write resolved entities + relationships into the store, gated.

A seam-bound ingestion pipeline step (no SPEC section-5 slug, so it registers nothing and authors no
ARD manifest — like chunk write, T20). It maps a document's T24 `ResolutionResult` to store-level
nodes and edges and writes them through the `Store` seam in ONE transaction (FR-S.1: a chunk and its
extracted entities land together), content-hash gated so an unchanged document does no graph work
(FR-I.5). The graph is the relationship layer only (SPEC §8): entity nodes carry id/name/type and edges
carry the relationship — no heavy structured data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from rag_wright.capabilities.entity_resolution import ResolutionResult
from rag_wright.contracts.ontology import EntityType
from rag_wright.corpus.canonicalize import normalize_entity_name
from rag_wright.store.seam import GraphEdge, GraphNode, Store

GraphWriteStatus = Literal["written", "skipped"]


@dataclass(frozen=True)
class GraphWriteResult:
    """The outcome of writing one document's graph."""

    source_doc_id: str
    status: GraphWriteStatus
    node_count: int
    edge_count: int


def _node_key(entity_id: Optional[str], surface_key: str) -> str:
    """The vertex identity: the canonical CIK when linked, else an `UNLINKED:<key>` surrogate (so a CIK
    lookup matches only linked entities, and an unlinked ref lands on the same node as its cluster)."""
    return entity_id if entity_id else f"UNLINKED:{surface_key}"


def to_graph(resolution: ResolutionResult) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Map a `ResolutionResult` to store nodes + edges. A relationship endpoint that has no standalone
    entity (a ref-only endpoint) gets a minimal node so every edge connects to a vertex."""
    nodes: dict[str, GraphNode] = {}
    for entity in resolution.entities:
        key = _node_key(entity.entity_id, entity.key)
        nodes[key] = GraphNode(
            node_key=key, entity_id=entity.entity_id or "", name=entity.representative,
            entity_type=entity.entity_type.value, confidence=entity.confidence.value,
            chunk_id=entity.chunk_ids[0] if entity.chunk_ids else "",
        )

    edges: list[GraphEdge] = []
    for rel in resolution.relationships:
        source_key = _node_key(rel.source_id, normalize_entity_name(rel.source_ref))
        target_key = _node_key(rel.target_id, normalize_entity_name(rel.target_ref))
        for key, resolved_id, ref in (
            (source_key, rel.source_id, rel.source_ref),
            (target_key, rel.target_id, rel.target_ref),
        ):
            nodes.setdefault(  # ref-only endpoint: minimal node (org by default, confidence from the edge)
                key,
                GraphNode(
                    node_key=key, entity_id=resolved_id or "", name=ref,
                    entity_type=EntityType.ORGANIZATION.value, confidence=rel.confidence.value,
                    chunk_id=rel.chunk_id,
                ),
            )
        edges.append(
            GraphEdge(
                source_key=source_key, target_key=target_key,
                relationship_type=rel.relationship_type.value, confidence=rel.confidence.value,
                chunk_id=rel.chunk_id,
            )
        )
    return list(nodes.values()), edges


class GraphWriter:
    """Writes a document's resolved graph to the store, content-hash gated (FR-I.5). A checkpoint per
    document keyed by content hash makes an unchanged document a no-op; the store holds the graph."""

    def __init__(self, store: Store, *, checkpoint_dir: Path) -> None:
        self._store = store
        self._checkpoints = Path(checkpoint_dir) / "graph_checkpoints"
        self._checkpoints.mkdir(parents=True, exist_ok=True)

    def write_document(
        self, source_doc_id: str, content_hash: str, resolution: ResolutionResult
    ) -> GraphWriteResult:
        """Write the document's nodes/edges (one transaction), unless an unchanged run already did."""
        checkpoint = self._load(source_doc_id)
        if checkpoint is not None and checkpoint["content_hash"] == content_hash:
            return GraphWriteResult(source_doc_id, "skipped", 0, 0)  # content-hash gate: no graph work

        nodes, edges = to_graph(resolution)
        self._store.write_graph(nodes, edges)
        self._save(source_doc_id, content_hash)
        return GraphWriteResult(source_doc_id, "written", len(nodes), len(edges))

    def _path(self, source_doc_id: str) -> Path:
        return self._checkpoints / f"{source_doc_id}.json"

    def _load(self, source_doc_id: str) -> Optional[dict]:
        path = self._path(source_doc_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def _save(self, source_doc_id: str, content_hash: str) -> None:
        self._path(source_doc_id).write_text(
            json.dumps({"content_hash": content_hash, "status": "complete"}), encoding="utf-8"
        )
