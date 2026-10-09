"""T55 (FR-R, ADR-0025): operative-span segmenter — re-chunk a clause into operative spans.

The recall investigation showed the function/property signal is a LOCAL operative provision, which a
whole-clause embedding drowns. So we index at the span level (small-to-big): split a clause body on legal
structure and point each span back to its parent clause. The span is the classified/retrieved unit; the parent
clause is what we return and rerank.

Deterministic and byte-faithful. Split signals (no LLM, no new dependency — the spaCy escalation is added only
if the downstream metric demands it):
  - enumeration markers at a provision start: `(a)`, `(i)`, `1.`, `12.1`, `12.1.1`, `§`,
  - semicolon / colon list separators,
  - sentence-ending `.` that is a genuine terminator (not a known abbreviation, not a decimal / section ref).
Sub-floor fragments (a lone heading like "12.1 Limitation of Liability.") fold into the following provision.

Invariant: the spans TILE the body -- `"".join(s.text) == body` -- so no character is lost, duplicated, or
reordered. The `function` tag and embeddings are filled by later tasks (T56); this task produces the spans and
their parent pointers only.
"""

from __future__ import annotations

import re

from rag_wright.contracts.ingestion import Span
from rag_wright.corpus.document_parser import LEADING_ENUM, is_bare_heading  # generic text rules (ADR-0124)
from rag_wright.contracts.span import to_span_record  # noqa: F401 - re-exported (moved, ING-4b)
from rag_wright.packs.contracts.ontology.loader import load_segmentation_vocab

# ING-3b (ADR-0066): the segmentation VOCABULARY (abbreviations, section words/symbols, furniture labels) is declared
# in contract_bridge.ttl (`cbr:segmentationVocabulary`); the regex structure around it below is mechanism.
_VOCAB = load_segmentation_vocab()


def _alternation(words) -> str:
    """A regex alternation over vocabulary words, longest first (deterministic; a longer word wins a shared prefix)."""
    return "|".join(re.escape(w) for w in sorted(words, key=lambda w: (-len(w), w)))


DEFAULT_MIN_CHARS = 25  # a span whose stripped text is shorter folds into its neighbour (a bare heading/marker)

# Abbreviations whose trailing '.' does not end a provision (lower-cased, no trailing dot): `cbr:nonTerminalAbbreviation`.
_ABBREV = set(_VOCAB["abbreviations"])

# An enumeration marker opening a provision: (a) (iv) (12) a) 12) 1. 12.1 12.1.1 §  -- captured as group 1.
_ENUM = re.compile(
    r"(?:(?<=\s)|(?<=[.;:])|\A)\s*"
    r"(\(\s*(?:[a-zA-Z]|[ivxlcdm]{1,4}|\d{1,3})\s*\)"      # (a) (iv) (12)
    r"|(?:[a-zA-Z]|[ivxlcdm]{1,4}|\d{1,3})\)"              # a) iv) 12)
    r"|\d+(?:\.\d+){1,3}\.?"                                # 12.1  12.1.1.
    r"|§+)\s+(?=[A-Z\"'(])"                                # followed by space + capital / quote / paren
)

# A sentence/list terminator followed by whitespace + start of a new provision.
_TERM = re.compile(r"[.;:]\s+(?=[A-Z\"'(])")


# issue 0014: a markdown TABLE row (a line whose first non-space character is a pipe). A run of >=2 such lines is
# a table block, kept as ONE atomic operative span -- the rows are meaningless without the header row, so a fee
# schedule / payment table must retrieve as a unit (header + all rows), never split by the paragraph/sentence cutter.
_TABLE_LINE = re.compile(r"^[ \t]*\|")
_MIN_TABLE_LINES = 2


def _table_block_ranges(body: str) -> list[tuple[int, int]]:
    """issue 0014: the char ranges of contiguous markdown table blocks (>= `_MIN_TABLE_LINES` consecutive
    pipe-led lines). Each range is atomic: `segment_clause` cuts at its edges and never inside it, so the whole
    table is one operative span. Byte offsets tile the body (a range ends at the start of the first non-table
    line, i.e. after the last row's newline)."""
    ranges: list[tuple[int, int]] = []
    run_start: int | None = None
    run_lines = 0
    pos = 0
    for line in body.splitlines(keepends=True):
        if _TABLE_LINE.match(line):
            if run_start is None:
                run_start, run_lines = pos, 0
            run_lines += 1
        else:
            if run_start is not None and run_lines >= _MIN_TABLE_LINES:
                ranges.append((run_start, pos))
            run_start, run_lines = None, 0
        pos += len(line)
    if run_start is not None and run_lines >= _MIN_TABLE_LINES:
        ranges.append((run_start, pos))
    return ranges


_MIN_CLAUSE_ALPHA = 6  # fewer alphabetic chars than this = a page number / "By:" / "9" -- not a clause
_MIN_ALLCAPS_WORDS = 6  # a short ALL-CAPS span is a label/heading ("EXHIBIT C"); a long one may be a real clause
_WORD_RE = re.compile(r"[A-Za-z]{2,}")
# A signature / execution-block or notice-block label line -- universal, domain-neutral contract furniture
# ("By: /s/ ...", "Name:", "Title:", "Attest:", "Its:", "Date:"; and the notice-block contacts "Attention:",
# "Fax:", "Email:", "Telephone:"). The trailing ':' anchored right after the leading word makes it a LABEL, not a
# provision that merely mentions the word (e.g. "By signing below, the parties agree ..." and "All notices shall
# be sent to the following address:" both start with other words, so neither matches). issue 0036 adds the notice
# contacts; a bare street/city address line has no such label and stays recall-first (kept -> a thin clause).
_FURNITURE_LINE = re.compile(rf"^\s*(?:{_alternation(_VOCAB['furniture_labels'])})\s*:", re.IGNORECASE)  # cbr:furnitureLabel
# A table-of-contents entry: a dotted leader (>=3 dots, optionally spaced) running to a trailing page number.
# High-precision furniture; a decimal like "Section 3.1" or "(see Section 3.1)." has no long leader-to-page run.
_TOC_LEADER = re.compile(r"(?:\.\s*){3,}\d+\s*$")


def is_extractable_span(text: str) -> bool:
    """EXTRACT-GUARD-1: whether a span is a CLAUSE worth attempting typed-property extraction on. RECALL-FIRST:
    returns True for anything carrying lowercase prose (a real provision has function words -- 'the', 'shall',
    'of'); only DECLINES clear document FURNITURE that bears no clause properties and only burns a docling-graph
    call + retries -- a page number ('9'), a docket reference, a short bare ALL-CAPS heading ('EXHIBIT C',
    'FORM OF SUBLICENSE', 'AMENDMENT OF DEFINITIONS.'), or a signature/execution-block label line ('By: /s/ ...',
    'Name:', 'Title:').

    A skipped span is NOT a content loss: it stays in the SPAN INDEX for retrieval (indexing is independent of
    clause extraction); the guard only declines to mint a clause-KG node from furniture. A long ALL-CAPS provision
    (a capitalised disclaimer, `>= _MIN_ALLCAPS_WORDS` words) is still extracted."""
    t = text.strip()
    if sum(c.isalpha() for c in t) < _MIN_CLAUSE_ALPHA:  # near-empty: page numbers, "By:", pure digits/punct
        return False
    if _FURNITURE_LINE.match(t):  # a signature/execution-block or notice-block contact-label line, not a provision
        return False
    if _TOC_LEADER.search(t):  # a table-of-contents dotted-leader-to-page-number line, not a provision
        return False
    if not any(c.islower() for c in t):  # ALL-CAPS: a label/heading unless it is a long (capitalised) clause
        return len(_WORD_RE.findall(t)) >= _MIN_ALLCAPS_WORDS
    return True  # has lowercase prose -> treat as a real clause (recall-first)


# issue 0038/0039: a span that STARTS a new numbered contract section -- the provision unit. DEPTH-CAPPED at
# TWO levels ('2.' or '2.1.'): a top-level or one-level-nested number opens a provision, but a DEEPER number
# ('10.5.1.', '10.5.1.1.') is a LIST ITEM within its parent provision and must FOLD IN, per 0038's own grouping
# rule (issue 0039: a naive marker/number regex over-splits at depth 3-4, trading one granularity bug for a
# smaller one). A parenthesised letter/roman item ('(a)', '(i)') is likewise not a section (no leading digit).
_SECTION_START = re.compile(r"^\(?\d{1,2}(?:\.\d{1,2})?\)?\.\s")
# A section-WORD prefix ('Section 8', 'Article 2', 'Clause 12', 'Sec.'/'Art.', '§3') before a number -- the dominant
# contract heading style. Stripped so the depth-capped number rule above decides exactly as for a bare '8.'. The
# digit lookahead means a prose line like 'Section hereof shall mean ...' is left untouched (not a start).
_SECTION_WORD = re.compile(  # cbr:sectionSymbol / cbr:sectionWord
    rf"^(?:(?:{_alternation(_VOCAB['section_symbols'])})\s*|(?:{_alternation(_VOCAB['section_words'])})\.?\s+)(?=\(?\d)",
    re.IGNORECASE)
# The number FOLLOWING a section word (depth-capped to two levels; trailing '.'/')' optional, since the section
# word already signals a heading): 'Section 8.' and 'Clause 12 Governing Law' both start a provision.
_SECTION_NUM = re.compile(r"^\(?\d{1,2}(?:\.\d{1,2})?\)?[.)]?(?:\s|$)")


def starts_new_provision(text: str) -> bool:
    """issue 0038/0039: whether a span BEGINS a new provision, used to group contiguous spans into a provision for
    clause extraction (retrieval stays per span). Tiered, deterministic:

      - a span with a LEADING NUMBER is decided SOLELY by `_SECTION_START` (depth-capped to two levels): '2.' or
        '2.1.' starts a provision; a deeper '10.5.1.'/'10.5.1.1.' is a list item and FOLDS IN (issue 0039 -- a
        depth-blind rule over-splits nested list items into their own provisions);
      - a span with NO leading number starts a provision if it is a bare Title-case heading or a short ALL-CAPS
        heading (an un-numbered but headed contract).

    When a document has none of these, no intra-chunk boundary fires and grouping falls back to the chunk (the
    caller also breaks on a chunk change), so a heading-less contract degrades to chunk-level -- never one clause
    per sentence, never per document."""
    t = text.strip()
    m = _SECTION_WORD.match(t)
    if m:  # an explicit 'Section/Article/Clause N' heading -> a start (depth-capped; trailing period optional)
        return bool(_SECTION_NUM.match(t[m.end():]))
    if re.match(r"^\(?\d", t):  # a numbered item: ONLY the depth-capped section rule decides (no heading override)
        return bool(_SECTION_START.match(t))
    if is_bare_heading(t):  # un-numbered but titled ('Governing Law', a short Title-case line)
        return True
    if t and not any(c.islower() for c in t) and len(_WORD_RE.findall(t)) < _MIN_ALLCAPS_WORDS:
        return True  # a short ALL-CAPS heading ('CONFIDENTIALITY')
    return False


def provision_boundary_verdict(text: str) -> str:
    """Three-way boundary classification used to group spans into provisions: `"start"` (a confident, deterministic
    provision start), `"continue"` (clearly provision body), or `"uncertain"` (a short, plausibly-heading line in a
    style the deterministic rules do not confidently classify -- e.g. a roman-numeral or colon heading). Only the
    `"uncertain"` residue is sent to a decision model (Jev) by `spans.boundary`; a deterministic `"start"`/
    `"continue"` never pays for a model call, so cost stays bounded to the ambiguous SHORT lines -- and new heading
    styles get a model's judgment instead of another regex (the flexibility regex alone cannot give)."""
    t = text.strip()
    if starts_new_provision(t):
        return "start"
    if _heading_candidate(t):
        return "uncertain"
    return "continue"


def _heading_candidate(text: str) -> bool:
    """A short line that plausibly BEGINS a provision but was not a confident deterministic start: an enumerated
    lead-in (number / roman numeral / letter) we did not confidently start, or a short capitalized title-like line
    that is NOT a full sentence (no sentence punctuation). Deliberately broad -- the model decides -- but bounded to
    SHORT lines, so ordinary body prose (long, lowercase-led, or a terminated sentence) is never a candidate."""
    t = text.strip()
    if not t or len(t) > 90 or t[0].islower():
        return False  # empty, long, or lowercase-led -> provision body, never a candidate
    if LEADING_ENUM.match(t):
        return True  # an enumerated lead-in the deterministic rules did not confidently start
    return not (re.search(r"\.\s", t) or t.endswith("."))  # short + capitalized + NOT a full sentence -> a heading candidate


# ING-1 (ADR-0124): the span contract is the engine's generic `Span`; this legal segmenter is one implementation
# of the `Segmenter` hook. The name is kept for the reference pack's callers.
OperativeSpan = Span


def _boundaries(body: str) -> list[int]:
    """Deterministic cut offsets that partition `body` into operative spans (includes 0 and len(body)).

    A markdown table block (issue 0014) is atomic: cuts are forced at its edges and every candidate cut INSIDE
    it is suppressed, so the whole table stays one span (header + rows)."""
    blocks = _table_block_ranges(body)

    def _inside(c: int) -> bool:  # strictly inside a table block -> not a valid cut
        return any(bs < c < be for bs, be in blocks)

    cuts: set[int] = {0, len(body)}
    for bs, be in blocks:  # a table block's edges are always boundaries
        cuts.add(bs)
        cuts.add(be)
    for m in re.finditer(r"\n+", body):  # paragraph breaks
        if not _inside(m.end()):
            cuts.add(m.end())
    for m in _ENUM.finditer(body):  # split BEFORE an enumeration marker opening a provision
        if not _inside(m.start(1)):
            cuts.add(m.start(1))
    for m in _TERM.finditer(body):  # split AFTER a genuine sentence/list terminator
        p = m.start()  # index of the '.' ';' or ':'
        if _inside(m.end()):
            continue
        if body[p] == ".":
            prev = body[p - 1] if p > 0 else ""
            if prev.isdigit():  # decimal / section reference (12.1) -- not a terminator
                continue
            word = re.search(r"([A-Za-z.]+)$", body[max(0, p - 8):p])
            if word and word.group(1).lower().strip(".") in _ABBREV:  # known abbreviation
                continue
        cuts.add(m.end())
    return sorted(cuts)


def _merge_subfloor(ranges: list[tuple[int, int]], body: str, min_chars: int) -> list[tuple[int, int]]:
    """Fold a sub-floor fragment (bare heading/marker) into the FOLLOWING span; a trailing one into the previous.
    Merges only extend adjacent ranges, so the tiling invariant (contiguous, gap-free) is preserved."""
    merged: list[tuple[int, int]] = []
    carry: int | None = None
    for i, (s, e) in enumerate(ranges):
        start = carry if carry is not None else s
        is_last = i == len(ranges) - 1
        # fold FORWARD a sub-floor fragment OR a bare heading (0006-B) -- so a heading never stands alone
        if (len(body[start:e].strip()) < min_chars or is_bare_heading(body[start:e])) and not is_last:
            carry = start  # carry its start into the next span (its body)
            continue
        merged.append((start, e))
        carry = None
    if carry is not None:  # trailing sub-floor: extend the previous span to the end
        last_end = ranges[-1][1]
        if merged:
            merged[-1] = (merged[-1][0], last_end)
        else:
            merged.append((carry, last_end))
    return merged


def segment_clause(
    parent_chunk_id: str,
    body: str,
    *,
    min_chars: int = DEFAULT_MIN_CHARS,
) -> list[OperativeSpan]:
    """Segment a clause body into operative spans. Deterministic; spans tile the body byte-faithfully."""
    if not body:
        return []
    cuts = _boundaries(body)
    ranges = [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1)]
    ranges = _merge_subfloor(ranges, body, min_chars)
    return [
        OperativeSpan(
            span_id=f"{parent_chunk_id}#{i}",
            parent_chunk_id=parent_chunk_id,
            span_index=i,
            start=s,
            end=e,
            text=body[s:e],
        )
        for i, (s, e) in enumerate(ranges)
    ]
