"""LIVE smoke for the recall-first compliance actor gate (engine issue 0013 / ADR-0068).

Reproduces the issue's EXACT case against a real ArcadeDB Requirement KG + the real BGE embedder + the real
OpenRouter judge. The two actor labels are pinned to the reported extractions (rule `advertiser` vs assertion
`seller`) so the ACTOR GATE is exercised on the precise silent-drop case; the REAL JUDGE then confirms the verdict.

  BEFORE ADR-0068: § 3 (advertiser) never paired with assertion C (seller) -> pricing violation silently dropped.
  AFTER:           § 3 pairs with C (advertiser/seller overlap, same domain) -> the real judge flags the violation,
                   and report.gated_pairs stays empty (nothing withheld among the advertising roles).

Exits non-zero if the pair is not formed or the judge does not flag the violation (a live regression guard).

  uv run --no-sync python -m scripts.compliance_actor_gate_smoke
  COMPLIANCE_DB=ragwright_issue0013 uv run --no-sync python -m scripts.compliance_actor_gate_smoke
"""
from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv


def log(m: str) -> None:
    print(m, flush=True)


async def main() -> int:
    load_dotenv()
    os.environ.setdefault("RAG_SERVING", "openrouter")

    from rag_wright.capabilities.embedding import BGEM3Embedder
    from rag_wright.contracts.compliance import CheckableFact, Constraint, DeonticType, Requirement
    from rag_wright.contracts.provenance import ConfidenceTag
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.store.arcadedb import REQUIREMENT_TYPE, ArcadeDBStore
    from rag_wright.subgraphs.compliance_check import production_generic_compliance_check

    db = os.environ.get("COMPLIANCE_DB", "ragwright_issue0013")
    log(f"[smoke] 1/4 fresh ArcadeDB {db!r} + write the 3-rule policy")
    store = ArcadeDBStore.from_env(database=db, reset=True)
    store.ensure_compliance_schema()

    def _req(cite: str, deontic: str, actor: str, text: str) -> Requirement:
        return Requirement(requirement_id=f"ftc:{cite}", source="ftc", citation=cite,
                           deontic_type=DeonticType(deontic), actor=actor, requirement_text=text,
                           confidence=ConfidenceTag.EXTRACTED)

    reqs = [
        _req("§ 1", "prohibition", "advertiser",
             "An advertiser must not claim a product cures a disease unless the claim is substantiated."),
        _req("§ 2", "obligation", "endorser",
             "An endorser must clearly disclose a material connection to the advertiser."),
        _req("§ 3", "prohibition", "advertiser",
             "An advertiser must not describe a price as a discount unless the higher price was the usual selling price."),
    ]
    store.write_requirements(reqs)
    n = store._query(f"SELECT count(*) AS n FROM {REQUIREMENT_TYPE}")[0]["n"]
    log(f"[smoke]   -> {n} Requirement nodes (§1 advertiser/prohib, §2 endorser/oblig, §3 advertiser/prohib)")

    # the 3 subject assertions with the issue's EXACT extracted actors (A->advertiser via manufacturer, B endorser, C seller)
    facts = [
        CheckableFact(fact_id="s:0", source_doc="ad",
                      assertion_text="Our new supplement cures arthritis in just two weeks.",
                      scope=[Constraint(dimension="actor", value="manufacturer")]),
        CheckableFact(fact_id="s:1", source_doc="ad",
                      assertion_text="Dr. Miller recommends it to all her patients.",
                      scope=[Constraint(dimension="actor", value="endorser")]),
        CheckableFact(fact_id="s:2", source_doc="ad",
                      assertion_text="Was $99, now only $49 this week.",
                      scope=[Constraint(dimension="actor", value="seller")]),
    ]

    def facts_fn(_text: str, _source: str) -> list:
        return facts

    log("[smoke] 2/4 build the real generic check (BGE embedder + OpenRouter judge)")
    graph = production_generic_compliance_check(
        store, judge_model_id=model_for(ModelRole.STRUCTURED_REASONING), embedder=BGEM3Embedder(),
        sources=["ftc"], facts_fn=facts_fn)

    log("[smoke] 3/4 run the check over the 3 assertions (real judge calls)")
    out = await graph.ainvoke(
        {"subject_text": "\n\n".join(f.assertion_text for f in facts), "source_doc": "ad"})
    report = out["report"]

    log("[smoke] 4/4 RESULT")
    by_claim = {"s:0": "A cure-claim (advertiser)", "s:1": "B endorsement (endorser)", "s:2": "C pricing (seller)"}
    for f in report.findings:
        log(f"[smoke]   [{f.verdict.value:12}] {f.citation_requirement.split(' (')[0]:8} "
            f"vs {by_claim.get(f.claim_id, f.claim_id)} :: {f.rationale[:88]}")

    s3_on_c = [f for f in report.findings if f.requirement_id == "ftc:§ 3" and f.claim_id == "s:2"]
    log(f"\n[smoke]   § 3 (pricing) findings: "
        f"{[(f.claim_id, f.verdict.value) for f in report.findings if f.requirement_id == 'ftc:§ 3']}")
    log(f"[smoke]   gated_pairs (expect EMPTY -- all advertising roles compatible): {report.gated_pairs}")
    ok_paired = bool(s3_on_c)
    ok_violation = any(f.verdict.value == "violation" for f in s3_on_c)
    ok_gated_empty = report.gated_pairs == []
    log(f"\n[smoke]   PAIR FORMED (§3 x seller C): {ok_paired}   |   REAL JUDGE = VIOLATION on C: {ok_violation}"
        f"   |   gated_pairs empty: {ok_gated_empty}")
    store.close()

    if ok_paired and ok_violation and ok_gated_empty:
        log("[smoke]   PASS: the pricing violation is caught, nothing silently dropped (issue 0013 fixed).")
        return 0
    log("[smoke]   FAIL: the recall-first actor gate did not behave as expected (see the rationales above).")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
