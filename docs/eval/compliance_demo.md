# Compliance demo — three legs (2026-08-11)

The compliance capability demonstrated across three regulatory domains, showing the same pipeline (ingest a
deontic rulebook/policy → a Requirement KG → assess a subject → a cited verdict) working from **advertising** to
**workplace safety** to a **customer's own policy** — with NO per-domain code. Reference for a future UI/video
demo. Each leg is reproducible; results are from live runs.

## Leg 1 — Advertising (FTC 16 CFR 255 endorsement rules) — the structured, ontology-enriched path
The tuned vertical: `claim_type` applicability routing + FTC substantiation/disclosure judge doctrine. Extracts an
ad's claims, matches each to the applicable requirements (`claim_type`), judges from the ad text, returns a cited
report. This is the one domain with full applicability enrichment (COMP-APPLIC-1 done).
- Ingest: `scripts/ingest_ftc_compliance.py` (FTC 255 → `ragwright_compliance`).
- Check: `scripts/compliance_engine_smoke.py` / `scripts/eval_compliance_gold.py` (gold: recall 1.00 on real FTC
  cases, the CC-7 tuning; `docs/eval/compliance_rung2_cc7.md`).
- MCP tool: `check_ad_compliance(ad_text, source_doc)`.

## Leg 2 — Workplace safety (OSHA 29 CFR 1904 injury/illness recordkeeping) — cross-domain generalization
A genuinely different domain (obligation-heavy recordkeeping vs advertising's prohibitions), ingested through the
SAME pipeline with zero code/ontology change (PROD-2 #4).
- Acquire + ingest: `scripts/acquire_ecfr.py` (TITLE=29 PART=1904 CHAPTER=XVII) → `scripts/ingest_compliance_prod2.py`.
- Result: 12 sections → 87 Requirement nodes, deontic 73 obligation / 6 prohibition / 8 permission (correctly
  obligation-heavy), 0 dead-lettered.
- Verdict (generic, no applicability ontology): `run_generic_compliance_verdict` on an "injury not logged"
  scenario → VIOLATION, cited to §1904.7/8/9; non-applicable rules correctly compliant. `docs/eval/prod2_readiness.md`.

## Leg 3 — A customer's OWN policy (community conduct / public-posting policy) — the generic "always answer" path
The core product scenario: a customer ingests a couple of their own policy documents, then checks an input
document (a blog post) against them — with NO domain ontology enrichment (COMP-VERDICT-GENERIC). Fixtures in
`eval/compliance_demo/` (policy adapted from the Contributor Covenant v2.1, CC BY 4.0; 3 synthetic posts with
known outcomes).

Reproduce: `uv run --no-sync python -m scripts.compliance_policy_demo`

**Live result** (real policy document → docling parse → 5 sections → 12 Requirement nodes; then 3 posts assessed):

| Subject post | Expected | Verdict | Reasoning (cited) |
|---|---|---|---|
| Derogatory attack + doxxing | violation | **violation** (6/0/0) | caught §4 publishing private info (home address + email) AND §3 derogatory personal attacks |
| Constructive feedback | compliant | **compliant** (0/0/6) | matched the "constructive feedback / respect differing opinions" obligations |
| Sharp but professional disagreement | compliant/needs_review | **needs_review** (1/5/0) | conservative escalation on an ambiguous case — not cleared, not hard-flagged |

All three matched their known labels. This validated, for the first time, (a) the document-path ingest of a REAL
policy (not a reconstructed regulation), (b) deontic extraction on the INFORMAL policy register, and (c) the
conservative needs_review escalation on the ambiguous subject. No enrichment was needed — the generic path
answered out of the box.

## What the three legs together show
- **One pipeline, three domains** (ads / safety / custom policy) with no per-domain code — the deontic core +
  generic verdict are domain-agnostic; advertising is the one structured-precision specialization.
- **The always-answer guarantee** (Leg 3, Leg 2's generic verdict): any customer's own policy yields a cited
  verdict without ontology work; enrichment (COMP-APPLIC-1) is an optional precision upgrade per domain.
- **Both-sided citations + conservative escalation** throughout — the trust product (no verdict without a cited
  requirement; ambiguity escalates to human review, never silently clears).
