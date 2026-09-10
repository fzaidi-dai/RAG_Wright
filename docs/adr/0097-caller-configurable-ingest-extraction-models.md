# ADR-0097: the ingest extraction models are caller-configurable (extract_model / list_model / samples)

**Status:** accepted · **Date:** 2026-09-10 · **Prompted by:** RuleWright (during issue 0033) · **Related:** ADR-0081 (tag-parse clause extraction + the gemma+granite list union), ADR-0039/0079 (the `RAG_SERVING` model backend), the query-side `production_typed_property_retrieval(extract_model=...)` and compliance `production_compliance_check(extract_model=...)` precedents

## Context

Every other model surface takes a caller argument — the query leg (`extract_model`, `judge_model_id`), compliance (`extract_model`, `judge_model_id`), the function classifier (`classify_fn`) — but the **ingest clause-property extraction model** was reachable only through the `RAG_SERVING` backend switch and env vars (`RAG_INGEST_LIST_MODEL`, `RAG_INGEST_CLAUSE_SAMPLES`). `aproduction_document_ingest` hardcoded `granite_clause_extractor(...)` with no model parameter, so a caller hand-building an ingest (RuleWright) could not choose the extraction model per run without setting process env. This is inconsistent, and it blocks the standing "try qwen-3.8 if granite is below par" lever on the ingest side.

There is a second, easily-overlooked model here: the ingest clause extractor runs a **cross-model list union** (ADR-0081) — for the LIST-bearing dims only (`carve_out` / `covered_subject` / `damage_type`), a second model (gemma by default) is run alongside the primary (granite) and their list values are unioned, because granite and gemma under-enumerate *different* items so the union recovers more than either alone. If a caller overrides only the primary model, the union's second model (and hence its value) is left to env defaults — the two must be chosen together to stay complementary.

## Decision

Expose the ingest extraction model configuration on `aproduction_document_ingest`, mirroring the query/compliance entrypoints; env vars remain the fallback so existing callers are unaffected.

- **`extract_model`**: the PRIMARY clause-property extraction model — an `ExtractionModel` OR a bare model-id string (wrapped via `default_extraction_model("clause-extract", id)`); `None` keeps the backend default (granite via `RAG_SERVING`).
- **`list_model`**: the SECOND model for the cross-model list union (list-bearing dims only) — a bare model-id string, `"off"` to disable, `None` for the default (gemma). Threaded to `granite_clause_extractor(list_model=...)`. Documented alongside `extract_model` so a caller overriding the primary knows to set the union model deliberately.
- **`samples`**: same-model multi-sample count for the list union (`None` → env default 1). Threaded to `granite_clause_extractor(samples=...)`.
- **`graph_extract_model`**: the model for BOTH party AND affiliation extraction — they share the one GP-1B graph-extract surface (`aproduction_extract_fn(model_id=)` and `aextract_affiliations(model_id=)`). A bare model-id string or an `ExtractionModel` (unwrapped to its `.model` id); `None` → the default (granite, `RAG_GRAPH_EXTRACT_MODEL`).
- **`judge_model`**: the ingest semantic-judge model (the ADR-0040 Layer-3 gate, `build_asemantic_judge_fn`). A model-id string or an `ExtractionModel`; `None` → `model_for(STRUCTURED_REASONING)`.

Every argument passes through to the existing constructors (`granite_clause_extractor(model=, list_model=, samples=, asemantic_judge_fn=)`, `aproduction_extract_fn(model_id=)`, `aextract_affiliations(model_id=)`) — no change to any extraction/judge mechanism, only its reachability. The graph/judge surfaces take a model-id *string*, so an `ExtractionModel` passed there is unwrapped to `.model`.

## Consequences

- **Parity across surfaces:** ingest, query, and compliance all take a caller-supplied extraction model now; RuleWright can select the ingest model per run (e.g. try qwen-3.8) without touching process env. The `RAG_SERVING`/env path is untouched and remains the default and the fallback.
- **The list union is a first-class knob, not a hidden env.** The gemma+granite union that improves list-field recall (the mechanism behind issue 0033's `carve_out` living on a list dim) is now visible and configurable at the call site, with the caveat documented that the two models must be complementary to be worth the second call.
- **No behavior change by default** (verified: no-arg calls pass `None` for every knob, so the backend/env defaults hold), and the corpus-ingest SKILL documents the new arguments.
- **Every ingest LLM surface a caller reasonably tunes is now an argument:** clause-property extraction (`extract_model` + the `list_model`/`samples` union), party+affiliation extraction (`graph_extract_model`), the function classifier (`classify_fn`, pre-existing), and the semantic judge (`judge_model`). This closes the parity gap with the query/compliance legs and makes the "try qwen-3.8 if granite is below par" lever available on every ingest model per run, not just via process env.
