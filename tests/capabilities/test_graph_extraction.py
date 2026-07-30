"""Graph extraction (T23, FR-C.6/FR-I.4): the hybrid extractor stack over parsed chunks.

Hermetic tests inject a fake NER pipeline and a stub structured-output factory (no spaCy model, no
network) to prove each extractor produces ontology-conforming facts anchored to chunk_id with a
confidence tag, and that the capability runs extraction concurrently (FR-I.6). Live paths are opt-in:
`-m ner` uses the real spaCy model, `-m model` the real DeepSeek structured calls.
"""

from __future__ import annotations

import threading
import time

import pytest

from rag_wright.capabilities.graph_extraction import (
    ContractExtractor,
    LlmEscalationExtractor,
    SpacyNerExtractor,
    _ContractExtraction,
    _EscalatedRelationship,
    _EscalatedRelationships,
    extract_chunks_sync,
    register_graph_extraction,
)
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.extraction import ExtractionResult, run_extractors
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.ontology import ClauseCategory, EntityType, RelationshipType
from rag_wright.contracts.provenance import ConfidenceTag

_CID = ChunkId.of("docA", 0, "chunk text")


class _FakeNer:
    """A fake NER pipeline returning fixed (surface, label) pairs."""

    def __init__(self, pairs: list[tuple[str, str]]) -> None:
        self._pairs = pairs

    def ner(self, text: str) -> list[tuple[str, str]]:
        return self._pairs


class _FakeRunnable:
    def __init__(self, output: object) -> None:
        self._output = output

    def invoke(self, prompt: str) -> object:
        return self._output


def _factory_returning(output: object):
    return lambda model_id, schema: _FakeRunnable(output)


# --- SpacyNerExtractor: mentions only, no edges (ADR-0012) ----------------------------------------


def test_spacy_extractor_emits_typed_confidence_bearing_mentions_no_edges():
    ner = _FakeNer([("Acme Corp", "ORG"), ("John Smith", "PERSON"), ("Delaware", "GPE")])

    result = SpacyNerExtractor(ner).extract(_CID, "text")

    assert result.chunk_id == _CID
    assert result.relationship_facts == []  # never emits edges (ADR-0012)
    assert result.clause_facts == []
    kinds = {(m.text, m.entity_type, m.confidence) for m in result.entity_mentions}
    assert kinds == {
        ("Acme Corp", EntityType.ORGANIZATION, ConfidenceTag.EXTRACTED),
        ("John Smith", EntityType.PERSON, ConfidenceTag.EXTRACTED),
    }  # GPE (off-ontology) dropped; ORG/PERSON kept and EXTRACTED


def test_spacy_extractor_dedups_repeated_mentions():
    ner = _FakeNer([("Acme", "ORG"), ("Acme", "ORG"), ("  Acme ", "ORG")])
    result = SpacyNerExtractor(ner).extract(_CID, "text")
    assert len(result.entity_mentions) == 1  # (surface, type) deduped within a chunk


# --- ContractExtractor: clause facts + party mentions + party-structure CONTRACTS_WITH ------------


def test_contract_extractor_emits_clauses_party_mentions_and_party_edges():
    output = _ContractExtraction(
        parties=["Acme Corp", "Beta LLC", "Acme Corp"],  # duplicate collapses
        clause_categories=[ClauseCategory.GOVERNING_LAW, ClauseCategory.EXCLUSIVITY],
    )
    extractor = ContractExtractor(model_id="m", structured_factory=_factory_returning(output))

    result = extractor.extract(_CID, "text")

    assert {m.text for m in result.entity_mentions} == {"Acme Corp", "Beta LLC"}
    assert all(m.confidence is ConfidenceTag.EXTRACTED for m in result.entity_mentions)
    # exactly one CONTRACTS_WITH between the two distinct parties, EXTRACTED (structural)
    assert len(result.relationship_facts) == 1
    edge = result.relationship_facts[0]
    assert edge.relationship_type is RelationshipType.CONTRACTS_WITH
    assert {edge.source_ref, edge.target_ref} == {"Acme Corp", "Beta LLC"}
    assert edge.confidence is ConfidenceTag.EXTRACTED
    assert edge.provenance.chunk_id == _CID  # anchored to the chunk
    assert {c.category for c in result.clause_facts} == {
        ClauseCategory.GOVERNING_LAW, ClauseCategory.EXCLUSIVITY
    }


def test_contract_extractor_single_party_emits_no_edge():
    output = _ContractExtraction(parties=["Acme Corp"], clause_categories=[])
    result = ContractExtractor(model_id="m", structured_factory=_factory_returning(output)).extract(
        _CID, "text"
    )
    assert result.relationship_facts == []  # need >= 2 parties for a CONTRACTS_WITH edge


# --- LlmEscalationExtractor: hard-case relationships, INFERRED ------------------------------------


def test_llm_escalation_emits_inferred_relationships_and_skips_self_loops():
    output = _EscalatedRelationships(relationships=[
        _EscalatedRelationship(source_ref="Acme Corp", relationship_type=RelationshipType.AFFILIATE_OF,
                               target_ref="Acme Subsidiary"),
        _EscalatedRelationship(source_ref="Beta", relationship_type=RelationshipType.AFFILIATE_OF,
                               target_ref="Beta"),  # ref-level self-loop -> skipped
    ])
    result = LlmEscalationExtractor(model_id="m", structured_factory=_factory_returning(output)).extract(
        _CID, "text"
    )
    assert len(result.relationship_facts) == 1
    fact = result.relationship_facts[0]
    assert fact.confidence is ConfidenceTag.INFERRED
    assert fact.relationship_type is RelationshipType.AFFILIATE_OF
    assert fact.provenance.chunk_id == _CID


# --- the seam merges the stack; capability runs concurrently (FR-I.6) -----------------------------


def test_run_extractors_merges_the_hybrid_stack():
    ner = SpacyNerExtractor(_FakeNer([("Acme Corp", "ORG")]))
    contract = ContractExtractor(model_id="m", structured_factory=_factory_returning(
        _ContractExtraction(parties=["Acme Corp", "Beta LLC"], clause_categories=[ClauseCategory.PARTIES])
    ))
    merged = run_extractors([ner, contract], _CID, "text")
    assert merged.chunk_id == _CID
    assert len(merged.entity_mentions) == 3  # 1 from spaCy + 2 parties from contract
    assert len(merged.relationship_facts) == 1  # the party CONTRACTS_WITH
    assert len(merged.clause_facts) == 1


class _ProbeExtractor:
    name = "probe"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.inflight = 0
        self.max_inflight = 0

    def extract(self, chunk_id: ChunkId, text: str) -> ExtractionResult:
        with self._lock:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
        time.sleep(0.03)  # stand in for a GPU/network call so extractions overlap
        with self._lock:
            self.inflight -= 1
        return ExtractionResult(chunk_id=chunk_id)


def test_extract_chunks_runs_concurrently_bounded_by_the_semaphore():
    probe = _ProbeExtractor()
    items = [(ChunkId.of("docA", i, f"t{i}"), f"t{i}") for i in range(12)]

    results = extract_chunks_sync(items, extractors=[probe], max_concurrency=4)

    assert len(results) == 12
    assert probe.max_inflight == 4  # concurrent AND bounded exactly by the semaphore (FR-I.6)


def test_extract_chunks_serial_reaches_only_one_in_flight():
    probe = _ProbeExtractor()
    items = [(ChunkId.of("docA", i, f"t{i}"), f"t{i}") for i in range(4)]
    extract_chunks_sync(items, extractors=[probe], max_concurrency=1)
    assert probe.max_inflight == 1


def test_registers_under_fr_c_6():
    registry = CapabilityRegistry()
    register_graph_extraction(registry)
    reg = registry.get("graph_extraction")
    assert reg.name == "graph_extraction"
    assert reg.contract is ExtractionResult
    assert reg.kind == "subgraph"  # CAP-REG-1: multi-step LLM extractor stack


# --- live spaCy (opt-in): the real NER model ------------------------------------------------------


@pytest.mark.ner
def test_live_spacy_ner_extracts_org_and_person_mentions():
    from rag_wright.capabilities.graph_extraction import SpacyPipeline

    extractor = SpacyNerExtractor(SpacyPipeline())
    result = extractor.extract(_CID, "Acme Corporation and Beta LLC entered into this agreement. "
                                     "John Smith signed on behalf of Acme.")

    types = {m.entity_type for m in result.entity_mentions}
    assert EntityType.ORGANIZATION in types  # real OntoNotes NER finds the orgs
    assert result.relationship_facts == []  # still no edges from the real model
    assert all(m.confidence is ConfidenceTag.EXTRACTED for m in result.entity_mentions)


# --- live LLM extraction (opt-in): real DeepSeek structured calls with the enum-typed schemas ------

_CONTRACT_CHUNK = (
    "This Distribution Agreement is entered into between Acme Corporation and Beta Distribution LLC. "
    "This Agreement shall be governed by the laws of the State of Delaware. Acme grants Beta the "
    "exclusive right to distribute the Products in the Territory."
)


@pytest.mark.model
def test_live_contract_extractor_emits_ontology_facts_with_real_model():
    """Confirms the real DeepSeek structured call handles the 41-value ClauseCategory enum schema and
    returns ontology-conforming parties + clause facts + a party-structure CONTRACTS_WITH edge."""
    result = ContractExtractor().extract(_CID, _CONTRACT_CHUNK)

    assert result.entity_mentions  # found the signing parties
    assert len(result.relationship_facts) >= 1  # >=2 parties -> a CONTRACTS_WITH edge
    assert all(e.relationship_type is RelationshipType.CONTRACTS_WITH for e in result.relationship_facts)
    assert all(f.confidence is ConfidenceTag.EXTRACTED for f in result.clause_facts)
    assert all(f.provenance.chunk_id == _CID for f in result.clause_facts)  # anchored


@pytest.mark.model
def test_live_llm_escalation_returns_valid_relationships_or_none():
    """The escalation path over the real model returns only ontology-conforming, INFERRED, anchored
    relationships (or none) — the schema/enum round-trips through the real structured call."""
    result = LlmEscalationExtractor().extract(_CID, _CONTRACT_CHUNK)

    for fact in result.relationship_facts:
        assert fact.relationship_type in RelationshipType
        assert fact.confidence is ConfidenceTag.INFERRED
        assert fact.provenance.chunk_id == _CID
        assert fact.source_ref != fact.target_ref
