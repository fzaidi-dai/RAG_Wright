"""PS-R5b: a domain's entity rules (role words / phrases that are not entities) reach the engine's disambiguation and
resolution, and the reference contracts pack supplies its own from `contract_bridge.ttl`."""
from __future__ import annotations

from rag_wright.capabilities.disambiguation import disambiguate
from rag_wright.contracts.extraction import EntityMention, ExtractionResult
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.corpus.canonicalize import EntityRules


def _result(*names):
    return ExtractionResult(chunk_id=ChunkId.of("d", 0, "t"), entity_mentions=[
        EntityMention(text=n, entity_type="ORGANIZATION", confidence=ConfidenceTag.EXTRACTED) for n in names])


def test_disambiguation_applies_the_domains_rules():
    results = [_result("Acme Corp", "Supplier")]
    assert {c.key for c in disambiguate(results).clusters} == {"acme", "supplier"}
    ruled = disambiguate(results, entity_rules=EntityRules(role_terms=frozenset({"supplier"})))
    assert {c.key for c in ruled.clusters} == {"acme"} and "Supplier" in ruled.rejected


def test_the_contracts_pack_declares_its_entity_rules_in_its_ontology():
    from rag_wright.packs.contracts.ontology.loader import load_entity_rules

    rules = load_entity_rules()
    assert {"buyer", "seller", "licensee", "the parties"} <= rules.role_terms
    assert "together with" in rules.role_phrases and "collectively" in rules.role_phrases
