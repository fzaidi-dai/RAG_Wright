"""T60 (FR-C.3, ADR-0026): bootstrap training labels for the 3 extended function classes from CUAD.

CUAD contracts -> operative spans (T55) -> the NONE spans (CUAD-unlabeled) -> keyword pre-filter ->
LLM confirm (DeepSeek via the model-profile seam, parallelized) -> positive spans for Indemnification,
the indirect/consequential damages waiver, and the warranty disclaimer. Written to a gitignored jsonl
that `train_legalbert_function.py` folds into the retrain (T60). ACORD is never touched (no leakage).

Two phases, both reported live to `data/models/label_progress.log` (tail it):
  1. segmentation (deterministic, no LLM) -- cached to `new_function_candidates.jsonl` so it runs once;
  2. LLM confirm (concurrent via `util.map_concurrent`, done/total/rate/eta in the progress file).

  LIMIT=6 uv run python -m scripts.label_new_functions    # dry-run: prove the pipeline cheaply (no cache)
  uv run python -m scripts.label_new_functions            # full run (uses/writes the candidate cache)
  FORCE=1 uv run python -m scripts.label_new_functions    # full run, rebuild the candidate cache
"""

from __future__ import annotations

import json
import os
import random
import time
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import load_dotenv

from rag_wright.spans.cuad_labels import label_operative_spans, parse_cuad
from rag_wright.spans.function_classifier import NONE_LABEL
from rag_wright.spans.new_function_labels import (
    NewFunctionTag,
    SeamNewFunctionConfirmer,
    keyword_candidates,
)
from rag_wright.util.concurrent import map_concurrent

CUAD = Path("data/cuad/extracted/CUAD_v1.json")
OUT = Path("data/models/new_function_spans.jsonl")  # gitignored (regenerable)
CACHE = Path("data/models/new_function_candidates.jsonl")  # cached slow segmentation pass
PROGRESS = Path("data/models/label_progress.log")  # tail this for live progress
SEED = 0
LIMIT = int(os.environ.get("LIMIT", "0"))
FORCE = os.environ.get("FORCE", "0") == "1"  # rebuild the candidate cache
PER_CLASS = 20 if LIMIT else 400  # positives to keep per class
CANDIDATE_CAP = PER_CLASS * 3  # bound LLM calls: at most ~3x per keyword-class (assumes >~1/3 confirm)
CONCURRENCY = int(os.environ.get("CONCURRENCY", "8"))


def _progress(msg: str) -> None:
    """A single-line heartbeat to the shared progress file (and stdout), overwritten each call."""
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(msg + "\n", encoding="utf-8")
    print(msg, flush=True)


def _gather_candidates(contracts) -> list[dict]:
    """NONE spans that hit a keyword, capped per keyword-class so the LLM cost is bounded. Each keeps its
    source `contract_id` (contract-disjoint retrain split, no leakage). Heartbeats every 25 contracts."""
    rng = random.Random(SEED)
    pool: list[tuple[str, list[str], str]] = []
    for i, c in enumerate(contracts, 1):
        for ls in label_operative_spans(c):
            if ls.label != NONE_LABEL:
                continue
            cands = keyword_candidates(ls.text)
            if cands:
                pool.append((ls.text, sorted(cands), ls.contract_id))
        if i % 25 == 0 or i == len(contracts):
            _progress(f"[segment] {i}/{len(contracts)} contracts  candidates_so_far={len(pool)}")
    rng.shuffle(pool)
    kept: list[dict] = []
    counts: Counter[str] = Counter()
    for text, cands, contract_id in pool:
        if any(counts[label] < CANDIDATE_CAP for label in cands):
            kept.append({"text": text, "candidates": cands, "contract_id": contract_id})
            for label in cands:
                counts[label] += 1
    return kept


def _cached_candidates(contracts) -> list[dict]:
    """Load the cached candidate set (the slow segmentation pass runs once); dry-runs skip the cache."""
    if CACHE.exists() and not FORCE and not LIMIT:
        rows = [json.loads(line) for line in CACHE.read_text(encoding="utf-8").splitlines() if line.strip()]
        _progress(f"[segment] loaded {len(rows)} candidates from cache ({CACHE})")
        return rows
    rows = _gather_candidates(contracts)
    if not LIMIT:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        with CACHE.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
    return rows


def main() -> None:
    load_dotenv()
    contracts = list(parse_cuad(CUAD))
    random.Random(SEED).shuffle(contracts)
    if LIMIT:
        contracts = contracts[:LIMIT]
    candidates = _cached_candidates(contracts)
    _progress(f"[confirm] {len(candidates)} candidates  concurrency={CONCURRENCY}  -> LLM confirm starting")
    if not candidates:
        print("no candidates; abort")
        return

    confirmer = SeamNewFunctionConfirmer()

    def _confirm(row: dict) -> NewFunctionTag:
        return confirmer(row["text"], frozenset(row["candidates"]))

    t0 = time.perf_counter()
    tags = map_concurrent(
        candidates, _confirm, max_concurrency=CONCURRENCY, progress_path=PROGRESS, label="[confirm]", every=10
    )
    _progress(f"[confirm] done: {len(tags)} spans in {time.perf_counter()-t0:.0f}s")

    by: dict[str, list[tuple[str, str]]] = defaultdict(list)  # label -> [(text, contract_id)]
    for row, tag in zip(candidates, tags):
        if tag != NewFunctionTag.NONE and len(by[tag.value]) < PER_CLASS:
            by[tag.value].append((row["text"], row["contract_id"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        for label, items in sorted(by.items()):
            for text, contract_id in items:
                fh.write(json.dumps({"label": label, "text": text, "contract_id": contract_id}) + "\n")
    print("\npositives per class:", flush=True)
    for label in sorted(by):
        print(f"  {len(by[label]):4d}  {label}")
    print(f"\nsaved {OUT}", flush=True)


if __name__ == "__main__":
    main()
