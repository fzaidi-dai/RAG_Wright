"""Snapshot the fixed Leg-A evidence into a committed JSON fixture (the silver-eval skeleton).

For each (function, question) the evidence is built ONCE the way intra_document_qa serves it (target-function
clauses + ADR-0044 carve-outs, rehydrated to real span text) and frozen into the fixture, so the downstream
generation-robustness / escalation (A) measurement needs only the MODELS, never the live KG. A human (silver
now, SME later) then adds the answer key alongside this snapshot; the snapshot itself is mechanical.

Also emits a few NEGATIVES: evidence served for one function paired with an OFF-TOPIC question the evidence
cannot answer -- so the measurement can score precision (does a strategy fabricate rather than abstain?).

  ARCADEDB_HOST=<rw-arcadedb>.modal.run ARCADEDB_PORT=443 ARCADEDB_PROTOCOL=https ARCADEDB_USER=root \
    ARCADEDB_PASSWORD=rag_wright_dev_2026 ARCADEDB_DATABASE=ragwright_cuad_full \
    uv run --no-sync python -m scripts.snapshot_leg_a_eval
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

_OUT = Path("tests/fixtures/leg_a_silver/evidence_snapshot.json")

# Cap evidence per query (doc order) so the silver key is authorable and the measurement is tractable +
# CONSISTENT across strategies. Over-serving (e.g. Minimum Commitment served 170 clauses) is a separate
# retrieval-scope issue; here we isolate generation, so all strategies see the same bounded evidence.
_MAX_EVIDENCE = 20

# answerable: (function, natural question). The served evidence contains clauses of this function.
_ANSWERABLE = [
    ("Cap On Liability", "How is liability capped in this contract, and under what conditions?"),
    ("Uncapped Liability", "For what matters is liability uncapped or unlimited in this contract?"),
    ("Governing Law", "What law governs this contract?"),
    ("Termination For Convenience", "Can this contract be terminated for convenience, and how?"),
    ("Non-Compete", "What non-compete restrictions does this contract impose?"),
    ("Audit Rights", "What audit rights does this contract grant?"),
    ("Anti-Assignment", "Are there restrictions on assigning this contract?"),
    ("Insurance", "What insurance is each party required to carry?"),
    ("Ip Ownership Assignment", "How is intellectual-property ownership assigned in this contract?"),
    ("Exclusivity", "What exclusivity obligations does this contract create?"),
    ("Minimum Commitment", "What minimum commitments does this contract impose?"),
    ("Revenue/Profit Sharing", "How is revenue or profit shared under this contract?"),
]

# negatives: (function_to_serve, OFF-TOPIC question the served evidence cannot answer). Confirmed at key time.
_NEGATIVES = [
    ("Governing Law", "What product warranty and warranty duration does this contract provide?"),
    ("Audit Rights", "What is the total contract price and payment schedule?"),
]


def _pick_contract(store, function: str) -> str | None:
    from rag_wright.packs.contracts.capabilities.contract_kg_store import CLAUSE_TYPE
    rows = store._query(f"SELECT clause_id FROM {CLAUSE_TYPE} WHERE function = '{function}'")
    if not rows:
        return None
    by = defaultdict(int)
    for r in rows:
        by[r["clause_id"].rsplit(":", 2)[0]] += 1
    return max(by, key=by.get)


def _evidence(store, contract_id: str, function: str) -> list[dict]:
    from rag_wright.packs.contracts.capabilities.contract_kg_serve import clauses_of_function
    from rag_wright.packs.contracts.capabilities.contract_kg_store import ContractKGStore  # EP-REF-1a-ii: typed reads via the domain store
    from rag_wright.packs.contracts.subgraphs.intra_document_qa import (
        _clause_to_evidence,
        attach_exception_links,
        rehydrate_clause_texts,
    )
    ckg = ContractKGStore(store)
    base = clauses_of_function(ckg, contract_id, function)
    linked = attach_exception_links(base, ckg.exceptions_of_clause, contract_id=contract_id)
    texts = rehydrate_clause_texts(store, contract_id, linked)  # raw store: generic span rehydrate
    items = [_clause_to_evidence(c, texts.get(c.clause_id)) for c in linked][:_MAX_EVIDENCE]
    return [{"chunk_id": e.chunk_id, "text": e.text, "confidence": e.confidence} for e in items]


def _record(store, function, question, kind, idx):
    contract_id = _pick_contract(store, function)
    if not contract_id:
        print(f"[skip] no clauses of {function!r}", flush=True)
        return None
    ev = _evidence(store, contract_id, function)
    if not ev:
        print(f"[skip] {function!r}: empty evidence", flush=True)
        return None
    print(f"[{kind}] {idx:>2} {function:<26} {len(ev):>3} items  {contract_id[-38:]}", flush=True)
    return {
        "id": f"{kind}-{idx}",
        "kind": kind,  # "answerable" | "negative"
        "function_served": function,
        "question": question,
        "contract_id": contract_id,
        "evidence": ev,
        # --- answer key (SILVER: filled in by a human below; SME-vetted later) ---
        "answerable": None,          # true/false -- is the question answerable FROM this evidence?
        "must_include": [],          # key facts a correct answer must state
        "expected_citations": [],    # chunk_ids that support the answer (subset of evidence ids)
        "notes": "",
    }


def main() -> None:
    load_dotenv()
    from rag_wright.store.arcadedb import ArcadeDBStore

    store = ArcadeDBStore.from_env(database="ragwright_cuad_full")
    records = []
    for i, (fn, q) in enumerate(_ANSWERABLE):
        rec = _record(store, fn, q, "answerable", i)
        if rec:
            records.append(rec)
    for i, (fn, q) in enumerate(_NEGATIVES):
        rec = _record(store, fn, q, "negative", i)
        if rec:
            records.append(rec)
    store.close()

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps({"records": records}, indent=2, ensure_ascii=False), encoding="utf-8")
    ans = sum(r["kind"] == "answerable" for r in records)
    neg = sum(r["kind"] == "negative" for r in records)
    print(f"\n[snapshot] wrote {len(records)} records ({ans} answerable + {neg} negative) -> {_OUT}", flush=True)


if __name__ == "__main__":
    main()
