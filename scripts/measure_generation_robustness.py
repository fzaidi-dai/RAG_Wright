"""Generation-robustness measurement: baseline (single structured call) vs B (reason->emit split) vs
C (best-of-N self-consistency), on the self-hosted vLLM-Granite stack.

Motivated by the finding that Granite-8B answer generation is NON-DETERMINISTIC near its abstain boundary
(the same answerable query abstained ~2/3 of runs at temperature 0). To isolate GENERATION-strategy variance,
the evidence for each query is built ONCE (target function -> serve clauses -> attach ADR-0044 carve-outs ->
rehydrate span text -> EvidenceItems) and the SAME evidence is fed to all three strategies R times. The
classifier is NOT in the loop (its own nondeterminism would confound the comparison).

Metrics per strategy, over answerable queries where evidence is non-empty (so an abstain is a FALSE abstain):
  - abstain_rate: fraction of (query x repeat) runs that abstained
  - flip_rate:    fraction of queries whose R runs are NOT unanimous (the flakiness we want to kill)
  - mean citations on the non-abstaining runs

Run on the A100 (streams X/N progress):
  ARCADEDB_HOST=<rw-arcadedb>.modal.run ARCADEDB_PORT=443 ARCADEDB_PROTOCOL=https ARCADEDB_USER=root \
    ARCADEDB_PASSWORD=rag_wright_dev_2026 ARCADEDB_DATABASE=ragwright_cuad_full \
    RAG_SERVING=vllm VLLM_BASE_URL=<a100>/v1 VLLM_API_KEY=rw-vllm-dev-key STACK_URL=<a100> \
    uv run --no-sync python -m scripts.measure_generation_robustness [REPEATS] [N_SAMPLES]
"""

from __future__ import annotations

import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

# (function, natural question) — each answerable from a contract that HAS clauses of that function. A
# spread of evidence sizes; the biggest-evidence functions (Audit Rights 59, Non-Compete 38) are left out to
# keep prompts and per-call latency sane -- the abstain flip shows on small evidence too (Governing Law).
_TARGETS = [
    ("Cap On Liability", "How is liability capped in this contract, and under what conditions?"),
    ("Governing Law", "What law governs this contract?"),
    ("Termination For Convenience", "Can this contract be terminated for convenience, and how?"),
]

_MAX_WORKERS = 3  # LOW on purpose: a single A100 inflates every request's latency under high concurrency,
# pushing calls past the 120s seam timeout into a 6x retry cascade. At ~3 concurrent, calls stay ~10-80s.


def _log(s: str = "") -> None:
    print(s, flush=True)


def _pick_contract(store, function: str) -> str | None:
    """The contract with the MOST clauses of `function` (richest, surely-answerable evidence)."""
    from rag_wright.store.arcadedb import CLAUSE_TYPE
    rows = store._query(f"SELECT clause_id FROM {CLAUSE_TYPE} WHERE function = '{function}'")
    if not rows:
        return None
    by_contract: dict[str, int] = defaultdict(int)
    for r in rows:
        by_contract[r["clause_id"].rsplit(":", 2)[0]] += 1
    return max(by_contract, key=by_contract.get)


def _build_evidence(store, contract_id: str, function: str):
    """Fixed evidence for one query: target-function clauses + their ADR-0044 carve-outs, rehydrated."""
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
    return [_clause_to_evidence(c, texts.get(c.clause_id)) for c in linked]


def main() -> None:
    load_dotenv()
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    n_samples = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    from rag_wright.capabilities.answer_generator import (
        SeamAnswerModel,
        SeamReasonModel,
        generate_answer,
        generate_answer_best_of_n,
        generate_answer_reasoned,
    )
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.store.arcadedb import ArcadeDBStore

    store = ArcadeDBStore.from_env(database="ragwright_cuad_full")
    llm = model_for(ModelRole.GENERAL)

    # 1. build the fixed-evidence answerable query set
    queries = []
    for function, question in _TARGETS:
        contract_id = _pick_contract(store, function)
        if not contract_id:
            _log(f"[skip] no clauses of function {function!r} in the KG")
            continue
        evidence = _build_evidence(store, contract_id, function)
        if not evidence:
            _log(f"[skip] {function!r} on {contract_id[-24:]}: empty evidence")
            continue
        queries.append({"function": function, "question": question,
                        "contract": contract_id, "evidence": evidence})
        _log(f"[query] {function:<28} {len(evidence):>3} evidence items  {contract_id[-40:]}")

    # 2. the three strategies (same fixed evidence each)
    strategies = {
        "baseline": lambda q: generate_answer(q["question"], q["evidence"], model=SeamAnswerModel(llm)),
        "B_reason_emit": lambda q: generate_answer_reasoned(
            q["question"], q["evidence"], reason_model=SeamReasonModel(llm), emit_model=SeamAnswerModel(llm)),
        "C_best_of_n": lambda q: generate_answer_best_of_n(
            q["question"], q["evidence"], model=SeamAnswerModel(llm, temperature=0.7), n=n_samples),
    }

    combos = [(name, qi, r) for name in strategies for qi in range(len(queries)) for r in range(repeats)]
    total = len(combos)
    _log(f"\n[run] {len(queries)} queries x {repeats} repeats x {len(strategies)} strategies = {total} runs "
         f"(C fans out {n_samples} samples each), max_workers={_MAX_WORKERS}\n")

    import time

    results: dict = {}
    lock_done = [0]

    def run_one(combo):
        name, qi, r = combo
        t0 = time.time()
        try:
            ans = strategies[name](queries[qi])
            return (name, qi, r, ans.abstained, len(ans.citations), time.time() - t0)
        except Exception as exc:  # noqa: BLE001 - a stuck/failed call counts as an abstain, never kills the run
            _log(f"[error] {name} {queries[qi]['function']} r{r}: {type(exc).__name__}: {str(exc)[:80]}")
            return (name, qi, r, True, 0, time.time() - t0)

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        for name, qi, r, abstained, ncit, dt in pool.map(run_one, combos):
            results.setdefault((name, qi), []).append((abstained, ncit))
            lock_done[0] += 1
            # per-combo HEARTBEAT: a visible line as each finishes (abstain/cites/latency), not sparse ticks
            verdict = "ABSTAIN" if abstained else f"answer({ncit}c)"
            _log(f"[{lock_done[0]:>2}/{total}] {name:<14} {queries[qi]['function']:<28} "
                 f"r{r} -> {verdict:<11} {dt:>5.0f}s")

    # 3. report
    _log("\n" + "=" * 90)
    _log(f"{'strategy':<16} {'abstain_rate':>13} {'flip_queries':>13} {'mean_cites':>11}")
    _log("-" * 90)
    for name in strategies:
        runs = [rec for qi in range(len(queries)) for rec in results.get((name, qi), [])]
        abstains = sum(a for a, _ in runs)
        flips = sum(
            0 < sum(a for a, _ in results.get((name, qi), [])) < repeats for qi in range(len(queries)))
        cites = [c for a, c in runs if not a]
        _log(f"{name:<16} {abstains}/{len(runs)} ({abstains/len(runs):>4.0%})   "
             f"{flips}/{len(queries)} ({flips/len(queries):>4.0%})   "
             f"{(sum(cites)/len(cites) if cites else 0):>8.1f}")
    _log("=" * 90)
    _log("\nPer-query abstain counts (out of %d repeats):" % repeats)
    _log(f"{'query':<30} " + "  ".join(f"{n:>13}" for n in strategies))
    for qi, q in enumerate(queries):
        cells = []
        for name in strategies:
            recs = results.get((name, qi), [])
            cells.append(f"{sum(a for a, _ in recs):>2}/{len(recs):<2} abst")
        _log(f"{q['function']:<30} " + "  ".join(f"{c:>13}" for c in cells))
    store.close()


if __name__ == "__main__":
    main()
