"""T58a (FR-Q, ADR-0025): populate the pivot store from the ACORD corpus.

Per clause: segment into operative spans (T55) -> classify each span's function (T56/T60 LegalBERT) ->
embed dense+sparse (BGE-M3) -> upsert_span (the function-filtered hybrid index). Then, per non-NONE
clause, extract properties (T57b, concurrent via map_concurrent) over the clause text and
write_property_graph (T57c). CLAUSE-LEVEL property records (the ACORD corpus items are already focused
clauses); the clause function is the classifier's read of the full clause text. `parent_okf_path` keeps
the original ACORD id so retrieval/eval can map a span back to its qrels clause.

  LIMIT=5 uv run python -m scripts.populate_property_store               # dry-run: prove the chain (own DB)
  PHASE=spans uv run python -m scripts.populate_property_store           # phase 1 only (span index + cache)
  PHASE=extract uv run python -m scripts.populate_property_store         # phase 2, RESUME (skip done clauses)
  FRESH=1 PHASE=extract uv run python -m scripts.populate_property_store # phase 2 from the top (keep spans)
  EXTRACT_MODEL=deepseek/deepseek-v4-flash PHASE=extract uv run ...      # switch the extraction LLM; RESUME
  uv run python -m scripts.populate_property_store                       # full (phase 1 + 2)

Phase-2 controls: (1) X/N progress echoed to stdout AND `populate_progress.log` (flushed, never buffered);
(2) DeepSeek routes by OpenRouter throughput (profile, ADR-0027); (3) each clause is written to ArcadeDB the
moment it is extracted (crash-safe); (4) RESUME by default / FRESH=1 to restart / PHASE=all to redo phase 1
too -- full control; (5) EXTRACT_MODEL switches the LLM at any run, RESUME finishes the rest with it.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

import torch
from dotenv import load_dotenv

from eval.acord import load_corpus
from rag_wright.capabilities.embedding import BGEM3Embedder
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.span import SpanRecord
from rag_wright.spans.function_classifier import NONE_LABEL
from rag_wright.spans.legalbert_classifier import LegalBertFunctionClassifier
from rag_wright.spans.property_extractor import SeamPropertyExtractor
from rag_wright.spans.segment import segment_clause
from rag_wright.util.concurrent import map_concurrent

MODEL_PATH = Path("data/models/legalbert_function")
PROGRESS = Path("data/models/populate_progress.log")
EXTRACT_CACHE = Path("data/models/populate_to_extract.jsonl")  # phase-1 -> phase-2 handoff (non-NONE clauses)
LIMIT = int(os.environ.get("LIMIT", "0"))
# PHASE=spans stops after phase 1 (spans + function tags) -- enough to measure the function-gate ceiling
# WITHOUT the ~3.9k-call property extraction; PHASE=all (default) also runs phase 2.
PHASE = os.environ.get("PHASE", "all")
DB = os.environ.get("PIVOT_DB", "ragwright_pivot_dryrun" if LIMIT else "ragwright_acord_pivot")
CONCURRENCY = int(os.environ.get("CONCURRENCY", "8"))
# FRESH=1 (PHASE=extract): clear the property graph (KEEP spans) and re-extract every clause from the top.
# Default (FRESH=0): RESUME -- skip clauses already written, so we continue exactly where we left off.
FRESH = os.environ.get("FRESH", "0") == "1"
# EXTRACT_MODEL: override the extraction model id at will (switch/pause/resume with a different LLM). Empty
# => the STRUCTURED_REASONING default (DeepSeek V4 Pro). Combined with RESUME, a re-run finishes the REST
# with whatever model is set now -- so different clauses can be done by different models across runs.
EXTRACT_MODEL = os.environ.get("EXTRACT_MODEL", "")
_SLUG = re.compile(r"[^A-Za-z0-9._-]+")


def _device() -> str:
    return "mps" if torch.backends.mps.is_available() else "cpu"


def _chunk_id_from_value(value: str) -> ChunkId:
    """Reconstruct a ChunkId from its canonical `source:index:hash` string (ADR-0025 rsplit parse)."""
    source, index, content_hash = value.rsplit(":", 2)
    return ChunkId(source_doc_id=source, chunk_index=int(index), content_hash=content_hash)


def _progress(msg: str) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(msg + "\n", encoding="utf-8")
    print(msg, flush=True)


def main() -> None:
    load_dotenv()
    clauses = load_corpus()
    if LIMIT:
        clauses = clauses[:LIMIT]
    from rag_wright.store.arcadedb import ArcadeDBStore

    # PHASE=extract resumes phase 2 on the EXISTING spans DB (no reset, no re-embed) from the cache.
    store = ArcadeDBStore.from_env(database=DB, reset=(PHASE != "extract"))
    store.ensure_schema()

    to_extract: list[tuple[ChunkId, str, str]] = []  # (clause chunk_id, clause function, clause text)
    if PHASE == "extract":
        rows = [json.loads(line) for line in EXTRACT_CACHE.read_text(encoding="utf-8").splitlines() if line.strip()]
        to_extract = [(_chunk_id_from_value(r["clause_id"]), r["function"], r["text"]) for r in rows]
        _progress(f"[extract] db={DB} loaded {len(to_extract)} non-NONE clauses from cache (spans kept)")
        n_spans = -1  # already populated in a prior PHASE=spans run
    else:
        # --- Phase 1: spans (local: segment -> classify -> embed -> upsert) ----------------------
        embedder = BGEM3Embedder()
        clf = LegalBertFunctionClassifier.load(MODEL_PATH, device=_device())
        _progress(f"[setup] db={DB} clauses={len(clauses)} device={_device()}")
        t0 = time.perf_counter()
        n_spans = 0
        for i, clause in enumerate(clauses, 1):
            cid = ChunkId.of("acord-" + _SLUG.sub("-", clause.clause_id), 0, clause.text)
            spans = [s for s in segment_clause(str(cid), clause.text) if s.text.strip()]
            if not spans:
                continue
            span_texts = [s.text.strip() for s in spans]
            # one batch: the whole clause (index 0) + each span -> clause function + per-span functions
            fns = clf.classify([clause.text] + span_texts, batch_size=32)
            clause_function, span_functions = fns[0], fns[1:]
            dense_vecs, sparse_vecs = embedder.encode_batch(span_texts)  # batched dense+sparse per clause
            for s, text, fn, dense, sparse in zip(spans, span_texts, span_functions, dense_vecs, sparse_vecs):
                store.upsert_span(SpanRecord(
                    span_id=s.span_id, parent_chunk_id=str(cid), parent_okf_path=clause.clause_id,
                    span_index=s.span_index, text=text, function=fn,
                    dense_vector=dense, sparse_vector=sparse,
                ))
                n_spans += 1
            if clause_function != NONE_LABEL:
                to_extract.append((cid, clause_function, clause.text))
            if i % 25 == 0 or i == len(clauses):
                rate = i / max(1e-9, time.perf_counter() - t0)
                _progress(f"[spans] {i}/{len(clauses)} clauses  spans={n_spans}  "
                          f"non-NONE={len(to_extract)}  rate={rate:.1f}/s")

        if PHASE == "spans":  # stop after the span index -- enough for the function-gate ceiling
            EXTRACT_CACHE.write_text(  # hand off the non-NONE clauses to a later PHASE=extract run
                "".join(json.dumps({"clause_id": str(cid), "function": fn, "text": txt}) + "\n"
                        for cid, fn, txt in to_extract),
                encoding="utf-8",
            )
            _progress(f"[spans] DONE (phase 1 only) db={DB}  spans={n_spans}  "
                      f"non-NONE clauses={len(to_extract)} (cached -> PHASE=extract)")
            store.close()
            return

    # --- Phase 2: properties (extract concurrently + write each clause AS IT COMPLETES) ----------
    # Durable + resumable: each clause is written the moment it is extracted (a stall/crash loses only
    # in-flight work), and a re-run skips clauses already in the graph. DB writes are serialized by a
    # lock (one store client); extraction stays concurrent.
    from rag_wright.store.arcadedb import CLAUSE_TYPE

    if FRESH:  # start property extraction over from the top, keeping the (expensive) span index
        store.clear_property_graph()
        _progress("[extract] FRESH: cleared the property graph (spans kept)")
    extractor = SeamPropertyExtractor(model_id=EXTRACT_MODEL or None)
    model_label = EXTRACT_MODEL or "default (DeepSeek V4 Pro)"
    existing = {r["clause_id"] for r in store._query(f"SELECT clause_id FROM {CLAUSE_TYPE}")}
    todo = [t for t in to_extract if str(t[0]) not in existing]
    _progress(f"[extract] {len(todo)} clauses to do ({len(existing)} already written, resume-skipped)  "
              f"model={model_label}  concurrency={CONCURRENCY}")
    write_lock = threading.Lock()

    def _extract_and_write(item: tuple[ChunkId, str, str]) -> int:
        cid, function, text = item
        rec = extractor(chunk_id=cid, function=function, text=text, span_id="")
        with write_lock:  # serialize the DB write (one client); extraction is the concurrent part
            store.write_property_graph(rec)  # each clause lands as it completes -> crash-safe / resumable
        return len(rec.assertions)

    per = map_concurrent(
        todo, _extract_and_write, max_concurrency=CONCURRENCY, progress_path=PROGRESS,
        label="[extract]", every=1, echo=True,  # X/N in front (stdout) AND in the flushed progress file
    )

    counts = store.property_graph_counts()
    print(f"\nDONE db={DB}  spans={n_spans}  clauses_this_run={len(todo)}  assertions_this_run={sum(per)}",
          flush=True)
    print(f"property graph: {counts}", flush=True)
    store.close()


if __name__ == "__main__":
    main()
