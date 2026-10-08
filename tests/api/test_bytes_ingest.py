"""PS-2 (G16): documents arrive as BYTES (uploads from object storage), so the public API parses and ingests bytes
without a temp file: `parse_document_bytes` / `aparse_document_bytes`, and an `IngestSource` of `data` + `name`
that `build_ingestion` and `evaluate_ingestion` handle exactly like a path."""
from __future__ import annotations

import asyncio
import pytest
from pydantic import ValidationError

from rag_wright.api import IngestSource, aparse_document_bytes, build_ingestion, evaluate_ingestion, parse_document_bytes
from tests.ingestion.test_builder import FIXTURES, _FakeEmbedder, _record_extractor, _ws

_SHEET = FIXTURES / "textile_spec_sheet.md"


def test_parse_document_bytes_forwards_to_the_engine_parse(monkeypatch):
    seen = {}

    def _stub(source_doc_id, name, data, *, cache_dir, metadata=None, include_hidden_sheets, tuning):
        seen.update(id=source_doc_id, name=name, data=data, hidden=include_hidden_sheets)
        return "sd"

    monkeypatch.setattr("rag_wright.capabilities.document_parse.parsed_source_document", _stub)
    assert parse_document_bytes("DOC", "upload.pdf", b"%PDF bytes", cache_dir="c", include_hidden_sheets=False) == "sd"
    assert seen == {"id": "DOC", "name": "upload.pdf", "data": b"%PDF bytes", "hidden": False}


def test_aparse_document_bytes_forwards_to_the_async_engine_parse(monkeypatch):
    seen = {}

    async def _astub(source_doc_id, name, data, *, cache_dir, metadata=None, include_hidden_sheets, tuning):
        seen.update(id=source_doc_id, name=name, data=data)
        return "sd"

    monkeypatch.setattr("rag_wright.capabilities.document_parse.aparsed_source_document", _astub)
    assert asyncio.run(aparse_document_bytes("DOC", "a.xlsx", b"PK", cache_dir="c")) == "sd"
    assert seen == {"id": "DOC", "name": "a.xlsx", "data": b"PK"}


def test_an_ingest_source_is_a_path_or_bytes_with_a_name():
    assert IngestSource(path="a.md").display_name == "a.md"
    src = IngestSource(data=b"# x", name="upload.md")
    assert src.display_name == "upload.md" and src.read_bytes() == b"# x"
    with pytest.raises(ValidationError):
        IngestSource(data=b"# x")  # bytes need a name (its type comes from the extension)
    with pytest.raises(ValidationError):
        IngestSource(path="a.md", data=b"# x", name="a.md")  # one form, not both
    with pytest.raises(ValidationError):
        IngestSource()  # neither


def test_build_ingestion_ingests_bytes_like_the_same_file(tmp_path):
    def run(source):
        ws = _ws()
        pipe = build_ingestion(_record_extractor, embedder=_FakeEmbedder(), progress=lambda _l: None)
        report = asyncio.run(pipe.aingest(ws, [source], cache_dir=tmp_path / "cache"))
        return report.documents[0], ws._store

    from_path, _ = run(IngestSource(path=str(_SHEET)))
    from_bytes, store = run(IngestSource(data=_SHEET.read_bytes(), name=_SHEET.name))
    assert from_bytes.dead_letter is None
    assert (from_bytes.doc_id, from_bytes.units, from_bytes.records, from_bytes.spans) == \
        (from_path.doc_id, from_path.units, from_path.records, from_path.spans)
    (doc,) = [n for n in store.nodes if n.type == "Document"]
    assert doc.props["filename"] == _SHEET.name and doc.props["media_type"] == "text/markdown"


def test_evaluate_ingestion_takes_bytes(tmp_path):
    ev = evaluate_ingestion([IngestSource(data=_SHEET.read_bytes(), name=_SHEET.name)], cache_dir=tmp_path)
    (doc,) = ev.documents
    assert doc.error is None and doc.doc == _SHEET.name and doc.units > 0


def test_bytes_entries_are_public():
    from rag_wright import api

    assert {"parse_document_bytes", "aparse_document_bytes"} <= set(api.__all__)
