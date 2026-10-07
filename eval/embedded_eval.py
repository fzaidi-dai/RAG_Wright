"""ING-6 (ADR-0124): eval for embedded-file extraction + record linking on real workbooks (LOCAL ONLY: client data).

For every Office package in `$RAG_EVAL_TEXTILE_DIR` (xlsx/xlsm/docx/pptx), parse it through the engine's public
`parse_document` and check its embedded children and their record links.

GATE: every embedded object is extracted or reported; every child is anchored; every table row holding children has
at least one VERIFIED (EXTRACTED) link; every cell holding a single child has it verified. DIAGNOSTIC: children
placed by content (INFERRED), with several candidate records (AMBIGUOUS), position-only, and unplaced (no record
anywhere in the workbook: still child documents); anchors outside any table.

Run: `RAG_EVAL_TEXTILE_DIR=/path uv run python -u eval/embedded_eval.py`; writes `data/eval/embedded/results.json`.
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "eval" / "embedded"


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> None:
    from rag_wright.api import parse_document
    from rag_wright.corpus.embedded import extract_embedded

    src = os.environ.get("RAG_EVAL_TEXTILE_DIR")
    if not src:
        raise SystemExit("set RAG_EVAL_TEXTILE_DIR")
    files = sorted(p for p in Path(src).iterdir() if p.suffix.lower() in (".xlsx", ".xlsm", ".docx", ".pptx"))
    log(f"[emb-eval] start N={len(files)}")
    rows = []
    totals = collections.Counter()
    for i, path in enumerate(files, 1):
        doc_id = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem)[:60]
        sd = parse_document(doc_id, path, cache_dir=OUT / "parse_cache")
        ext = extract_embedded(path.name, path.read_bytes())
        totals["found"] += ext.found
        totals["children"] += len(sd.embedded)
        totals["duplicates"] += ext.duplicates
        totals["skipped"] += len(sd.embedded_skipped)
        cell_kids = collections.defaultdict(list)
        for c in sd.embedded:
            totals["unanchored"] += not c.anchors
            for a in c.anchors:
                if a.table_ref is None:
                    totals["anchors_outside_tables"] += a.sheet is not None
                else:
                    cell_kids[(a.sheet, a.cell, a.table_ref, a.table_row)].append(c)
            kinds = {(lk.confidence.value, lk.basis) for lk in c.links}
            kind = ("verified" if ("EXTRACTED", "anchor") in kinds else "placed_by_content" if ("INFERRED", "content")
                    in kinds else "ambiguous" if ("AMBIGUOUS", "content") in kinds else "position_only" if kinds
                    else "unplaced")
            totals[kind] += 1
        for (_sheet, _cell, ref, row), kids in cell_kids.items():
            verified = [k for k in kids if any(lk.table_ref == ref and lk.table_row == row
                                               and lk.confidence.value == "EXTRACTED" for lk in k.links)]
            totals["rows_with_children"] += 1
            totals["rows_verified"] += bool(verified)
            if len(kids) == 1:
                totals["single_child_cells"] += 1
                totals["single_child_verified"] += bool(verified)
            else:
                totals["stacked_cells"] += 1
        rows.append({"doc": path.name, "found": ext.found, "children": len(sd.embedded), "skipped": sd.embedded_skipped})
        log(f"[emb-eval] {i}/{len(files)} {path.name[:50]:50} found={ext.found} children={len(sd.embedded)} "
            f"dup={ext.duplicates} skipped={len(sd.embedded_skipped)}")
    checks = {
        "all_objects_accounted": totals["found"] == totals["children"] + totals["duplicates"] + totals["skipped"],
        "no_unanchored_children": totals["unanchored"] == 0,
        "every_row_with_children_verified": totals["rows_verified"] == totals["rows_with_children"],
        "every_single_child_cell_verified": totals["single_child_verified"] == totals["single_child_cells"],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps({"rows": rows, "totals": totals,
                                                  "gate": checks}, indent=2))
    log(f"[emb-eval] totals {dict(totals)}")
    log(f"[emb-eval] gate {checks} -> {'PASS' if all(checks.values()) else 'FAIL'}")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
