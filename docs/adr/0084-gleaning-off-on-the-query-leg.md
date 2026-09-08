# ADR-0084: gleaning is configurable; off on the query leg

**Status:** accepted · **Date:** 2026-09-08 · **Issue:** engine 0019 (RuleWright) · **Affects:** `capabilities/dg_extraction.py`, `subgraphs/typed_property_retrieval.py`

## Context

RuleWright wired `typed_property_retrieval` into the product's query path (T-4.1/T-4.2) and, under Langfuse tracing, measured **two `dg_extraction.clause` generations for a single `aextract_clause` call** — ~7,000 input tokens each, for a 40-character question — while docling-graph logged one `Calling LLM` and one success. Not a retry after failure (no failure logged). The product asked the engine to determine, from the inside, whether the second call is necessary.

Instrumented in-engine (the `record_generation` emission point receives `input=messages` on every `_call_api`; captured the caller stack), the second call is docling-graph's **gleaning pass**: after the extraction, `llm_backend._call_llm_for_extraction` runs `gleaning.run_gleaning_pass_direct` — a full-document "extract any ADDITIONAL information not already extracted" completeness turn — gated on `PipelineConfig.gleaning_enabled`, which **defaults to `True`**. So every extraction, ingestion and query alike, makes a second LLM call.

Measured marginal yield on the query leg (deterministic across repeated runs, so not sampling noise): gleaning adds exactly **one redundant constraint** per query that re-expresses what the first pass already captured — `cap_quantum="a multiple of fees"` restating `cap_basis=multiple_of_fees`; `party_asymmetry=symmetric` restating `mutuality=mutual` — at 2× cost and latency. The raw-phrase values do not even match the normalized indexed vocabulary, so they are dead weight in `property_boosted_retrieval`.

## Decision

Make gleaning configurable and turn it **off on the query leg**, on by default everywhere else.

- Add `gleaning: bool = True` to `build_pipeline_config` (→ `PipelineConfig(gleaning_enabled=gleaning)`) and thread it through `extract_parties` / `extract_clause` / `aextract_clause`. Default `True` preserves ingestion and party-extraction behavior unchanged.
- `typed_property_retrieval.constraints_fn` calls `aextract_clause(query, extract_model, gleaning=False)`: a user query is short and has nothing to glean; the completeness call is redundant restatement at 2× the hottest path's cost.

## Consequences

- Query-leg constraint extraction goes **2 LLM calls → 1**, halving its cost and interactive latency, dropping only redundant constraints (verified live: OFF vs ON constraint sets differ only by the redundant restatements above).
- **Ingestion is untouched** (gleaning stays on for a full clause, where the completeness pass may genuinely recover missed fields). Whether gleaning earns its keep on *ingestion* is a separate, unmeasured question; this ADR does not change it.
- The flag makes the query-leg choice **reversible**: if a retrieval-recall A/B later shows the extra constraints help ranking, flip `gleaning=True` at that one call site. A full recall A/B was not run here; the basis for `False` is the deterministically redundant nature of the extra constraints plus the reversible knob.
