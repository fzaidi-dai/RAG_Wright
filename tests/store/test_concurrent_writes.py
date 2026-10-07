"""ING-5 (found by the doc audit's live run): ArcadeDB answers concurrent writes to the same bucket with a
`ConcurrentModificationException` ("Please retry the operation"); the store must retry them, or concurrent ingestion
(`IngestionTuning.document_concurrency`, default 2) silently loses spans and records. A failed command or transaction
is rolled back whole, so the retry is safe."""
from __future__ import annotations

import concurrent.futures as cf

import pytest

from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM
from rag_wright.contracts.span import SpanRecord
from rag_wright.store.arcadedb import ArcadeDBStore, _is_retryable_conflict
from rag_wright.store.seam import KgNode

_DB = "ragwright_test_concurrent_writes"


def test_only_a_concurrent_modification_is_retried():
    assert _is_retryable_conflict(Exception(
        "Cannot execute command: Concurrent modification on page PageId(db/9/0) in file 'Span_0.9.65536.v0.bucket' "
        "(current v.1 <> database v.2). Please retry the operation (threadId=1)"))
    assert not _is_retryable_conflict(Exception("Type with name 'Clause' was not found"))


@pytest.mark.store
def test_concurrent_span_and_record_writes_all_land():
    s = ArcadeDBStore.from_env(database=_DB, reset=True)
    try:
        s.ensure_schema()
        s._command("CREATE VERTEX TYPE Rec")
        s._command("CREATE PROPERTY Rec.k STRING")
        s._command("CREATE INDEX ON Rec (k) UNIQUE")

        def span(i):
            s.upsert_span(SpanRecord(span_id=f"d{i % 2}:0:h#{i}", parent_chunk_id=f"d{i % 2}:0:h", span_index=i,
                                     text=f"t{i}", dense_vector=[0.01] * BGE_M3_DENSE_DIM, sparse_vector={i: 1.0},
                                     document_id=f"d{i % 2}"))

        def rec(i):
            s.kg_write([KgNode("Rec", "k", {"k": f"r{i}"})])

        with cf.ThreadPoolExecutor(8) as ex:
            list(ex.map(span, range(120)))
            list(ex.map(rec, range(120)))
        assert s._query("SELECT count(*) AS n FROM Span")[0]["n"] == 120
        assert s._query("SELECT count(*) AS n FROM Rec")[0]["n"] == 120
    finally:
        s.drop()
        s.close()
