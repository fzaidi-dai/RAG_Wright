"""T16: parsing (Docling) — FR-C.1, RAC-16.

Hermetic tests inject a stub `Parser` (a call-counting fake `DocumentConverter`) to prove the
capability's own logic: it produces a `ParsedDocument`, caches the structured representation so a
document is parsed once (content-hash gated), re-parses on changed content, reloads the structured
doc, and registers under FR-C.1. The real Docling parse of a CUAD PDF and a scanned filing (headings,
tables, OCR) is the opt-in `-m parse` test, since it needs Docling models and the gitignored corpus.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from docling_core.types.doc.document import DoclingDocument

from rag_wright.capabilities.parsing import (
    ParsedDocument,
    load_document,
    parse,
    register_parsing,
)
from rag_wright.capabilities.registry import CapabilityRegistry


def _stub_doc(law: str = "Delaware") -> DoclingDocument:
    doc = DoclingDocument(name="stub")
    doc.add_text(label="section_header", text="Governing Law")
    doc.add_text(label="text", text=f"This Agreement is governed by the laws of {law}.")
    return doc


class _StubParser:
    """A fake DocumentConverter that counts conversions (to prove parse-once)."""

    def __init__(self) -> None:
        self.calls = 0

    def convert(self, source: Path) -> DoclingDocument:
        self.calls += 1
        return _stub_doc()


def _pdf(path: Path, content: bytes = b"%PDF-1.4 fake") -> Path:
    path.write_bytes(content)
    return path


def test_parse_writes_a_manifest_and_returns_a_parsed_document(tmp_path):
    src = _pdf(tmp_path / "contract_a.pdf")
    parser = _StubParser()

    parsed = parse(src, cache_dir=tmp_path / "parsed", parser=parser)

    assert isinstance(parsed, ParsedDocument)
    assert parsed.source_doc_id == "contract_a"
    assert parsed.content_hash and len(parsed.content_hash) == 64  # sha256 hex
    assert Path(parsed.manifest_path).exists()  # structured representation cached
    assert parser.calls == 1


def test_parse_is_content_hash_gated_so_a_document_is_parsed_once(tmp_path):
    src = _pdf(tmp_path / "contract_a.pdf")
    parser = _StubParser()

    first = parse(src, cache_dir=tmp_path / "parsed", parser=parser)
    second = parse(src, cache_dir=tmp_path / "parsed", parser=parser)  # unchanged content

    assert parser.calls == 1  # the second call is a cache hit, not a re-parse
    assert second.content_hash == first.content_hash
    assert second.manifest_path == first.manifest_path


def test_changed_content_is_reparsed(tmp_path):
    src = _pdf(tmp_path / "contract_a.pdf", b"%PDF-1.4 one")
    parser = _StubParser()
    parse(src, cache_dir=tmp_path / "parsed", parser=parser)

    _pdf(src, b"%PDF-1.4 TWO different bytes")  # same name, new content
    reparsed = parse(src, cache_dir=tmp_path / "parsed", parser=parser)

    assert parser.calls == 2  # a new content hash forces a re-parse
    assert reparsed.content_hash  # distinct manifest for the new content


def test_source_doc_id_is_sanitized_to_the_citation_safe_charset(tmp_path):
    src = _pdf(tmp_path / "Some Contract (v2).pdf")
    parsed = parse(src, cache_dir=tmp_path / "parsed", parser=_StubParser())
    # HYG-1: the ONE canonical slug -- runs of spaces/parens collapse to '_', trailing '_' stripped
    assert parsed.source_doc_id == "Some_Contract_v2"


def test_load_document_reloads_the_structured_representation(tmp_path):
    src = _pdf(tmp_path / "contract_a.pdf")
    parsed = parse(src, cache_dir=tmp_path / "parsed", parser=_StubParser())

    doc = load_document(parsed)
    assert isinstance(doc, DoclingDocument)
    headings = [t.text for t in doc.texts if getattr(t, "label", None) == "section_header"]
    assert "Governing Law" in headings
    assert "Governing Law" in doc.export_to_markdown()


def test_parsing_registers_under_frc1_as_a_function(tmp_path):
    reg = CapabilityRegistry()
    register_parsing(reg)
    registration = reg.get("parsing")
    assert registration.kind == "function"
    assert registration.contract is ParsedDocument
    assert registration.skeleton.identifier == "urn:air:dreamai.io:rag_wright:parsing"


# --- live Docling parse over the real corpus (opt-in: needs models + the gitignored corpus) ------

_PDF_DIR = Path("data/cuad/subset/pdf")
_SCANNED = Path("data/cuad/subset/scanned.json")


def _scanned_ids() -> list[str]:
    return json.loads(_SCANNED.read_text())["contract_ids"] if _SCANNED.exists() else []


@pytest.mark.parse
def test_real_cuad_pdf_parses_into_structured_representation(tmp_path):
    from rag_wright.capabilities.parsing import DoclingParser

    ids = set(_scanned_ids())
    text_pdfs = sorted(p for p in _PDF_DIR.glob("*.pdf") if p.stem not in ids)
    if not text_pdfs:
        pytest.skip("CUAD subset PDFs not present")
    pdf = min(text_pdfs, key=lambda p: p.stat().st_size)  # smallest text-layer contract

    parsed = parse(pdf, cache_dir=tmp_path / "parsed", parser=DoclingParser())
    doc = load_document(parsed)

    assert doc.texts  # reading order / sections recovered
    assert doc.export_to_markdown().strip()
    assert Path(parsed.manifest_path).exists()


@pytest.mark.parse
def test_real_scanned_filing_ocrs_to_text(tmp_path):
    from rag_wright.capabilities.parsing import DoclingParser

    ids = _scanned_ids()
    if not ids:
        pytest.skip("scanned set not present")
    scan = _PDF_DIR / f"{ids[0]}.pdf"
    if not scan.exists():
        pytest.skip("scanned image-only PDF not present")

    parsed = parse(scan, cache_dir=tmp_path / "parsed", parser=DoclingParser())
    text = load_document(parsed).export_to_markdown()

    assert len(text.strip()) > 50  # OCR produced machine-readable text from the image-only PDF
