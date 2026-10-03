"""Tests for the ontology and extraction-target models (T4, FR-C.8, RAC-4).

The ontology is the closed vocabulary the graph conforms to: the 41 CUAD clause categories
(ADR-0002, authoritative) plus the party/entity types and relationship types. The extraction-target
models (EntityNode, ClauseFact, RelationshipFact) are what graph extraction (T5) produces and graph
storage (T24) writes; because their type fields are the ontology enums, a fact whose type is not in
the ontology is rejected at construction. T8 populates/derives the ontology from CUAD + EDGAR and
finalizes the entity/relationship membership.
"""

import pytest
from pydantic import ValidationError

from rag_wright.contracts.identifiers import ChunkId, EntityId
from rag_wright.contracts.ontology import ClauseCategory, ClauseFact, EntityNode, RelationshipFact
from rag_wright.contracts.provenance import ConfidenceTag, Provenance
from rag_wright.ontology import contract_taxonomy as T


def _prov(source="doc-1"):
    return Provenance.of(ChunkId.of(source, 0, "body"))


def _eid(value="0000000001"):
    return EntityId.of(value)


# --- ClauseCategory: the 41 CUAD categories (RAC-4, ADR-0002) --------------------------------


def test_clause_category_has_exactly_41_members():
    assert len(list(ClauseCategory)) == 41


def test_clause_category_values_are_unique():
    values = [c.value for c in ClauseCategory]
    assert len(set(values)) == len(values)


def test_clause_category_includes_known_cuad_categories():
    # A spot-check of anchor categories from the CUAD label set.
    assert ClauseCategory.GOVERNING_LAW.value == "Governing Law"
    assert ClauseCategory.NON_COMPETE.value == "Non-Compete"
    assert ClauseCategory.THIRD_PARTY_BENEFICIARY.value == "Third Party Beneficiary"
    assert ClauseCategory.PARTIES.value == "Parties"


# --- Entity/relationship taxonomy: de-domained to the reference pack (DD-5, ADR-0066/0117) --
# The engine contracts are taxonomy-free (entity_type/relationship_type are plain strings); the reference
# CONTRACT pack declares the closed value sets. The hardcoded EntityType/RelationshipType enums are gone.


def test_engine_contracts_no_longer_export_the_hardcoded_taxonomy_enums():
    import rag_wright.contracts.ontology as onto
    assert not hasattr(onto, "EntityType") and not hasattr(onto, "RelationshipType")


def test_reference_pack_declares_the_contract_entity_and_relationship_vocab():
    assert T.ENTITY_TYPES == {"Organization", "Person"}
    assert T.RELATIONSHIP_TYPES == {"Contracts With", "Affiliate Of"}


# --- EntityNode: canonical node, carries an opaque entity-type string (RAC-4, DD-5) ---------


def test_entity_node_valid():
    node = EntityNode(entity_id=_eid(), entity_type=T.ORGANIZATION, name="Acme Corp")
    assert node.entity_type == "Organization"
    assert node.name == "Acme Corp"


def test_entity_node_accepts_any_domain_entity_type():
    # DD-5: the engine no longer constrains the type -- a non-contract domain's type is equally valid
    node = EntityNode(entity_id=_eid(), entity_type="Vehicle", name="Unit 7")
    assert node.entity_type == "Vehicle"


def test_entity_node_requires_id_type_and_name():
    with pytest.raises(ValidationError):
        EntityNode(entity_type=T.ORGANIZATION, name="Acme")  # missing entity_id
    with pytest.raises(ValidationError):
        EntityNode(entity_id=_eid(), entity_type=T.ORGANIZATION)  # missing name


# --- ClauseFact: conforms to ClauseCategory, carries provenance + confidence (RAC-4) --------


def test_clause_fact_valid_and_carries_provenance_and_confidence():
    fact = ClauseFact(
        category=ClauseCategory.GOVERNING_LAW,
        provenance=_prov("doc-1"),
        confidence=ConfidenceTag.EXTRACTED,
    )
    assert fact.category is ClauseCategory.GOVERNING_LAW
    assert fact.provenance.source_doc_id == "doc-1"
    assert fact.confidence is ConfidenceTag.EXTRACTED


def test_clause_fact_rejects_category_outside_ontology():
    with pytest.raises(ValidationError):
        ClauseFact(category="Force Majeure", provenance=_prov(), confidence=ConfidenceTag.EXTRACTED)


def test_clause_fact_requires_confidence():
    with pytest.raises(ValidationError):
        ClauseFact(category=ClauseCategory.INSURANCE, provenance=_prov())


# --- RelationshipFact: directed, pre-resolution mention refs, conformance (RAC-4) -----------
# Endpoints are pre-resolution entity mentions (surface forms), directed source_ref -> target_ref.


def _rel(**overrides):
    base = dict(
        source_ref="Acme Corp",
        relationship_type=T.CONTRACTS_WITH,
        target_ref="Beta LLC",
        provenance=_prov(),
        confidence=ConfidenceTag.INFERRED,
    )
    base.update(overrides)
    return RelationshipFact(**base)


def test_relationship_fact_valid_carries_direction_provenance_confidence():
    fact = _rel()
    assert fact.source_ref == "Acme Corp"
    assert fact.target_ref == "Beta LLC"
    assert fact.relationship_type == "Contracts With"
    assert fact.confidence is ConfidenceTag.INFERRED


def test_relationship_fact_accepts_any_domain_edge_type():
    # DD-5: relationship_type is an opaque string; a non-contract edge is equally valid
    assert _rel(relationship_type="Owns").relationship_type == "Owns"


def test_relationship_fact_is_directed_not_symmetric():
    # source and target are distinct roles; swapping them yields a different fact.
    a = _rel(source_ref="Acme Corp", target_ref="Beta LLC")
    b = _rel(source_ref="Beta LLC", target_ref="Acme Corp")
    assert a != b


def test_relationship_fact_rejects_ref_level_self_loop():
    with pytest.raises(ValidationError):
        _rel(source_ref="Acme Corp", target_ref="Acme Corp")


def test_relationship_fact_allows_distinct_mentions_that_may_resolve_to_one_entity():
    # Two distinct surface forms are allowed here; whether they resolve to the same entity_id is
    # entity resolution's concern (T24), not this contract's.
    fact = _rel(source_ref="Acme Corp", target_ref="Acme Corporation")
    assert fact.source_ref != fact.target_ref


@pytest.mark.parametrize("ref", ["", "   "])
def test_relationship_fact_rejects_blank_ref(ref):
    with pytest.raises(ValidationError):
        _rel(source_ref=ref)


def test_contracts_with_references_agreement_via_provenance():
    # The agreement a CONTRACTS_WITH fact derives from is its provenance source document, which is
    # what makes shared-party multi-hop answerable ("which companies share an agreement with X").
    fact = _rel(provenance=_prov("agreement-7"))
    assert fact.provenance.source_doc_id == "agreement-7"
