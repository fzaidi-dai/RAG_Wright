"""Entity resolution (FR-C.7, T24): link canonical mention clusters to a canonical-id registry (closed-world).

The closed-world linking stage after canonicalization (T23b). It resolves each `MentionCluster` to its
canonical `entity_id` (the registry's canonical id) against the injected registry, and resolves the relationship endpoints
(the `RelationshipFact` `source_ref`/`target_ref` refs) as the SAME stream, so an entity that appears
both as a standalone mention and as a relationship endpoint lands on one node, not two (FR-C.7, ADR-0004).

Matching strategy (§16.3, ADR-0013): exact normalized-surface-form match against the registry,
closed-world — an unknown surface form resolves to `None` (unlinked), never a fabricated id. No fuzzy /
embedding / LLM matching: a wrong fuzzy link is a silent false merge onto a canonical entity, worse than
leaving a cluster unlinked (which the human sees), and T23b already collapsed the surface variants, so
exact-normalized is high-recall for entities the registry knows. Two post-resolution invariants live
here, not in the T4 contract, because both need resolved ids: a relationship whose two refs resolve to
the SAME entity_id is dropped as a self-loop, and the two mention channels are deduped across each other.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Optional

from pydantic import BaseModel

from rag_wright.capabilities.disambiguation import DisambiguationResult, MentionCluster
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.extraction import ExtractionResult
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.corpus.canonicalize import EntityRules, is_entity, normalize_entity_name
from rag_wright.ontology.registry import EntityResolver


class ResolvedEntity(BaseModel):
    """A mention cluster linked (or not) to a canonical registry id. `entity_id is None` = unlinked
    (closed-world: not in the registry — expected for private/unknown entities — never fabricated)."""

    key: str
    representative: str
    entity_type: str  # opaque domain entity type (DD-5); carried through from the cluster
    entity_id: Optional[str]  # the registry's canonical id, or None (unlinked)
    confidence: ConfidenceTag
    chunk_ids: list[str]


class ResolvedRelationship(BaseModel):
    """A relationship whose endpoints are resolved to canonical ids (or None). Self-loops are dropped
    before this is emitted (a relationship whose refs resolve to the same entity_id)."""

    source_ref: str
    target_ref: str
    source_id: Optional[str]
    target_id: Optional[str]
    relationship_type: str  # opaque domain edge type (DD-5); carried through from the fact
    confidence: ConfidenceTag
    chunk_id: str


class ResolutionResult(BaseModel):
    """The capability's output: resolved entities and relationships (self-loops removed)."""

    entities: list[ResolvedEntity]
    relationships: list[ResolvedRelationship]


def _resolve_cluster(cluster: MentionCluster, resolver: EntityResolver) -> Optional[str]:
    """Resolve a cluster to a canonical id: the first of its surface forms the resolver knows (closed-world)."""
    for surface in (cluster.representative, *cluster.variants):
        entity_id = resolver.resolve(surface)
        if entity_id is not None:
            return entity_id.value
    return None


def resolve_entities(
    clusters: DisambiguationResult,
    results: Sequence[ExtractionResult],
    *,
    resolver: EntityResolver,
    entity_rules: Optional[EntityRules] = None,
) -> ResolutionResult:
    """Link clusters to canonical ids and resolve relationship endpoints as one stream (self-loops dropped).

    Each cluster resolves to a canonical id (or None) via the injected `EntityResolver` seam (DD-3) — the
    resolution STRATEGY is the domain's concern, not this capability's. A relationship ref resolves by matching
    a cluster key first — so a ref that is the same entity as a standalone mention takes that cluster's id (the
    two-channel dedup, ADR-0004) — falling back to a direct resolver lookup only for a ref with no cluster. A
    relationship whose two refs resolve to the same non-None id is dropped (self-loop). These invariants are
    domain-neutral and stay here; only the surface-form lookup is delegated to the resolver. `entity_rules` (the
    domain's non-entity role words / phrases, PS-R5b) apply to relationship endpoints as in `disambiguate`.
    """
    entities: list[ResolvedEntity] = []
    key_to_id: dict[str, Optional[str]] = {}
    for cluster in clusters.clusters:
        entity_id = _resolve_cluster(cluster, resolver)
        key_to_id[cluster.key] = entity_id  # channel-unification map (may be None: same unlinked entity)
        entities.append(
            ResolvedEntity(
                key=cluster.key, representative=cluster.representative,
                entity_type=cluster.entity_type, entity_id=entity_id,
                confidence=cluster.confidence, chunk_ids=cluster.chunk_ids,
            )
        )

    def _resolve_ref(ref: str) -> Optional[str]:
        if not is_entity(ref, entity_rules):
            return None
        key = normalize_entity_name(ref)
        if key in key_to_id:  # same entity as a standalone mention -> its id (even if None)
            return key_to_id[key]
        resolved = resolver.resolve(ref)
        return resolved.value if resolved is not None else None

    relationships: list[ResolvedRelationship] = []
    for result in results:
        chunk_id = result.chunk_id.value
        for fact in result.relationship_facts:
            source_id = _resolve_ref(fact.source_ref)
            target_id = _resolve_ref(fact.target_ref)
            if source_id is not None and source_id == target_id:
                continue  # post-resolution self-loop: two distinct mentions, one entity -> dropped
            relationships.append(
                ResolvedRelationship(
                    source_ref=fact.source_ref, target_ref=fact.target_ref,
                    source_id=source_id, target_id=target_id,
                    relationship_type=fact.relationship_type, confidence=fact.confidence,
                    chunk_id=chunk_id,
                )
            )
    return ResolutionResult(entities=entities, relationships=relationships)


def fragmentation_rate(result: ResolutionResult, gold_by_key: dict[str, str]) -> float:
    """Residual fragmentation after resolution (risk 5), measured against gold entity labels: the
    fraction of true entities that end as more than one node. Resolution reduces fragmentation when the
    registry links surface variants T23b's exact-key clustering left separate (e.g. an acronym alias) to
    the same canonical id. A cluster is a node by its `entity_id` if linked, else by its own key (unlinked)."""
    nodes_per_entity: dict[str, set[str]] = defaultdict(set)
    for entity in result.entities:
        gold = gold_by_key.get(entity.key)
        if gold is None:
            continue
        nodes_per_entity[gold].add(entity.entity_id or f"unlinked:{entity.key}")
    if not nodes_per_entity:
        return 0.0
    fragmented = sum(1 for nodes in nodes_per_entity.values() if len(nodes) > 1)
    return fragmented / len(nodes_per_entity)


def register_entity_resolution(registry: CapabilityRegistry) -> None:
    """Register under FR-C.7 (`entity_resolution`, an in-process `function`)."""
    registry.register(
        "entity_resolution",
        contract=ResolutionResult,
        kind="function",
        display_name="Entity resolution (closed-world to a canonical-id registry)",
    )
