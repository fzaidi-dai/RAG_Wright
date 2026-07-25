"""P2 ingestion step: extract v2 KG structural features (once) for every in-pool clause in the dataset, and
write clause_id -> compact feature string to data/models/ce/clause_features.jsonl. Reuses any cached v2
extractions; crash-safe incremental cache. Gemma via the model-profile seam.

  uv run --no-sync python -m scripts.distill.extract_features
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from dotenv import load_dotenv

from rag_wright.models.profiles import profile_for
from rag_wright.models.seam import build_model
from rag_wright.util.concurrent import map_concurrent
from scripts.distill.relational_features import PROMPT, RelationalClauseV2, build_feat_string

OUT = Path("data/models/ce")
CACHE = OUT / "clause_v2.jsonl"  # raw v2 records, crash-safe
SEED = Path("/private/tmp/claude-501/-Users-farhan-work-RAG-Wright/"
            "8692571d-7b17-4a40-bebc-755567fbdb7f/scratchpad/rel_v2_features.jsonl")  # reuse prior extractions
MODEL = "google/gemma-4-31b-it"


def main() -> None:
    load_dotenv()
    # in-pool clause -> text, from the dataset
    text_of, needed = {}, set()
    for line in (OUT / "dataset.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("in_pool"):
                needed.add(r["clause_id"])
                text_of[r["clause_id"]] = r["text"]

    done: dict[str, dict] = {}
    for src in (SEED, CACHE):
        if src.exists():
            for line in src.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    done[r["cid"]] = r["rec"]
    OUT.mkdir(parents=True, exist_ok=True)

    prof = profile_for(MODEL)
    skw = {"method": prof.structured_method}
    if prof.structured_extra_body is not None:
        skw["extra_body"] = prof.structured_extra_body
    runner = build_model(MODEL, timeout=60.0, max_retries=1).with_structured_output(RelationalClauseV2, **skw)
    lock = threading.Lock()

    def one(cid):
        if cid in done:
            return cid, done[cid]
        try:
            rec = runner.invoke(PROMPT.format(clause=text_of[cid][:1800])).model_dump(mode="json")
        except Exception:  # noqa: BLE001
            return cid, None
        with lock:
            with CACHE.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"cid": cid, "rec": rec}) + "\n")
                f.flush()
            done[cid] = rec
        return cid, rec

    todo = [c for c in needed if c not in done]
    print(f"[extract] {len(needed)} in-pool clauses, {len(todo)} to extract via {MODEL}", flush=True)
    if todo:
        for cid, rec in map_concurrent(todo, one, max_concurrency=8, label="[extract]", echo=True, every=25):
            if rec is not None:
                done[cid] = rec

    with (OUT / "clause_features.jsonl").open("w", encoding="utf-8") as f:
        n = 0
        for cid in needed:
            if cid in done:
                f.write(json.dumps({"clause_id": cid, "feat": build_feat_string(done[cid])}) + "\n")
                n += 1
    print(f"wrote {n}/{len(needed)} clause feature strings -> {OUT}/clause_features.jsonl", flush=True)


if __name__ == "__main__":
    main()
