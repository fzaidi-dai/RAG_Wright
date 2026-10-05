# ADR-0119: Jev typed-decision model for the compliance closed-set decisions

Status: accepted (2026-10-05)
Related: ADR-0115/0116 (classifier-only Step-3a for contracts), ADR-0117/0118 (capability runtime), the `setfit`
and `laya` skills, `docs/proposals/compliance-ingest-classifier-decomposition.md`.

## Context

The CIC arc set out to replace per-field ingest LLM calls on the compliance side with local classifiers, the way
contracts moved Step-3a to a SetFit/Laya fleet (ADR-0115/0116). We pressure-tested the hardest field first, the
**operative-rule gate** ("is this span a binding rule, vs a definition / illustrative example / cross-reference /
negated requirement?"), as a rigorous data-science project:

- A local **SetFit** classifier plateaued at **~0.82** on a trustworthy gold (built via two independent rubric
  passes, inter-annotator agreement 0.905, disagreements adjudicated). Its errors were **confident and
  jointly-agreed** across backbones, so **no uncertainty signal (confidence, ensemble disagreement, margin) could
  route them** and **calibration is monotonic** so it does not change routing. A confidence-routed hybrid could
  not cheaply exceed 0.82.
- **Laya** (the open ModernBERT-large decision head) did not beat SetFit here (0.70–0.74).
- The task is **label-ambiguity-bound** (~0.90 inter-annotator) and **data-starved** (small real corpus; the one
  public human-labeled negative set, LEXDEMOD, is gone). Synthetic rounds oscillated 0.78–0.85.
- A per-call **LLM with the rubric reached 0.92** — i.e. the label ceiling.

We then evaluated **Jev** (TypeSafe's "System-1" typed-decision model: calibrated `noul`/`choice`/`score` answers,
no text, ~70–500 ms, available on the **OpenRouter Decisions API**, `typesafe/jev-1.13`, $0.042/M input, output
free). Laya is its open-weight analog. Results on the trustworthy gold / teacher-labelled fields:

| decision | Jev | SetFit | per-call LLM |
|---|---|---|---|
| operative-rule gate (binary, zero-shot) | **0.92** (rule-recall 1.00) | 0.82 | 0.92 |
| claim_types (8-way multi-label, few-shot, thr 0.6) | **micro-F1 0.853**, exact 0.75 | — (data-starved) | reference |
| actor (single-label `choice`, few-shot) | **0.90** | — | reference |

Jev's probabilities are **genuinely calibrated** (its errors cluster near 0.5 and route cleanly), unlike SetFit's
overconfident head. Few-shot exemplars placed in the `state` help the multi-value fields (claim_types +0.03, actor
+0.02) but bias the binary gate (operative drops to 0.84), so: **few-shot for choice/multi-label, zero-shot for
the binary gate.** Total OpenRouter spend across the whole Jev investigation: <$0.02.

## Decision

1. **Adopt Jev as the decision layer for the compliance closed-set decisions** — the operative-rule gate
   (zero-shot `noul`), claim_types (few-shot 8×`noul`), and actor (few-shot `choice`). It reaches the per-call
   LLM's accuracy (the label ceiling) while being calibrated, training-free, fast, and ~$0.00002/call.
2. **Register it as a generic, domain-free `jev_decision` `model` capability** (`capabilities/jev_decision.py`,
   impl_ref on the manifest, canonical slug). It is **I/O-bound → an ASYNC model cap**, invoked via
   `api.ainvoke_model` (the EP-API-7 async surface); the sync `invoke_model` refuses it. It forwards a Decisions-API
   request (`state` + typed `questions`) and returns the calibrated answers; the domain supplies the questions /
   criteria (reference pack / ttl), keeping the capability domain-neutral.
3. **Retire the local-classifier path for the compliance operative gate** (SetFit/Laya). The reliable gold, the
   reusable data pipeline, and the finding stay on record; if an expert later supplies real in-domain labels at
   scale, a local model can be revisited to cut the (already small) query cost.
4. **Laya remains the open-weight / on-prem fallback** when a managed API is not acceptable (it needs fine-tuning;
   Jev wins zero-shot).

This is compliance-specific and does NOT reverse ADR-0115/0116: contracts have high per-provision volume + a large
labelled corpus (CUAD), where a trained local fleet pays off; compliance ingest is low-volume (tens of calls per
short regulation) and data-starved, where a calibrated zero/few-shot decision model is the better tool.

## Consequences

- Compliance closed-set fields are decided by `jev_decision` (via `ainvoke_model`), not by the un-decomposed
  per-section LLM extraction nor a local classifier. The question-sets (operative rubric, the 8 claim-type `noul`s,
  the actor `choice`) live in the reference/domain layer, not the capability.
- A hard dependency on a managed API (OpenRouter → TypeSafe) for this decision; mitigated by the Laya fallback and
  by the capability abstraction (swapping the impl_ref is the only change).
- `OPENROUTER_API_KEY` gates it; the live test and callers are skipped without it.
- The CIC-0 deterministic decomposition (split + cue-rule + verbatim) and the `deontic_type` cue-rule stay useful
  as the *span-production* front end that feeds `jev_decision`; they were never the blocker.
