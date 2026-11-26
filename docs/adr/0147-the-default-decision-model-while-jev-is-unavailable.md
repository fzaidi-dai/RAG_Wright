# ADR-0147: The default decision model is Perplexity's decider while Jev is unavailable

**Status:** accepted · **Date:** 2026-11-26 · **Related:** ADR-0119 (Jev, the decision-model profile), ADR-0146 (the
harness routes with a decision model), PS-87

## Context

On 2026-11-26 every Jev call failed upstream: OpenRouter forwards `typesafe/jev-1.13` (the default) and
`typesafe/jev-1.14`, and TypeSafe answers `400 Unknown model: jev-1.13.0` / `jev-1.14.0`, for any question;
`typesafe/jev-latest` and `typesafe/jev-router` do not exist on the Decisions API. Each caller degrades as designed
(the harness routes with its model, ingestion falls back to rules or the LLM), so the outage showed only as a
`decision_model_unavailable` log line, and every product lost its decision-model routing.
`perplexity/pplx-decider-v1.2` answers the same Decisions API (`noul` and `choice`, calibrated probabilities, usage
with cost, about $0.000003 a call).

## Decision

`pplx-decider-v1.2` (served `perplexity/pplx-decider-v1.2`) is registered in `DECISION_PROFILES` and is the default
decision model until Jev returns. Jev's profiles stay registered: `RAG_DECISION_MODEL=jev-1.13` selects it, and
restoring `_DEFAULT_DECISION_MODEL` brings it back as the default.

## Consequences

- The thresholds measured on Jev (`RoutingThresholds`, ADR-0146; the ingestion judge's) are not re-measured on the new
  model. Measured live in PS-87 on the quickstart: record questions routed at 0.91-1.0, a greeting to `ask_the_user`
  at 0.95, a thanks at 0.5 (below D1's 0.7).
- A product pinned to Jev by `RAG_DECISION_MODEL` keeps failing over to its fallback until Jev returns.

## Addendum, 2026-11-26: Jev returned

The same day, `typesafe/jev-1.13` and `typesafe/jev-1.14` answered again (served `-20260917` and `-20261014`), both
for yes/no and choice questions. The default is now `jev-1.14` (the user's choice); `jev-1.13` and
`pplx-decider-v1.2` stay registered. The thresholds were measured on `jev-1.13` (ADR-0146) and are not re-measured on
1.14. That an outage of the decision model turns routing off for every product with only a log line is PS-89.

## Addendum, PS-89: a fallback

A `DecisionModelProfile` names an optional `fallback`, asked once when it fails; both Jev profiles fall back to
`pplx-decider-v1.2`. A run keeps the model that answered it on every tier, and its ledger records which answered or that
none could, so the next outage routes on the fallback and is visible per run rather than in one log line.
