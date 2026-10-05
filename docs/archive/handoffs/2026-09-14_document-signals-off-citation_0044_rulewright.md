# RuleWright handoff: `[DOCUMENT SIGNALS]` scaffolding is off the citation (issue 0044)

Date: 2026-09-14 · on `origin/main` · ADR-0106 · **Additive contract change (back-compatible defaults). `citation_claim` no longer contains engine scaffolding.**

---

## Your finding, fixed

On the ad obligation path, the DEON-8 signal line (`[DOCUMENT SIGNALS] disclosures present…`) was appended to the evidence bundle and became `CheckableFact.assertion_text`, which `assemble_finding` renders as `citation_claim` — so it surfaced under your "IN YOUR DOCUMENT" heading as if the user had written it. Now the signals are **judge-only** and the citation is **document text only**.

We did **both** of your asks (option 1 + option 2), because option 1 alone would still leave the citation as an assembled multi-span bundle that reads as one quote:

1. **Signals off the citation.** `CheckableFact` gains `document_signals: str` (default `""`). The obligation builder puts the DEON-8 line there, not in `assertion_text`. The judge still sees it (`_base_tail` appends `document_signals`, so the judge prompt is byte-identical) — only the citation changed.
2. **Verbatim vs assembled, flagged.** `ComplianceFinding` gains **`citation_claim_kind: "verbatim" | "assembled"`** (from a new `CheckableFact.citation_kind`). The obligation bundle — the top-N relevant spans joined by `\n\n` — is `"assembled"`; a single-span claim is `"verbatim"`. So you can render assembled evidence differently instead of quoting it, **without parsing prose or matching a marker you don't own**.

## What changes for you

- `finding.citation_claim` on the ad obligation path is now the document text only. The `[DOCUMENT SIGNALS]` suffix is gone.
- Read `finding.citation_claim_kind`: `"verbatim"` → one span, safe to quote as the user's words; `"assembled"` → the top-N evidence bundle, render it as evidence/excerpt, not a verbatim quote. This applies to the **generic path too** (where a bundled citation was already assembled — now truthfully labelled).
- Everything defaults `"verbatim"` and `document_signals=""`, so nothing else moves; the generic path and existing findings are unchanged.

## Where the signals went (if you need them)

They are on the fact, not the finding: `CheckableFact.document_signals` carries the DEON-8 disclosure/evidence union for the judge. It is not on `ComplianceFinding` (a finding is the verdict + citations); if you want the signals surfaced to a user as *context* (distinct from a quote), tell us and we'll decide whether they belong on the finding as their own labelled field rather than inferred.

Reference: ADR-0106, `contracts/compliance.py` (`CheckableFact.document_signals` / `.citation_kind`, `ComplianceFinding.citation_claim_kind`), `subgraphs/compliance_check.py::build_obligation_pairs_fn`, `capabilities/compliance_judgment.py` (`_base_tail`, `assemble_finding`), engine issue `docs/engine-issues/0044-...`. Full suite: 1579 passed.
