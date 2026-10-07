"""The GENERIC graph-extraction targets (moved from `contracts.ontology`, ING-8b): a canonical entity node and a
directed entity-to-entity relationship fact. Their type fields are OPAQUE domain strings (DD-5, ADR-0066/0117) --
the domain pack names the taxonomy; the engine constrains only the shape (provenance, confidence, non-empty
distinct endpoints)."""

from __future__ import annotations

from pydantic import BaseModel, field_validator, model_validator

from rag_wright.contracts.identifiers import EntityId
from rag_wright.contracts.provenance import GraphFact


class EntityNode(BaseModel):
    """A canonical entity node in the graph skeleton (SPEC.md section 8): identifier, name, type.

    No facts and no confidence: entity nodes are canonical (resolved against the registry, FR-C.7), not
    extracted facts. DD-5 (ADR-0066/0117): `entity_type` is an OPAQUE string the domain names -- the engine
    does not constrain the taxonomy. The reference contract pack's value set lives in
    `ontology/contract_taxonomy.py` (e.g. "Organization"/"Person"); a new domain names its own.
    """

    entity_id: EntityId
    entity_type: str
    name: str


class RelationshipFact(GraphFact):
    """A directed entity-to-entity relationship extracted from a chunk (extends `GraphFact`:
    provenance + confidence).

    The endpoints are pre-resolution entity mentions (surface forms), directed `source_ref ->
    target_ref`: source and target are distinct roles, not a symmetric pair, so T8 can add directed
    corporate-hierarchy relationship types without reopening this model. Entity resolution
    (FR-C.7 / T24) later maps each ref to a canonical `entity_id`.

    `relationship_type` is an OPAQUE domain string (DD-5, ADR-0066/0117): the engine does not constrain the
    edge taxonomy; the caller (a domain graph) names it, and the reference contract pack's value set lives in
    `ontology/contract_taxonomy.py` (e.g. "Contracts With"/"Affiliate Of"). The agreement a co-party fact
    derives from is its provenance's source document (`provenance.source_doc_id`); because every `GraphFact`
    requires provenance, that reference is always present, which makes shared-party multi-hop questions
    answerable from the graph.

    Self-loop is rejected here only at the ref level (the same mention as both source and target).
    The post-resolution check (two *distinct* mentions that resolve to the same `entity_id`) belongs
    with entity resolution (T24), because two mentions can legitimately resolve to one entity.
    """

    source_ref: str  # pre-resolution entity mention (surface form)
    relationship_type: str  # opaque domain edge type (DD-5); the caller/domain pack names it
    target_ref: str  # pre-resolution entity mention (surface form)

    @field_validator("source_ref", "target_ref")
    @classmethod
    def _ref_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("source_ref and target_ref must be non-empty entity mentions")
        return v

    @model_validator(mode="after")
    def _no_ref_self_loop(self) -> RelationshipFact:
        if self.source_ref.strip() == self.target_ref.strip():
            raise ValueError(
                "source_ref and target_ref must be distinct mentions (ref-level self-loop); the "
                "post-resolution same-entity_id check belongs with entity resolution (T24)"
            )
        return self
