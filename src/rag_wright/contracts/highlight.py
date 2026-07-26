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
    page: int | None = None  # optional PDF-overlay location
    bbox: tuple[float, float, float, float] | None = None
    extracted_value: str | None = None  # for value-type categories: the pinpointed value within the span
    confidence: float = 1.0

    @model_validator(mode="after")
    def _check_offsets(self) -> "HighlightSpan":
        if self.doc_start is not None and self.doc_end is not None and self.doc_end < self.doc_start:
            raise ValueError(f"doc_end ({self.doc_end}) must be >= doc_start ({self.doc_start})")
        return self


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
