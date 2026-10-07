"""EP-REF-1b-iii (R3b, ADR-0117): the clause-type taxonomy facade + `span_locations` on `ContractKGStore`
(what EP-SEAM-3 lifts). Hermetic tests prove the pure taxonomy helpers and the span/clause-id assembly over a
fake store; the `-m store` test proves `span_locations` reads a real span index + clause KG and maps the two
identifier spaces (span_id <-> clause_id) via each clause's `span_id`.
"""
from __future__ import annotations

import json

import pytest

from rag_wright.packs.contracts.capabilities.contract_kg_store import ContractKGStore


# --- pure clause-type taxonomy (no store) -------------------------------------------------------


def test_clause_type_vocabulary_is_the_function_label_set():
    vocab = ContractKGStore.clause_type_vocabulary()
    assert isinstance(vocab, tuple) and len(vocab) > 0 and all(isinstance(v, str) for v in vocab)
    assert "Cap On Liability" in vocab  # a known clause type


def test_canonical_clause_type_maps_or_returns_none():
    assert ContractKGStore.canonical_clause_type("Cap On Liability") == "Cap On Liability"  # canonical -> itself
    assert ContractKGStore.canonical_clause_type("CAP ON LIABILITY") == "Cap On Liability"  # case-insensitive
    assert ContractKGStore.canonical_clause_type("not a real clause type xyz") is None  # unmappable -> None


# --- span_locations over a fake store -----------------------------------------------------------


class _FakeStore:
    """Fakes the reads span_locations uses: the generic all_spans_by_document + the clause-range query (ING-8e)."""

    def all_spans_by_document(self, contract_id):
        return [
            {"span_id": "K:0:h#0", "text": "cap clause text", "pages": [3],
             "bbox": json.dumps([1.0, 2.0, 3.0, 4.0]), "doc_start": 10, "doc_end": 42},
            {"span_id": "K:1:h#0", "text": "no-clause span", "pages": [], "bbox": None,
             "doc_start": 50, "doc_end": 60},  # a span that never became a clause -> clause_ids == []
        ]

    def _query(self, sql):
        assert "FROM Clause" in sql
        return [
            {"clause_id": "K:0:h", "function": "Cap On Liability", "span_id": "K:0:h#0"},
            {"clause_id": "K:9:h", "function": "Indemnification", "span_id": "OTHER#9"},  # span not here -> dropped
        ]


def test_span_locations_assembles_positions_and_maps_clause_ids():
    locs = {loc.span_id: loc for loc in ContractKGStore(_FakeStore()).span_locations("K")}
    assert set(locs) == {"K:0:h#0", "K:1:h#0"}
    a = locs["K:0:h#0"]
    assert a.clause_ids == ["K:0:h"] and a.pages == [3] and a.bbox == (1.0, 2.0, 3.0, 4.0)
    assert a.doc_start == 10 and a.doc_end == 42 and a.text == "cap clause text"
    assert locs["K:1:h#0"].clause_ids == []  # a span with no clause carries an empty list, not an error
    # the clause whose span ("OTHER#9") is not in this document was dropped (no invented location)
    assert all("OTHER#9" != loc.span_id for loc in locs.values())


# --- live ArcadeDB (opt-in) ----------------------------------------------------------------------


@pytest.fixture
def store():
    from rag_wright.store.arcadedb import ArcadeDBStore

    s = ArcadeDBStore.from_env(database="ragwright_test_span_locations", reset=True)
    s.ensure_schema()
    yield s
    s.drop()
    s.close()


@pytest.mark.store
def test_span_locations_roundtrip_live(store):
    from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM
    from rag_wright.contracts.identifiers import ChunkId
    from rag_wright.packs.contracts.schemas.property import ClausePropertyRecord, PropertyAssertion, PropertyDimension
    from rag_wright.contracts.provenance import ConfidenceTag, Provenance
    from rag_wright.contracts.span import SpanRecord

    cid = ChunkId.of("K", 0, "K body")
    span_id = f"{cid}#0"
    dense = [0.0] * BGE_M3_DENSE_DIM
    dense[0] = 1.0
    store.upsert_span(SpanRecord(
        span_id=span_id, parent_chunk_id=str(cid), span_index=0, text="In no event shall Supplier be liable.",
        primary_tag="Cap On Liability", dense_vector=dense, sparse_vector={1: 1.0}, document_id="K",
        doc_start=10, doc_end=48, pages=[3], bbox=(1.0, 2.0, 3.0, 4.0)))
    ContractKGStore(store).write_clause_kg(ClausePropertyRecord(
        clause_id=str(cid), function="Cap On Liability", span_id=span_id,  # the clause-level span bridge
        assertions=[PropertyAssertion(provenance=Provenance.of(cid), confidence=ConfidenceTag.EXTRACTED,
                                      dimension=PropertyDimension.MUTUALITY, value="mutual", span_id=span_id)]))

    locs = ContractKGStore(store).span_locations("K")
    assert len(locs) == 1
    loc = locs[0]
    assert loc.span_id == span_id and loc.clause_ids == [str(cid)]  # the span<->clause bridge resolved
    assert loc.pages == [3] and loc.bbox == (1.0, 2.0, 3.0, 4.0)
    assert loc.doc_start == 10 and loc.doc_end == 48 and "liable" in loc.text
