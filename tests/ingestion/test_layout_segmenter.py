"""ING-2 (ADR-0124): the engine's default, domain-neutral segmenter (docling-layout based, NLP sentence rules).

Hermetic: the fixtures are markdown, which docling parses without any model, and chunking uses the deterministic
structural discoverer. The hand gold (`tests/fixtures/ingestion/segmenter_gold.json`) is the acceptance criterion."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from rag_wright.api import LayoutItem, check_tiling
from rag_wright.ingestion import chunk_layouts, layout_kind, segment_layout, text_layout

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ingestion"
GOLD = json.loads((FIXTURES / "segmenter_gold.json").read_text())


def _norm(s: str) -> str:
    return re.sub(r"-{3,}", "---", " ".join(s.split()))


def _chunks(name: str):
    from docling.document_converter import DocumentConverter

    from rag_wright.capabilities.rlm_chunking import StructuralBoundaryDiscoverer, chunk_texts

    doc = DocumentConverter().convert(FIXTURES / name).document
    texts = chunk_texts(doc, discoverer=StructuralBoundaryDiscoverer())
    return texts, chunk_layouts(doc, texts)


def _segment_doc(name: str, *, use_layout: bool = True) -> list:
    texts, layouts = _chunks(name)
    spans = []
    for i, (text, layout) in enumerate(zip(texts, layouts)):
        cid = f"doc:{i}:h"
        got = segment_layout(cid, text, layout if use_layout else [])
        check_tiling(cid, text, got)
        spans += got
    return spans


@pytest.mark.parametrize("name", ["textile_spec_sheet.md", "textile_test_report.md"])
def test_matches_the_hand_gold(name):
    assert [_norm(s.text) for s in _segment_doc(name)] == GOLD[name]


def test_layout_maps_onto_chunk_offsets():
    texts, layouts = _chunks("textile_spec_sheet.md")
    for text, layout in zip(texts, layouts):
        for item in layout:
            assert text[item.start:item.end].strip() == item.text.strip()
    kinds = [it.kind for it in layouts[1]]
    assert kinds[0] == "heading" and "table" in kinds


def test_every_docling_label_maps_to_an_engine_kind():
    from docling_core.types.doc import DocItemLabel

    for label in DocItemLabel:
        LayoutItem(kind=layout_kind(label), text="x", start=0, end=1)  # validates against the closed set


def test_text_only_fallback_still_splits_tables_and_sentences():
    """No parse layout (a plain-text source): tables still split per row and prose per sentence."""
    spans = _segment_doc("textile_test_report.md", use_layout=False)
    norm = [_norm(s.text) for s in spans]
    assert "| Bursting strength | ASTM D3786 | 412 kPa | 350 kPa min. | Pass |" in norm
    assert "Keep the current knitting parameters." in norm


def test_text_layout_recognizes_blocks():
    text = "# Washing\n\nTemperature: 60 °C.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- first\n- second\n"
    kinds = [it.kind for it in text_layout(text)]
    assert kinds == ["heading", "paragraph", "table", "list_item", "list_item"]


@pytest.mark.parametrize("text", [
    "",
    "   \n\n  ",
    "N",
    "SDF#\n\nDATE:\n\nCustomer name",
    'A "quoted" [bracketed] line.\nNext line (e.g. this) ends?! Yes. 4.5% done.',
    "| only | a | table |\n|---|---|---|\n| x | y | z |",
    "Heading only",
])
def test_always_tiles(text):
    check_tiling("c:0:h", text, segment_layout("c:0:h", text, []))


def test_no_legal_rules_by_default():
    """Domain-neutral: a contract-style enumeration inside a sentence is not a boundary for the generic default."""
    text = "The supplier shall (a) deliver the yarn and (b) invoice monthly."
    assert len(segment_layout("c:0:h", text, [])) == 1


def test_tiny_fragments_fold_into_a_neighbour():
    text = "N\n\nYarn count: 40s Ne."
    spans = segment_layout("c:0:h", text, [])
    assert len(spans) == 1 and _norm(spans[0].text) == "N Yarn count: 40s Ne."


def test_a_fragment_folds_one_way_only():
    """A page number between two paragraphs folds back into the first; the second keeps its own span."""
    text = "Yarn count: 40s Ne.\n\n1\n\nTwist direction: Z."
    assert [_norm(s.text) for s in segment_layout("c:0:h", text, [])] == ["Yarn count: 40s Ne. 1",
                                                                          "Twist direction: Z."]


def test_an_empty_table_does_not_glue_two_tables_together():
    text = "| A | B |\n|---|---|\n| 1 | 2 |\n\n|     |\n|-----|\n\n| C | D |\n|---|---|\n| 3 | 4 |"
    norm = [_norm(s.text) for s in segment_layout("c:0:h", text, [])]
    assert norm[-2:] == ["| C | D | |---|---|", "| 3 | 4 |"]


def test_a_heading_that_ends_the_chunk_joins_the_previous_span():
    text = "Final yarn composition: 60/40.\n\nNORTHWIND KNITTING MILLS"
    layout = [LayoutItem(kind="paragraph", text="Final yarn composition: 60/40.", start=0, end=30),
              LayoutItem(kind="heading", text="NORTHWIND KNITTING MILLS", start=32, end=56)]
    assert [_norm(s.text) for s in segment_layout("c:0:h", text, layout)] == [
        "Final yarn composition: 60/40. NORTHWIND KNITTING MILLS"]


@pytest.mark.parametrize("marker", ["9.", "12.1.", "(a)", "iv.", "B."])
def test_a_list_number_is_not_a_sentence_end(marker):
    """A numbered clause the parse labelled a PARAGRAPH (not a list item): its number must not end a sentence."""
    first, second = "The terms survive.", f"{marker} Indemnity. Each party shall indemnify the other."
    text = f"{first}\n\n{second}"
    layout = [LayoutItem(kind="paragraph", text=first, start=0, end=len(first)),
              LayoutItem(kind="paragraph", text=second, start=len(first) + 2, end=len(text))]
    assert [_norm(s.text) for s in segment_layout("c:0:h", text, layout)] == [
        "The terms survive.", f"{marker} Indemnity.", "Each party shall indemnify the other."]


def test_spans_carry_their_kind():
    """ING-3: each span records what it starts with, so a unit grouper reads structure off the spans."""
    spans = _segment_doc("textile_test_report.md")
    kinds = {_norm(s.text): s.kind for s in spans}
    assert kinds["Summary The sample meets the dimensional stability requirement."] == "heading"
    assert kinds["Results | Test | Method | Result | Requirement | Pass/Fail | |---|---|---|---|---|"] == "heading"
    assert kinds["| Bursting strength | ASTM D3786 | 412 kPa | 350 kPa min. | Pass |"] == "table_row"
    assert kinds["Keep the current knitting parameters."] == "list_item"
    assert kinds["Fibre content claimed: 60% cotton / 40% polyester."] == "paragraph"
    table = segment_layout("c:0:h", "| a | b |\n|---|---|\n| 1 | 2 |", [])
    assert [s.kind for s in table] == ["table", "table_row"]
