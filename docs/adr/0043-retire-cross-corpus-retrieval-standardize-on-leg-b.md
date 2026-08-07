# ADR-0043: Retire cross_corpus_retrieval; standardize on typed_property_retrieval (Leg B)

Date: 2026-08-07
Status: Accepted

## Context

Phase-A validation on Modal (MCP-PROTO) exercised `cross_corpus_retrieval` end-to-end for the first time and
surfaced two problems:

1. **A latent bug** — its query-constraint extraction (`production_record_fn` → `clause_to_record(function="")`)
   failed `ClausePropertyRecord`'s function-taxonomy validator; the error was swallowed by `_degrading_io` as
   **empty constraints**, so the property match was dead on every query (every span `match=0.0`).
2. **An inferior pool** — even with constraints, its pool was **function-only** (`spans_by_functions`, first-200
   arbitrary), which missed most constrained spans (measured 2 of 13 for a `cap_basis=multiple_of_fees` query).

The decisive observation: `typed_property_retrieval` (Leg B) is **functionally the same leg** — query →
constraints + functions → ranked cited spans — but with the **correct BGE+property pool**
(`property_boosted_retrieval`, already proven on Modal: "cap_basis=multiple_of_fees → ranks 1-4 match"). The two
subgraphs were redundant; `cross_corpus_retrieval` was the inferior copy. (Leg B had worked around the same
constraint-extraction bug with a hardcoded `function="Cap On Liability"` hack that mislabeled every query.)

## Decision

**Retire `cross_corpus_retrieval`. `typed_property_retrieval` (Leg B) is THE corpus-wide function+property
retrieval leg.** Do not recreate `cross_corpus_retrieval`, re-register it, or wrap it as an MCP tool.

Also, the proper fix for the shared constraint-extraction bug: a query has no clause function → the **`NO_FUNCTION`
sentinel** (`contracts/function.NO_FUNCTION = "NONE"`), which `ClausePropertyRecord` now accepts (a real ingested
clause never uses it). Both the query path (`production_record_fn`) and Leg B's inline `constraints_fn` use it;
Leg B's hardcoded-function hack is removed.

Removed: `subgraphs/cross_corpus_retrieval.py` + its test; the `cross_corpus_retrieval` slug and manifest; the two
store reads added only for its pool/hydrate (`spans_by_functions`, `span_vectors_by_id`).

## Consequences

- **One correct retrieval leg**, no hacks, no redundant inferior pool. Leg B re-validated on Modal after the
  change (top-4 `[MATCH]` for the fee-multiple-cap query).
- **MCP Tier-1 candidate list is updated** (the suspended Phase B): the read-only query legs to expose as MCP
  tools are `compliance_check`, `intra_document_qa`, `relational_qa`, and **`typed_property_retrieval`** — NOT
  `cross_corpus_retrieval`. Do not wrap the retired leg.
- `candidate_routing`, `query_constraint_extraction`, and `production_record_fn` remain registered
  capabilities/utilities but are now **unused in production** (their only caller was `cross_corpus_retrieval`).
  Kept as general utilities for now; retire separately if they stay unused.
- Behavior-preserving for Leg B: the `NO_FUNCTION` swap changed no output (the function was only used for
  validation, never the constraints), so Leg B's prior Modal proof (MS1-6) still holds.
