"""Step-4 (classifier rare-class improvement): mine SILVER training spans for the scarce CUAD function classes
from the CUAD-unlabeled (NONE) operative spans of the TRAIN contracts only (the SEED=0 holdout is excluded, so
the eval stays pure gold -- no leakage). Same shape as T60's label_new_functions.py:

  1. segmentation + keyword pre-filter (deterministic, cached to scarce_function_candidates.jsonl);
  2. LLM confirm (GEMMA via the seam -- benchmarked = DeepSeek on this task, CU-D2 A/B; concurrent via
     util.map_concurrent), keeping only spans confirmed as a scarce class.

Silver -> data/models/scarce_function_spans.jsonl, folded into the retrain by train_legalbert_function.py.

  LIMIT=8 uv run --no-sync python -m scripts.mine_scarce_functions   # dry-run (no cache)
  uv run --no-sync python -m scripts.mine_scarce_functions           # full mine
  FORCE=1 uv run --no-sync python -m scripts.mine_scarce_functions   # rebuild the candidate cache
"""

from __future__ import annotations

import json
import os
import random
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from rag_wright.packs.contracts.spans.cuad_labels import label_operative_spans, parse_cuad
from rag_wright.packs.contracts.spans.scarce_function_labels import NONE_LABEL, SeamScarceConfirmer, scarce_candidates
from rag_wright.util.concurrent import map_concurrent

load_dotenv("/Users/farhan/work/RAG_Wright/.env")
CUAD = Path("data/cuad/extracted/CUAD_v1.json")
OUT = Path("data/models/scarce_function_spans.jsonl")
CACHE = Path("data/models/scarce_function_candidates.jsonl")
PROGRESS = Path("data/models/scarce_mine_progress.log")
SEED = 0
LIMIT = int(os.environ.get("LIMIT", "0"))
FORCE = bool(os.environ.get("FORCE"))


def _progress(msg: str) -> None:
    PROGRESS.write_text(msg + "\n")
    print(msg, flush=True)


def _candidates() -> list[dict]:
    """Keyword-hit NONE spans of the TRAIN contracts (cached; the SEED=0 holdout is excluded = no leakage)."""
    if CACHE.exists() and not FORCE and not LIMIT:
        return [json.loads(l) for l in CACHE.read_text().splitlines() if l.strip()]
    contracts = list(parse_cuad(CUAD))
    random.Random(SEED).shuffle(contracts)
    if LIMIT:
        contracts = contracts[:LIMIT]
    train_c = contracts[max(1, len(contracts) // 5):]  # drop the holdout fifth
    rows = []
    for c in train_c:
        for ls in label_operative_spans(c):
            if ls.label == NONE_LABEL:
                cands = scarce_candidates(ls.text)
                if cands:
                    rows.append({"text": ls.text, "contract_id": c.contract_id, "cands": sorted(cands)})
    if not LIMIT:
        CACHE.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return rows


def main() -> None:
    t0 = time.perf_counter()
    cands = _candidates()
    _progress(f"[candidates] {len(cands)} keyword-hit NONE spans in TRAIN contracts")
    confirm = SeamScarceConfirmer()  # Gemma (GENERAL) by default

    def work(row: dict) -> str:
        return confirm(row["text"], frozenset(row["cands"]))

    labels = map_concurrent(cands, work, max_concurrency=8, progress_path=PROGRESS,
                            label="[confirm]", every=25, echo=True)
    kept = [{"label": lab, "text": r["text"], "contract_id": r["contract_id"]}
            for r, lab in zip(cands, labels) if lab != NONE_LABEL]
    OUT.write_text("".join(json.dumps({"label": r["label"], "text": r["text"],
                                       "contract_id": r["contract_id"]}) + "\n" for r in kept))
    counts = Counter(r["label"] for r in kept)
    _progress(f"[done] confirmed {len(kept)}/{len(cands)} silver spans in {time.perf_counter()-t0:.0f}s -> {OUT}")
    for label, n in counts.most_common():
        print(f"    {label:<38} +{n}", flush=True)


if __name__ == "__main__":
    main()
