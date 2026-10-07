"""KG-3 (FR-C/FR-S, ADR-0033): populate the TYPED unified contract KG.

Per non-NONE clause: extract its typed properties with granite-4.1-8b (KG-2's `DGClausePropertyExtractor`
= docling-graph + `clause_template.Clause`, grounding-judge gated) -> `write_clause_kg` (KG-3, typed edges +
ODRL/FOLIO grounding + provenance). This is a phase-2-only driver: it reuses the EXISTING span/clause cache
(`populate_to_extract.jsonl`, ~3,887 non-NONE clauses from T58a phase 1), so NO re-segment / re-embed.

Reuses the property-store driver's controls (T58a): X/N progress to stdout AND a flushed log; each clause is
written the moment it is extracted (crash-safe); RESUME by default (skip clauses already in the typed graph)
or FRESH=1 to rebuild; concurrent extraction, serialized typed writes.

  LIMIT=5 uv run python -m scripts.populate_clause_kg              # dry-run on a scratch DB (own DB, granite)
  uv run python -m scripts.populate_clause_kg                      # full run (RESUME) -> ragwright_acord_pivot
  FRESH=1 uv run python -m scripts.populate_clause_kg              # rebuild the typed graph from the top
  EXTRACT_MODEL=ibm-granite/granite-4.1-8b uv run ...             # override the OpenRouter model id (default)
  # CUAD (Leg A) via the self-hosted Granite-on-Modal server (free credits):
  CLAUSE_KG_CACHE=data/models/cuad_clause_cache.jsonl CLAUSE_KG_DB=ragwright_cuad \
    MODAL_GRANITE_URL=https://<modal-url> uv run python -m scripts.populate_clause_kg

Model = granite-4.1-8b via OpenRouter (the Leg-C winner; no A/B -- DeepSeek is a KG-6 below-par contingency).
The grounding-judge gate (ADR-0028) is standing -- it is applied inside the extractor on every clause.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from rag_wright.packs.contracts.capabilities.contract_kg_store import ContractKGStore
from rag_wright.packs.contracts.capabilities.dg_extraction import extract_clause, ollama_model, openrouter_model
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.packs.contracts.spans.clause_kg_extractor import DGClausePropertyExtractor
from rag_wright.util.concurrent import map_concurrent

# Cache: the (clause_id, function, text[, span_id]) handoff. Default = the ACORD T58a cache; override with
# CLAUSE_KG_CACHE (e.g. the CUAD cache from scripts/build_cuad_clause_cache.py for Leg A).
EXTRACT_CACHE = Path(os.environ.get("CLAUSE_KG_CACHE", "data/models/populate_to_extract.jsonl"))
PROGRESS = Path("data/models/populate_clause_kg_progress.log")
LIMIT = int(os.environ.get("LIMIT", "0"))
DB = os.environ.get("CLAUSE_KG_DB", "ragwright_clause_kg_dryrun" if LIMIT else "ragwright_acord_pivot")
CONCURRENCY = int(os.environ.get("CONCURRENCY", "8"))
FRESH = os.environ.get("FRESH", "0") == "1"
EXTRACT_MODEL = os.environ.get("EXTRACT_MODEL", "ibm-granite/granite-4.1-8b")
# MODAL_GRANITE_URL: point at a self-hosted Granite-on-Modal Ollama server (uses free Modal credits) instead
# of OpenRouter; MODAL_GRANITE_TAG is its Ollama tag. Empty => OpenRouter granite-4.1-8b (the default).
MODAL_GRANITE_URL = os.environ.get("MODAL_GRANITE_URL", "")
MODAL_GRANITE_TAG = os.environ.get("MODAL_GRANITE_TAG", "granite4.1:8b-bf16")


def _chunk_id_from_value(value: str) -> ChunkId:
    """Reconstruct a ChunkId from its canonical `source:index:hash` string (ADR-0025 rsplit parse)."""
    source, index, content_hash = value.rsplit(":", 2)
    return ChunkId(source_doc_id=source, chunk_index=int(index), content_hash=content_hash)


def _progress(msg: str) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(msg + "\n", encoding="utf-8")
    print(msg, flush=True)


def build_extractor() -> DGClausePropertyExtractor:
    """The typed clause extractor: granite-4.1-8b, grounding-gated. Self-hosted on Modal (Ollama) when
    MODAL_GRANITE_URL is set (free credits), else OpenRouter (EXTRACT_MODEL). Same seam either way."""
    if MODAL_GRANITE_URL:
        model = ollama_model("granite-4.1-8b", MODAL_GRANITE_TAG, base_url=MODAL_GRANITE_URL)
    else:
        model = openrouter_model("granite-4.1-8b", EXTRACT_MODEL)
    return DGClausePropertyExtractor(lambda text: extract_clause(text, model))


def extract_and_write(
    item: tuple[ChunkId, str, str, str], *, extractor: Any, store: Any,
    write_lock: threading.Lock, errors: list[int],
) -> int:
    """Extract one clause's typed properties and write it (crash-safe: a per-clause failure is logged and
    skipped, never fatal to the long resumable run; the DB write is serialized, extraction is concurrent).
    Returns the assertion count. Injectable `extractor`/`store` so the loop is hermetically testable."""
    cid, function, text, span_id = item
    try:
        rec = extractor(chunk_id=cid, function=function, text=text, span_id=span_id)
        with write_lock:
            ContractKGStore(store).write_clause_kg(rec)  # typed edges; lands as it completes -> crash-safe / resumable
        return len(rec.assertions)
    except Exception as e:  # noqa: BLE001 - isolate a per-clause failure; resume re-attempts it later
        with write_lock:
            errors[0] += 1
        print(f"  [skip] {cid} ({function}): {type(e).__name__}: {str(e)[:100]}", flush=True)
        return 0


def main() -> None:
    load_dotenv()
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.packs.contracts.capabilities.contract_kg_store import CLAUSE_TYPE

    rows = [json.loads(line) for line in EXTRACT_CACHE.read_text(encoding="utf-8").splitlines() if line.strip()]
    if LIMIT:
        rows = rows[:LIMIT]
    to_extract = [
        (_chunk_id_from_value(r["clause_id"]), r["function"], r["text"], r.get("span_id", "")) for r in rows
    ]

    store = ArcadeDBStore.from_env(database=DB, reset=False)
    store.ensure_schema()
    if FRESH:  # rebuild the typed graph from the top (spans, if any, are left intact)
        ContractKGStore(store).clear_clause_kg()
        _progress(f"[clause-kg] FRESH: cleared the typed clause KG in {DB}")

    existing = {r["clause_id"] for r in store._query(f"SELECT clause_id FROM {CLAUSE_TYPE}")}
    todo = [t for t in to_extract if str(t[0]) not in existing]
    _progress(f"[clause-kg] db={DB} model={EXTRACT_MODEL}  {len(todo)} clauses to do "
              f"({len(existing)} already written, resume-skipped)  concurrency={CONCURRENCY}")

    extractor = build_extractor()
    write_lock = threading.Lock()
    errors = [0]
    t0 = time.perf_counter()

    per = map_concurrent(
        todo,
        lambda item: extract_and_write(item, extractor=extractor, store=store,
                                       write_lock=write_lock, errors=errors),
        max_concurrency=CONCURRENCY, progress_path=PROGRESS, label="[clause-kg]", every=1, echo=True,
    )

    counts = ContractKGStore(store).clause_kg_counts()
    rate = len(todo) / max(1e-9, time.perf_counter() - t0)
    print(f"\nDONE db={DB}  clauses_this_run={len(todo)}  assertions_this_run={sum(per)}  "
          f"errors={errors[0]}  rate={rate:.2f}/s", flush=True)
    print(f"typed clause KG: {counts}", flush=True)
    store.close()


if __name__ == "__main__":
    main()
