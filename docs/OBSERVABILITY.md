# Engine LLM observability — Langfuse emission contract (issue 0017)

Date: 2026-09-05 (updated 2026-10-07: default model, the decision model and OCR, correlation scope). The engine owns its model calls, so it owns their instrumentation. This is the **emission
contract** the product queries against — the engine WRITES generations to Langfuse; it is **not** a read API
(the product queries Langfuse's own REST/v3 API directly).

---

## Activation (two independent gates, BOTH required)

1. `RAG_TRACE_LEVEL` ∈ {`off` (default), `generations`, `verbose`}.
2. `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` (+ `LANGFUSE_HOST`) configured.

Activation gates on real **configuration**, never on importability (langfuse and its transitive
`opentelemetry-sdk` being installed says nothing about intent). Levels:
- `off` — nothing emitted.
- `generations` — model id, token usage, cost (where available), latency, and metadata. **No prompt/response text.**
- `verbose` — the above **plus** the input prompt and output text.

Tracing never breaks a model call: every langfuse touch degrades to a no-op on error.

---

## What is emitted

**One Langfuse `generation` per completed model call.** Emitted at the three points every call funnels through:

| path | covers | `metadata.stage` |
|---|---|---|
| `models.seam.astream_text` (LangChain streaming) | answer generation, query understanding, query constraint extraction, the LLM fallbacks of the reference ingest (the LLM function classifier, the residual clause-value call) | `astream_text` |
| `models.seam.build_structured` (forced structured output, `.ainvoke`/`.invoke`) | the relevance judge (`span-relevance`), the compliance judge, and every other forced-schema caller (issue 0025) | `build_structured` |
| docling-graph litellm client (`capabilities.dg_extraction`) | party / clause extraction on the legacy path | `litellm` |

### Fields (the contract)

| field | meaning |
|---|---|
| `name` | the call-site **label** (ADR-0058), e.g. `span-relevance`, `query-constraints`, `affiliations`, `docling-graph-extract` |
| `model` | the engine model id the call was made with; by default every role uses the Qwen3.8-27B profile `qwen3.8-27b-modal-or` (`models/profiles.py`), unless a `RAG_MODEL_*` override names another |
| `usage_details` | `{"input": <prompt tokens>, "output": <completion tokens>}` |
| `cost_details` | `{"total": <USD>}` — the provider's **actual** reported cost, passed through on **all three paths**: `litellm` via `usage.cost`, `astream_text` via the cost-capturing streaming client (issue 0021), `build_structured` via the raw response's `token_usage.cost` (issue 0025). `cost` is None (→ Langfuse prices from its table) only when the backend does not surface it (e.g. self-hosted vLLM) |
| `metadata` | `label`, `role`, `stage` (`astream_text`\|`build_structured`\|`litellm`), `latency_ms`, `document_id`, + any caller metadata |
| `input` / `output` | prompt / completion text — **only at `verbose`** |

---

## Correlation (cost per document / per job)

Each document's ingest runs inside `traced_run(document_id, job_id)` (langfuse `propagate_attributes`), stamping
every nested generation. Wired in `arun_corpus_ingestion` — `document_id = source_doc_id` flows automatically;
`job_id` is the optional value the **product supplies** (`arun_corpus_ingestion(..., job_id=...)`).

This is the only place correlation is set. A document ingested any other way (`build_ingestion(...).aingest(...)`,
or `ainvoke_subgraph("contract_ingestion_pipeline", ...)` through the API) still emits its generations, and each
API invoke is timed as an `invoke:<name>` span, but they carry no `document_id` / `job_id`; wrap the call in
`models.tracing.traced_run(document_id=..., job_id=...)` yourself if you need per-document cost.

- **`session_id`** = `job_id` if given, else `document_id`.
- **`metadata.document_id`** = the document's `source_doc_id`, always.

So the queries the product runs:
- **Cost per document** (no job id): group generations by `session_id` (= document_id).
- **Cost per document** (with job id): filter generations by `metadata.document_id`.
- **Cost per job**: group by `session_id` (= job_id).

Cost-per-document is one Langfuse query; no bespoke script, no engine read API.

---

## Pricing (product-owned)

Token counts and call volume are real as soon as generations emit. When the provider reports the actual cost
(OpenRouter does, on all three paths) it is passed through in `cost_details` and needs no price table. Where it does
not (for example a self-hosted vLLM endpoint), add a price row to the deployment's Langfuse for each model you serve
(self-hosted Langfuse ships none for these models), using your provider's current rates.

---

## Coverage & caveats

- Covered: `astream_text` (the dominant path) + the docling-graph litellm path. Any remaining direct
  `build_model(...).invoke` caller is not yet instrumented (add the same `record_generation` pattern if one is
  found).
- **Not emitted to Langfuse:** decision-model (Jev) calls (`jev_decision`: the reference ingest's provision
  boundaries, extraction judge and residual values) and VLM OCR pages (docling makes those requests itself). Both
  are metered in-band by `measure_usage()`: Jev calls with the tokens and cost the endpoint reports, OCR as one
  uncosted call per page (`calls_without_cost`). Use `measure_usage()` for the full call count of an ingest.
- Token classes to expect: **per-document / per-chunk** calls (party and affiliation extraction, an over-cap
  section's chunk refinement; these scale with text size) and, only where the reference ingest falls back to the
  LLM (no decision model, or `RAG_SEMANTIC_JUDGE=llm` / `RAG_RESIDUAL_EXTRACTOR=llm`), **per-provision** calls
  (the residual values call and the judge; small, times the number of provisions). With the decision model, the
  per-provision work is Jev calls, which appear only in `measure_usage()`.
- Reference: `models/tracing.py` (emitter), `models/seam.py::astream_text`, `packs/contracts/capabilities/dg_extraction.py`
  (litellm path), `packs/contracts/subgraphs/contract_ingestion_pipeline.py::arun_corpus_ingestion` (correlation).
