"""A1 fix (persist-clause-span-id): backfill the clause-level `span_id` on an existing KG, in place.

NON-DESTRUCTIVE / IN-PLACE: for each `Clause`, recover its operative span_id by the DETERMINISTIC content-hash
join -- the `clause_id` embeds `sha256(op.text)` and the Span index stores that same RAW `op.text`
(`to_span_record`), so `sha256(span.text) == the hash in clause_id` by construction. Then `UPDATE Clause SET
span_id`. No re-ingest, no re-extraction, no LLM. Touches only the new `Clause.span_id` field; spans, edges,
entities, PARTY_TO links are untouched.

Idempotent (skips clauses already set correctly). `DRY_RUN=1` reports the match rate without writing. The store
is env-selected, so the SAME script runs against the local KG or the Modal KG (a live writable https service):

  DRY_RUN=1 ARCADEDB_DATABASE=ragwright_cuad_full uv run --no-sync python -m scripts.backfill_clause_span_id
  # apply in place (local, or Modal via ARCADEDB_HOST/PORT/PROTOCOL=https/...):
  ARCADEDB_DATABASE=ragwright_cuad_full uv run --no-sync python -m scripts.backfill_clause_span_id

Match on TEXT (content hash), so even the rare identical-text edge case is faithful (the cited text is right
regardless of which span_id). An unmatched clause (a best-effort span-write that failed at ingest) keeps
span_id="" and is left as-is -- safe.
"""

from __future__ import annotations

import hashlib
import os
from rag_wright.packs.contracts.capabilities.contract_kg_store import ContractKGStore


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _content_hash(clause_id: str) -> str:
    """The `sha256(op.text)` embedded as the last `:`-segment of `<source_doc_id>:<index>:<hash>`."""
    return clause_id.rsplit(":", 1)[1]


def plan_backfill(spans: list[dict], clauses: list[dict]) -> tuple[dict[str, str], dict[str, int]]:
    """PURE (testable without a store): (span rows, clause rows for one contract) -> ({clause_id: span_id to
    write}, stats). Deterministic content-hash join; skips clauses already correct; counts matched/unmatched."""
    by_hash: dict[str, str] = {}
    for s in spans:
        by_hash.setdefault(_sha(s["text"]), s["span_id"])  # first-wins; identical text -> identical citation
    updates: dict[str, str] = {}
    stats = {"total": 0, "matched": 0, "already": 0, "unmatched": 0}
    for c in clauses:
        stats["total"] += 1
        want = by_hash.get(_content_hash(c["clause_id"]))
        if want is None:
            stats["unmatched"] += 1
            continue
        stats["matched"] += 1
        if (c.get("span_id") or "") == want:
            stats["already"] += 1
            continue
        updates[c["clause_id"]] = want
    return updates, stats


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    from rag_wright.store.arcadedb import ArcadeDBStore, _sql_str
    from rag_wright.packs.contracts.capabilities.contract_kg_store import CLAUSE_TYPE

    dry = os.environ.get("DRY_RUN") == "1"
    store = ArcadeDBStore.from_env()
    contracts = [r["contract_id"] for r in ContractKGStore(store).all_contracts()]
    n = len(contracts)
    print(f"[backfill] {'DRY-RUN' if dry else 'APPLY'} clause span_id over {n} contracts", flush=True)

    agg = {"total": 0, "matched": 0, "already": 0, "unmatched": 0, "updated": 0}
    for i, cid in enumerate(contracts, 1):
        spans = store.all_spans_by_document(cid)
        clauses = ContractKGStore(store).clauses_in_contract(cid)
        updates, stats = plan_backfill(spans, clauses)
        for key in ("total", "matched", "already", "unmatched"):
            agg[key] += stats[key]
        if not dry:
            for clause_id, span_id in updates.items():
                store._command(
                    f"UPDATE {CLAUSE_TYPE} SET span_id = {_sql_str(span_id)} WHERE clause_id = {_sql_str(clause_id)}")
        agg["updated"] += len(updates)
        if i % 25 == 0 or i == n:
            print(f"[backfill] {i}/{n} contracts | clauses={agg['total']} matched={agg['matched']} "
                  f"already={agg['already']} {'would-update' if dry else 'updated'}={agg['updated']} "
                  f"unmatched={agg['unmatched']}", flush=True)

    rate = agg["matched"] / agg["total"] if agg["total"] else 0.0
    print(f"[backfill] DONE: clauses={agg['total']} matched={agg['matched']} ({rate:.1%}) "
          f"already-set={agg['already']} {'would-update' if dry else 'updated'}={agg['updated']} "
          f"unmatched={agg['unmatched']}", flush=True)
    store.close()


if __name__ == "__main__":
    main()
