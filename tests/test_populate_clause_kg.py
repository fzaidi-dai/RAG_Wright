"""KG-3: the typed-KG population driver's crash-safe per-clause loop (hermetic; no LLM, no store)."""

from __future__ import annotations

import threading

from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.property import ClausePropertyRecord, PropertyAssertion, PropertyDimension
from rag_wright.contracts.provenance import ConfidenceTag, Provenance
from scripts.populate_clause_kg import extract_and_write


class _FakeStore:
    """DD-1b: `extract_and_write` now writes via `ContractKGStore(store).write_clause_kg`, so the fake implements
    the generic store seam it uses -- `kg_read` (the content-hash gate -> absent) + `kg_write` (record the clause)."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def kg_read(self, node_type, **kwargs):
        return []  # content-hash gate: the clause is not already written

    def kg_write(self, nodes, edges=()):
        self.written.extend(n.props["clause_id"] for n in nodes if n.key_field == "clause_id")


def _item(seed: str = "capA") -> tuple[ChunkId, str, str, str]:
    return ChunkId.of(seed, 0, seed + " body"), "Cap On Liability", "some clause text", "span-1"


def _record(cid: ChunkId) -> ClausePropertyRecord:
    prov = Provenance.of(cid)
    return ClausePropertyRecord(
        clause_id=str(cid), function="Cap On Liability",
        assertions=[PropertyAssertion(
            provenance=prov, confidence=ConfidenceTag.EXTRACTED,
            dimension=PropertyDimension.MUTUALITY, value="mutual", span_id="",
        )],
    )


def test_extract_and_write_writes_typed_record() -> None:
    item = _item()
    cid = item[0]
    store = _FakeStore()
    errors = [0]
    seen: dict = {}
    def _extract(*, chunk_id, function, text, span_id):
        seen["span_id"] = span_id  # the cache's span_id must reach the extractor (CUAD citation)
        return _record(chunk_id)
    n = extract_and_write(item, extractor=_extract, store=store, write_lock=threading.Lock(), errors=errors)
    assert n == 1
    assert store.written == [str(cid)]
    assert seen["span_id"] == "span-1"
    assert errors == [0]


def test_a_failing_clause_is_isolated_not_fatal() -> None:
    """A per-clause extraction error is logged + counted, never fatal to the resumable run."""
    def _boom(**_kwargs):
        raise RuntimeError("model timeout")

    store = _FakeStore()
    errors = [0]
    n = extract_and_write(
        _item(), extractor=_boom, store=store, write_lock=threading.Lock(), errors=errors,
    )
    assert n == 0
    assert store.written == []
    assert errors == [1]
