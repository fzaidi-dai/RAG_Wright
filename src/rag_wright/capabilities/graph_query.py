"""Graph query (FR-C.5, FR-Q.3, T26): answer relational/multi-hop questions by graph traversal.

Traverses the knowledge graph (T25) from a start entity over relationship edges and returns the answer
shaped as EVIDENCE for fusion (T27) — cited `chunk_id`s, `entity_id`s, and confidence tags — treated as
evidence to verify, not truth (SPEC §8/§14), never a final ranked list. It **surfaces** confidence on
every path; it does **not** filter or down-weight edges by confidence (FR-C.5/FR-Q.3). Acting on
confidence — being confidence-aware, abstaining — is the answer generator's job (FR-Q.6 / T29).
"""

from __future__ import annotations

from pydantic import BaseModel

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.ontology import RelationshipType
from rag_wright.store.seam import Store


class GraphEvidence(BaseModel):
    """One reached entity as a cited evidence item (not a scored result)."""

    entity_id: str  # the reached entity (a candidate answer)
    name: str
    hops: int
    path_entity_ids: list[str]  # the entity_id chain from start to target (the traversal evidence)
    chunk_ids: list[str]  # the chunks the path's edges were extracted from (no claim without a citation)
    confidences: list[str]  # the confidence tag of each edge on the path (surfaced, not filtered)


class GraphAnswer(BaseModel):
    """The graph query capability's output: candidate answers as evidence for fusion (FR-C.5, FR-Q.3)."""

    start_entity_id: str
    relationship_type: str
    evidence: list[GraphEvidence]  # evidence for fusion (T27), NOT a final ranked list


def graph_query(
    start_entity_id: str,
    *,
    store: Store,
    relationship_type: RelationshipType = RelationshipType.CONTRACTS_WITH,
    max_hops: int = 1,
) -> GraphAnswer:
    """Traverse `relationship_type` from `start_entity_id` up to `max_hops` and return cited evidence.

    Every edge on every path is surfaced with its `chunk_id` and confidence tag; no edge is dropped or
    re-weighted by confidence here (that is the generator's job, FR-Q.6). The evidence is unranked.
    """
    rows = store.graph_neighbors(
        start_entity_id, relationship_type=relationship_type.value, max_hops=max_hops
    )
    evidence = [
        GraphEvidence(
            entity_id=row["target_id"], name=row["target_name"], hops=row["hops"],
            path_entity_ids=row["path_entity_ids"], chunk_ids=row["path_chunk_ids"],
            confidences=row["path_confidences"],
        )
        for row in rows
    ]
    return GraphAnswer(
        start_entity_id=start_entity_id, relationship_type=relationship_type.value, evidence=evidence
    )


def register_graph_query(registry: CapabilityRegistry) -> None:
    """Register graph query under FR-C.5 (`graph_query`, an in-process `function`)."""
    registry.register(
        "graph_query",
        contract=GraphAnswer,
        kind="function",
        display_name="Graph query (cited relational/multi-hop answer)",
    )
