# ADR-0127: Retire the OKF code

**Status:** accepted · **Date:** 2026-10-09 · **Related:** ADR-0022 / ADR-0023 (the OKF experiment, already retired
by ADR-0025 and ADR-0046), PS-R5 (domain vocabulary out of the generic engine)

## Context

The OKF (Open Knowledge Format) navigation experiment was shelved by the retrieval pivot (ADR-0025) and dropped
from the pipeline when ACORD folded into the one production KG (ADR-0046), but its code stayed: `rag_wright/okf/`
(an ACORD-specific bundle compiler, with ACORD's nine attorney categories and a contract enrichment prompt), the
`okf_navigate` capability and skill, the `okf_compile` / `okf_navigate` reference-pack slugs, the `OKF_ENRICHMENT`
model role, and the experiment's eval scripts. PS-R5's audit found it as domain code in the generic engine. Nothing
uses it: no pipeline, no product (RuleWright's own notes put `okf_navigate` on no path it calls), only the
experiment's eval scripts.

## Decision

Delete it rather than move it into the reference pack (user decision): `rag_wright/okf/`,
`capabilities/okf_navigate.py`, `skills/okf_navigate/`, the two pack slugs and the `okf_navigate` manifest, the
`ModelRole.OKF_ENRICHMENT` role (its Gemma model profile stays registered as an ordinary dev-override model), the
import-linter entry, the OKF tests, and the experiment's eval scripts (`okf_gold`, `reachability`, `ablation`,
`category_retrieval` and their tests). Git history keeps all of it.

## Consequences

- The generic engine no longer carries an ACORD-specific module; the domain-vocabulary ratchet drops accordingly and
  its skill allow-list is empty.
- `ModelRole` loses `OKF_ENRICHMENT` (unreleased change; no product configures it). The legacy `parent_okf_path`
  span property is unaffected: it was already out of the engine `Span` (ING-8d) and stays only in old databases.
