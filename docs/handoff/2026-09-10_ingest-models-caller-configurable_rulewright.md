# RuleWright handoff: every ingest LLM model is now caller-configurable

Date: 2026-09-10 · on `origin/main` (commit `c9167ae`) · ADR-0097 · **All additive optional args, no breaking change, env stays the fallback.** (Supersedes the two split notes `..._ingest-extraction-model-configurable_...` and `..._ingest-party-affiliation-judge-models_...`.)

---

## What changed

The ingest models were reachable only through `RAG_SERVING` + env vars, unlike the query and compliance legs which take model arguments. `aproduction_document_ingest` now exposes **every ingest LLM surface** as a call-site argument, matching the other legs:

```python
aproduction_document_ingest(
    store, cache_dir=..., registry=...,
    extract_model=...,        # clause-property extraction (primary)
    list_model=..., samples=..., # the list-field cross-model union (2nd model + multi-sample)
    graph_extract_model=...,  # party + affiliation extraction (they share one model)
    judge_model=...,          # the ingest semantic-judge (ADR-0040 Layer-3 gate)
    chunk_model=...,          # the chunker's boundary-refinement model (over-cap sections only)
    classify_fn=...,          # the function classifier (pre-existing)
)
```

## The complete set of ingest LLM surfaces

| Surface | Argument | Default when `None` |
|---|---|---|
| Clause-property extraction (primary) | `extract_model` | granite (`RAG_SERVING` backend) |
| List-field cross-model union (2nd model) | `list_model` | gemma (`RAG_INGEST_LIST_MODEL`) |
| List-field same-model multi-sample | `samples` | 1 (`RAG_INGEST_CLAUSE_SAMPLES`) |
| Party + affiliation extraction | `graph_extract_model` | granite (`RAG_GRAPH_EXTRACT_MODEL`) |
| Semantic judge (Layer-3 gate) | `judge_model` | `model_for(STRUCTURED_REASONING)` |
| Chunker boundary-refinement (over-cap sections) | `chunk_model` | `model_for(GENERAL)` |
| Function classifier | `classify_fn` | `model_for(FUNCTION_CLASSIFY)` |

That is the full set — the only other LLM-ish stage, the chunk summarizer, is disabled on this path (`_NoSummary`, no model). Each argument accepts a **bare model-id string or an `ExtractionModel`** (for the graph/judge/chunk surfaces, which take a model-id string, an `ExtractionModel` is unwrapped to its `.model` id).

## Two things worth calling out

- **The list-field union is a *pair* of models.** For the LIST-bearing dims only (`carve_out` / `covered_subject` / `damage_type`), a second model (`list_model`, gemma by default) is run alongside the primary (`extract_model`, granite) and their list values are unioned — granite and gemma under-enumerate *different* items, so the union recovers more than either alone (this is the mechanism behind the issue-0033 `carve_out` fix, since it's a list dim). **If you override `extract_model` (e.g. to qwen), set `list_model` deliberately** — the union only helps if the two models are complementary; a model unioned with itself buys nothing. `list_model="off"` disables the second model.
- **`chunk_model` almost never fires.** Structural boundaries are deterministic (zero model calls); the chunk model is used only to refine a section that exceeds the token cap. A fully-structured document makes no `chunk_model` call at all.

## No behavior change by default

Every argument defaults to `None` → the backend/env default holds, verified. Env vars (`RAG_SERVING`, `RAG_INGEST_LIST_MODEL`, `RAG_INGEST_CLAUSE_SAMPLES`, `RAG_GRAPH_EXTRACT_MODEL`) remain the fallback, so **nothing you already run changes**. This just gives you the per-run, per-surface lever the query and compliance legs already had — including the "try qwen-3.8 if granite is below par" swap, now available on each ingest model independently.

Reference: ADR-0097, `subgraphs/contract_ingestion_pipeline.py::aproduction_document_ingest`, `spans/clause_kg_extractor.py::granite_clause_extractor`, `spans/tag_clause_extractor.py` (list union), `capabilities/graph_extraction.py` (party/affiliation), `spans/semantic_judge.py::build_asemantic_judge_fn`, `capabilities/rlm_chunking.py::StructuralModelFallbackDiscoverer`, `skills/corpus_ingest/SKILL.md`.
