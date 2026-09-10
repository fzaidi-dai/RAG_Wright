# RuleWright handoff: party/affiliation + semantic-judge models now caller-configurable on ingest

Date: 2026-09-10 · on `origin/main` (commit `8ae1fe6`) · extends ADR-0097 · **Additive optional args, no breaking change.**

---

Following the `extract_model`/`list_model`/`samples` change, the last two ingest LLM surfaces are now call-site arguments too:

```python
aproduction_document_ingest(
    store, cache_dir=..., registry=...,
    extract_model=..., list_model=..., samples=...,   # clause-property extraction (already shipped)
    graph_extract_model=...,   # NEW: party + affiliation extraction (they share one model)
    judge_model=...,           # NEW: the ingest semantic-judge (ADR-0040 Layer-3 gate)
)
```

- **`graph_extract_model`** — drives **both** party extraction and affiliation (AFFILIATE_OF) extraction; they use the one GP-1B graph-extract surface, so one argument sets both. A bare model-id string or an `ExtractionModel` (unwrapped to its id). `None` → default (granite, `RAG_GRAPH_EXTRACT_MODEL`).
- **`judge_model`** — the ingest-side semantic judge that runs the Layer-3 verify-or-refute gate on extracted properties. A model-id string or an `ExtractionModel`. `None` → `model_for(STRUCTURED_REASONING)`.

## Every ingest LLM surface is now an argument

| Surface | Argument |
|---|---|
| Clause-property extraction | `extract_model` |
| List-field cross-model union (2nd model) | `list_model` (+ `samples`) |
| Party + affiliation extraction | **`graph_extract_model`** |
| Function classifier | `classify_fn` (pre-existing) |
| Semantic judge (Layer-3 gate) | **`judge_model`** |

So the "try qwen-3.8 if granite is below par" lever is now available per run on each ingest model independently, not just through process env. Env vars remain the fallback and the default; **no-arg calls are unchanged**, so nothing you already run is affected.

Reference: ADR-0097, `subgraphs/contract_ingestion_pipeline.py::aproduction_document_ingest`, `capabilities/graph_extraction.py` (`aproduction_extract_fn`, `aextract_affiliations`), `spans/semantic_judge.py::build_asemantic_judge_fn`, `skills/corpus_ingest/SKILL.md`.
