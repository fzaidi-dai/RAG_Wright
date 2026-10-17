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


_CONFLICT = ("Cannot execute command: Concurrent modification on page PageId(db/9/680) in file 'Span_0.9.65536.v0.bucket' "
             "(current v.113 <> database v.114). Please retry the operation (threadId=65)")


def _flaky(failures: int):
    """A write that fails like the ArcadeDB client does: it logs the error response, then raises."""
    import logging

    client_log = logging.getLogger("arcadedb_python.api.sync")
    calls = []

    def write():
        calls.append(1)
        if len(calls) <= failures:
            client_log.error("ArcadeDB Error Response: {'detail': %r, 'exception': "
                             "'com.arcadedb.exception.ConcurrentModificationException'}", _CONFLICT)
            raise Exception(_CONFLICT)
        return "ok"
    return write, calls


def test_a_retried_conflict_prints_no_error_and_says_it_was_retried(caplog, monkeypatch):
    # PS-49 (TexWright's 2026-10-17 request): a conflict the retry absorbs must not read like a failure.
    from rag_wright.store import arcadedb

    monkeypatch.setattr(arcadedb.time, "sleep", lambda _s: None)
    write, calls = _flaky(failures=2)
    with caplog.at_level("INFO"):
        assert arcadedb._retry_on_conflict(write) == "ok"
    assert len(calls) == 3
    assert not [r for r in caplog.records if r.levelname == "ERROR"]
    retried = [r.getMessage() for r in caplog.records if r.name == arcadedb.__name__]
    assert len(retried) == 1 and "retried 2 times" in retried[0] and "landed" in retried[0]


def test_a_conflict_that_outlasts_the_retries_is_raised_and_logged(caplog, monkeypatch):
    from rag_wright.store import arcadedb

    monkeypatch.setattr(arcadedb.time, "sleep", lambda _s: None)
    write, calls = _flaky(failures=arcadedb._CONFLICT_RETRIES)
    with caplog.at_level("INFO"), pytest.raises(Exception, match="Concurrent modification"):
        arcadedb._retry_on_conflict(write)
    assert len(calls) == arcadedb._CONFLICT_RETRIES
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1 and errors[0].name == arcadedb.__name__ and "gave up" in errors[0].getMessage()


def test_an_error_outside_a_retry_still_prints():
    # The filter hides only conflicts the retry is handling; any other client error response still reaches the log.
    import logging

    from rag_wright.store import arcadedb  # noqa: F401 - installs the filter

    record = logging.LogRecord("arcadedb_python.api.sync", logging.ERROR, __file__, 0,
                               "ArcadeDB Error Response: %s", (_CONFLICT,), None)
    assert all(f.filter(record) for f in logging.getLogger("arcadedb_python.api.sync").filters)
