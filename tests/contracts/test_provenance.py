"""Tests for the provenance and confidence contracts (T2, FR-S.4, RAC-2).

FR-S.4 has two rules, and these tests pin both: every text-bearing unit carries provenance (the
source document and the chunk it came from), and every graph-derived fact carries a confidence tag
drawn from a closed set. Provenance is what makes "no claim without a citation" (FR-Q.6) enforceable
downstream, and the confidence tag is what marks a graph fact as evidence, not truth.
"""

import enum

import pytest
from pydantic import ValidationError

from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.provenance import ConfidenceTag, GraphFact, Provenance


def _chunk_id(source="doc-1", index=0, content="body"):
    return ChunkId.of(source, index, content)


# --- ConfidenceTag: the closed set (RAC-2, FR-S.4) ------------------------------------------


def test_confidence_tag_has_exactly_the_three_values():
    assert {t.value for t in ConfidenceTag} == {"EXTRACTED", "INFERRED", "AMBIGUOUS"}


def test_confidence_tag_is_a_str_enum():
    assert issubclass(ConfidenceTag, enum.Enum)
    assert ConfidenceTag.EXTRACTED == "EXTRACTED"


# --- Provenance: source document + chunk (RAC-2, FR-S.4) ------------------------------------


def test_provenance_of_derives_source_doc_from_chunk_id():
    cid = _chunk_id(source="contract-42", index=3)
    prov = Provenance.of(cid)
    assert prov.chunk_id == cid
    assert prov.source_doc_id == "contract-42"


def test_provenance_requires_both_source_and_chunk():
    cid = _chunk_id()
    with pytest.raises(ValidationError):
        Provenance(source_doc_id="doc-1")  # missing chunk_id
    with pytest.raises(ValidationError):
        Provenance(chunk_id=cid)  # missing source_doc_id


def test_provenance_rejects_source_inconsistent_with_chunk():
    cid = _chunk_id(source="doc-1")
    with pytest.raises(ValidationError):
        Provenance(source_doc_id="doc-2", chunk_id=cid)


def test_provenance_accepts_consistent_explicit_fields():
    cid = _chunk_id(source="doc-1")
    prov = Provenance(source_doc_id="doc-1", chunk_id=cid)
    assert prov.source_doc_id == cid.source_doc_id


def test_provenance_is_frozen():
    prov = Provenance.of(_chunk_id())
    with pytest.raises(ValidationError):
        prov.source_doc_id = "other"


# The redundant source_doc_id is only safe because the two fields cannot disagree on ANY path a
# Provenance comes into existence, including deserialization from the store. Pin every path.


def test_provenance_model_validate_rejects_inconsistent():
    cid = _chunk_id(source="doc-1")
    with pytest.raises(ValidationError):
        Provenance.model_validate({"source_doc_id": "doc-2", "chunk_id": cid.model_dump()})


def test_provenance_model_validate_json_rejects_inconsistent():
    # The store-load path (deserialize from persisted JSON) must enforce consistency too.
    cid = _chunk_id(source="doc-1")
    good_json = Provenance.of(cid).model_dump_json()
    tampered = good_json.replace('"source_doc_id":"doc-1"', '"source_doc_id":"doc-2"', 1)
    with pytest.raises(ValidationError):
        Provenance.model_validate_json(tampered)


def test_provenance_round_trips_through_json():
    prov = Provenance.of(_chunk_id(source="doc-1", index=4))
    assert Provenance.model_validate_json(prov.model_dump_json()) == prov


# --- GraphFact: provenance + confidence (RAC-2, FR-S.4, FR-I.4) -----------------------------


def test_graph_fact_carries_provenance_and_confidence():
    cid = _chunk_id(source="doc-1")
    fact = GraphFact(provenance=Provenance.of(cid), confidence=ConfidenceTag.EXTRACTED)
    assert fact.provenance.chunk_id == cid  # the originating chunk_id (FR-I.4)
    assert fact.confidence is ConfidenceTag.EXTRACTED


def test_graph_fact_accepts_confidence_as_string():
    fact = GraphFact(provenance=Provenance.of(_chunk_id()), confidence="INFERRED")
    assert fact.confidence is ConfidenceTag.INFERRED


@pytest.mark.parametrize("bad", ["MAYBE", "HIGH", "extracted", "", "UNKNOWN"])
def test_graph_fact_rejects_confidence_outside_the_set(bad):
    with pytest.raises(ValidationError):
        GraphFact(provenance=Provenance.of(_chunk_id()), confidence=bad)


def test_graph_fact_requires_provenance_and_confidence():
    with pytest.raises(ValidationError):
        GraphFact(confidence=ConfidenceTag.EXTRACTED)  # missing provenance
    with pytest.raises(ValidationError):
        GraphFact(provenance=Provenance.of(_chunk_id()))  # missing confidence
