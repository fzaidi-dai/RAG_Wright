"""Score generation strategies against the Leg-A SILVER set (tests/fixtures/leg_a_silver/).

Validates the PREMISE of lever A -- is a stronger model (Gemma 4) actually better than Granite-8B? -- before
building any escalation machinery. The silver evidence is frozen, so this needs NO KG and NO A100: both models
run through the seam on OpenRouter (RAG_SERVING=openrouter), so it is a clean same-backend model comparison.

Metrics vs the silver key (generate_answer already drops fabricated citations + coerces uncited->abstain, so a
non-abstaining answer always carries >=1 valid in-evidence citation):
  - RECALL     = fraction of ANSWERABLE runs that answered (did not abstain).      false abstain = A's target
  - PRECISION  = fraction of UNANSWERABLE runs that abstained.                      answering = fabrication
  - flip       = fraction of records whose R repeats are not unanimous (stability)
Fact coverage (does the answer state must_include) is left to a manual spot-check of the dumped answers.

  OPENROUTER_API_KEY=... (from .env) \
    uv run --no-sync python -m scripts.measure_silver [REPEATS]
"""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

_FIXTURE = Path("tests/fixtures/leg_a_silver/evidence_snapshot.json")
_DUMP = Path("/private/tmp/claude-501/-Users-farhan-work-RAG-Wright/"
             "46292cd1-831b-4882-9a22-4954123663a0/scratchpad/silver_answers.txt")

_GRANITE = "ibm-granite/granite-4.1-8b"
_GEMMA4 = "google/gemma-4-31b-it"
# OpenRouter batches fine at 8; a SINGLE self-hosted A100 doing json_schema guided decoding wants LOW
# concurrency (xgrammar workspace per in-flight request -> OOM risk), so make it env-tunable (SILVER_WORKERS).
_MAX_WORKERS = int(os.environ["SILVER_WORKERS"]) if os.environ.get("SILVER_WORKERS") else 8


def _log(s: str = "") -> None:
    print(s, flush=True)


def main() -> None:
    load_dotenv()
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    # Backend + models are ENV-DRIVEN so this scores OpenRouter OR a self-hosted vLLM endpoint unchanged:
    #   RAG_SERVING (default openrouter) selects the backend (set vllm + VLLM_BASE_URL for self-hosted);
    #   SILVER_MODELS="label:model_id,label2:model_id2" picks the strategies (default = OpenRouter granite vs gemma).
    os.environ.setdefault("RAG_SERVING", "openrouter")
    spec = os.environ.get("SILVER_MODELS", f"granite-base:{_GRANITE},gemma4-base:{_GEMMA4}")

    from rag_wright.capabilities.answer_generator import (
        EvidenceItem,
        answer_model_for,
        generate_answer,
    )

    records = json.loads(_FIXTURE.read_text(encoding="utf-8"))["records"]
    for r in records:
        r["_ev"] = [EvidenceItem(chunk_id=e["chunk_id"], text=e["text"], confidence=e["confidence"])
                    for e in r["evidence"]]
    _log(f"[silver] {len(records)} records "
         f"({sum(r['answerable'] for r in records)} answerable + {sum(not r['answerable'] for r in records)} not)")

    max_tokens = int(os.environ["SILVER_MAX_TOKENS"]) if os.environ.get("SILVER_MAX_TOKENS") else None
    strategies = {}
    for part in spec.split(","):
        label, mid = part.split(":", 1)
        # answer_model_for picks per-profile: client-side tag-parse for self-hosted Gemma, the seam otherwise
        strategies[label.strip()] = answer_model_for(mid.strip(), max_tokens=max_tokens)
    _log(f"[backend] RAG_SERVING={os.environ['RAG_SERVING']}  strategies={list(strategies)}  max_tokens={max_tokens}")
    combos = [(name, ri, rep) for name in strategies for ri in range(len(records)) for rep in range(repeats)]
    total = len(combos)
    _log(f"[run] {len(strategies)} strategies x {len(records)} records x {repeats} repeats = {total} runs\n")

    results: dict = {}
    answers: dict = {}  # (name, ri) -> a sample answer for spot-checking
    latencies: list = []  # (name, dt_seconds, err) per call -> per-call latency stats
    done = [0]

    def run_one(combo):
        name, ri, rep = combo
        rec = records[ri]
        t0 = time.time()
        try:
            ans = generate_answer(rec["question"], rec["_ev"], model=strategies[name])
            return (name, ri, ans.abstained, len(ans.citations), ans.answer, time.time() - t0, None)
        except Exception as exc:  # noqa: BLE001
            return (name, ri, True, 0, "", time.time() - t0, f"{type(exc).__name__}: {str(exc)[:80]}")

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        for name, ri, abst, ncit, ans_text, dt, err in pool.map(run_one, combos):
            results.setdefault((name, ri), []).append(abst)
            latencies.append((name, dt, err))
            if (name, ri) not in answers and not abst:
                answers[(name, ri)] = ans_text
            done[0] += 1
            rec = records[ri]
            verdict = "ERR" if err else ("ABSTAIN" if abst else f"answer({ncit}c)")
            tag = "ANS" if rec["answerable"] else "NO "
            _log(f"[{done[0]:>3}/{total}] {name:<13} {rec['id']:<13}[{tag}] -> {verdict:<11} {dt:>7.2f}s"
                 + (f"  {err}" if err else ""))

    # --- score ---
    ans_ids = [ri for ri, r in enumerate(records) if r["answerable"]]
    neg_ids = [ri for ri, r in enumerate(records) if not r["answerable"]]
    _log("\n" + "=" * 78)
    _log(f"{'strategy':<14} {'RECALL(answered/ans)':>22} {'PRECISION(abst/neg)':>22} {'flip':>8}")
    _log("-" * 78)
    for name in strategies:
        ans_runs = [a for ri in ans_ids for a in results[(name, ri)]]
        neg_runs = [a for ri in neg_ids for a in results[(name, ri)]]
        answered = sum(not a for a in ans_runs)
        abstained_neg = sum(neg_runs)
        flips = sum(0 < sum(results[(name, ri)]) < repeats for ri in range(len(records)))
        _log(f"{name:<14} {answered}/{len(ans_runs)} ({answered/len(ans_runs):>4.0%})        "
             f"{abstained_neg}/{len(neg_runs)} ({abstained_neg/len(neg_runs):>4.0%})       "
             f"{flips}/{len(records)}")
    _log("=" * 78)

    # --- per-call latency (seconds), successful calls only ---
    import statistics
    _log("\n=== per-call latency (seconds), SUCCESSFUL calls only ===")
    _log(f"{'strategy':<16} {'n':>4} {'min':>8} {'max':>8} {'avg':>8} {'median':>8}  errors")
    for name in strategies:
        lat = sorted(dt for (n, dt, err) in latencies if n == name and err is None)
        errs = [err for (n, dt, err) in latencies if n == name and err is not None]
        if lat:
            _log(f"{name:<16} {len(lat):>4} {min(lat):>8.2f} {max(lat):>8.2f} "
                 f"{sum(lat)/len(lat):>8.2f} {statistics.median(lat):>8.2f}  {len(errs)}")
        else:
            _log(f"{name:<16} {0:>4} {'-':>8} {'-':>8} {'-':>8} {'-':>8}  {len(errs)} (all failed)")
        for e in errs[:3]:
            _log(f"   err: {e}")
    _log("=" * 78)

    # per-record recall (answered-count / repeats), answerable rows only
    _log("\nPer-answerable-record answered-count (out of %d):" % repeats)
    _log(f"{'record':<28} " + "  ".join(f"{n:>12}" for n in strategies))
    for ri in ans_ids:
        cells = [f"{repeats - sum(results[(name, ri)])}/{repeats}" for name in strategies]
        _log(f"{records[ri]['function_served']:<28} " + "  ".join(f"{c:>12}" for c in cells))

    # dump sample answers for a manual fact-coverage spot-check
    _DUMP.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for ri, r in enumerate(records):
        lines.append(f"\n{'='*90}\n[{r['id']}] {r['function_served']} | answerable={r['answerable']}\nQ: {r['question']}")
        lines.append(f"must_include: {r['must_include']}")
        for name in strategies:
            a = answers.get((name, ri), "(abstained in all repeats)")
            lines.append(f"\n--- {name} ---\n{a[:900]}")
    _DUMP.write_text("\n".join(lines), encoding="utf-8")
    _log(f"\n[dump] sample answers for spot-check -> {_DUMP}")


if __name__ == "__main__":
    main()
