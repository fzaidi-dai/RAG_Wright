# ADR-0085: query constraint extraction moves to client-side tag-parse

**Status:** accepted · **Date:** 2026-09-08 · **Issue:** engine 0020 (RuleWright) · **Builds on:** ADR-0045 (client-side tag-parse), ADR-0084 (gleaning), ADR-0066 (ontology = single source)

## Context

`typed_property_retrieval` turned a user's question into typed `(dimension, value)` constraints by calling `aextract_clause` with the **full `Clause` extraction template** — the document-shaped ingestion schema. For an ~88-char query that was **~6,800 input tokens** (40% of it phrase-cue descriptions a query does not need) for ~26 tokens of output, and it made **two** LLM calls (the gleaning pass, issue 0019). Yet ADR-0045 and the CLAUDE.md standing rule already say query-side structured output should be **client-side tag-parse**, not server-side guided decoding — the docling-graph route on the query leg was the anomaly.

## Decision

Extract query constraints via `build_tag_structured(model_id, Clause)` over the **same** `Clause` contract (ADR-0066 single source preserved — no parallel template), in a new module-level `aquery_constraints(query, model_id, *, structured_factory=build_tag_structured)`. `production_typed_property_retrieval` derives the model id from `extract_model.model`, so the product API is unchanged. Same `Clause -> clause_to_record` mapping, so constraints are identical in shape.

Two problems surfaced and were fixed at the root (measure-before-commit; no unproven attribution):

1. **Intermittent empty extractions (~⅓ of runs).** qwen3.8 is a reasoning model, and on the free-text/streaming path reasoning was **unset** (the profile's `structured_extra_body` reasoning setting binds only to forced-structured calls). Unset, the provider default intermittently returns reasoning-only / empty content. Fix: a new `ModelProfile.text_extra_body` slot — the free-text counterpart to `structured_extra_body` — applied by `astream_text`; qwen3.8 sets `{"reasoning":{"enabled":False}}` there. `build_model` now MERGES a caller `extra_body` over the profile's base routing (so reasoning is added without dropping provider routing). Reasoning-OFF (not ON) because constraint extraction is mechanical: OFF is deterministic, cheaper, and reasoning-ON measured *worse* (cap variance + value-form drift).

2. **A dropped `temporal_bound` (was misread as "provider variance" — it was not).** Deterministic (6/6). The model emits a nested model's sub-fields **fully flat** with no `<bounded_by>` wrapper; `parse_tagged` skipped the field because `_extract(text, name)` was None, so the wrapper-present flatten recovery never ran. Fix: `parse_tagged` recovers a flattened nested single model from the full text by its (unique) sub-field names even when the wrapper tag is absent. Strictly additive (only fires when the value would otherwise be dropped) — also recovers such props on the ingestion side.

## Consequences

- Query-leg constraint extraction: **~68% fewer input tokens** (~7,113 → ~2,335/query), **one call** (no gleaning), **zero empties**. Measured parity with the old path on a 3-query set: 2/3 exact; the cap query differs only by `cap_quantum`, which is **provably** non-load-bearing — `value_match` matches free-text dimensions by exact string (no `VALUE_ROLLUP` entry), so that query value can never boost a real clause; the closed-enum `cap_basis` (the matchable constraint) is extracted deterministically.
- Supersedes 0019's query-leg `gleaning=False` (the query leg no longer touches docling-graph); 0019's configurable-gleaning stays for ingestion.
- `text_extra_body` is now the model-level home for free-text reasoning control; other reasoning models onboarded later set it explicitly rather than inheriting an unstable provider default.
- Next: A-lean (a description-free query variant → ~83% cut), watching specifically for *field-level* misses (the real test of whether the ingestion hints matter on the query leg).
