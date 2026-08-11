"""Compliance demo (the "custom policy" leg): ingest ONE real policy DOCUMENT -> a Requirement KG, then assess
input documents (blog posts) against it via the DOMAIN-AGNOSTIC generic verdict -- the "ingest a policy, check a
document" flow that needs NO domain ontology enrichment (COMP-VERDICT-GENERIC). Sibling legs: FTC ads
(scripts/compliance_engine_smoke.py / eval_compliance_gold.py) and OSHA safety
(scripts/ingest_compliance_prod2.py). See docs/eval/compliance_demo.md.

  uv run --no-sync python -m scripts.compliance_policy_demo
  POLICY=<path.md/pdf> SUBJECTS_DIR=<dir> COMPLIANCE_DB=ragwright_policy_demo uv run --no-sync python -m scripts.compliance_policy_demo
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def log(m: str) -> None:
    print(m, flush=True)


def main() -> None:
    load_dotenv()
    os.environ.setdefault("RAG_SERVING", "openrouter")
    from rag_wright.capabilities.dg_extraction import default_extraction_model
    from rag_wright.capabilities.remote_encoders import query_embedder
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.store.arcadedb import REQUIREMENT_TYPE, ArcadeDBStore
    from rag_wright.subgraphs.compliance_check import run_generic_compliance_verdict
    from rag_wright.subgraphs.compliance_ingestion import run_compliance_document_ingestion

    root = Path("eval/compliance_demo")
    policy = Path(os.environ.get("POLICY", root / "policy" / "community_conduct_policy.md"))
    subjects_dir = Path(os.environ.get("SUBJECTS_DIR", root / "subjects"))
    db = os.environ.get("COMPLIANCE_DB", "ragwright_policy_demo")
    source = os.environ.get("SOURCE", "Community Conduct Policy")

    store = ArcadeDBStore.from_env(database=db, reset=os.environ.get("RESET", "1") == "1")
    model = default_extraction_model("requirement-extract", "ibm-granite/granite-4.1-8b")

    # 1. INGEST the policy DOCUMENT (bytes -> docling parse -> heading-split sections -> Requirement KG)
    data = policy.read_bytes()
    log(f"[demo] STEP 1: ingest policy document {policy.name} ({len(data)} bytes) -> {db!r}")
    report = run_compliance_document_ingestion(policy.name, data, store, model=model, source=source)
    n = store._query(f"SELECT count(*) AS n FROM {REQUIREMENT_TYPE}")[0]["n"]
    log(f"[demo]   -> {report.documents_ingested} sections, {len(report.dead_lettered)} dead-lettered, "
        f"{n} Requirement nodes")

    # 2. ASSESS each subject document against the policy KG (generic verdict; no ontology enrichment)
    judge_id = model_for(ModelRole.STRUCTURED_REASONING)
    embedder = query_embedder()
    subjects = sorted(subjects_dir.glob("*.txt"))
    log(f"\n[demo] STEP 2: assess {len(subjects)} document(s) against the {n}-requirement policy KG")
    for subj in subjects:
        text = subj.read_text(encoding="utf-8").strip()
        rep = run_generic_compliance_verdict(
            text, subj.stem, store=store, judge_model_id=judge_id, embedder=embedder, k=6)
        breakdown = {k: rep.summary.get(k, 0) for k in ("violation", "needs_review", "compliant")}
        log(f"\n[demo] === {subj.name} ===")
        log(f"[demo]   verdict: {rep.verdict.value} | {breakdown}")
        for f in rep.findings[:4]:
            log(f"[demo]     [{f.verdict.value}] {f.citation_requirement[:45]} :: {f.rationale[:95]}")
    store.close()
    log("\n[demo] DONE: ingest-a-policy-doc -> assess-input-docs works end to end (no ontology enrichment needed).")


if __name__ == "__main__":
    main()
