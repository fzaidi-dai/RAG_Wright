"""GP-1(A): the no-LLM CUAD-Parties graph-extraction path. `parties_to_extraction` turns a contract's
known signing parties (from the CUAD 'Parties' annotation) into an `ExtractionResult` -- ORGANIZATION
mentions + a CONTRACTS_WITH fact between each pair -- with NO model call, then composes through the
existing disambiguate -> resolve_entities -> to_graph stack into a CIK-keyed graph. This is the cheap
first-light edge source (ADR-0012-compliant: real signatories, not proximity edges)."""

from __future__ import annotations

from rag_wright.capabilities.disambiguation import disambiguate
from rag_wright.capabilities.entity_resolution import resolve_entities
from rag_wright.packs.contracts.capabilities.graph_extraction import parties_to_extraction
from rag_wright.capabilities.graph_storage import to_graph
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.packs.contracts.corpus.edgar import normalize_cik
from rag_wright.ontology.registry import EntityRegistry, RegistryRecord

_CID = ChunkId.of("Acme_v_Beta_Agreement", 0, "parties")


def test_two_parties_give_mentions_and_one_contracts_with():
    er = parties_to_extraction(_CID, ["Acme Corp", "Beta Inc"])
    assert er.chunk_id == _CID
    assert {m.text for m in er.entity_mentions} == {"Acme Corp", "Beta Inc"}
    assert all(m.entity_type == "Organization" for m in er.entity_mentions)
    assert all(m.confidence == ConfidenceTag.EXTRACTED for m in er.entity_mentions)
    assert len(er.relationship_facts) == 1
    rf = er.relationship_facts[0]
    assert rf.relationship_type == "Contracts With"
    assert {rf.source_ref, rf.target_ref} == {"Acme Corp", "Beta Inc"}
    assert rf.confidence == ConfidenceTag.EXTRACTED


def test_dedups_strips_and_handles_single_or_zero_parties():
    er = parties_to_extraction(_CID, [" Acme Corp ", "Acme Corp", "", "Beta Inc"])  # dup + whitespace + empty
    assert {m.text for m in er.entity_mentions} == {"Acme Corp", "Beta Inc"}
    assert len(er.relationship_facts) == 1  # one pair, not duplicated

    solo = parties_to_extraction(_CID, ["Solo Corp"])
    assert len(solo.entity_mentions) == 1 and solo.relationship_facts == []  # a party has no counterparty

    empty = parties_to_extraction(_CID, [])
    assert empty.entity_mentions == [] and empty.relationship_facts == []


def test_three_parties_give_all_pairwise_edges():
    er = parties_to_extraction(_CID, ["A Co", "B Co", "C Co"])
    assert len(er.entity_mentions) == 3
    assert len(er.relationship_facts) == 3  # C(3,2)
    assert all(rf.relationship_type == "Contracts With" for rf in er.relationship_facts)


def test_composes_into_a_cik_keyed_graph():
    registry = EntityRegistry()
    registry.add(RegistryRecord(entity_id=normalize_cik("1"), canonical_name="Acme Corp"))
    registry.add(RegistryRecord(entity_id=normalize_cik("2"), canonical_name="Beta Inc"))
    cik1, cik2 = normalize_cik("1").value, normalize_cik("2").value

    er = parties_to_extraction(_CID, ["Acme Corp", "Beta Inc"])
    resolution = resolve_entities(disambiguate([er]), [er], resolver=registry)

    assert {e.entity_id for e in resolution.entities} == {cik1, cik2}  # both linked closed-world
    assert len(resolution.relationships) == 1
    rr = resolution.relationships[0]
    assert {rr.source_id, rr.target_id} == {cik1, cik2}

    nodes, edges = to_graph(resolution)
    assert {n.node_key for n in nodes} == {cik1, cik2}  # keyed by CIK (linked, not UNLINKED)
    assert len(edges) == 1
    assert edges[0].relationship_type == "Contracts With"
    assert {edges[0].source_key, edges[0].target_key} == {cik1, cik2}
