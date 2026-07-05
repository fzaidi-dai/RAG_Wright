"""Tests for the graph extraction contract and extractor seam (T5, FR-C.6 / FR-I.4, RAC-5).

Graph extraction is a hybrid stack (FR-C.6): contract extraction, a lightweight NER/dependency
path, an LLM escalation, and later OpenIE. The point of this contract is the *seam*: a real
`Extractor` interface the capability iterates over, so a new extractor (OpenIE) plugs in without
reopening the capability. The tests bind stub extractors behind the seam and prove that adding a
second one requires no change to `run_extractors` or `ExtractionResult`, and that every produced
fact conforms to the ontology and is anchored to the originating `chunk_id`.
"""

import pytest
from pydantic import ValidationError

from rag_wright.contracts.extraction import (
    EntityMention,
    Extractor,
    ExtractionResult,
    run_extractors,
)
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.ontology import (
    ClauseCategory,
    ClauseFact,
    EntityType,
    RelationshipFact,
    RelationshipType,
)
from rag_wright.contracts.provenance import ConfidenceTag, Provenance


def _cid(source="doc-1", index=0, content="body"):
    return ChunkId.of(source, index, content)


# Two stub extractors standing in for real ones. _ClauseStub is the "ships first" path; _OpenIEStub
# is the future extractor that must slot in behind the same seam with no code change.


class _ClauseStub:
    name = "clause-stub"

    def extract(self, chunk_id: ChunkId, text: str) -> ExtractionResult:
        return ExtractionResult(
            chunk_id=chunk_id,
            entity_mentions=[EntityMention(text="Acme Corp", entity_type=EntityType.ORGANIZATION)],
            clause_facts=[
                ClauseFact(
                    category=ClauseCategory.GOVERNING_LAW,
                    provenance=Provenance.of(chunk_id),
                    confidence=ConfidenceTag.EXTRACTED,
                )
            ],
        )


class _OpenIEStub:
    name = "openie-stub"

    def extract(self, chunk_id: ChunkId, text: str) -> ExtractionResult:
        return ExtractionResult(
            chunk_id=chunk_id,
            relationship_facts=[
                RelationshipFact(
                    source_ref="Acme Corp",
                    relationship_type=RelationshipType.CONTRACTS_WITH,
                    target_ref="Beta LLC",
                    provenance=Provenance.of(chunk_id),
                    confidence=ConfidenceTag.INFERRED,
                )
            ],
        )


# --- the extractor seam (RAC-5) -------------------------------------------------------------


def test_stub_extractors_conform_to_the_extractor_protocol():
    assert isinstance(_ClauseStub(), Extractor)
    assert isinstance(_OpenIEStub(), Extractor)


def test_object_without_extract_is_not_an_extractor():
    class NotAnExtractor:
        name = "nope"

    assert not isinstance(NotAnExtractor(), Extractor)


def test_run_single_extractor_returns_its_facts():
    cid = _cid()
    result = run_extractors([_ClauseStub()], cid, "some text")
    assert result.chunk_id == cid
    assert [f.category for f in result.clause_facts] == [ClauseCategory.GOVERNING_LAW]
    assert result.relationship_facts == []


def test_seam_is_load_bearing_second_extractor_added_without_code_change():
    # Adding _OpenIEStub is just appending to the extractor list; run_extractors and
    # ExtractionResult are untouched. This is the OpenIE extension point proven load-bearing.
    cid = _cid()
    result = run_extractors([_ClauseStub(), _OpenIEStub()], cid, "some text")
    assert len(result.clause_facts) == 1
    assert len(result.relationship_facts) == 1
    assert result.relationship_facts[0].relationship_type is RelationshipType.CONTRACTS_WITH


def test_empty_pipeline_returns_empty_result_anchored_to_chunk():
    cid = _cid()
    result = run_extractors([], cid, "text")
    assert result.chunk_id == cid
    assert result.clause_facts == [] and result.relationship_facts == []


# --- ExtractionResult conforms to the ontology and is anchored to chunk_id (RAC-5, FR-I.4) ---


def test_result_facts_conform_and_carry_chunk_id_and_confidence():
    cid = _cid()
    result = run_extractors([_ClauseStub(), _OpenIEStub()], cid, "text")
    for fact in [*result.clause_facts, *result.relationship_facts]:
        assert fact.provenance.chunk_id == cid  # originating chunk_id (FR-I.4)
        assert fact.confidence in ConfidenceTag


def test_result_rejects_a_fact_anchored_to_a_different_chunk():
    cid = _cid("doc-1")
    other = _cid("doc-2")
    with pytest.raises(ValidationError):
        ExtractionResult(
            chunk_id=cid,
            clause_facts=[
                ClauseFact(
                    category=ClauseCategory.INSURANCE,
                    provenance=Provenance.of(other),  # points at a different chunk
                    confidence=ConfidenceTag.EXTRACTED,
                )
            ],
        )


def test_merge_rejects_results_for_different_chunks():
    cid_a = _cid("doc-1")
    cid_b = _cid("doc-2")
    result_b = _ClauseStub().extract(cid_b, "text")
    with pytest.raises((ValidationError, ValueError)):
        ExtractionResult.merge(cid_a, [result_b])


# --- EntityMention: typed pre-resolution mention --------------------------------------------


def test_entity_mention_is_typed():
    m = EntityMention(text="Beta LLC", entity_type=EntityType.ORGANIZATION)
    assert m.entity_type is EntityType.ORGANIZATION


def test_entity_mention_rejects_blank_text():
    with pytest.raises(ValidationError):
        EntityMention(text="  ", entity_type=EntityType.PERSON)


def test_entity_mention_rejects_type_outside_ontology():
    with pytest.raises(ValidationError):
        EntityMention(text="Acme", entity_type="ROBOT")
