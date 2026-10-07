"""Entity resolution (T24, FR-C.7): closed-world linking of clusters to a canonical-id resolver.

Hermetic tests over a small in-memory resolver: known -> correct canonical id / unknown -> None (never
fabricated), the two-channel dedup (a relationship ref that is the same entity as a standalone mention
takes that cluster's id), the post-resolution self-loop drop, fragmentation measured on a labeled
fixture, and registration. DD-3 (ADR-0067 P5c): the resolution STRATEGY is the injected `EntityResolver`
seam -- the generic default is the exact-normalized surface-form `EntityRegistry`; the SEC pack injects the
EDGAR-CIK registry; a product may bind any strategy honoring the seam.
"""

from __future__ import annotations

from rag_wright.capabilities.disambiguation import DisambiguationResult, MentionCluster
from rag_wright.capabilities.entity_resolution import (
    ResolutionResult,
    fragmentation_rate,
    register_entity_resolution,
    resolve_entities,
)
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.identifiers import ChunkId, EntityId
from rag_wright.packs.contracts.schemas.ontology import RelationshipFact
from rag_wright.contracts.provenance import ConfidenceTag, Provenance
from rag_wright.ontology.registry import EntityRegistry, EntityResolver, RegistryRecord

_ORG = "Organization"
_X = ConfidenceTag.EXTRACTED
_ACME = EntityId.of("0000000001")
_BETA = EntityId.of("0000000002")


def _registry() -> EntityRegistry:
    reg = EntityRegistry()
    reg.add(RegistryRecord(entity_id=_ACME, canonical_name="Acme Corporation", ticker="ACME",
                           aliases=["Acme Inc"]))
    reg.add(RegistryRecord(entity_id=_BETA, canonical_name="Beta Distribution LLC"))
    return reg


def _cluster(key: str, representative: str, *variants: str, chunk_ids=("docA:0:h",)) -> MentionCluster:
    return MentionCluster(key=key, representative=representative, variants=list(variants or (representative,)),
                          entity_type=_ORG, confidence=_X, chunk_ids=list(chunk_ids))


def _disambig(*clusters: MentionCluster) -> DisambiguationResult:
    return DisambiguationResult(clusters=list(clusters), rejected=[])


def _rel_result(idx, source_ref, rel, target_ref):
    cid = ChunkId.of("docA", idx, f"t{idx}")
    fact = RelationshipFact(provenance=Provenance.of(cid), confidence=_X,
                            source_ref=source_ref, relationship_type=rel, target_ref=target_ref)
    from rag_wright.contracts.extraction import ExtractionResult
    return ExtractionResult(chunk_id=cid, relationship_facts=[fact])


# --- closed-world linking -------------------------------------------------------------------------


def test_known_cluster_resolves_to_cik_unknown_resolves_to_none():
    clusters = _disambig(
        _cluster("acme corporation", "Acme Corporation"),
        _cluster("private co", "Private Co"),  # not in the registry
    )
    result = resolve_entities(clusters, [], resolver=_registry())

    by_key = {e.key: e for e in result.entities}
    assert by_key["acme corporation"].entity_id == "0000000001"  # linked to the correct CIK
    assert by_key["private co"].entity_id is None  # closed-world: unlinked, never fabricated


def test_cluster_resolves_via_an_alias_variant():
    # the representative fails but a variant ("Acme Inc") is a registry alias -> linked
    clusters = _disambig(_cluster("acme", "Acme", "Acme Inc"))
    result = resolve_entities(clusters, [], resolver=_registry())
    assert result.entities[0].entity_id == "0000000001"


# --- two-channel dedup + self-loop drop (RAC-24) -------------------------------------------------


def test_relationship_ref_takes_the_matching_cluster_id():
    clusters = _disambig(_cluster("acme corporation", "Acme Corporation"),
                         _cluster("beta distribution", "Beta Distribution LLC"))
    results = [_rel_result(0, "Acme Corporation", "Contracts With", "Beta Distribution LLC")]

    result = resolve_entities(clusters, results, resolver=_registry())

    edge = result.relationships[0]
    assert edge.source_id == "0000000001"  # same node as the standalone Acme mention
    assert edge.target_id == "0000000002"


def test_post_resolution_self_loop_is_dropped():
    # two DISTINCT surface forms that resolve to the same entity -> self-loop dropped (RAC-24)
    clusters = _disambig(_cluster("acme corporation", "Acme Corporation"))
    results = [_rel_result(0, "Acme Corporation", "Affiliate Of", "Acme Inc")]

    result = resolve_entities(clusters, results, resolver=_registry())

    assert result.relationships == []  # both refs -> 0000000001, so the edge is a self-loop


def test_unresolved_refs_do_not_self_loop_drop():
    # two distinct unlinked refs are different entities -> kept (not dropped)
    clusters = _disambig()
    results = [_rel_result(0, "Private One", "Affiliate Of", "Private Two")]
    result = resolve_entities(clusters, results, resolver=_registry())
    assert len(result.relationships) == 1
    assert result.relationships[0].source_id is None and result.relationships[0].target_id is None


# --- fragmentation (risk 5) ----------------------------------------------------------------------


def test_resolution_reduces_fragmentation_via_alias_linking():
    # two clusters T23b left separate ("Acme Corporation" and its alias "Acme Inc") both link to one CIK
    clusters = _disambig(_cluster("acme corporation", "Acme Corporation"),
                         _cluster("acme inc", "Acme Inc"))
    result = resolve_entities(clusters, [], resolver=_registry())

    gold = {"acme corporation": "ACME", "acme inc": "ACME"}  # both are truly the same entity
    assert fragmentation_rate(result, gold) == 0.0  # both -> CIK 0000000001 -> one node


def test_fragmentation_counts_a_split_entity():
    clusters = _disambig(_cluster("acme corporation", "Acme Corporation"),
                         _cluster("acme systems", "Acme Systems"))  # unlinked (not in registry)
    result = resolve_entities(clusters, [], resolver=_registry())
    gold = {"acme corporation": "ACME", "acme systems": "ACME"}  # (hypothetically) one entity
    assert fragmentation_rate(result, gold) == 1.0  # one linked, one unlinked -> two nodes -> fragmented


def test_registers_under_fr_c_7():
    registry = CapabilityRegistry()
    register_entity_resolution(registry)
    reg = registry.get("entity_resolution")
    assert reg.name == "entity_resolution"
    assert reg.contract is ResolutionResult
    assert reg.kind == "function"


# --- DD-3: the EntityResolver seam (ADR-0067 P5c) ------------------------------------------------


def test_entity_registry_satisfies_the_entity_resolver_protocol():
    # the generic default resolver structurally satisfies the seam (runtime_checkable)
    assert isinstance(EntityRegistry(), EntityResolver)


def test_resolve_entities_uses_any_injected_resolver_not_just_the_registry():
    # a product may bind ANY resolution strategy; the capability depends on the SEAM, not EntityRegistry
    class _CustomResolver:
        """A stand-in custom strategy: resolves only the exact surface 'GAMMA' -> a canonical id."""

        def resolve(self, surface_form: str):
            return EntityId.of("gamma-ltd") if surface_form == "GAMMA" else None

    assert isinstance(_CustomResolver(), EntityResolver)
    clusters = _disambig(_cluster("gamma", "GAMMA"), _cluster("delta", "Delta"))

    result = resolve_entities(clusters, [], resolver=_CustomResolver())

    by_key = {e.key: e for e in result.entities}
    assert by_key["gamma"].entity_id == "gamma-ltd"  # resolved by the injected strategy
    assert by_key["delta"].entity_id is None  # unknown -> closed-world None


def test_sec_corpus_resolves_via_the_injected_cik_resolver():
    # acceptance: the SEC pack's EDGAR-CIK resolver (build_edgar_registry) is injected unchanged
    from rag_wright.packs.contracts.corpus.edgar import build_edgar_registry

    sec = build_edgar_registry([{"cik_str": 320193, "title": "Apple Inc.", "ticker": "AAPL"}])
    clusters = _disambig(_cluster("apple inc", "Apple Inc."))

    result = resolve_entities(clusters, [], resolver=sec)

    assert result.entities[0].entity_id == "0000320193"  # the EDGAR CIK (format owned by the SEC pack)


def test_non_sec_doc_resolves_via_the_surface_form_default():
    # acceptance: the generic default links a NON-CIK canonical id by exact normalized surface form
    reg = EntityRegistry()
    reg.add(RegistryRecord(entity_id=EntityId.of("acme-holdings-ltd"), canonical_name="Acme Holdings Ltd"))
    clusters = _disambig(_cluster("acme holdings", "Acme Holdings Ltd"))

    result = resolve_entities(clusters, [], resolver=reg)

    assert result.entities[0].entity_id == "acme-holdings-ltd"  # generic surface-form id, no CIK shape
