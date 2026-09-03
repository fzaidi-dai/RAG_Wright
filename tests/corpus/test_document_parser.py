"""DOCPARSE-1 (ADR-0049): the generic raw-doc -> (text | sections) parser -- the shared gap for customer PDF/DOCX
on BOTH the contract and compliance sides. Hermetic: a fake DoclingDocument (duck-typed items), no docling models."""

from __future__ import annotations

from docling_core.types.doc.labels import DocItemLabel

from rag_wright.corpus.document_parser import content_items, document_to_sections, document_to_text


class _Item:
    def __init__(self, label, text):
        self.label = label
        self.text = text


class _FakeTable:
    """Duck-typed docling TableItem: label TABLE, caption_text(doc) + export_to_markdown(doc)."""

    label = DocItemLabel.TABLE

    def __init__(self, markdown, caption=""):
        self._md = markdown
        self._caption = caption

    def caption_text(self, _doc):
        return self._caption

    def export_to_markdown(self, _doc=None):
        return self._md


class _FakeAnnotation:
    def __init__(self, text):
        self.text = text


class _FakePicture:
    """Duck-typed docling PictureItem: label PICTURE, caption_text(doc) + description annotations."""

    label = DocItemLabel.PICTURE

    def __init__(self, caption="", annotations=()):
        self._caption = caption
        self.annotations = list(annotations)

    def caption_text(self, _doc):
        return self._caption


class _FakeDoc:
    """Duck-typed DoclingDocument: export_to_markdown() + iterate_items() -> (item, level) + `.texts`."""

    def __init__(self, items, markdown="# Doc\n\nbody text"):
        self._items = items
        self._md = markdown
        # `.texts` is the text-family subset (what a `.texts`-only chunker would see; the coverage backstop reads it)
        self.texts = [it for it in items if isinstance(it, _Item)]

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


# --- issue 0014: the reading-order content view (text + tables + figures) ---------------------


def test_content_items_includes_a_table_in_reading_order():
    # the load-bearing 0014 fix: a TABLE lives in `document.tables`, NEVER in `.texts`; the reading-order view
    # must place its markdown between the surrounding prose so the chunker/index sees it.
    doc = _FakeDoc([
        _Item(DocItemLabel.SECTION_HEADER, "1. Fees"),
        _Item(DocItemLabel.TEXT, "The Customer shall pay the fees set out below."),
        _FakeTable("| Service tier | Annual fee (GBP) |\n|---|---|\n| Enterprise | 48,000 |"),
        _Item(DocItemLabel.SECTION_HEADER, "2. Liability"),
    ])
    items = content_items(doc)
    labels = [it.label for it in items]
    assert labels == [DocItemLabel.SECTION_HEADER, DocItemLabel.TEXT, DocItemLabel.TABLE,
                      DocItemLabel.SECTION_HEADER]
    table = items[2]
    assert "48,000" in table.text and "Annual fee (GBP)" in table.text  # header + data, atomic


def test_content_items_table_prefixes_caption_when_present():
    doc = _FakeDoc([_FakeTable("| a | b |\n|---|---|\n| 1 | 2 |", caption="Schedule A: Fees")])
    (item,) = content_items(doc)
    assert item.text.startswith("Schedule A: Fees")
    assert "| 1 | 2 |" in item.text


def test_content_items_includes_picture_caption_and_description():
    doc = _FakeDoc([
        _FakePicture(caption="Figure 1: signature block",
                     annotations=[_FakeAnnotation("A scanned wet-ink signature of the Supplier.")]),
    ])
    (item,) = content_items(doc)
    assert item.label == DocItemLabel.PICTURE
    assert "signature block" in item.text and "wet-ink signature" in item.text  # caption + description


def test_content_items_is_a_superset_of_texts_coverage_backstop():
    # NO-SILENT-LOSS (0014 Q2): any `.texts` item the reading-order walk missed is still appended, so a content
    # item can never vanish upstream of the failure accounting the way the table did.
    orphan = _Item(DocItemLabel.TEXT, "An orphan clause the body walk did not reach.")
    doc = _FakeDoc([_Item(DocItemLabel.SECTION_HEADER, "1. Term")])  # iterate_items yields only the header...
    doc.texts.append(orphan)                                          # ...but `.texts` also carries the orphan
    texts = [it.text for it in content_items(doc)]
    assert "An orphan clause the body walk did not reach." in texts


# --- DEFRAG-1: reconstruct paragraphs from docling's per-line items (no per-line shattering) ---


def test_content_items_merges_line_wrapped_sentence_into_one_paragraph():
    # DEFRAG-1 (NEONSYSTEMS): docling emits each PDF LINE as its own item; one sentence must NOT become 4 items.
    doc = _FakeDoc([
        _Item(DocItemLabel.TEXT, 'THIS FIRST AMENDMENT (this "Amendment") is made'),
        _Item(DocItemLabel.TEXT, "and entered into as of the 1st day of January, 1999, by and between"),
        _Item(DocItemLabel.TEXT, 'Perseus Therapeutics, Inc., a Delaware corporation ("Licensor"), and'),
        _Item(DocItemLabel.TEXT, 'NEON Systems, Inc., a Delaware corporation ("Licensee").'),
    ])
    items = content_items(doc)
    assert len(items) == 1                                            # the wrapped lines rejoin into ONE clause
    t = items[0].text
    assert "is made and entered into" in t and 'NEON Systems' in t   # continuations joined with a space
    assert "\n\n" not in t                                            # no residual paragraph break inside a sentence


def test_content_items_keeps_real_paragraph_breaks():
    # two COMPLETE sentences (each ends a sentence, next starts one) stay two items -- clean docs unaffected.
    doc = _FakeDoc([
        _Item(DocItemLabel.TEXT, "The Receiving Party shall keep the Confidential Information secret."),
        _Item(DocItemLabel.TEXT, "This obligation survives termination of the Agreement."),
    ])
    assert len(content_items(doc)) == 2


def test_content_items_de_hyphenates_a_wrapped_word():
    doc = _FakeDoc([
        _Item(DocItemLabel.TEXT, "The Distribu-"),
        _Item(DocItemLabel.TEXT, "tor Agreement is hereby amended."),
    ])
    (item,) = content_items(doc)
    assert "Distributor Agreement is hereby amended." in item.text    # hyphen dropped, no space inserted


def test_content_items_does_not_merge_across_a_heading_or_table():
    # a heading and a table are hard boundaries -- a wrapped line before one never absorbs across it.
    doc = _FakeDoc([
        _Item(DocItemLabel.TEXT, "intro text that wraps"),
        _Item(DocItemLabel.SECTION_HEADER, "1. Fees"),
        _Item(DocItemLabel.TEXT, "The Customer shall pay the fees."),
        _FakeTable("| a | b |\n|---|---|\n| 1 | 2 |"),
        _Item(DocItemLabel.TEXT, "Next clause wraps here"),
    ])
    labels = [it.label for it in content_items(doc)]
    assert labels == [DocItemLabel.TEXT, DocItemLabel.SECTION_HEADER, DocItemLabel.TEXT,
                      DocItemLabel.TABLE, DocItemLabel.TEXT]           # boundaries preserved, no cross-merge
