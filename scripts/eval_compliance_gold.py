"""RG-3/RG-4 (compliance rung 2, roadmap C-7): score the engine on the first-pass FTC ad-claims gold.

Runs `compliance_check` (full 155-rule KG, CC-8 narrowing on, LLM-CALL-TIMEOUT so it can't hang) over each gold
case and scores the VIOLATION class -- reporting PRECISION (alert fatigue) and RECALL (missed violations)
SEPARATELY, per roadmap §13.4. Ad-level predicted = "violation" if the report has any violation finding.

FIRST-PASS gold (expert-review pending) -> directional numbers, not a shippable legal benchmark. Also broken out
by provenance (real FTC cases vs constructed compliant) so the thin/constructed negative class is visible.

The store, judge/extraction models, and the CC-8 narrowing embedder are all env-selected, so the SAME script
runs local or fully on Modal (KG on the rw-arcadedb Volume + Granite/BGE on the A100), MODAL-COMPLIANCE.

  # local (dev):
  RAG_SERVING=openrouter EMBED_DEVICE=cpu uv run --no-sync python -m scripts.eval_compliance_gold
  # fully on Modal (A100 Granite + A100 BGE + Modal compliance KG):
  ARCADEDB_HOST=farhan-zaidi--rw-arcadedb-serve.modal.run ARCADEDB_PORT=443 ARCADEDB_PROTOCOL=https \
    ARCADEDB_USER=root ARCADEDB_PASSWORD=rag_wright_dev_2026 \
    RAG_SERVING=vllm VLLM_BASE_URL=<a100>/v1 VLLM_API_KEY=rw-vllm-dev-key STACK_URL=<a100> \
    uv run --no-sync python -m scripts.eval_compliance_gold
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv


async def main() -> None:
    load_dotenv()
    from rag_wright.capabilities.dg_extraction import default_extraction_model
    from rag_wright.capabilities.remote_encoders import query_embedder
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.subgraphs.compliance_check import production_compliance_check

    gold = json.loads(Path("data/compliance/gold_cases/manifest.json").read_text())["cases"]
    store = ArcadeDBStore.from_env(database="ragwright_compliance")
    print(f"[gold-eval] {len(gold)} cases | {len(store.all_requirements())} requirements | narrowing k=5", flush=True)
    graph = production_compliance_check(
        store, extract_model=default_extraction_model("claim-extract", "ibm-granite/granite-4.1-8b"),
        judge_model_id=model_for(ModelRole.STRUCTURED_REASONING), embedder=query_embedder(), k=5)

    rows = []
    for i, c in enumerate(gold, 1):
        text = Path(f"data/compliance/gold_cases/{c['id']}.txt").read_text()
        report = (await graph.ainvoke({"subject_text": text, "source_doc": c["id"]}))["report"]
        s = report.summary
        predicted = report.verdict.value  # RG-5 ad-level rollup: >=2 violations -> violation; else any -> needs_review
        rows.append({**c, "predicted": predicted, "summary": s})
        # a real violation CLEARED (predicted compliant) is the true miss; escalation (needs_review) is not a miss
        bad = (c["expected_verdict"] == "violation" and predicted == "compliant") or \
              (c["expected_verdict"] == "compliant" and predicted == "violation")
        print(f"[gold-eval] {i}/{len(gold)} {'XX ' if bad else 'ok '} {c['id'][:34]:34} "
              f"exp={c['expected_verdict']:9} pred={predicted:12} {s}", flush=True)
    store.close()

    def _report(name, subset):
        viol = [r for r in subset if r["expected_verdict"] == "violation"]
        comp = [r for r in subset if r["expected_verdict"] == "compliant"]
        # violation side: flagged (hard) / escalated (needs_review) / CLEARED (the dangerous miss)
        v_flag = sum(r["predicted"] == "violation" for r in viol)
        v_esc = sum(r["predicted"] == "needs_review" for r in viol)
        v_clear = sum(r["predicted"] == "compliant" for r in viol)  # <-- true missed violation
        # compliant side: cleared (good) / escalated (soft) / flagged (hard false alarm)
        c_clear = sum(r["predicted"] == "compliant" for r in comp)
        c_esc = sum(r["predicted"] == "needs_review" for r in comp)
        c_flag = sum(r["predicted"] == "violation" for r in comp)  # <-- hard false positive (alert fatigue)
        clearance_safety = 1 - v_clear / len(viol) if viol else float("nan")  # never CLEAR a real violation
        hard_fp_rate = c_flag / len(comp) if comp else float("nan")
        hard_prec = v_flag / (v_flag + c_flag) if (v_flag + c_flag) else float("nan")  # of hard 'violation' calls
        print(f"  {name:16} viol[flag={v_flag} esc={v_esc} CLEARED={v_clear}] comp[clear={c_clear} esc={c_esc} "
              f"FLAGGED={c_flag}]  clearance-safety={clearance_safety:.2f} hard-viol-precision={hard_prec:.2f} "
              f"hard-FP-rate={hard_fp_rate:.2f}", flush=True)

    print("\n=== RG-4 (3-way, procedural fix): CLEARED = missed violation; needs_review = honest escalation ===", flush=True)
    _report("ALL", rows)
    _report("real FTC+NAD", [r for r in rows if r["provenance"] != "constructed"])
    _report("constructed", [r for r in rows if r["provenance"] == "constructed"])
    print("[gold-eval] DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
