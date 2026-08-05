"""RG-3/RG-4 (compliance rung 2, roadmap C-7): score the engine on the first-pass FTC ad-claims gold.

Runs `compliance_check` (full 155-rule KG, CC-8 narrowing on, LLM-CALL-TIMEOUT so it can't hang) over each gold
case and scores the VIOLATION class -- reporting PRECISION (alert fatigue) and RECALL (missed violations)
SEPARATELY, per roadmap §13.4. Ad-level predicted = "violation" if the report has any violation finding.

FIRST-PASS gold (expert-review pending) -> directional numbers, not a shippable legal benchmark. Also broken out
by provenance (real FTC cases vs constructed compliant) so the thin/constructed negative class is visible.

  RAG_SERVING=openrouter EMBED_DEVICE=cpu uv run --no-sync python -m scripts.eval_compliance_gold
"""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    from rag_wright.capabilities.dg_extraction import default_extraction_model
    from rag_wright.capabilities.embedding import BGEM3Embedder
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.subgraphs.compliance_check import production_compliance_check

    gold = json.loads(Path("data/compliance/gold_cases/manifest.json").read_text())["cases"]
    store = ArcadeDBStore.from_env(database="ragwright_compliance")
    print(f"[gold-eval] {len(gold)} cases | {len(store.all_requirements())} requirements | narrowing k=5", flush=True)
    graph = production_compliance_check(
        store, extract_model=default_extraction_model("claim-extract", "ibm-granite/granite-4.1-8b"),
        judge_model_id=model_for(ModelRole.STRUCTURED_REASONING), embedder=BGEM3Embedder(), k=5)

    rows = []
    for i, c in enumerate(gold, 1):
        text = Path(f"data/compliance/gold_cases/{c['id']}.txt").read_text()
        report = graph.invoke({"subject_text": text, "source_doc": c["id"]})["report"]
        predicted = "violation" if report.summary.get("violation", 0) > 0 else "compliant"
        rows.append({**c, "predicted": predicted, "summary": report.summary})
        mark = "OK " if predicted == c["expected_verdict"] else "XX "
        print(f"[gold-eval] {i}/{len(gold)} {mark} {c['id'][:34]:34} exp={c['expected_verdict']:9} "
              f"pred={predicted:9} {report.summary}", flush=True)
    store.close()

    def _score(subset):
        tp = sum(r["expected_verdict"] == "violation" and r["predicted"] == "violation" for r in subset)
        fn = sum(r["expected_verdict"] == "violation" and r["predicted"] == "compliant" for r in subset)
        fp = sum(r["expected_verdict"] == "compliant" and r["predicted"] == "violation" for r in subset)
        tn = sum(r["expected_verdict"] == "compliant" and r["predicted"] == "compliant" for r in subset)
        recall = tp / (tp + fn) if (tp + fn) else float("nan")
        precision = tp / (tp + fp) if (tp + fp) else float("nan")
        acc = (tp + tn) / len(subset) if subset else float("nan")
        return tp, fn, fp, tn, recall, precision, acc

    print("\n=== RG-4: violation-class scores (first-pass gold; directional) ===", flush=True)
    for name, subset in (("ALL", rows),
                         ("real FTC cases", [r for r in rows if r["provenance"] == "ftc_case"]),
                         ("constructed", [r for r in rows if r["provenance"] == "constructed"])):
        tp, fn, fp, tn, rec, prec, acc = _score(subset)
        print(f"  {name:16} n={len(subset):2}  TP={tp} FN={fn} FP={fp} TN={tn}  "
              f"recall={rec:.2f} (missed viol) precision={prec:.2f} (alert fatigue) acc={acc:.2f}", flush=True)
    print("[gold-eval] DONE", flush=True)


if __name__ == "__main__":
    main()
