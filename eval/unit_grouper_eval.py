"""ING-3 (ADR-0124): eval for the engine's default unit grouper (`group_units`), over the same sources as
`eval/segmenter_eval.py` (synthetic hand gold, LOCAL client forms via `$RAG_EVAL_TEXTILE_DIR`, a pinned public
report, contract PDFs as a diagnostic). Spans come from the default segmenter; structure truth from the parse.

GATE (non-legal real documents pooled, all = 1.0): every heading span starts a unit; every table's rows land in one
unit (or in cap-split parts whose continuations repeat the header row); no page furniture inside a unit; every
content span is covered; every unit fits the cap (or is a single over-long span). DIAGNOSTIC: unit counts/sizes,
and on contracts the generic units vs the reference provision units.

Run: `RAG_EVAL_TEXTILE_DIR=/path/to/forms uv run python -u eval/unit_grouper_eval.py`
Writes `data/eval/unit_grouper/results.json` (gitignored).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent))
import segmenter_eval as S  # noqa: E402 - shared sources, parse cache and helpers

OUT = S.ROOT / "data" / "eval" / "unit_grouper"


def log(msg: str) -> None:
    print(msg, flush=True)


def _spans(texts, layouts):
    from rag_wright.ingestion import segment_layout

    return [s for ci, (t, lay) in enumerate(zip(texts, layouts)) for s in segment_layout(f"eval:{ci}:h", t, lay)]


def _score(texts, layouts, max_chars: int) -> dict:
    from rag_wright.api import TaggedSpan, check_units
    from rag_wright.ingestion import group_units

    spans = _spans(texts, layouts)
    units = asyncio.run(group_units([TaggedSpan(span=s) for s in spans], max_chars=max_chars))
    check_units(spans, units)
    unit_of = {s.span_id: u for u in units for s in u.spans}
    anchors = {u.anchor.span_id for u in units}
    heads = [s for s in spans if s.kind in ("heading", "title")]
    content = [s for s in spans if s.kind not in ("page_header", "page_footer") and S._alnum(s.text) >= 2]
    # a table = a 'table' (or heading-opened) span followed by its 'table_row' spans
    tables, cur = [], None
    for s in spans:
        if s.kind == "table_row" and cur is not None:
            cur.append(s)
        else:
            cur = [s] if s.kind in ("table", "heading", "title") else None
            if cur is not None:
                tables.append(cur)
    tables = [t for t in tables if len(t) > 1]
    tables_ok = 0
    for t in tables:
        parts = list(dict.fromkeys(unit_of[s.span_id].index for s in t if s.span_id in unit_of))
        header = t[0].text.strip()
        tables_ok += len(parts) == 1 or all(units[i].text.startswith(header) for i in parts[1:])
    sizes = [len(u.text) for u in units]
    return {
        "units": len(units), "median_unit_chars": median(sizes) if sizes else 0, "max_unit_chars": max(sizes or [0]),
        "headings_start_units": sum(h.span_id in anchors for h in heads) / len(heads) if heads else None,
        "tables": len(tables), "tables_whole": tables_ok / len(tables) if tables else None,
        "furniture_in_units": sum(s.kind in ("page_header", "page_footer") for u in units for s in u.spans),
        "coverage": sum(s.span_id in unit_of for s in content) / len(content) if content else None,
        "cap_ok": all(len(u.text) <= max_chars or len(u.spans) == 1 for u in units),
    }


def _reference_units(texts) -> int:
    from rag_wright.api import TaggedSpan
    from rag_wright.spans.segment import segment_clause
    from rag_wright.subgraphs.contract_ingestion_pipeline import provision_units

    ops = [op for k, c in enumerate(texts) for op in segment_clause(f"eval:{k}:h", c) if op.text.strip()]
    return len(asyncio.run(provision_units([TaggedSpan(span=op) for op in ops])))


def main() -> None:
    from rag_wright.ingestion import DEFAULT_MAX_UNIT_CHARS

    client_dir = os.environ.get("RAG_EVAL_TEXTILE_DIR")
    client = sorted(p for p in Path(client_dir).iterdir() if p.suffix.lower() in (".pdf", ".xlsx", ".xlsm")) \
        if client_dir else []
    sources = [("synthetic", p) for p in sorted(S.FIXTURES.glob("*.md"))] + [("client", p) for p in client] \
        + [("public", p) for p in S._fetch_public()] + [("contract", p) for p in S.CONTRACTS if p.exists()]
    log(f"[unit-eval] start N={len(sources)} (client={len(client)}) max_chars={DEFAULT_MAX_UNIT_CHARS}")
    rows = []
    for i, (kind, path) in enumerate(sources, 1):
        texts, layouts = S._chunked(path)
        row = {"source": kind, "doc": path.name, **_score(texts, layouts, DEFAULT_MAX_UNIT_CHARS)}
        if kind == "contract":
            row["reference_provision_units"] = _reference_units(texts)
        rows.append(row)
        log(f"[unit-eval] {i}/{len(sources)} {kind:9} {path.name[:44]:44} units={row['units']:4} "
            f"heads={row['headings_start_units']} tables={row['tables']}/{row['tables_whole']} "
            f"furniture={row['furniture_in_units']} cov={row['coverage']} cap={row['cap_ok']}"
            + (f" ref_units={row['reference_provision_units']}" if kind == "contract" else ""))

    real = [r for r in rows if r["source"] in ("client", "public")]

    def all_one(key: str) -> bool:
        return all(r[key] in (None, 1.0) for r in real)

    checks = {
        "headings_start_units": all_one("headings_start_units"),
        "tables_whole": all_one("tables_whole"),
        "no_furniture_in_units": all(r["furniture_in_units"] == 0 for r in real),
        "full_coverage": all_one("coverage"),
        "cap_respected": all(r["cap_ok"] for r in rows),
        "client_docs_present": bool(client),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps({"rows": rows, "gate": checks}, indent=2))
    log(f"[unit-eval] gate {checks} -> {'PASS' if all(checks.values()) else 'FAIL'}")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
