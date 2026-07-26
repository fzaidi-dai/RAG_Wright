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

from pydantic import BaseModel

from rag_wright.contracts.span import SpanRecord

DEFAULT_MIN_CHARS = 25  # a span whose stripped text is shorter folds into its neighbour (a bare heading/marker)

# Abbreviations whose trailing '.' does not end a provision (lower-cased, no trailing dot).
_ABBREV = {
    "inc", "corp", "co", "ltd", "llc", "llp", "plc", "no", "nos", "art", "sec", "secs", "para", "paras",
    "cf", "vs", "v", "mr", "mrs", "ms", "dr", "st", "ave", "etc", "al", "viz", "e.g", "i.e", "u.s", "u.s.c",
}

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


class OperativeSpan(BaseModel):
    """One operative span of a clause, pointing back to its parent clause (FR-R small-to-big unit)."""

    span_id: str  # "{parent_chunk_id}#{span_index}" -- embeds the parent (identifier rule, ADR-0025)
    parent_chunk_id: str
    parent_okf_path: str  # where the parent clause lives in the clause OKF bundle (locate/fetch for rerank)
    span_index: int
    start: int  # char offset into the parent clause body
    end: int  # exclusive; spans tile the body: body[start:end] concatenated == body
    text: str  # body[start:end] (raw slice; strip at use time)


def _boundaries(body: str) -> list[int]:
    """Deterministic cut offsets that partition `body` into operative spans (includes 0 and len(body))."""
    cuts: set[int] = {0, len(body)}
    for m in re.finditer(r"\n+", body):  # paragraph breaks
        cuts.add(m.end())
    for m in _ENUM.finditer(body):  # split BEFORE an enumeration marker opening a provision
        cuts.add(m.start(1))
    for m in _TERM.finditer(body):  # split AFTER a genuine sentence/list terminator
        p = m.start()  # index of the '.' ';' or ':'
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
        if len(body[start:e].strip()) < min_chars and not is_last:
            carry = start  # too small: carry its start into the next span
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
    parent_okf_path: str = "",
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
            parent_okf_path=parent_okf_path,
            span_index=i,
            start=s,
            end=e,
            text=body[s:e],
        )
        for i, (s, e) in enumerate(ranges)
    ]


def to_span_record(
    op: OperativeSpan,
    *,
    contract_id: str,
    chunk_doc_start: int,
    dense_vector: list[float],
    sparse_vector: dict[int, float],
    function: str = "",
    parent_okf_path: str | None = None,
) -> SpanRecord:
    """CU-B2 (ADR-0029): OperativeSpan -> SpanRecord with DOCUMENT-ABSOLUTE offsets.

    Composes `doc_start = chunk_doc_start + op.start`, `doc_end = chunk_doc_start + op.end` (the span's
    clause-relative offsets shifted by the parent chunk's offset in the canonical document text, CU-B1). The
    RAW span text (`op.text = body[start:end]`) is stored -- NOT stripped -- so the citation invariant
    `canonical_document_text[doc_start:doc_end] == span.text` holds byte-faithfully. The caller may embed over
    `op.text.strip()`; the stored text stays raw for the highlight.
    """
    return SpanRecord(
        span_id=op.span_id,
        parent_chunk_id=op.parent_chunk_id,
        parent_okf_path=op.parent_okf_path if parent_okf_path is None else parent_okf_path,
        span_index=op.span_index,
        text=op.text,
        function=function,
        dense_vector=dense_vector,
        sparse_vector=sparse_vector,
        contract_id=contract_id,
        doc_start=chunk_doc_start + op.start,
        doc_end=chunk_doc_start + op.end,
    )
