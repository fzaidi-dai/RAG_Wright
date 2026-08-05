"""CC-7 (compliance §13.4, Track-1): the end-to-end engine smoke.

Runs `compliance_check` over the labeled CC-0 ad samples against the `ragwright_compliance` Requirement KG and
scores the ad-level signal ("any VIOLATION finding") vs each sample's manifest `expected_signal`. A tiny but
real end-to-end gate (the Track-1 proxy is ContractNLI, eval/contractnli_judge.py; rung-2 adds real ad gold).

Capped to a few §255.5 disclosure requirements to sidestep the CC-6 cross-product (the semantic-narrowing item).

  RAG_SERVING=openrouter uv run --no-sync python -m scripts.compliance_engine_smoke
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    os.environ.setdefault("ARCADEDB_DATABASE", "ragwright_compliance")
    from rag_wright.capabilities.claim_extraction import claim_extraction
    from rag_wright.capabilities.compliance_judgment import build_compliance_judge_fn
    from rag_wright.capabilities.dg_extraction import default_extraction_model
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.subgraphs.compliance_check import _requirement_from_row, build_compliance_check

    store = ArcadeDBStore.from_env(database="ragwright_compliance")
    reqs = [_requirement_from_row(r) for r in store.all_requirements()
            if r["citation"] == "§ 255.5" and "disclos" in r["requirement_text"].lower()][:3]
    emodel = default_extraction_model("claim-extract", "ibm-granite/granite-4.1-8b")
    graph = build_compliance_check(
        claims_fn=lambda text, source: claim_extraction(text, model=emodel, source_doc=source)[:3],
        requirements_fn=lambda: reqs,
        judge_fn=build_compliance_judge_fn(model_for(ModelRole.STRUCTURED_REASONING)))

    samples = json.loads(Path("data/compliance/subject_samples/manifest.json").read_text())["samples"]
    print(f"[engine] compliance_check vs manifest ({len(reqs)} §255.5 rules)\n", flush=True)
    correct = 0
    for s in samples:
        name = s["file"].replace(".txt", "")
        text = Path(f"data/compliance/subject_samples/{name}.txt").read_text()
        report = graph.invoke({"subject_text": text, "source_doc": name})["report"]
        predicted = "violation" if report.summary.get("violation", 0) > 0 else "compliant"
        ok = predicted == s["expected_signal"]
        correct += ok
        print(f"[engine] {'OK ' if ok else 'XX '} {name[:34]:34} "
              f"expected={s['expected_signal']:9} predicted={predicted:9} {report.summary}", flush=True)
    print(f"\n[engine] ad-level: {correct}/{len(samples)} correct", flush=True)
    store.close()


if __name__ == "__main__":
    main()
