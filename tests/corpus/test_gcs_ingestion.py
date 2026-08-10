"""PROD-1 (ADR-0049 generic-customer lens): the GCS `CorpusAdapter` — a real source integration. Hermetic: a
fake google-cloud-storage client is injected (no network, no bucket), so the adapter's blob-listing, parse-by-
extension, id/metadata, limit, and skip logic are all unit-tested."""

from __future__ import annotations

import pytest

from rag_wright.corpus.gcs_ingestion import GcsCorpusAdapter
from rag_wright.subgraphs.contract_ingestion_pipeline import SourceDocument


class _FakeBlob:
    def __init__(self, name: str, *, text: str | None = None, data: bytes | None = None):
        self.name = name
        self._text = text
        self._data = data

    def download_as_text(self) -> str:
        if self._text is None:
            raise AssertionError("download_as_text on a non-text blob")
        return self._text

    def download_as_bytes(self) -> bytes:
        return self._data if self._data is not None else b""


class _FakeClient:
    def __init__(self, blobs):
        self._blobs = blobs

    def list_blobs(self, bucket, prefix=""):  # mirrors google.cloud.storage.Client.list_blobs
        return [b for b in self._blobs if b.name.startswith(prefix)]


def _adapter(blobs, **kw):
    return GcsCorpusAdapter("dreamai-bucket", "prod1-corpus/", client=_FakeClient(blobs), **kw)


def test_yields_source_documents_from_text_blobs():
    docs = list(_adapter([
        _FakeBlob("prod1-corpus/maud_contract_0.txt", text="AGREEMENT AND PLAN OF MERGER ..."),
        _FakeBlob("prod1-corpus/contractnli_nda_001.txt", text="NON-DISCLOSURE AGREEMENT ..."),
    ]).documents())
    assert len(docs) == 2
    assert all(isinstance(d, SourceDocument) for d in docs)
    assert docs[0].text.startswith("AGREEMENT AND PLAN OF MERGER")
    # canonical id is derived from the blob BASENAME (stable, corpus-agnostic); metadata carries provenance
    assert docs[0].metadata == {"source": "gcs", "bucket": "dreamai-bucket", "blob": "prod1-corpus/maud_contract_0.txt"}
    assert docs[0].source_doc_id and docs[1].source_doc_id != docs[0].source_doc_id


def test_only_lists_under_the_prefix_and_skips_dir_markers_and_empty():
    docs = list(_adapter([
        _FakeBlob("prod1-corpus/", text=""),                       # dir marker -> skipped
        _FakeBlob("prod1-corpus/a.txt", text="   "),               # whitespace-only -> skipped
        _FakeBlob("prod1-corpus/b.txt", text="real contract text"),
        _FakeBlob("other/c.txt", text="not under prefix"),         # filtered by list_blobs prefix
    ]).documents())
    assert [d.text for d in docs] == ["real contract text"]


def test_limit_caps_the_document_count():
    blobs = [_FakeBlob(f"prod1-corpus/{i}.txt", text=f"c{i}") for i in range(10)]
    assert len(list(_adapter(blobs, limit=3).documents())) == 3


def test_non_text_blob_uses_the_injected_parse_bytes_seam():
    # a real customer PDF/docx: the adapter routes bytes -> the injected docling-style parser
    docs = list(_adapter(
        [_FakeBlob("prod1-corpus/deal.pdf", data=b"%PDF-1.7 ...")],
        parse_bytes=lambda name, data: f"parsed:{name}:{len(data)}",
    ).documents())
    assert docs[0].text == "parsed:prod1-corpus/deal.pdf:12"


def test_non_text_blob_without_a_parser_fails_clearly():
    with pytest.raises(NotImplementedError, match="parse_bytes"):
        list(_adapter([_FakeBlob("prod1-corpus/deal.pdf", data=b"x")]).documents())


def test_include_filters_to_a_curated_subset_by_basename():
    blobs = [_FakeBlob(f"prod1-corpus/{n}.txt", text=n) for n in ("a", "b", "c", "d")]
    got = list(_adapter(blobs, include=frozenset({"b.txt", "d.txt"})).documents())
    assert sorted(d.text for d in got) == ["b", "d"]
