"""CU-B4 (ADR-0029): CUAD ingestion driver -- parse -> single-call chunk -> segment -> classify -> embed ->
store, over the SEED=0 20% holdout. Two phases, per the standing split of slow-network from fast-local work:

  Phase 1 (CONCURRENT, async + semaphore): build a DoclingDocument from each contract's text, then
     `chunk()` it with `SingleCallBoundaryDiscoverer` -- ONE LLM call per contract, repaired to a valid
     partition. This is the only network-bound step, so it is parallelized (no interpreter -> no process
     lock -> ordinary async, no process pool; ADR-0029 / the chunk-model comparison).
  Phase 2 (SEQUENTIAL): per contract -> segment each chunk into operative spans -> LegalBERT function ->
     BGE-M3 dense+sparse -> document-absolute span offsets (`to_span_record`) -> upsert Contract + Spans.
     Local + store writes stay sequential (the classifier/embedder/HTTP store are shared, not thread-safe).

Every stored span is checked against the citation invariant on the spot:
  `canonical_document_text[doc_start:doc_end] == span.text`.

Env knobs: LIMIT (int, 0=all holdout), DB, RESET (1/0), CONCURRENCY, MINSZ, MAXSZ. Content-hash gated
(re-run is idempotent); progress flushed; crash-safe per phase.

Usage:  uv run --no-sync python -m scripts.ingest_cuad          # smoke: LIMIT=6
        LIMIT=0 uv run --no-sync python -m scripts.ingest_cuad  # full holdout
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import random
import time
from pathlib import Path

from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.labels import DocItemLabel
from dotenv import load_dotenv

from rag_wright.capabilities.embedding import BGEM3Embedder
from rag_wright.capabilities.parsing import ParsedDocument
from rag_wright.capabilities.rlm_chunking import (
    SingleCallBoundaryDiscoverer,
    canonical_document_text,
    chunk,
)
from rag_wright.contracts.contract_meta import ContractRecord
from rag_wright.contracts.function import canonical_function
from rag_wright.spans.cuad_labels import parse_cuad
from rag_wright.spans.function_families import RARE_TARGETS
from rag_wright.spans.hybrid_classifier import HybridFunctionClassifier
from rag_wright.spans.legalbert_classifier import LegalBertFunctionClassifier
from rag_wright.spans.segment import segment_clause, to_span_record

CUAD = Path("data/cuad/extracted/CUAD_v1/CUAD_v1.json")
MODEL_PATH = Path("data/models/legalbert_function")
CACHE = Path("data/cache/cuad")
PARSE_DIR, CHUNK_DIR, CANON_DIR = CACHE / "parse", CACHE / "chunks", CACHE / "canonical"

LIMIT = int(os.environ.get("LIMIT", "6"))  # 0 = all holdout; default = smoke
DB = os.environ.get("DB", "ragwright_cuad")
RESET = os.environ.get("RESET", "1") == "1"
CONCURRENCY = int(os.environ.get("CONCURRENCY", "6"))
MINSZ, MAXSZ = int(os.environ.get("MINSZ", "0")), int(os.environ.get("MAXSZ", "0"))

_SAFE = __import__("re").compile(r"[^A-Za-z0-9._-]+")


def _progress(m: str) -> None:
    print(m, flush=True)


def _slug(contract_id: str) -> str:
    return _SAFE.sub("_", contract_id).strip("_") or "contract"


def _device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class _NoSummary:
    """CUAD highlights on span vectors, not chunk summaries -> a no-op summarizer (no LLM cost)."""

    def summarize(self, text: str) -> str:  # noqa: ARG002
        return ""


def _holdout() -> list:
    contracts = list(parse_cuad(CUAD))
    random.Random(0).shuffle(contracts)  # SEED=0: the leak-free 20% the classifier never trained on
    held = contracts[: max(1, len(contracts) // 5)]
    if MINSZ or MAXSZ:
        lo, hi = MINSZ or 0, MAXSZ or 10**9
        held = [c for c in held if lo <= len(c.context) <= hi]
    return held[:LIMIT] if LIMIT else held


def _build_parsed(contract_id: str, context: str) -> ParsedDocument:
    """Build a DoclingDocument from the contract text (one TextItem per non-blank line) and cache it, so the
    standard `chunk()` path (which loads a real DoclingDocument) works from CUAD's extracted text."""
    content_hash = hashlib.sha256(context.encode("utf-8")).hexdigest()
    manifest_path = PARSE_DIR / f"{contract_id}.{content_hash[:16]}.json"
    if not manifest_path.exists():
        doc = DoclingDocument(name=contract_id)
        for line in context.split("\n"):
            if line.strip():
                doc.add_text(label=DocItemLabel.TEXT, text=line)
        doc.save_as_json(manifest_path)
    return ParsedDocument(source_doc_id=contract_id, content_hash=content_hash, manifest_path=str(manifest_path))


def _chunk_one(c, discoverer, summarizer) -> dict:
    """Phase-1 unit (runs in a worker thread): build doc, single-call chunk, persist canonical text."""
    t0 = time.perf_counter()
    sid = _slug(c.contract_id)
    parsed = _build_parsed(sid, c.context)
    manifest = chunk(parsed, summarizer=summarizer, cache_dir=CHUNK_DIR, discoverer=discoverer)
    canonical = canonical_document_text(list(manifest.chunks))
    (CANON_DIR / f"{sid}.txt").write_text(canonical, encoding="utf-8")
    return {"c": c, "sid": sid, "parsed": parsed, "manifest": manifest,
            "canonical": canonical, "secs": round(time.perf_counter() - t0, 1)}


async def _phase1(contracts, discoverer, summarizer) -> list[dict]:
    sem = asyncio.Semaphore(CONCURRENCY)
    done = 0

    async def one(c):
        nonlocal done
        async with sem:
            try:
                r = await asyncio.to_thread(_chunk_one, c, discoverer, summarizer)
            except Exception as e:  # noqa: BLE001 -- one bad contract must not crash the holdout run
                done += 1
                _progress(f"[chunk] {done}/{len(contracts)} {_slug(c.contract_id)[:44]} "
                          f"FAILED {type(e).__name__}: {str(e)[:80]}")
                return None
        done += 1
        _progress(f"[chunk] {done}/{len(contracts)} {r['sid'][:44]} "
                  f"chunks={len(r['manifest'].chunks)} {r['secs']}s")
        return r

    return await asyncio.gather(*[one(c) for c in contracts])


def main() -> None:
    load_dotenv()
    for d in (PARSE_DIR, CHUNK_DIR, CANON_DIR):
        d.mkdir(parents=True, exist_ok=True)
    contracts = _holdout()
    _progress(f"[setup] holdout={len(contracts)} db={DB} reset={RESET} concurrency={CONCURRENCY} "
              f"sizes={[len(c.context) for c in contracts][:8]}{'...' if len(contracts) > 8 else ''}")

    # --- Phase 1: concurrent single-call chunking (the only network-bound step) --------------------
    t0 = time.perf_counter()
    results = asyncio.run(_phase1(contracts, SingleCallBoundaryDiscoverer(), _NoSummary()))
    _progress(f"[phase1] chunked {len(results)} contracts in {time.perf_counter() - t0:.1f}s "
              f"(concurrency={CONCURRENCY})")

    # --- Phase 2: sequential segment -> classify -> embed -> store (+ offset round-trip) -----------
    from rag_wright.store.arcadedb import ArcadeDBStore

    embedder = BGEM3Embedder()
    # TARGETED LLM hybrid: LegalBERT everywhere, Gemma fallback only when a rare-target class is in the
    # top-2 (a fraction of a percent of spans) -- rescues the rare clause types, zero risk to the rest.
    clf = HybridFunctionClassifier(
        LegalBertFunctionClassifier.load(MODEL_PATH, device=_device()), targets=RARE_TARGETS)
    store = ArcadeDBStore.from_env(database=DB, reset=RESET)
    store.ensure_schema()
    _progress(f"[phase2] device={_device()} store={DB}")

    ok_results = [r for r in results if r is not None]
    n_skipped = len(results) - len(ok_results)
    t1 = time.perf_counter()
    n_spans = n_contracts = n_bad = 0
    for r in ok_results:
        c, sid, canonical = r["c"], r["sid"], r["canonical"]
        store.upsert_contract(ContractRecord(
            contract_id=sid, name=c.contract_id, source_doc_id=sid, content_hash=r["parsed"].content_hash))
        n_contracts += 1
        for ch in r["manifest"].chunks:
            ops = [s for s in segment_clause(ch.chunk_id, ch.text) if s.text.strip()]
            if not ops:
                continue
            span_texts = [s.text.strip() for s in ops]
            fns = clf.classify([ch.text] + span_texts, batch_size=32)  # index 0 = clause; 1: = spans
            dense_vecs, sparse_vecs = embedder.encode_batch(span_texts)
            for op, fn, dense, sparse in zip(ops, fns[1:], dense_vecs, sparse_vecs):
                fn = canonical_function(fn) or fn  # normalize classifier casing at the boundary (e.g. Ip->IP)
                rec = to_span_record(op, contract_id=sid, chunk_doc_start=ch.doc_start,
                                     dense_vector=dense, sparse_vector=sparse, function=fn)
                if canonical[rec.doc_start:rec.doc_end] != op.text:  # the citation invariant, checked live
                    n_bad += 1
                store.upsert_span(rec)
                n_spans += 1
        if n_contracts % 10 == 0 or n_contracts == len(ok_results):
            _progress(f"[store] {n_contracts}/{len(ok_results)} {sid[:40]} "
                      f"spans_total={n_spans} bad_offsets={n_bad}")

    _progress(f"[done] contracts={n_contracts} skipped={n_skipped} spans={n_spans} bad_offsets={n_bad} "
              f"phase2={time.perf_counter() - t1:.1f}s total={time.perf_counter() - t0:.1f}s")
    if n_bad:
        raise SystemExit(f"OFFSET ROUND-TRIP FAILED for {n_bad} spans -- citation invariant broken")


if __name__ == "__main__":
    main()
