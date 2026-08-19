"""CHUNK-5 (ADR-0058, issue 0004): the clause-integrity A/B that GATES the default-discoverer switch.

Compares chunk integrity of the structure-first discoverers against the old whole-document single-call, on a
sample of REAL docling-parsed contracts (CUAD PDFs -- plain .txt has no heading labels, so structural needs
PDF/DOCX/markdown). We do NOT trade correctness for latency blind: the gate reports losslessness, cap
compliance, chunk-size distribution, heading alignment, boundary agreement, latency, and timeouts, so a human
can confirm structural is equal-or-better before it becomes the default.

Modes (per `--mode`):
  structural  -- StructuralBoundaryDiscoverer (Tier-1, MODEL-FREE): the cheap headline run.
  composite   -- StructuralModelFallbackDiscoverer (Tier-1 + per-section tag-parse fallback): needs a model.
  single      -- SingleCallBoundaryDiscoverer (the OLD whole-doc call): needs a model; may hit the 180s deadline.
  ab          -- run `structural` (or `composite`) AND `single` on the SAME docs and report the delta.

Streams `[ab] i/N <doc>` progress (long-running: monitor it). Usage:
  uv run python scripts/eval_chunking_ab.py --mode structural --limit 12
  uv run python scripts/eval_chunking_ab.py --mode ab --refined --limit 6   # composite vs single (model)
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path
from time import perf_counter


# --- pure metric helpers (hermetically tested; no IO/model) ---------------------------------------------------

def cut_starts(spans) -> frozenset:
    """The set of chunk-START item indices a partition implies -- the comparable 'where are the cuts' signal."""
    return frozenset(s.start_index for s in spans)


def boundary_agreement(a, b) -> float:
    """Jaccard of two partitions' cut-start sets in [0,1]: 1.0 = identical boundaries, 0.0 = disjoint. Two empty
    partitions agree (1.0)."""
    sa, sb = cut_starts(a), cut_starts(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def heading_alignment(document, spans) -> float:
    """Fraction of chunks whose FIRST item is a docling heading -- structural chunks should score high (they cut
    at headings); a fixed-size splitter scores near 0. Requires the doc's items to carry `.label`."""
    from rag_wright.corpus.document_parser import _HEADING_LABELS
    if not spans:
        return 0.0
    aligned = sum(1 for s in spans
                  if getattr(document.texts[s.start_index], "label", None) in _HEADING_LABELS)
    return aligned / len(spans)


def size_stats(texts: list[str]) -> dict:
    """Chunk-count + char-length distribution of the FINALIZED chunk texts."""
    lengths = sorted(len(t) for t in texts)
    if not lengths:
        return {"n_chunks": 0, "p50": 0, "p95": 0, "max": 0}
    p95 = lengths[min(len(lengths) - 1, int(0.95 * len(lengths)))]
    return {"n_chunks": len(lengths), "p50": int(statistics.median(lengths)), "p95": p95, "max": lengths[-1]}


# --- the A/B run (IO + optional model) ------------------------------------------------------------------------

def _log(msg: str) -> None:
    print(msg, flush=True)


def _discoverer(mode: str, *, refined: bool, model_id):
    from rag_wright.capabilities.rlm_chunking import (
        SingleCallBoundaryDiscoverer,
        StructuralBoundaryDiscoverer,
        StructuralModelFallbackDiscoverer,
    )
    if mode == "single":
        return SingleCallBoundaryDiscoverer(model_id)
    if refined:
        return StructuralModelFallbackDiscoverer(model_id)
    return StructuralBoundaryDiscoverer()  # model-free Tier-1


def _measure(discoverer, parsed, token_cap: int) -> dict:
    from rag_wright.capabilities.parsing import load_document
    from rag_wright.capabilities.rlm_chunking import _finalize_chunks, _validate_partition
    from rag_wright.models.seam import ModelCallTimeout

    document = load_document(parsed)
    t0 = perf_counter()
    try:
        spans = discoverer.discover(document)
        timed_out = False
    except ModelCallTimeout:
        return {"timed_out": True, "latency_s": round(perf_counter() - t0, 1)}
    latency = perf_counter() - t0
    _validate_partition(spans, len(document.texts))  # HARD: lossless coverage or it raises
    texts = _finalize_chunks(document, spans, token_cap)
    return {
        "timed_out": timed_out, "latency_s": round(latency, 2), "spans": spans,
        "heading_alignment": round(heading_alignment(document, spans), 3),
        **size_stats(texts),
    }


def _sample(corpus: Path, limit: int) -> list[Path]:
    pdfs = sorted(p for p in corpus.rglob("*") if p.suffix.lower() == ".pdf")
    return pdfs[:limit] if limit else pdfs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["structural", "composite", "single", "ab"], default="structural")
    ap.add_argument("--corpus", default="data/cuad/subset/pdf")
    ap.add_argument("--cache-dir", default="data/cache/chunk_ab")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--token-cap", type=int, default=20_000)
    ap.add_argument("--refined", action="store_true", help="use the composite (structural+fallback) for the A side")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv()
    from rag_wright.capabilities.parsing import DoclingParser, parse
    from rag_wright.models.profiles import ModelRole, model_for

    model_id = model_for(ModelRole.GENERAL)
    cache_dir = Path(args.cache_dir)
    docs = _sample(Path(args.corpus), args.limit)
    if not docs:
        _log(f"[ab] no PDFs under {args.corpus}")
        sys.exit(1)
    n = len(docs)
    a_label = "composite" if args.refined else "structural"
    _log(f"[ab] {n} docs | mode={args.mode} | A={a_label} model={model_id} cap={args.token_cap}")

    a_rows, b_rows, agrees = [], [], []
    for i, pdf in enumerate(docs, 1):
        parsed = parse(pdf, cache_dir=cache_dir, parser=DoclingParser())  # cached parse
        if args.mode in ("structural", "composite", "ab"):
            a = _measure(_discoverer("composite" if args.refined else "structural",
                                     refined=args.refined, model_id=model_id), parsed, args.token_cap)
            a_rows.append(a)
        if args.mode in ("single", "ab"):
            b = _measure(_discoverer("single", refined=False, model_id=model_id), parsed, args.token_cap)
            b_rows.append(b)
        if args.mode == "ab" and not a.get("timed_out") and not b.get("timed_out"):
            agrees.append(boundary_agreement(a["spans"], b["spans"]))
        tail = ""
        if a_rows and not a_rows[-1].get("timed_out"):
            tail += f" A:{a_rows[-1]['n_chunks']}ch {a_rows[-1]['latency_s']}s ha={a_rows[-1]['heading_alignment']}"
        if b_rows:
            tail += " B:TIMEOUT" if b_rows[-1].get("timed_out") else f" B:{b_rows[-1]['n_chunks']}ch {b_rows[-1]['latency_s']}s"
        _log(f"[ab] {i}/{n} {pdf.name[:44]:44}{tail}")

    def _summary(label, rows):
        ok = [r for r in rows if not r.get("timed_out")]
        if not ok:
            _log(f"[ab] {label}: all {len(rows)} timed out")
            return
        _log(f"[ab] {label}: {len(ok)}/{len(rows)} ok, {len(rows) - len(ok)} timed out | "
             f"median latency {statistics.median(r['latency_s'] for r in ok):.2f}s | "
             f"median chunks {int(statistics.median(r['n_chunks'] for r in ok))} | "
             f"median heading-align {statistics.median(r['heading_alignment'] for r in ok):.2f} | "
             f"lossless {len(ok)}/{len(ok)} (validated)")

    _log("\n[ab] === SUMMARY ===")
    if a_rows:
        _summary(f"A ({a_label})", a_rows)
    if b_rows:
        _summary("B (single-call)", b_rows)
    if agrees:
        _log(f"[ab] boundary agreement A vs B (Jaccard of cut points): median {statistics.median(agrees):.2f}")


if __name__ == "__main__":
    main()
