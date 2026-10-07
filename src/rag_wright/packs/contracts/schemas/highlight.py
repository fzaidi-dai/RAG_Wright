"""The serve-side highlight response contract (CU-A1 / CU-C2, ADR-0029).

What the pipeline returns for a query about a known contract: the SET of spans that pertain (possibly empty ->
"not present"), each with its exact document location so an app can highlight it, plus provenance for citation.
`HighlightResult` also carries the presence/absence and out-of-taxonomy/low-confidence signals the app/agent
routes on. Offsets mirror `SpanRecord` (document-absolute char offsets; optional page/bbox).
"""

from __future__ import annotations

from pydantic import BaseModel, model_validator


class HighlightSpan(BaseModel):
    """One span to highlight, with its citation (location + parent-clause reference)."""

    model_config = {"frozen": True}

    span_id: str
    contract_id: str
    function: str  # the clause type this span was matched under
    clause_ref: str  # human-readable parent-clause reference (heading/number or parent_chunk_id)
    text: str
    doc_start: int | None = None  # document-absolute char offsets (the highlight range)
    doc_end: int | None = None
    page: int | None = None  # optional PDF-overlay location (FIRST page; == pages[0] when known)
    pages: list[int] = []  # issue 0032: ALL source pages this span overlaps (page-level click-through)
    bbox: tuple[float, float, float, float] | None = None
    extracted_value: str | None = None  # for value-type categories: the pinpointed value within the span
    confidence: float = 1.0

    @model_validator(mode="after")
    def _check_offsets(self) -> "HighlightSpan":
        if self.doc_start is not None and self.doc_end is not None and self.doc_end < self.doc_start:
            raise ValueError(f"doc_end ({self.doc_end}) must be >= doc_start ({self.doc_start})")
        return self


class SpanLocation(BaseModel):
    """Where one span sits in the ORIGINAL document, for a citation PREVIEW (EP-REF-1b): a location + the text
    to confirm it landed, plus the clause ids extracted from it (so an answer's `citation_id` -- a span id OR a
    clause id, two different spaces -- resolves either way). Lighter than `HighlightSpan` (no function / clause
    ref / extracted value / confidence): a preview answers "show me this span", not "which spans pertain". `pages`
    is a LIST (a span can cross a page break; the first page is where the preview opens) and `bbox` is best-effort
    -- a page is nearly always known and a rectangle usually is, so a missing rectangle never costs the page."""

    span_id: str
    clause_ids: list[str] = []
    pages: list[int] = []
    bbox: tuple[float, float, float, float] | None = None
    doc_start: int | None = None
    doc_end: int | None = None
    text: str = ""


class HighlightResult(BaseModel):
    """The full response to one query about one contract."""

    model_config = {"frozen": True}

    query: str
    contract_id: str
    clause_types: list[str] = []  # the types searched (from QueryIntent)
    intent: str = "highlight"
    spans: list[HighlightSpan] = []  # the pertaining set; empty => not present
    present: bool = False  # spans is non-empty
    in_taxonomy: bool = True
    low_confidence: bool = False  # out-of-taxonomy semantic fallback used

    @model_validator(mode="after")
    def _check_present(self) -> "HighlightResult":
        if self.present != bool(self.spans):
            raise ValueError("`present` must equal whether `spans` is non-empty")
        return self
