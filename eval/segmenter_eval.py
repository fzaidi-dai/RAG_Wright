"""ING-2 (ADR-0124): eval for the engine's default segmenter vs the reference pack's legal `segment_clause`.

Sources (each parsed through the engine's public `parse_document`, content-hash cached; chunked with the
deterministic structural discoverer so the run is model-free and reproducible):
  - synthetic  : the committed textile fixtures, scored against the hand gold (exact spans);
  - client     : real textile forms from `$RAG_EVAL_TEXTILE_DIR` (PDF + XLSX/XLSM) -- LOCAL ONLY, never committed;
  - public     : a public textile test report, downloaded at run time (pinned sha256), never committed;
  - contract   : a few contract PDFs (diagnostic only; the reference pack keeps `segment_clause` for contracts).

Structural truth for real documents comes from the parse's own layout (table rows, headings, item edges), so these
metrics measure how faithfully a segmenter follows the document's structure, not parse quality.

GATE (generic segmenter, non-legal real documents pooled): every chunk tiles; table-row integrity >= 0.98;
layout respect >= 0.98; zero bare-heading spans (a chunk that is ONLY a heading is the chunker's
output, reported as `heading_only_chunks`, not counted); synthetic exact-span match = 1.0. Everything else is DIAGNOSTIC.

Run: `RAG_EVAL_TEXTILE_DIR=/path/to/forms uv run python -u eval/segmenter_eval.py`
Writes `data/eval/segmenter/results.json` (gitignored).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "eval" / "segmenter"
FIXTURES = ROOT / "tests" / "fixtures" / "ingestion"
PUBLIC = [("assevero_test_report.pdf",
           "https://openlab.citytech.cuny.edu/tassevero-eportfolio/files/2023/05/Technical-Report-Assevero.pdf",
           "1d125be3bda6ac5eeb312c80a2cadcf03f94ec46848c8d854041d6be82ab8ce1")]
CONTRACTS = [ROOT / "tests" / "fixtures" / "table-bearing-contract.pdf"] + sorted(
    (ROOT / "data" / "cuad" / "subset" / "pdf").glob("*.pdf"))[:4]
GATE = {"table_row_integrity": 0.98, "layout_respect": 0.98}


def log(msg: str) -> None:
    print(msg, flush=True)


def _norm(s: str) -> str:
    return re.sub(r"-{3,}", "---", " ".join(s.split()))


def _fetch_public() -> list[Path]:
    dest = OUT / "public"
    dest.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, url, sha in PUBLIC:
        p = dest / name
        if not p.exists():
            urllib.request.urlretrieve(url, p)
        got = hashlib.sha256(p.read_bytes()).hexdigest()
        if got != sha:
            raise SystemExit(f"{name}: sha256 {got} != pinned {sha} (the public source changed)")
        paths.append(p)
    return paths


def _doc_id(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem)[:80] + "_" + path.suffix.lstrip(".")


def _chunked(path: Path):
    from rag_wright.api import parse_document
    from rag_wright.capabilities.parsing import load_document
    from rag_wright.capabilities.rlm_chunking import StructuralBoundaryDiscoverer, chunk_texts
    from rag_wright.ingestion import chunk_layouts

    src = parse_document(_doc_id(path), path, cache_dir=OUT / "parse_cache")
    doc = load_document(src.parsed)
    texts = chunk_texts(doc, discoverer=StructuralBoundaryDiscoverer())
    return texts, chunk_layouts(doc, texts)


def _segmenters():
    from rag_wright.ingestion import segment_layout
    from rag_wright.packs.contracts.spans.segment import segment_clause

    return {"generic": segment_layout, "segment_clause": lambda cid, text, layout: segment_clause(cid, text)}


def _table_rows(text: str, item) -> list[tuple[int, int]]:
    """A table item's rows as (start, end) chunk offsets, excluding separator rows."""
    rows = []
    for m in re.finditer(r"[^\n]+", text[item.start:item.end]):
        if not re.fullmatch(r"[ \t]*\|?[ \t:|-]*-[ \t:|-]*\|?[ \t]*", m.group()):
            rows.append((item.start + m.start(), item.start + m.end()))
    return rows


def _alnum(s: str) -> int:
    return sum(c.isalnum() for c in s)


def _score(texts, layouts, segment) -> dict:
    from rag_wright.api import IngestionContractError, check_tiling

    tiles, rows_ok, rows_all, respect, n_spans, bare, heading_only = True, 0, 0, 0, 0, 0, 0
    lengths = []
    for ci, (text, layout) in enumerate(zip(texts, layouts)):
        cid = f"eval:{ci}:h"
        spans = segment(cid, text, layout)
        try:
            check_tiling(cid, text, spans)
        except IngestionContractError:
            tiles = False
        n_spans += len(spans)
        lengths += [len(s.text.strip()) for s in spans]
        content = [it for it in layout if it.kind not in ("heading", "title") and _alnum(it.text) >= 2]
        if not content and text.strip():
            heading_only += 1  # the CHUNKER produced a chunk with nothing but a heading (not a segmenter choice)
        for s in spans:
            touched = [it for it in content if it.start < s.end and it.end > s.start]
            # a table's rows are separate spans: inside a table, a span must stay within ONE row
            respect += len(touched) <= 1
            heads = [it for it in layout if it.kind in ("heading", "title")]
            if content and s.text.strip() and not touched and any(h.start < s.end and h.end > s.start for h in heads):
                bare += 1
        for it in layout:
            if it.kind != "table":
                continue
            rows = _table_rows(text, it)
            for k, (rs, re_) in enumerate(rows):
                rows_all += 1
                holders = [s for s in spans if s.start < re_ and s.end > rs]
                if len(holders) == 1:
                    h = holders[0]
                    others = [r for j, r in enumerate(rows) if j != k and h.start < r[1] and h.end > r[0]]
                    rows_ok += not others
    return {"tiles": tiles, "spans": n_spans, "median_chars": median(lengths) if lengths else 0,
            "table_rows": rows_all, "table_row_integrity": rows_ok / rows_all if rows_all else None,
            "layout_respect": respect / n_spans if n_spans else None, "bare_heading_spans": bare,
            "heading_only_chunks": heading_only}


def _cuts(texts, layouts, segment) -> set:
    out = set()
    for ci, (text, layout) in enumerate(zip(texts, layouts)):
        out |= {(ci, s.start) for s in segment(f"eval:{ci}:h", text, layout)[1:]}
    return out


def main() -> None:
    segs = _segmenters()
    client_dir = os.environ.get("RAG_EVAL_TEXTILE_DIR")
    client = sorted(p for p in Path(client_dir).iterdir() if p.suffix.lower() in (".pdf", ".xlsx", ".xlsm")) \
        if client_dir else []
    sources = [("synthetic", p) for p in sorted(FIXTURES.glob("*.md"))] + [("client", p) for p in client] \
        + [("public", p) for p in _fetch_public()] + [("contract", p) for p in CONTRACTS if p.exists()]
    log(f"[seg-eval] start N={len(sources)} (client={len(client)}{'' if client_dir else ' -- RAG_EVAL_TEXTILE_DIR unset'})")
    gold = json.loads((FIXTURES / "segmenter_gold.json").read_text())
    rows = []
    for i, (kind, path) in enumerate(sources, 1):
        texts, layouts = _chunked(path)
        row = {"source": kind, "doc": path.name, "chunks": len(texts)}
        for name, seg in segs.items():
            row[name] = _score(texts, layouts, seg)
        if kind == "synthetic":
            got = [_norm(s.text) for ci, (t, lay) in enumerate(zip(texts, layouts))
                   for s in segs["generic"](f"eval:{ci}:h", t, lay)]
            row["gold_exact"] = got == gold[path.name]
        if kind == "contract":
            g, c = _cuts(texts, layouts, segs["generic"]), _cuts(texts, layouts, segs["segment_clause"])
            row["cut_agreement"] = {"clause_cuts_kept_by_generic": len(g & c) / len(c) if c else None,
                                    "generic_cuts_shared_with_clause": len(g & c) / len(g) if g else None}
        rows.append(row)
        gm = row["generic"]
        log(f"[seg-eval] {i}/{len(sources)} {kind:9} {path.name[:48]:48} spans={gm['spans']:4} "
            f"rows={gm['table_row_integrity']} respect={gm['layout_respect']} bare={gm['bare_heading_spans']} "
            f"tiles={gm['tiles']}")

    def pooled(name: str, kinds: tuple) -> dict:
        sel = [r[name] for r in rows if r["source"] in kinds]
        rows_all = sum(s["table_rows"] for s in sel)
        spans = sum(s["spans"] for s in sel)
        return {
            "docs": len(sel), "tiles": all(s["tiles"] for s in sel), "spans": spans,
            "table_row_integrity": sum((s["table_row_integrity"] or 0) * s["table_rows"] for s in sel) / rows_all
            if rows_all else None,
            "layout_respect": sum((s["layout_respect"] or 0) * s["spans"] for s in sel) / spans if spans else None,
            "bare_heading_spans": sum(s["bare_heading_spans"] for s in sel),
            "heading_only_chunks": sum(s["heading_only_chunks"] for s in sel),
        }

    real = ("client", "public")
    summary = {name: {"non_legal_real": pooled(name, real), "contract": pooled(name, ("contract",))}
               for name in segs}
    g = summary["generic"]["non_legal_real"]
    checks = {
        "tiles_everywhere": all(r[n]["tiles"] for r in rows for n in segs),
        "synthetic_gold_exact": all(r.get("gold_exact", True) for r in rows),
        "table_row_integrity": (g["table_row_integrity"] or 0) >= GATE["table_row_integrity"],
        "layout_respect": (g["layout_respect"] or 0) >= GATE["layout_respect"],
        "no_bare_headings": g["bare_heading_spans"] == 0,
        "client_docs_present": bool(client),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps({"rows": rows, "summary": summary, "gate": checks}, indent=2))
    log(f"[seg-eval] summary {json.dumps(summary, indent=1)}")
    log(f"[seg-eval] gate {checks} -> {'PASS' if all(checks.values()) else 'FAIL'}")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
