"""T58a (FR-Q, ADR-0025): populate the pivot store from the ACORD corpus.

Per clause: segment into operative spans (T55) -> classify each span's function (T56/T60 LegalBERT) ->
embed dense+sparse (BGE-M3) -> upsert_span (the function-filtered hybrid index). Then, per non-NONE
clause, extract properties (T57b, concurrent via map_concurrent) over the clause text and
write_property_graph (T57c). CLAUSE-LEVEL property records (the ACORD corpus items are already focused
clauses); the clause function is the classifier's read of the full clause text. `parent_okf_path` keeps
the original ACORD id so retrieval/eval can map a span back to its qrels clause.

  LIMIT=5 uv run python -m scripts.populate_property_store    # dry-run: prove the chain (own DB)
  uv run python -m scripts.populate_property_store            # full population
"""

from __future__ import annotations

import json
import os
import re
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

    # --- Phase 2: properties (API: extract concurrently, then write sequentially) ----------------
    extractor = SeamPropertyExtractor()

    def _extract(item: tuple[ChunkId, str, str]):
        cid, function, text = item
        return extractor(chunk_id=cid, function=function, text=text, span_id="")

    records = map_concurrent(
        to_extract, _extract, max_concurrency=CONCURRENCY, progress_path=PROGRESS,
        label="[extract]", every=5,  # fine-grained progress file (done/total/rate/eta)
    )
    n_assertions = 0
    for rec in records:  # sequential DB writes (one store client)
        store.write_property_graph(rec)
        n_assertions += len(rec.assertions)

    counts = store.property_graph_counts()
    print(f"\nDONE db={DB}  spans={n_spans}  clauses_extracted={len(records)}  assertions={n_assertions}",
          flush=True)
    print(f"property graph: {counts}", flush=True)
    store.close()


if __name__ == "__main__":
    main()
