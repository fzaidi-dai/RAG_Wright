# RuleWright handoff: the ingest extraction models are now caller-configurable

Date: 2026-09-10 · on `origin/main` (commit `89bab55`) · ADR-0097 · **Additive optional args — no breaking change, env stays the fallback.**

---

## What you asked for

The ingest clause-property extraction model was reachable only through `RAG_SERVING` + env vars, unlike the query and compliance legs which take a `extract_model` argument. It's now a caller argument on `aproduction_document_ingest`, matching the other surfaces:

```python
aproduction_document_ingest(
    store, cache_dir=..., registry=...,
    extract_model=...,   # primary clause-property extraction model
    list_model=...,      # the SECOND model for the list-field cross-model union
    samples=...,         # same-model multi-sample union count
)
```

- **`extract_model`** — the primary model that produces the typed clause properties. An `ExtractionModel` **or a bare model-id string** (e.g. `"qwen/qwen3.8-27b"`, wrapped for you). `None` (default) keeps the `RAG_SERVING` backend model (granite). This is the one you'd swap to try qwen-3.8.

## The second model you flagged — the gemma+granite list union

You reminded me we run a **cross-model union** on the **list-bearing dimensions only** — `carve_out`, `covered_subject`, `damage_type`. granite and gemma under-enumerate *different* list items, so running both and unioning their list values recovers more than either alone (this is the mechanism behind yesterday's `carve_out` fix living on a list dim). That second model is now a first-class argument too:

- **`list_model`** — the second (complementary) model for the list union. A bare model-id string, `"off"` to disable, or `None` for the default (gemma). Cost-scoped to list-bearing groups only.
- **`samples`** — same-model multi-sample union count (`None` → env default 1).

**Important if you override `extract_model`:** the union only helps when the two models are *complementary*. If you switch the primary to qwen, set `list_model` deliberately (keep gemma, or choose another second model) rather than assuming the default pairing still makes sense — a union of a model with itself buys nothing.

## No behavior change unless you pass them

Every argument defaults to `None`, which preserves exactly today's behavior (backend/env defaults: granite primary, gemma list-union, 1 sample). Env vars (`RAG_SERVING`, `RAG_INGEST_LIST_MODEL`, `RAG_INGEST_CLAUSE_SAMPLES`) remain the fallback, so nothing you already run changes. Existing callers (the engine's CUAD path, your current ingest) are unaffected.

## Parity summary

| Leg | Extraction model arg |
|---|---|
| Query (`production_typed_property_retrieval`) | `extract_model` (+ `judge_model_id`) |
| Compliance (`production_*_compliance_*`) | `extract_model`, `judge_model_id` |
| **Ingest (`aproduction_document_ingest`)** | **`extract_model`, `list_model`, `samples`** ← new |

Not yet caller-args: the party/affiliation extraction model and the ingest semantic-judge model (still their own defaults). Same one-line pattern to expose if you need them — tell us.

Reference: ADR-0097, `subgraphs/contract_ingestion_pipeline.py::aproduction_document_ingest`, `spans/clause_kg_extractor.py::granite_clause_extractor`, `spans/tag_clause_extractor.py` (the list-union mechanism), `skills/corpus_ingest/SKILL.md`.
