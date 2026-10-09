"""ING-4b (ADR-0124): `evaluate_ingestion` -- the structural eval a developer runs on THEIR OWN sample documents to
check (and tune, via `IngestionTuning`) the default ingestion hooks before trusting them.

It runs parse + chunk (deterministic structural boundaries, no model) + segment + group -- no store, no model calls
-- and measures how faithfully spans and units follow each document's structure:

  segmentation : spans tile the chunk text; each table row is its own span (`table_row_integrity`); no span crosses
                 into another layout item (`layout_respect`); no span is a bare heading.
  grouping     : every heading starts a unit; every table lands whole in one unit (or in header-carrying parts /
                 one unit per row for a record table); no page furniture in a unit; every content span covered;
                 every unit within the cap.
  table modes  : with `table_labels` (`[{"pattern": <regex on the header row>, "label": "record"|"block"}]`, first
                 match wins): no BLOCK table split per row, every RECORD table of `record_table.min_cols`+ columns
                 split, every table labelled.

  labels       : with `unit_labels` (gold: per document, `[{"text": <a snippet of one unit>, "label": <its expected
                 label, "" for none>}]`), each gold unit's label (its leading tag, after the `span_tagger` and any
                 `unit_representative`) against the gold, per unit: accuracy, per-label results, confusion pairs.
                 A snippet that finds no unit or several, and a unit whose snippets carry different labels, are
                 failures (that gold cannot be scored); accuracy is a measure, not a check.

`passed` is True when every check holds; `failures` says which did not.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from rag_wright.contracts.ingestion import (
    IngestionContractError,
    IngestionTuning,
    IngestSource,
    Segmenter,
    SpanTagger,
    TaggedSpan,
    UnitGrouper,
    UnitRepresentative,
    check_tiling,
    check_units,
)

_MIN_RATIO = 0.98  # table-row integrity and layout respect, pooled
_SEPARATOR = re.compile(r"[ \t]*\|?[ \t:|-]*-[ \t:|-]*\|?[ \t]*")


@dataclass
class DocumentEvaluation:
    """One sample document's structural measures. Segmentation: `tiles` (spans tile every chunk),
    `table_row_integrity` (share of table rows that are their own span), `layout_respect` (share of spans inside one
    layout item), `bare_heading_spans`. Grouping: `headings_start_units` (share of headings that start a unit),
    `tables_whole` (share of tables kept whole or split by the record/header rule), `furniture_in_units` (page
    furniture that leaked into units), `coverage` (share of content spans in some unit), `cap_ok` (every unit within
    `max_unit_chars`). `table_modes`: the mode chosen per table. `error`: why the document could not be evaluated."""

    doc: str
    tiles: bool = True
    spans: int = 0
    units: int = 0
    table_rows: int = 0
    table_row_integrity: Optional[float] = None
    layout_respect: Optional[float] = None
    bare_heading_spans: int = 0
    headings_start_units: Optional[float] = None
    tables: int = 0
    tables_whole: Optional[float] = None
    furniture_in_units: int = 0
    coverage: Optional[float] = None
    cap_ok: bool = True
    table_modes: list[dict] = field(default_factory=list)
    embedded_children: int = 0
    label_results: list[dict] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class LabelEvaluation:
    """Unit labelling against the gold (`evaluate_ingestion(unit_labels=...)`), scored per UNIT: snippets that land
    in the same unit with the same label count once. `gold`: units scored; `correct`: those whose label matches;
    `accuracy`: correct / gold. `per_label`:
    `{label: {gold, predicted, correct, recall, precision}}` over the gold units (precision is among the gold units
    predicted with that label; `None` when none were). `confusions`: `{gold, predicted, count}` for every wrong pair,
    most frequent first. `unmatched` / `ambiguous`: `"<doc>: <snippet>"` for a snippet found in no unit / in several.
    `conflicting`: `"<doc>: unit <i>: <labels>"` for a unit whose snippets carry different labels (not scored: one
    label cannot match them all). An empty label means the unit should carry none."""

    gold: int = 0
    correct: int = 0
    accuracy: Optional[float] = None
    per_label: dict[str, dict] = field(default_factory=dict)
    confusions: list[dict] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    conflicting: list[str] = field(default_factory=list)


@dataclass
class IngestionEvaluation:
    """The result of `evaluate_ingestion`: per-document measures, the `failures` (each check that did not hold, by
    document) and `unlabelled_tables` (tables no `table_labels` pattern matched). `passed` (property) is True when
    there are no failures."""

    documents: list[DocumentEvaluation] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    unlabelled_tables: list[str] = field(default_factory=list)
    labels: Optional[LabelEvaluation] = None

    @property
    def passed(self) -> bool:
        return not self.failures


def _alnum(s: str) -> int:
    return sum(c.isalnum() for c in s)


def _rows(text: str, start: int, end: int) -> list[tuple[int, int]]:
    return [(start + m.start(), start + m.end()) for m in re.finditer(r"[^\n]+", text[start:end])
            if not _SEPARATOR.fullmatch(m.group())]


def _squash(text: str) -> str:
    return " ".join(text.split())


async def _tag_and_group(per_chunk: list, tagger: Optional[SpanTagger], group: Any,
                         representative: Optional[UnitRepresentative]) -> list:
    from rag_wright.ingestion.group import apply_unit_representative

    async def tag(text: str, spans: list) -> list[TaggedSpan]:
        live = [s for s in spans if s.text.strip()]
        by_id = {t.span.span_id: t for t in (await tagger(text, live) if tagger and live else [])}
        return [by_id.get(s.span_id) or TaggedSpan(span=s) for s in spans]

    tagged = [t for ts in await asyncio.gather(*(tag(text, ss) for text, ss in per_chunk)) for t in ts]
    units = await group(tagged, decider=None)
    if representative is not None:
        units = apply_unit_representative(units, tagged, representative)
    return units


def _label_results(units: list, gold: list[dict]) -> list[dict]:
    texts = [_squash(u.text) for u in units]
    out = []
    for g in gold:
        hits = [i for i, t in enumerate(texts) if _squash(g["text"]) in t]
        out.append({"text": g["text"], "gold": g.get("label") or "", "units": hits,
                    "predicted": (units[hits[0]].tags[:1] or [""])[0] if len(hits) == 1 else None})
    return out


def _evaluate_document(src: IngestSource, tuning: IngestionTuning, cache: Path,
                       segmenter: Optional[Segmenter], grouper: Optional[UnitGrouper],
                       tagger: Optional[SpanTagger] = None, representative: Optional[UnitRepresentative] = None,
                       gold: Optional[list[dict]] = None) -> DocumentEvaluation:
    from rag_wright.api.documents import parse_document_bytes
    from rag_wright.capabilities.parsing import load_document
    from rag_wright.capabilities.rlm_chunking import StructuralBoundaryDiscoverer, chunk_texts
    from rag_wright.ingestion.group import group_units
    from rag_wright.ingestion.layout import chunk_layouts
    from rag_wright.ingestion.segment import segment_layout

    name = src.display_name
    ev = DocumentEvaluation(doc=name)
    sd = parse_document_bytes(re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem)[:80] or "doc", name,
                              src.read_bytes(), cache_dir=cache, include_hidden_sheets=src.include_hidden_sheets,
                              tuning=tuning)
    ev.embedded_children = len(sd.embedded)
    document = load_document(sd.parsed)
    texts = chunk_texts(document, discoverer=StructuralBoundaryDiscoverer())
    layouts = chunk_layouts(document, texts)
    segment = segmenter or (lambda cid, text, layout: segment_layout(cid, text, layout, tuning=tuning))
    spans, rows_ok, respect, per_chunk = [], 0, 0, []
    for ci, (text, layout) in enumerate(zip(texts, layouts)):
        cid = f"eval:{ci}:h"
        chunk_spans = segment(cid, text, layout)
        per_chunk.append((text, chunk_spans))
        try:
            check_tiling(cid, text, chunk_spans)
        except IngestionContractError:
            ev.tiles = False
        spans += chunk_spans
        content = [it for it in layout if it.kind not in ("heading", "title") and _alnum(it.text) >= 2]
        heads = [it for it in layout if it.kind in ("heading", "title")]
        for s in chunk_spans:
            touched = [it for it in content if it.start < s.end and it.end > s.start]
            respect += len(touched) <= 1
            if content and s.text.strip() and not touched and any(h.start < s.end and h.end > s.start for h in heads):
                ev.bare_heading_spans += 1
        for it in layout:
            if it.kind != "table":
                continue
            rows = _rows(text, it.start, it.end)
            for k, (rs, re_) in enumerate(rows):
                ev.table_rows += 1
                holders = [s for s in chunk_spans if s.start < re_ and s.end > rs]
                if len(holders) == 1:
                    h = holders[0]
                    rows_ok += not any(j != k and h.start < r[1] and h.end > r[0] for j, r in enumerate(rows))
    ev.spans = len(spans)
    ev.table_row_integrity = rows_ok / ev.table_rows if ev.table_rows else None
    ev.layout_respect = respect / len(spans) if spans else None

    group = grouper or (lambda tagged, decider=None: group_units(tagged, decider=decider, tuning=tuning,
                                                                  table_mode=src.table_mode))
    units = asyncio.run(_tag_and_group(per_chunk, tagger, group, representative))
    check_units(spans, units)
    if gold is not None:
        ev.label_results = _label_results(units, gold)
    ev.units = len(units)
    unit_of = {s.span_id: u for u in units for s in u.spans}
    anchors = {u.anchor.span_id for u in units}
    heads = [s for s in spans if s.kind in ("heading", "title")]
    ev.headings_start_units = sum(h.span_id in anchors for h in heads) / len(heads) if heads else None
    content = [s for s in spans if s.kind not in ("page_header", "page_footer") and _alnum(s.text) >= 2]
    ev.coverage = sum(s.span_id in unit_of for s in content) / len(content) if content else None
    ev.furniture_in_units = sum(s.kind in ("page_header", "page_footer") for u in units for s in u.spans)
    ev.cap_ok = all(len(u.text) <= tuning.max_unit_chars
                    or sum(s.kind not in ("table", "heading", "title") for s in u.spans) <= 1 for u in units)
    tables, cur = [], None
    for s in spans:
        if s.kind == "table_row" and cur is not None:
            cur.append(s)
        else:
            cur = [s] if s.kind in ("table", "heading", "title") else None
            if cur is not None:
                tables.append(cur)
    tables = [t for t in tables if len(t) > 1]
    whole = 0
    for t in tables:
        parts = list(dict.fromkeys(unit_of[s.span_id].index for s in t if s.span_id in unit_of))
        header = t[0].text.strip()
        whole += len(parts) == 1 or all(units[i].text.startswith(header) for i in parts[1:])
        line = next((ln for ln in t[0].text.split("\n") if ln.strip().startswith("|")), "")
        ev.table_modes.append({"header": " ".join(line.split())[:160], "cols": line.count("|") - 1,
                               "rows": len(t) - 1, "split": len(parts) == len(t) - 1 and len(parts) > 1})
    ev.tables = len(tables)
    ev.tables_whole = whole / len(tables) if tables else None
    return ev


def evaluate_ingestion(sources: Sequence[Union[str, Path, IngestSource]], *, cache_dir: Union[str, Path],
                       tuning: Optional[IngestionTuning] = None, segmenter: Optional[Segmenter] = None,
                       unit_grouper: Optional[UnitGrouper] = None,
                       table_labels: Optional[list[dict[str, Any]]] = None,
                       span_tagger: Optional[SpanTagger] = None,
                       unit_representative: Optional[UnitRepresentative] = None,
                       unit_labels: Optional[dict[str, list[dict[str, str]]]] = None) -> IngestionEvaluation:
    """Measure the ingestion hooks' structural fidelity on YOUR sample documents, before trusting them. Runs parse,
    chunk, segment and group only (no store, no model calls) with the default hooks or the `segmenter` /
    `unit_grouper` you pass, under `tuning`. Checks: spans tile the text, table rows stay whole, spans respect layout
    items, no bare-heading spans, headings start units, tables stay whole (or split per row for a record table), no
    page furniture in units, full coverage, units within the cap. `table_labels`
    (`[{"pattern": <regex on the header row>, "label": "record" | "block"}]`, first match wins) also checks each
    table's mode. Returns an `IngestionEvaluation`; tune `IngestionTuning` until `passed`.

    Labelling (PS-R4): pass your `span_tagger` (and any `unit_representative`) with `unit_labels`, the gold
    `{<document file name>: [{"text": <a snippet that occurs in exactly one unit>, "label": <expected label, "" for
    none>}]}`, and `labels` (a `LabelEvaluation`) reports each gold unit's label against it: accuracy, per-label
    results, confusion pairs. The tagger is yours, so it may call models; nothing else does."""
    tuning = tuning or IngestionTuning()
    out = IngestionEvaluation()
    for s in sources:
        src = s if isinstance(s, IngestSource) else IngestSource(path=str(s))
        try:
            gold = None if unit_labels is None else unit_labels.get(src.display_name, [])
            out.documents.append(_evaluate_document(src, tuning, Path(cache_dir), segmenter, unit_grouper,
                                                    span_tagger, unit_representative, gold))
        except Exception as exc:  # noqa: BLE001 - a document the hooks cannot handle is a reported failure
            out.documents.append(DocumentEvaluation(doc=src.display_name, error=f"{type(exc).__name__}: {exc}"))
    docs = out.documents
    f = out.failures
    f += [f"{d.doc}: {d.error}" for d in docs if d.error]
    f += [f"{d.doc}: spans do not tile the text" for d in docs if not d.error and not d.tiles]
    rows = sum(d.table_rows for d in docs)
    if rows and sum((d.table_row_integrity or 0) * d.table_rows for d in docs) / rows < _MIN_RATIO:
        f.append(f"table-row integrity below {_MIN_RATIO}")
    spans = sum(d.spans for d in docs)
    if spans and sum((d.layout_respect or 0) * d.spans for d in docs) / spans < _MIN_RATIO:
        f.append(f"layout respect below {_MIN_RATIO}")
    for d in docs:
        if d.error:
            continue
        if d.bare_heading_spans:
            f.append(f"{d.doc}: {d.bare_heading_spans} bare-heading span(s)")
        for name in ("headings_start_units", "tables_whole", "coverage"):
            value = getattr(d, name)
            if value is not None and value < 1.0:
                f.append(f"{d.doc}: {name} = {value:.3f}")
        if d.furniture_in_units:
            f.append(f"{d.doc}: page furniture inside units")
        if not d.cap_ok:
            f.append(f"{d.doc}: a unit exceeds the cap")
    if table_labels is not None:
        def label_of(header: str) -> Optional[str]:
            return next((x["label"] for x in table_labels if re.search(x["pattern"], header, re.I)), None)

        for d in docs:
            for m in d.table_modes:
                label = label_of(m["header"])
                if label is None:
                    out.unlabelled_tables.append(m["header"])
                elif label == "block" and m["split"]:
                    f.append(f"{d.doc}: block table split per row: {m['header'][:80]}")
                elif label == "record" and m["cols"] >= tuning.record_table.min_cols and not m["split"]:
                    f.append(f"{d.doc}: wide record table kept whole: {m['header'][:80]}")
        out.unlabelled_tables = sorted(set(out.unlabelled_tables))
        if out.unlabelled_tables:
            f.append(f"{len(out.unlabelled_tables)} table(s) without a label")
    if unit_labels is not None:
        out.labels = _score_labels(docs, f)
        names = {d.doc for d in docs}
        f += [f"gold for {name!r}: not among the sources" for name in unit_labels if name not in names]
    return out


def _score_labels(docs: list[DocumentEvaluation], failures: list[str]) -> LabelEvaluation:
    lab = LabelEvaluation()
    pairs: dict[tuple[str, str], int] = {}
    for d in docs:
        for r in d.label_results:
            where = f"{d.doc}: {r['text']}"
            if not r["units"]:
                lab.unmatched.append(where)
                failures.append(f"{where!r}: the gold snippet matches no unit")
            elif len(r["units"]) > 1:
                lab.ambiguous.append(where)
                failures.append(f"{where!r}: the gold snippet matches {len(r['units'])} units")
        by_unit: dict[int, dict[str, str]] = {}
        for r in d.label_results:
            if len(r["units"]) == 1:
                by_unit.setdefault(r["units"][0], {})[r["gold"]] = r["predicted"]
        for unit, golds in sorted(by_unit.items()):
            if len(golds) > 1:
                names = ", ".join(sorted(golds))
                lab.conflicting.append(f"{d.doc}: unit {unit}: {names}")
                failures.append(f"{d.doc}: unit {unit}: the gold gives it different labels ({names})")
                continue
            ((gold, predicted),) = golds.items()
            pairs[(gold, predicted)] = pairs.get((gold, predicted), 0) + 1
    for (gold, predicted), n in pairs.items():
        lab.gold += n
        lab.correct += n if gold == predicted else 0
        for label, key in ((gold, "gold"), (predicted, "predicted")):
            lab.per_label.setdefault(label, {"gold": 0, "predicted": 0, "correct": 0})[key] += n
        if gold == predicted:
            lab.per_label[gold]["correct"] += n
    for row in lab.per_label.values():
        row["recall"] = row["correct"] / row["gold"] if row["gold"] else None
        row["precision"] = row["correct"] / row["predicted"] if row["predicted"] else None
    lab.accuracy = lab.correct / lab.gold if lab.gold else None
    lab.confusions = sorted(({"gold": g, "predicted": p, "count": n} for (g, p), n in pairs.items() if g != p),
                            key=lambda c: (-c["count"], c["gold"], c["predicted"]))
    return lab
