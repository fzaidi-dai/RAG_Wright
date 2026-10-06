"""ING-1 (ADR-0124): the generic ingestion hook contracts.

Ingestion is parse -> chunk -> segment -> (tag) -> group into units -> extract -> write, plus the span index and the
entity graph. The engine owns the wiring, indexing, page provenance, ids, writing, progress and dead-lettering; a
DOMAIN overrides only the domain-shaped hooks below, each with a domain-neutral engine default. The reference
(contract) pack's legal segmenter, provision grouping, boundary decider, function classifier and clause extractor
are one set of implementations of these hooks, not the engine's definition of them.

The `check_*` functions are what the engine enforces on every hook's output, default or override alike.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Literal, Optional, Protocol, Sequence

from pydantic import BaseModel, model_validator

from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.store.seam import KgEdge, KgNode


class IngestionContractError(ValueError):
    """A hook's output broke the ingestion contract (tiling, unit integrity, or record provenance)."""


# The engine's domain-neutral layout kinds. The docling-backed default maps docling's item labels onto these, so a
# hook never depends on the parser's own label set.
LayoutKind = Literal[
    "title", "heading", "paragraph", "list_item", "table", "caption", "footnote",
    "page_header", "page_footer", "code", "formula", "form", "other",
]


class LayoutItem(BaseModel):
    """One layout element of the parsed document that overlaps a chunk, in CHUNK-relative offsets."""

    model_config = {"frozen": True}

    kind: LayoutKind
    text: str
    start: int
    end: int  # exclusive
    level: Optional[int] = None  # heading depth (1 = top), when the parse knows it
    pages: list[int] = []  # 1-based source pages, when the parse carried them

    @model_validator(mode="after")
    def _offsets(self) -> "LayoutItem":
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"invalid layout offsets [{self.start}, {self.end})")
        return self


class Span(BaseModel):
    """One span: the smallest citeable unit, indexed for retrieval. It points back to its parent chunk; `span_id` is
    `<parent_chunk_id>#<span_index>` (identifier rule) and `start`/`end` are offsets into the chunk text."""

    span_id: str
    parent_chunk_id: str
    parent_okf_path: str = ""  # reference-pack legacy locator; unused by the generic path
    span_index: int
    start: int
    end: int  # exclusive; the spans of a chunk tile its text
    text: str  # text[start:end], raw (strip at use time)
    pages: list[int] = []  # the source page(s) the span overlaps (set at ingest)
    bbox: tuple[float, float, float, float] | None = None  # best-effort single-item box (l, t, r, b)

    @model_validator(mode="after")
    def _offsets(self) -> "Span":
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"invalid span offsets [{self.start}, {self.end})")
        return self


class TaggedSpan(BaseModel):
    """A span with the optional span tagger's soft tags (primary first) and their scores."""

    span: Span
    tags: list[str] = []
    scores: dict[str, float] = {}


class Unit(BaseModel):
    """The extraction unit: consecutive spans grouped by the unit grouper. `text` is what the extractor reads;
    `anchor` is the citation anchor for records read from it (a member span)."""

    index: int
    anchor: Span
    spans: list[Span]
    text: str
    tags: list[str] = []

    @model_validator(mode="after")
    def _integrity(self) -> "Unit":
        if not self.spans:
            raise ValueError("a unit needs at least one span")
        if self.anchor.span_id not in {s.span_id for s in self.spans}:
            raise ValueError(f"anchor {self.anchor.span_id!r} is not a member of the unit")
        if not self.text.strip():
            raise ValueError("a unit needs non-blank text")
        return self


class UnitExtraction(BaseModel):
    """What an extractor returns for one unit: typed KG nodes/edges in the pack's schema. Every node carries
    `span_id` (a span of the unit) and `confidence` (a `ConfidenceTag` value) as props (FR-S.4)."""

    model_config = {"arbitrary_types_allowed": True}

    nodes: list[KgNode] = []
    edges: list[KgEdge] = []


# --- the hooks -------------------------------------------------------------------------------------------------

class Segmenter(Protocol):
    """chunk -> spans that tile its text. Sync (CPU). `layout` is the parse's layout overlapping the chunk (empty
    for a text-only source)."""

    def __call__(self, chunk_id: str, text: str, layout: Sequence[LayoutItem]) -> list[Span]: ...


class SpanTagger(Protocol):
    """Optional: soft tags per span of one chunk, aligned to `spans`."""

    def __call__(self, chunk_text: str, spans: Sequence[Span]) -> Awaitable[list[TaggedSpan]]: ...


# Optional residue decider for the unit grouper: candidate texts -> "starts a new unit?" per text.
BoundaryDecider = Callable[[list[str]], Awaitable[list[bool]]]


class UnitGrouper(Protocol):
    """A document's spans (in order, across chunks) -> extraction units. May drop spans (e.g. page furniture); a
    dropped span stays in the span index. `decider`, when set, adjudicates boundaries the grouper is unsure of."""

    def __call__(
        self, spans: Sequence[TaggedSpan], *, decider: Optional[BoundaryDecider] = None
    ) -> Awaitable[list[Unit]]: ...


class Extractor(Protocol):
    """One unit -> the domain's typed records (required; the domain-specific step)."""

    def __call__(self, unit: Unit, *, source_doc_id: str) -> Awaitable[UnitExtraction]: ...


class RecordWriter(Protocol):
    """Optional: persist a document's extractions. The engine default writes them with `kg_write`."""

    def __call__(self, source_doc_id: str, extractions: Sequence[UnitExtraction]) -> Awaitable[None]: ...


# --- the engine-enforced checks --------------------------------------------------------------------------------

def check_tiling(chunk_id: str, text: str, spans: Sequence[Span]) -> None:
    """A segmenter's spans must tile `text` in order, byte-faithfully, under the `span_id` scheme."""
    pos = 0
    for i, s in enumerate(spans):
        if s.parent_chunk_id != chunk_id:
            raise IngestionContractError(f"span {i}: parent_chunk_id {s.parent_chunk_id!r} != {chunk_id!r}")
        if s.span_index != i or s.span_id != f"{chunk_id}#{i}":
            raise IngestionContractError(f"span {i}: span_id {s.span_id!r} / span_index {s.span_index} off the "
                                         f"'<chunk_id>#<index>' scheme")
        if s.start != pos:
            raise IngestionContractError(f"span {i}: starts at {s.start}, expected {pos} (spans must tile the text)")
        if s.text != text[s.start:s.end]:
            raise IngestionContractError(f"span {i}: text is not the slice text[{s.start}:{s.end}]")
        pos = s.end
    if pos != len(text):
        raise IngestionContractError(f"spans end at {pos}, text has {len(text)} chars (spans must tile the text)")


def check_units(spans: Sequence[Span], units: Sequence[Unit]) -> None:
    """A grouper's units must use known spans, each at most once, in document order, indexed 0..k-1."""
    order = {s.span_id: i for i, s in enumerate(spans)}
    seen: set[str] = set()
    last = -1
    for k, u in enumerate(units):
        if u.index != k:
            raise IngestionContractError(f"unit {k}: index {u.index}, expected {k}")
        for s in u.spans:
            if s.span_id not in order:
                raise IngestionContractError(f"unit {k}: unknown span {s.span_id!r}")
            if s.span_id in seen:
                raise IngestionContractError(f"unit {k}: span {s.span_id!r} is in more than one unit")
            if order[s.span_id] <= last:
                raise IngestionContractError(f"unit {k}: span {s.span_id!r} is out of document order")
            seen.add(s.span_id)
            last = order[s.span_id]


_CONFIDENCE = {c.value for c in ConfidenceTag}


def check_extraction(unit: Unit, extraction: UnitExtraction) -> None:
    """Every record node must cite a span of its unit and carry a `ConfidenceTag` (FR-S.4)."""
    members = {s.span_id for s in unit.spans}
    for n in extraction.nodes:
        if n.props.get("span_id") not in members:
            raise IngestionContractError(f"{n.type} node: span_id {n.props.get('span_id')!r} is not a span of "
                                         f"unit {unit.index}")
        if n.props.get("confidence") not in _CONFIDENCE:
            raise IngestionContractError(f"{n.type} node: confidence {n.props.get('confidence')!r} is not one of "
                                         f"{sorted(_CONFIDENCE)}")
