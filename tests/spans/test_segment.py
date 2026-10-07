"""T55 (FR-R, ADR-0025): tests for the operative-span segmenter.

Hermetic and deterministic (no model). The load-bearing invariant is byte-faithful tiling -- the spans must
reconstruct the clause exactly -- so no operative text is ever lost or duplicated. The rest asserts the legal
structure is split (enumeration, sentences) while abbreviations and section references are NOT false-split.
"""

from __future__ import annotations

from rag_wright.packs.contracts.spans.segment import (
    DEFAULT_MIN_CHARS,
    OperativeSpan,
    _table_block_ranges,
    is_extractable_span,
    segment_clause,
)

_MESSY = (
    "12. LIMITATION OF LIABILITY AND WARRANTIES.\n"
    "(a) In no event shall Supplier be liable for consequential [see 9.1] or indirect damages.\n"
    '(b) Supplier\'s total liability shall not exceed the fees paid in the prior 12 months.\n'
    '(c) The Products are provided "AS-IS".'
)


def _reconstructs(body: str) -> list[OperativeSpan]:
    spans = segment_clause("aaa1:0:hash", body, parent_okf_path="limitation-of-liability/aaa1.md")
    assert "".join(s.text for s in spans) == body  # byte-faithful tiling: nothing lost/duplicated/reordered
    for s in spans:  # offsets are self-consistent
        assert body[s.start : s.end] == s.text
    for a, b in zip(spans, spans[1:]):  # contiguous, gap-free, ordered
        assert a.end == b.start
    return spans


# --- the load-bearing invariant --------------------------------------------------------------


def test_byte_faithful_tiling_on_messy_multi_provision():
    spans = _reconstructs(_MESSY)
    assert len(spans) >= 3  # the three enumerated provisions are separated


def test_empty_body_yields_no_spans():
    assert segment_clause("x:0:h", "") == []


# --- structural splitting --------------------------------------------------------------------


def test_enumeration_markers_split_provisions():
    spans = _reconstructs(_MESSY)
    joined = [s.text for s in spans]
    assert any(t.lstrip().startswith("(a)") for t in joined)
    assert any(t.lstrip().startswith("(b)") for t in joined)
    assert any(t.lstrip().startswith("(c)") for t in joined)


def test_sentence_boundary_splits():
    spans = _reconstructs("Losses are capped at fees paid. Warranties are disclaimed entirely by Supplier.")
    assert len(spans) == 2


# --- do NOT false-split ----------------------------------------------------------------------


def test_abbreviation_is_not_a_boundary():
    # "Corp." is a known abbreviation -> the following capitalized word must not start a new span
    spans = _reconstructs("Amounts are payable to Acme Corp. Losses under this clause are capped at cost.")
    assert len(spans) == 1


def test_section_reference_decimal_is_not_a_boundary():
    # "12.1" is a section reference, not a sentence end -> one span
    spans = _reconstructs("Liability under Section 12.1 is limited to direct damages only.")
    assert len(spans) == 1


# --- sub-floor merge, ids, determinism -------------------------------------------------------


def test_bare_heading_folds_into_following_provision():
    body = "12.1. In no event shall either party be liable for consequential damages of any kind whatsoever."
    spans = _reconstructs(body)
    # a lone "12.1." marker (< min_chars) must not be its own span
    assert all(len(s.text.strip()) >= DEFAULT_MIN_CHARS or len(spans) == 1 for s in spans)
    assert not any(s.text.strip().rstrip(".").replace(".", "").isdigit() for s in spans)  # no bare-number span


def test_numbered_title_heading_folds_into_body_not_standalone():
    # 0006-B: "9. Limitation of Liability" (26 chars, ABOVE the 25 sub-floor) must NOT be a standalone span --
    # a standalone heading gets classified as a clause pointing at a bare heading (issue 0006 Problem 2).
    body = "9. Limitation of Liability\n\nSupplier's total liability shall not exceed the fees paid."
    spans = segment_clause("c:0:h", body)
    texts = [s.text.strip() for s in spans]
    assert "9. Limitation of Liability" not in texts                       # no standalone heading-only span
    assert any("Limitation of Liability" in t and "total liability" in t for t in texts)  # heading stays with body
    assert "".join(s.text for s in spans) == body                          # tiling invariant preserved


def test_unnumbered_title_heading_also_folds():
    body = "Term and Renewal\n\nThis Agreement renews automatically for successive one-year terms."
    spans = segment_clause("c:0:h", body)
    assert "Term and Renewal" not in [s.text.strip() for s in spans]       # not standalone
    assert "".join(s.text for s in spans) == body


def test_short_body_sentence_is_not_treated_as_a_heading():
    # a genuine short provision (has a sentence terminator) is NOT a heading -- must not wrongly fold away
    body = "10. Governing Law\n\nThis Agreement is governed by New York law. The parties consent to jurisdiction."
    spans = segment_clause("c:0:h", body)
    assert any("governed by New York law" in s.text for s in spans)        # the provision survives
    assert "".join(s.text for s in spans) == body


def test_span_id_embeds_parent_and_index():
    spans = _reconstructs(_MESSY)
    for i, s in enumerate(spans):
        assert s.span_id == f"aaa1:0:hash#{i}"
        assert s.parent_chunk_id == "aaa1:0:hash"
        assert s.parent_okf_path == "limitation-of-liability/aaa1.md"
        assert s.span_index == i


def test_segmentation_is_deterministic():
    a = [(s.start, s.end, s.text) for s in segment_clause("c:0:h", _MESSY)]
    b = [(s.start, s.end, s.text) for s in segment_clause("c:0:h", _MESSY)]
    assert a == b


# --- issue 0014: a markdown table is ONE atomic operative span --------------------------------

_FEE_TABLE = (
    "1. Fees\n\nThe Customer shall pay the fees set out in the schedule below, annually in advance.\n\n"
    "| Service tier | Annual fee (GBP) | Included seats | Support response |\n"
    "|---|---|---|---|\n"
    "| Starter | 6,000 | 10 | 2 business days |\n"
    "| Professional | 18,000 | 50 | 1 business day |\n"
    "| Enterprise | 48,000 | 250 | 4 hours |"
)


def test_table_block_ranges_finds_the_contiguous_pipe_run():
    ranges = _table_block_ranges(_FEE_TABLE)
    assert len(ranges) == 1
    s, e = ranges[0]
    block = _FEE_TABLE[s:e]
    assert block.lstrip().startswith("| Service tier")  # header row
    assert block.rstrip().endswith("4 hours |")          # last data row


def test_markdown_table_is_one_atomic_span_with_header_and_all_rows():
    # issue 0014: the fee table must retrieve as a UNIT -- the header row (column semantics) stays with every
    # data row, so "48,000" is knowable as the Enterprise Annual fee. A row split across spans loses that.
    spans = _reconstructs(_FEE_TABLE)  # also asserts byte-faithful tiling
    table_spans = [s for s in spans if "48,000" in s.text]
    assert len(table_spans) == 1                                   # ONE atomic table span, not row-per-span
    t = table_spans[0].text
    assert "Annual fee (GBP)" in t                                 # header preserved
    assert all(tier in t for tier in ("Starter", "Professional", "Enterprise"))  # every row present


def test_table_rows_are_not_split_by_paragraph_or_sentence_cutter():
    spans = _reconstructs(_FEE_TABLE)
    # no span is a lone table ROW (a '|'-led fragment missing the header)
    for s in spans:
        if s.text.lstrip().startswith("|"):
            assert "Service tier" in s.text  # any pipe span is the whole table (carries the header)


def test_non_table_pipe_line_does_not_trigger_a_block():
    # a single stray '|' line in prose is not a table (needs >= 2 consecutive rows) -> normal segmentation
    body = "The formula a | b applies. Losses are capped at the fees paid in the prior twelve months."
    assert _table_block_ranges(body) == []
    _reconstructs(body)  # still tiles


# --- EXTRACT-GUARD-1: is_extractable_span (furniture is not a clause) --------------------------


def test_furniture_spans_are_not_extractable():
    # the exact NEONSYSTEMS furniture that hard-failed docling-graph extraction -- none is a clause.
    for furniture in ("9", "22", "By: /s/ Joe Backer", "Name: ", "Title:", "SKUNWARE, INC.",
                      "EXHIBIT C", "FORM OF SUBLICENSE", "AMENDMENT OF DEFINITIONS.",
                      "66069:53214:DALLAS:277267.9", "  ", "1.11"):
        assert is_extractable_span(furniture) is False, furniture


def test_real_clauses_are_extractable_recall_first():
    for clause in (
        "Net 30 days.",                                                    # short but real prose
        "The Customer shall pay the fees set out in the schedule below.",
        '1.11 "Annual Royalty Advance Requirement" shall mean $1,000,000.',
        "NOW, THEREFORE, for and consideration of the mutual covenants of the parties set forth herein",
        "THE PRODUCTS ARE PROVIDED AS-IS WITHOUT WARRANTY OF ANY KIND.",   # long ALL-CAPS disclaimer -> real
    ):
        assert is_extractable_span(clause) is True, clause


# issue 0036 (task 2): tighten the furniture filter with more DETERMINISTIC, high-precision non-provision forms --
# table-of-contents dotted-leader lines and notice-block contact-label lines. Recall-first still holds: each rule
# targets a form that is furniture in essentially every contract, and the anchoring keeps operative prose out.
def test_toc_dotted_leader_lines_are_not_extractable():
    for toc in (
        "Limitation of Liability ................................ 12",
        "ARTICLE 5   INDEMNIFICATION.....46",
        "Section 3.1 Payment Terms . . . . . . . . 7",
    ):
        assert is_extractable_span(toc) is False, toc


def test_notice_block_contact_labels_are_not_extractable():
    for contact in ("Attention: General Counsel", "Attn: Legal Department", "Facsimile: (212) 555-0100",
                    "Fax: (212) 555-0100", "Email: legal@acme.com", "Telephone: +1 415 555 0123"):
        assert is_extractable_span(contact) is False, contact


def test_tightening_does_not_drop_provisions_that_mention_contact_or_dots():
    # a provision that MENTIONS notices/contact or contains dots is NOT the furniture form -> still extractable.
    for clause in (
        "All notices shall be sent to the following address:",             # notice CLAUSE, not the address line
        "The Supplier shall provide telephone support during business hours.",
        "Payment is due within 30 days (see Section 3.1).",                # dots in decimals, not a TOC leader
    ):
        assert is_extractable_span(clause) is True, clause


# --- issue 0032: to_span_record carries page provenance from the OperativeSpan ------------------------------

def test_to_span_record_carries_pages_and_bbox_from_op():
    from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM
    from rag_wright.packs.contracts.spans.segment import to_span_record

    op = OperativeSpan(span_id="docA:0:h#0", parent_chunk_id="docA:0:h", parent_okf_path="", span_index=0,
                       start=0, end=12, text="A clause .", pages=[7, 8], bbox=(1.0, 2.0, 3.0, 4.0))
    rec = to_span_record(op, contract_id="docA", chunk_doc_start=100,
                         dense_vector=[0.0] * BGE_M3_DENSE_DIM, sparse_vector={1: 1.0})
    assert rec.pages == [7, 8]
    assert rec.page == 7  # FIRST page mirrors into the singular highlight field
    assert rec.bbox == (1.0, 2.0, 3.0, 4.0)
    assert rec.doc_start == 100 and rec.doc_end == 112  # offsets unchanged


def test_to_span_record_no_pages_leaves_page_none():
    from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM
    from rag_wright.packs.contracts.spans.segment import to_span_record

    op = OperativeSpan(span_id="docA:0:h#0", parent_chunk_id="docA:0:h", parent_okf_path="", span_index=0,
                       start=0, end=5, text="clause")
    rec = to_span_record(op, contract_id="docA", chunk_doc_start=0,
                         dense_vector=[0.0] * BGE_M3_DENSE_DIM, sparse_vector={1: 1.0})
    assert rec.pages == [] and rec.page is None and rec.bbox is None  # text-only leg: no provenance


# --- Bug-A: provision boundary detection (section-word prefixes + a 3-way verdict for the residue) ---

def test_section_word_prefixed_headings_start_a_provision():
    """The dominant contract heading style is 'Section N'/'Article N'/'Clause N'/'§N' -- not a bare leading digit.
    These collapsed into one provision before (quickstart abstained); they must now start a provision."""
    from rag_wright.packs.contracts.spans.segment import starts_new_provision
    for t in ["Section 8. Limitation of Liability.", "Article 2. Term and Termination.",
              "§ 3. Fees", "Sec. 4. Notices", "Clause 12 Governing Law"]:
        assert starts_new_provision(t), t


def test_section_word_without_a_following_number_is_not_a_start():
    from rag_wright.packs.contracts.spans.segment import starts_new_provision
    assert not starts_new_provision("Section hereof shall mean the provisions of this agreement and its exhibits")


def test_bare_leading_number_still_starts_a_provision():  # regression
    from rag_wright.packs.contracts.spans.segment import starts_new_provision
    assert starts_new_provision("8. Limitation of Liability shall apply.")
    assert starts_new_provision("2.1. Sub-provision text follows here.")


def test_provision_boundary_verdict_is_three_way():
    from rag_wright.packs.contracts.spans.segment import provision_boundary_verdict
    assert provision_boundary_verdict("Section 8. Limitation of Liability.") == "start"      # deterministic start
    assert provision_boundary_verdict("the parties agree to indemnify each other for any losses") == "continue"
    assert provision_boundary_verdict("Limitation of Liability:") == "uncertain"      # colon heading -> ask the model
