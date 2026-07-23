"""T57b (FR-C.6, ADR-0025/0026): the property extractor. Hermetic (pure mapping + stub runnable)."""

from __future__ import annotations

from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.property import PropertyDimension
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.spans.property_extractor import (
    ExtractedProperty,
    PropertyExtraction,
    SeamPropertyExtractor,
    build_record,
    dimensions_for,
)

_CID = ChunkId.of("docA", 3, "a liability cap clause body")


def _extraction(*props: ExtractedProperty) -> PropertyExtraction:
    return PropertyExtraction(properties=list(props))


def test_build_record_maps_in_scope_properties_with_provenance_and_folio():
    extraction = _extraction(
        ExtractedProperty(dimension=PropertyDimension.MUTUALITY, value="mutual", confidence=ConfidenceTag.EXTRACTED),
        ExtractedProperty(dimension=PropertyDimension.CARVE_OUT, value="indemnification", confidence=ConfidenceTag.EXTRACTED),
    )
    rec = build_record(chunk_id=_CID, function="Cap On Liability", span_id=f"{_CID}#0", extraction=extraction)
    assert rec.clause_id == str(_CID) and rec.function == "Cap On Liability"
    assert rec.folio_iri.startswith("https://folio.openlegalstandard.org/")  # clause-type IRI attached
    assert {(a.dimension, a.value) for a in rec.assertions} == {
        (PropertyDimension.MUTUALITY, "mutual"), (PropertyDimension.CARVE_OUT, "indemnification"),
    }
    for a in rec.assertions:  # every assertion is anchored + span-cited (FR-S.4 / FR-Q.6)
        assert str(a.provenance.chunk_id) == str(_CID) and a.span_id == f"{_CID}#0"


def test_build_record_drops_out_of_scope_dimensions():
    # damage_type does NOT belong to Governing Law -> dropped (function-aware schema)
    extraction = _extraction(
        ExtractedProperty(dimension=PropertyDimension.JURISDICTION, value="england", confidence=ConfidenceTag.EXTRACTED),
        ExtractedProperty(dimension=PropertyDimension.DAMAGE_TYPE, value="punitive", confidence=ConfidenceTag.EXTRACTED),
    )
    rec = build_record(chunk_id=_CID, function="Governing Law", span_id="", extraction=extraction)
    assert PropertyDimension.DAMAGE_TYPE not in {a.dimension for a in rec.assertions}
    assert {a.value for a in rec.assertions} == {"england"}
    assert PropertyDimension.JURISDICTION in dimensions_for("Governing Law")


def test_build_record_coerces_out_of_vocab_value_to_ambiguous():
    # a novel carve_out subject the LLM emitted as EXTRACTED must be retained as the AMBIGUOUS 'other' escape
    extraction = _extraction(
        ExtractedProperty(dimension=PropertyDimension.CARVE_OUT, value="act_of_god", confidence=ConfidenceTag.EXTRACTED),
    )
    rec = build_record(chunk_id=_CID, function="Cap On Liability", span_id="", extraction=extraction)
    assert len(rec.assertions) == 1
    a = rec.assertions[0]
    assert a.value == "act_of_god" and a.confidence is ConfidenceTag.AMBIGUOUS


def test_build_record_drops_empty_value():
    extraction = _extraction(
        ExtractedProperty(dimension=PropertyDimension.MUTUALITY, value="   ", confidence=ConfidenceTag.EXTRACTED),
    )
    rec = build_record(chunk_id=_CID, function="Cap On Liability", span_id="", extraction=extraction)
    assert rec.assertions == []


class _StubRunnable:
    """A structured-output runnable stub: returns None once (transient miss), then a fixed extraction."""

    def __init__(self, extraction: PropertyExtraction) -> None:
        self._extraction = extraction
        self._calls = 0

    def invoke(self, _prompt: str):
        self._calls += 1
        return None if self._calls == 1 else self._extraction  # exercises the None-retry


def test_seam_extractor_plumbing_with_stub_runnable_and_retry():
    extraction = _extraction(
        ExtractedProperty(dimension=PropertyDimension.FAVORABILITY, value="seller_favorable", confidence=ConfidenceTag.INFERRED),
    )
    extractor = SeamPropertyExtractor(runnable=_StubRunnable(extraction))
    rec = extractor(chunk_id=_CID, function="Cap On Liability", text="in no event ... liability", span_id=f"{_CID}#1")
    assert rec.function == "Cap On Liability"
    assert [(a.dimension, a.value, a.confidence) for a in rec.assertions] == [
        (PropertyDimension.FAVORABILITY, "seller_favorable", ConfidenceTag.INFERRED)
    ]
    assert rec.assertions[0].span_id == f"{_CID}#1"
