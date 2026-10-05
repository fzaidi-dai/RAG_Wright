# RuleWright handoff: engine issue 0017 resolved — LLM calls now emit Langfuse traces

Date: 2026-09-05 · **Re:** engine-issue 0017 (LLM calls emit no traces, cost-per-document unmeasurable)
Engine commit range: `e60df03..8a3d435` (on `origin/main`) · Contract: `docs/OBSERVABILITY.md`

---

## TL;DR

**Fixed. The engine now instruments its own model calls** and emits one Langfuse **generation** per call, gated on
real configuration, correlated per document. Bump the engine dependency to `origin/main` (`8a3d435`), set
`RAG_TRACE_LEVEL`, publish `LANGFUSE_*`, pass a `job_id`, add two price rows — and cost-per-document is one
Langfuse query. **You were right on every design point, including "no read API"** — we did not build one.

---

## Your four asks → what shipped

1. **Engine instruments its own model calls.** One generation per call at `models/seam.py` (`astream_text`) **and**
   the docling-graph litellm path — so both paths are covered, uniformly. Each carries model id, token usage,
   latency, and the metadata only the engine holds: the ADR-0058 `label` (e.g. `clause-group`,
   `semantic_judge.judge`, `clause_function_classifier.classify_spans`), `role`, `stage`, and `document_id`.
2. **Trace-level knob, gated on real config.** `RAG_TRACE_LEVEL=off|generations|verbose` (default `off`), activation
   gated on `LANGFUSE_*` being **configured**, never on importability — exactly the FastMCP/OTel lesson you cited.
   `generations` = numbers only; `verbose` = + prompt/response text.
3. **Caller correlation id — the load-bearing piece.** `traced_run(document_id, job_id)` wraps each document's
   ingest inside `arun_corpus_ingestion`. `document_id` (= `source_doc_id`) flows automatically; **`job_id` is
   yours to pass** (`arun_corpus_ingestion(..., job_id=...)`). Every generation the document emits is stamped, so
   attribution needs no reconstruction.
4. **A contract, not a read API.** `docs/OBSERVABILITY.md` documents the observation names, metadata keys, and the
   session/`document_id` convention. **We did not build a read/report wrapper** — you query Langfuse directly, as
   you said. Where OpenRouter hands us the actual cost (the litellm path), we pass it through as `cost_details`;
   the `astream_text` bulk is token-priced from your Langfuse table.

---

## What you do to consume it

- **Bump the engine dependency** to `origin/main` @ `8a3d435`.
- **Publish `LANGFUSE_*`** through `export_engine_env` (alongside ArcadeDB/OpenRouter), and set `RAG_TRACE_LEVEL`
  (`generations` for cost/volume; `verbose` when you want to see the prompts).
- **Pass `job_id`** per run into `arun_corpus_ingestion` (per-document correlation works without it — it falls back
  to `document_id` as the session).
- **Add two Langfuse price rows** so the `astream_text` bulk gets currency (the litellm path is already priced):

  | model | input $/M | output $/M |
  |---|---|---|
  | `ibm-granite/granite-4.2-8b` | 0.10 | 0.15 |
  | `google/gemma-4-31b-it` | 0.09 | 0.34 |

  (OpenRouter list rates 2026-09-05 — confirm against your deployment's provider.)
- **Query Langfuse** for reports: cost-per-document = group by `session_id` (no job id) or filter
  `metadata.document_id` (with a job id); cost-per-job = group by `session_id`.

---

## What we already measured (validated live against Langfuse, clean emit)

A small 7-clause NDA through the full default pipeline: **~86–97 generations, ~50–58k tokens** — granite ~75% of
tokens (full extraction + classification + semantic judge), gemma ~25% (list-union groups, tiny outputs). Priced
at OpenRouter rates: **~0.6¢ for that NDA** (granite ~76% / gemma ~24%).

Two things this surfaced that matter for your cost model:
- **Two token classes:** *per-chunk* calls (classification + party extraction — scale with chunk size, these are
  your 5–7k-token calls on real contracts) and *per-clause* calls (tag-parse groups + judge — small, ×clauses).
  So cost scales with **clause count**, not document count; extrapolate accordingly.
- **The semantic judge and cross-model union are visible line items now** — you can decide `RAG_INGEST_LIST_MODEL=off`
  (gemma is only ~24% of cost here) or whether the ADR-0040 semantic judge earns its per-clause call, on evidence.

---

## Coverage note

`astream_text` (the dominant path) + the litellm path are covered. If you find a residual direct
`build_model(...).invoke` caller emitting nothing, flag it — it takes the same one-line `record_generation`
pattern. Currency for the `astream_text` bulk depends on your two price rows; the litellm path carries actual cost.

Reference: `docs/OBSERVABILITY.md`, `models/tracing.py`, and the issue-0017 commit range above.
