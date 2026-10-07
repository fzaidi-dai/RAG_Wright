"""PROD-1 (ADR-0049 generic-customer lens): the GCS `CorpusAdapter` — a real source integration. Hermetic: a
fake google-cloud-storage client is injected (no network, no bucket), so the adapter's blob-listing, parse-by-
extension, id/metadata, limit, and skip logic are all unit-tested."""

from __future__ import annotations

import pytest

from rag_wright.packs.contracts.corpus.gcs_ingestion import GcsCorpusAdapter
from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import SourceDocument


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


def test_non_text_blob_defers_to_a_pending_document_via_parse_doc():
    # CHUNK-7 (ADR-0058) + 0009-ASYNC-INGEST: a non-text customer document yields a PendingDocument (the parse --
    # incl. OCR/VLM escalation -- is DEFERRED so the async ingest runs it concurrently + bounded, not upfront).
    # Its `parse` thunk routes through parse_doc (structure-preserving `.parsed`); the gcs metadata rides along.
    from rag_wright.capabilities.parsing import ParsedDocument
    from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import PendingDocument

    seen = {}

    def parse_doc(sid, name, data):
        seen["args"] = (sid, name, len(data))
        return SourceDocument(
            source_doc_id=sid, text="Section 1 body",
            parsed=ParsedDocument(source_doc_id=sid, content_hash="a" * 64, manifest_path="/m"))

    docs = list(_adapter([_FakeBlob("prod1-corpus/deal.pdf", data=b"%PDF-1.7")], parse_doc=parse_doc).documents())
    assert isinstance(docs[0], PendingDocument)  # DEFERRED, not parsed upfront
    assert docs[0].metadata["source"] == "gcs" and docs[0].metadata["blob"] == "prod1-corpus/deal.pdf"
    sd = docs[0].parse()  # the thunk parses (downloads + parses) on demand
    assert sd.parsed is not None and sd.text == "Section 1 body"
    assert seen["args"] == (docs[0].source_doc_id, "prod1-corpus/deal.pdf", 8)


def test_parse_doc_is_preferred_over_parse_bytes_when_both_set():
    from rag_wright.capabilities.parsing import ParsedDocument
    from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import PendingDocument

    def parse_doc(sid, name, data):
        return SourceDocument(source_doc_id=sid, text="structured",
                              parsed=ParsedDocument(source_doc_id=sid, content_hash="b" * 64, manifest_path="/m"))

    docs = list(_adapter([_FakeBlob("prod1-corpus/deal.pdf", data=b"x")],
                         parse_doc=parse_doc, parse_bytes=lambda n, d: "flat-text").documents())
    assert isinstance(docs[0], PendingDocument)          # parse_doc path chosen (deferred), not parse_bytes
    assert docs[0].parse().text == "structured"          # parse_doc wins (structure preserved)


def test_non_text_blob_without_a_parser_fails_clearly():
    with pytest.raises(NotImplementedError, match="parse_bytes"):
        list(_adapter([_FakeBlob("prod1-corpus/deal.pdf", data=b"x")]).documents())


def test_include_filters_to_a_curated_subset_by_basename():
    blobs = [_FakeBlob(f"prod1-corpus/{n}.txt", text=n) for n in ("a", "b", "c", "d")]
    got = list(_adapter(blobs, include=frozenset({"b.txt", "d.txt"})).documents())
    assert sorted(d.text for d in got) == ["b", "d"]


def test_parse_bytes_seam_routes_pdf_through_document_to_text():
    # DOCPARSE-1: a PDF blob is parsed to text via the injected parse_bytes (docling in production)
    from rag_wright.corpus.document_parser import document_to_text

    class _Doc:
        def export_to_markdown(self, **_):
            return "# Master Services Agreement\n\nThe parties agree ..."

    docs = list(_adapter(
        [_FakeBlob("prod1-corpus/msa.pdf", data=b"%PDF-1.7 real bytes")],
        parse_bytes=lambda name, data: document_to_text(_Doc()),
    ).documents())
    assert docs[0].text.startswith("# Master Services Agreement")
    assert docs[0].metadata["blob"].endswith("msa.pdf")
