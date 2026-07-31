"""INGEST-REFACTOR smoke: ingest a few CUAD docs through the GENERIC LG-3d pipeline into a SCRATCH database.

Proves `run_cuad_ingestion(CuadAdapter(), store)` populates + connects a real KG end-to-end -- one call, no
`ingest_cuad()`. Non-destructive: a fresh scratch db (`reset=True`), never the live `ragwright_cuad`.
Simplifications (INGEST-REFACTOR scope): function="" clause extraction, no span/embedding index.

  SMOKE_DB=ragwright_ingest_smoke LIMIT=2 uv run python -m scripts.ingest_smoke
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    from rag_wright.store.arcadedb import PARTY_TO_EDGE_TYPE, ArcadeDBStore
    from rag_wright.subgraphs.contract_ingestion_pipeline import run_cuad_ingestion

    db = os.environ.get("SMOKE_DB", "ragwright_ingest_smoke")
    limit = int(os.environ.get("LIMIT", "2"))
    store = ArcadeDBStore.from_env(database=db, reset=True)  # fresh scratch db -- non-destructive
    print(f"[smoke] ingesting {limit} CUAD docs through the generic pipeline into scratch db {db!r}", flush=True)

    report = run_cuad_ingestion(
        Path("data/cuad/extracted/CUAD_v1.json"), store,
        cache_dir=Path("data/cache/ingest_smoke"), limit=limit)

    print(f"[smoke] REPORT: {report.model_dump()}", flush=True)
    print(f"[smoke] clause KG: {store.clause_kg_counts()}", flush=True)
    print(f"[smoke] entity graph: {store.graph_counts()}", flush=True)
    n = store._query(f"SELECT count(*) AS n FROM {PARTY_TO_EDGE_TYPE}")[0]["n"]
    print(f"[smoke] PARTY_TO edges connecting the two: {n}", flush=True)

    # INGEST-REFACTOR phase 2a: the dense/sparse Span retrieval index -- prove it landed AND that hybrid
    # retrieval returns hits over it.
    from rag_wright.capabilities.embedding import BGEM3Embedder
    from rag_wright.store.arcadedb import SPAN_TYPE

    spans = store._query(f"SELECT count(*) AS n FROM {SPAN_TYPE}")[0]["n"]
    print(f"[smoke] Span retrieval index: {spans} spans", flush=True)
    emb = BGEM3Embedder()
    q = "limitation of the parties' liability under the agreement"
    hits = store.span_hybrid_search(emb.encode_dense(q), emb.encode_sparse(q), k=3)
    print(f"[smoke] hybrid_search({q!r}) -> {len(hits)} hits "
          f"(top function={hits[0].get('function') if hits else None})", flush=True)
    store.close()


if __name__ == "__main__":
    main()
