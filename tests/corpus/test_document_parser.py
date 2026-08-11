"""DOCPARSE-1 (ADR-0049): the generic raw-doc -> (text | sections) parser -- the shared gap for customer PDF/DOCX
on BOTH the contract and compliance sides. Hermetic: a fake DoclingDocument (duck-typed items), no docling models."""

from __future__ import annotations

from docling_core.types.doc.labels import DocItemLabel

from rag_wright.corpus.document_parser import document_to_sections, document_to_text


class _Item:
    def __init__(self, label, text):
        self.label = label
        self.text = text


class _FakeDoc:
    """Duck-typed DoclingDocument: export_to_markdown() + iterate_items() -> (item, level)."""

    def __init__(self, items, markdown="# Doc\n\nbody text"):
        self._items = items
        self._md = markdown

    def export_to_markdown(self, **_):
        return self._md

    def iterate_items(self, **_):
        return [(it, 0) for it in self._items]


def test_document_to_text_uses_markdown_export():
    assert document_to_text(_FakeDoc([], markdown="# Title\n\nclause one")) == "# Title\n\nclause one"


def test_document_to_sections_groups_body_under_headings():
    doc = _FakeDoc([
        _Item(DocItemLabel.SECTION_HEADER, "1. Confidentiality"),
        _Item(DocItemLabel.TEXT, "The Receiving Party shall keep Confidential Information secret."),
        _Item(DocItemLabel.TEXT, "This obligation survives termination."),
        _Item(DocItemLabel.SECTION_HEADER, "2. Governing Law"),
        _Item(DocItemLabel.TEXT, "This Agreement is governed by the laws of Delaware."),
    ])
    secs = document_to_sections(doc)
    assert [s["heading"] for s in secs] == ["1. Confidentiality", "2. Governing Law"]
    assert secs[0]["section"] == "1" and secs[1]["section"] == "2"
    assert "keep Confidential Information secret" in secs[0]["text"]
    assert "survives termination" in secs[0]["text"]  # both body items joined under the heading
    assert "Delaware" in secs[1]["text"]


def test_document_to_sections_preamble_before_first_heading_becomes_a_section():
    doc = _FakeDoc([
        _Item(DocItemLabel.TEXT, "NON-DISCLOSURE AGREEMENT between A and B."),
        _Item(DocItemLabel.SECTION_HEADER, "1. Definitions"),
        _Item(DocItemLabel.TEXT, "Confidential Information means ..."),
    ])
    secs = document_to_sections(doc)
    # the preamble (body before any heading) is preserved as its own leading section, never dropped
    assert secs[0]["heading"] == "" and "NON-DISCLOSURE AGREEMENT" in secs[0]["text"]
    assert secs[1]["heading"] == "1. Definitions"


def test_document_to_sections_title_and_field_heading_also_split():
    doc = _FakeDoc([
        _Item(DocItemLabel.TITLE, "MASTER SERVICES AGREEMENT"),
        _Item(DocItemLabel.TEXT, "intro"),
        _Item(DocItemLabel.FIELD_HEADING, "Payment"),
        _Item(DocItemLabel.TEXT, "Net 30."),
    ])
    secs = document_to_sections(doc)
    assert [s["heading"] for s in secs] == ["MASTER SERVICES AGREEMENT", "Payment"]


def test_document_to_sections_skips_page_headers_and_empty():
    doc = _FakeDoc([
        _Item(DocItemLabel.PAGE_HEADER, "Page 1 of 5"),  # noise -> not a section boundary, not body
        _Item(DocItemLabel.SECTION_HEADER, "1. Term"),
        _Item(DocItemLabel.TEXT, "  "),                  # whitespace -> skipped
        _Item(DocItemLabel.TEXT, "Five years."),
    ])
    secs = document_to_sections(doc)
    assert [s["heading"] for s in secs] == ["1. Term"]
    assert secs[0]["text"].strip() == "Five years."
