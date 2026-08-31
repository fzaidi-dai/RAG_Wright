# ADR-0065: Deontic type + actor as the primary applicability gates (query-side compliance), FTC tables demoted to curated overrides

Date: 2026-08-31
Status: Accepted (implemented; DEON-1..10, issue 0012)

Extends **ADR-0040** (neuro-symbolic extraction fidelity: the domain rules live in the ontology / typed fields,
not in code; symbolic layers gate, the LLM reasons over the irreducible residual). ADR-0040 applied this on the
**ingestion side** (SHACL over the extracted A-Box). This ADR applies the same axiom on the **query side**
(compliance checking). Related: **ADR-0044** (the `IS_EXCEPTION_TO` carve-out pattern, reused here on the
requirement side), **ADR-0045** (client-side structured output).

## Context

The compliance selector narrowed requirements by looking up **FTC 16 CFR 255 section numbers** in two hardcoded
tables (`SECTION_RULE_SCOPE`, `SECTION_CLAIM_TYPES`). A customer policy (`§1/§2/§3`) matches neither, so the KG's
already-typed rule fields (`deontic_type`, `actor`, `applicability_scope`) were **inert**: every rule collapsed to
`CONTENT` (the always-include disclosure guarantee never fired — silent recall loss at scale), `applies_to`
returned true for everything (no claim-type narrowing), and every obligation was judged **per assertion** — an
obligation cannot be answered per sentence ("is it satisfied *anywhere*?"), so it degraded to "unclear", and cost
was `assertions × rules`. This violated the neuro-symbolic axiom: the LLM brute-forced every pair instead of the
symbolic layer gating and the LLM reasoning once over bounded, grounded context (issue 0012, filed by RuleWright).

## Decision

Make the KG's typed rule fields **load-bearing symbolic gates**; reserve the LLM for one grounded reasoning call
over retrieved evidence. Nothing depends on the policy being FTC.

1. **Deontic routing (rule side).** `deontic_route(requirement)` derives the judge path from `deontic_type`, not
   a section number: **obligation** → judged ONCE over bounded evidence (always-include / CONTEXT); **prohibition**
   → per-assertion where the subject asserts something related (CONTENT); **permission** → excluded from
   violation-judging (see 4); **ambiguous** (off-vocab deontic, coerced + flagged) → recall-first per-assertion.
   `SECTION_RULE_SCOPE` is demoted to a **curated override** a domain pack may still pin.

2. **Obligation = judged once over bounded evidence, NOT the whole document in one prompt.** The obligation is
   asked once *for the document* (the unit of the question), over a symbolic + vector-narrowed context: the actor
   gate (KG, zero LLM) → top-N relevant passages up to a char budget (embeddings, zero LLM) → **one** LLM call.
   Cost = obligations-passing-the-gate × 1, over small contexts — not `assertions × rules`.

3. **Actor + constraint gates (both O/F and claim-type).** A rule's `actor` and `applicability_scope` gate which
   rules a subject is judged against, via the dimension-agnostic `constraint_applies` + a canonical-vocabulary
   actor match (`canonical_actor` + a generic role-synonym map; **exact match on the canonical role, recall-first**
   — an empirically-necessary choice: no BGE cosine threshold separates same-role from different-role single
   words, e.g. `advertiser~manufacturer 0.63 < employer~manufacturer 0.68`). An obligation whose bound actor is
   **absent** from the document is skipped entirely (zero LLM). The advertising `claim_type` routing is the ad-path
   analog: `applicable_claim_types` is **override-based** — FTC `SECTION_CLAIM_TYPES` wins when pinned (FTC
   byte-identical), else the extracted `claim_type` scope **narrows** for a customer policy.

4. **Permission-as-defense (ADR-0044 pattern, requirement side).** A permission is not judged standalone; where it
   is a conditional carve-out it is **linked** (same policy source + actor-compatible, ranked by proximity, capped
   — rank+cap, no fragile threshold) to the O/F rule it modifies and passed to that rule's judge as **structured
   exception context**, so a legitimate carve-out is not a false violation. The LLM decides whether the subject
   *actually* falls within the exception (never a blanket excuse). Linking is query-time (no re-ingest, no KG edge
   write) — consistent with the rest of the query-side arc.

5. **Ad-path parity.** The advertising path routes through the **same** deontic split + gates (obligations judged
   once + actor-gated, prohibitions per-assertion, permissions excluded + defense-linked), with the ad judge given
   the obligation framing and made tolerant of a plain evidence bundle. FTC remains the runnable **reference
   domain pack** (its curated tables are overrides on top of the load-bearing typed fields, not the mechanism).

## Consequences

- **Domain-retargetable.** A customer policy routes by what its rules ARE (deontic type, actor, scope), with FTC
  as one curated override — the engine stays domain-neutral (no FTC section numbers in the routing).
- **Neuro-symbolic, end-to-end.** The KG's typed fields gate/constrain (zero-LLM symbolic layer); the LLM reasons
  once over bounded, grounded, cited evidence. No claim without a citation (FR-Q.6). The same axiom now holds on
  both the ingestion side (ADR-0040) and the query side (this ADR).
- **Cost scales with relevant pairs, not `assertions × rules`.** Absent-actor obligations cost zero; an obligation
  costs one call over a small context; prohibitions are actor/claim-type narrowed. Verified in DEON-10 (3 calls vs
  a naive 12 on a 4-rule / 3-assertion case).
- **Contract touch (query-time only).** `Requirement.defenses: list[str]` and `CheckableFact.scope`/`Claim.scope`
  are query-side fields, never persisted; the store schema and identifiers are unchanged (FR-S.2/S.3).
- **Ambiguity is never silently dropped.** An off-vocab deontic is coerced + flagged and routed recall-first.
- **Generation nondeterminism remains the residual risk** (a borderline obligation verdict can flip run-to-run,
  [[generation-nondeterministic-abstain]]); the lever is model strength, not routing. Measured at the eval gate.
