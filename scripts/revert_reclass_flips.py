"""INGEST-LLM-CLASSIFIER (ADR-0048) Phase A selective revert: undo the flip transitions the independent audit
(scripts/audit_reclass_flips.py -> reclass_audit.json) marked REVERT, restoring each clause's OLD (pre-write)
function. Precise + reversible: the write checkpoint (reclass_write_checkpoint.jsonl) holds old->new per clause,
so a REVERT transition's clauses are set back to `old` (functions -> single-label [old, high] for consistency).

Note: a reverted clause's typed property edges were marked AMBIGUOUS by the write (mark-stale) and are LEFT
AMBIGUOUS here -- the pre-write confidences were not snapshotted, so they stay conservatively flagged (never
wrong, only down-weighted) until Phase B re-extraction / a reground pass. Reverting the LABEL is exact.

  uv run --no-sync python -m scripts.revert_reclass_flips          # DRY-RUN (counts only)
  APPLY=1 uv run --no-sync python -m scripts.revert_reclass_flips  # apply the revert
"""
from __future__ import annotations

import json
import os

from dotenv import load_dotenv


def log(m: str) -> None:
    print(m, flush=True)


def main() -> None:
    os.environ.setdefault("RAG_SERVING", "openrouter")
    load_dotenv()
    from rag_wright.store.arcadedb import ArcadeDBStore, _sql_str

    apply = os.environ.get("APPLY", "0") == "1"
    db = os.environ.get("QA_DB", "ragwright_cuad_full")
    audit = json.load(open("data/eval/taxonomy_gaps/reclass_audit.json"))
    revert_set = {(r["old"], r["new"]) for r in audit["rows"] if r["verdict"] == "REVERT"}
    log(f"[revert] {len(revert_set)} REVERT transitions: " + "; ".join(f"{o}->{n}" for o, n in revert_set))

    # collect the clauses to revert from the write checkpoint (clause_id, old) for those transitions
    to_revert: list[tuple[str, str]] = []
    for line in open("data/eval/taxonomy_gaps/reclass_write_checkpoint.jsonl"):
        if not line.strip():
            continue
        for r in json.loads(line)["reclass"]:
            if (r["old"], r["new"]) in revert_set and r["new"] != r["old"]:
                to_revert.append((r["clause_id"], r["old"]))
    log(f"[revert] {len(to_revert)} clauses to revert to their OLD label")

    if not apply:
        log("[revert] DRY-RUN (no writes). Re-run with APPLY=1 to revert.")
        return

    store = ArcadeDBStore.from_env(database=db)
    done = 0
    for clause_id, old in to_revert:
        fjson = json.dumps([{"function": old, "confidence": "high"}])
        try:
            store._command(
                f"UPDATE Clause SET function = {_sql_str(old)}, functions = {_sql_str(fjson)} "
                f"WHERE clause_id = {_sql_str(clause_id)}")
            done += 1
        except Exception as e:  # noqa: BLE001
            log(f"  revert err {clause_id[:40]}: {str(e)[:60]}")
        if done % 100 == 0:
            log(f"[revert] {done}/{len(to_revert)} reverted")
    log(f"[revert] DONE: reverted {done}/{len(to_revert)} clauses to their OLD label "
        f"(property edges left AMBIGUOUS -> Phase B / reground).")
    store.close()


if __name__ == "__main__":
    main()
