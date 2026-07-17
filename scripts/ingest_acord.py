#!/usr/bin/env python
"""Ingest the ACORD corpus (T33) through the real pipeline into ArcadeDB + the chunk-text sidecar.

Each pre-segmented ACORD clause is ingested as one chunk with `source_doc_id` = the ACORD `_id`, so a
retrieved `chunk_id` rsplits back to its corpus-id for qrels scoring (no side map). The clause is its own
summary (ACORD is pre-segmented; the RLM chunker is not exercised, ADR-0011), so dense-over-summary is
dense-over-clause. Writes go through the real `ChunkWriter.write_document` (index upsert + chunk-text
sidecar under one content-hash gate + completeness guard), NOT a direct `upsert_chunk` — that is the pinned
invariant that lets chunk_read never miss on this ingest. Content-hash gated, so re-runs are idempotent.

    uv run python scripts/ingest_acord.py
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from eval.acord import load_corpus
from rag_wright.capabilities.chunk_write import ChunkWriter, to_chunk_record
from rag_wright.capabilities.embedding import ChunkEmbedding
from rag_wright.capabilities.rlm_chunking import Chunk
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.store.arcadedb import ArcadeDBStore
from rag_wright.store.chunk_text import ChunkTextStore

ACORD_DB = "ragwright_acord"
CHECKPOINT_DIR = Path("data/acord/ingest")
TEXT_DIR = Path("data/acord/chunk_text")
EMBED_BATCH = 16  # in-process encode batch size (CPU)
EMBED_MAX_LENGTH = 1024  # cap embedding compute (full text still stored in the sidecar); covers ~p95 clauses


def _chunk(clause_id: str, text: str) -> Chunk:
    return Chunk(
        chunk_id=ChunkId.of(clause_id, 0, text).value,
        chunk_index=0,
        text=text,
        summary=text,  # pre-segmented clause is its own summary (ACORD scope; RLM chunker not exercised)
        token_estimate=max(1, len(text) // 4),
    )


def _embed_batched(chunks: list[Chunk]) -> dict[str, ChunkEmbedding]:
    """Batch-embed with BGE-M3, single-process CPU (the default multi-process pool deadlocks on macOS).

    `devices=["cpu"]` forces one in-process worker; `.encode` batches internally (its own tqdm). `max_length`
    caps the EMBEDDING compute only — the full clause text is still persisted to the sidecar for synthesis;
    a small tail of very long clauses (p99 ~1700 tokens) is truncated for the vector, a disclosed, standard
    embedding config. Vectors are identical to the per-text path (BGE-M3 encodes each text independently).
    """
    from FlagEmbedding import BGEM3FlagModel

    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False, devices=["cpu"])
    texts = [c.text for c in chunks]
    enc = model.encode(
        texts, batch_size=EMBED_BATCH, max_length=EMBED_MAX_LENGTH, return_dense=True, return_sparse=True
    )
    out: dict[str, ChunkEmbedding] = {}
    for chunk, dense, lw in zip(chunks, enc["dense_vecs"], enc["lexical_weights"]):
        out[chunk.chunk_id] = ChunkEmbedding(
            chunk_id=chunk.chunk_id,
            dense_vector=dense.tolist(),
            sparse_vector={int(k): float(v) for k, v in lw.items()},
        )
    return out


def main() -> None:
    load_dotenv()  # ARCADEDB_* credentials for the live store
    clauses = load_corpus()
    print(f"[ingest] {len(clauses)} ACORD clauses -> db {ACORD_DB!r}")

    store = ArcadeDBStore.from_env(database=ACORD_DB, reset=False)
    store.ensure_schema()
    text_store = ChunkTextStore(TEXT_DIR)
    writer = ChunkWriter(store, text_store=text_store, checkpoint_dir=CHECKPOINT_DIR)

    print("[ingest] loading BGE-M3 and batch-embedding (dense over clause, sparse over clause) ...")
    chunks = [_chunk(c.clause_id, c.text) for c in clauses]
    emb_by_id = _embed_batched(chunks)
    print(f"[ingest] embedded {len(emb_by_id)} clauses; writing (index + sidecar, gated) ...")

    tally: Counter[str] = Counter()
    for clause, chunk in zip(clauses, chunks):
        record = to_chunk_record(chunk, emb_by_id[chunk.chunk_id], source_doc_id=clause.clause_id)
        content_hash = ChunkId.of(clause.clause_id, 0, chunk.text).content_hash
        result = writer.write_document(
            clause.clause_id, content_hash, [record], texts={chunk.chunk_id: chunk.text}
        )
        tally[result.status] += 1

    print(f"[ingest] done: {dict(tally)}")
    print(f"[ingest] store chunk_count = {store.chunk_count()}")
    if writer.dead_letter_ids():
        print(f"[ingest] WARNING dead-lettered: {sorted(writer.dead_letter_ids())}")
    store.close()


if __name__ == "__main__":
    main()
