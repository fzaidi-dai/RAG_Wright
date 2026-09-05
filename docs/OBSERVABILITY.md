# Engine LLM observability — Langfuse emission contract (issue 0017)

Date: 2026-09-05. The engine owns its model calls, so it owns their instrumentation. This is the **emission
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

**One Langfuse `generation` per completed model call.** Emitted at the two points every call funnels through:

| path | covers | `metadata.stage` |
|---|---|---|
| `models.seam.astream_text` (LangChain streaming) | tag-parse clause extraction, function classification, the ADR-0040 semantic judge, answer generation, query understanding | `astream_text` |
| docling-graph litellm client (`capabilities.dg_extraction`) | party / clause extraction on the legacy path | `litellm` |

### Fields (the contract)

| field | meaning |
|---|---|
| `name` | the call-site **label** (ADR-0058), e.g. `clause-group`, `semantic_judge.judge`, `clause_function_classifier.classify_spans`, `docling-graph-extract` |
| `model` | the model id, e.g. `ibm-granite/granite-4.2-8b`, `google/gemma-4-31b-it` |
| `usage_details` | `{"input": <prompt tokens>, "output": <completion tokens>}` |
| `cost_details` | `{"total": <USD>}` — **only on the `litellm` path** (a pass-through of OpenRouter's *actual* reported cost). `astream_text` calls carry **no** cost (LangChain does not surface it) → Langfuse prices them from its own model table |
| `metadata` | `label`, `role`, `stage` (`astream_text`\|`litellm`), `latency_ms`, `document_id`, + any caller metadata |
| `input` / `output` | prompt / completion text — **only at `verbose`** |

---

## Correlation (cost per document / per job)

Each document's ingest runs inside `traced_run(document_id, job_id)` (langfuse `propagate_attributes`), stamping
every nested generation. Wired in `arun_corpus_ingestion` — `document_id = source_doc_id` flows automatically;
`job_id` is the optional value the **product supplies** (`arun_corpus_ingestion(..., job_id=...)`).

- **`session_id`** = `job_id` if given, else `document_id`.
- **`metadata.document_id`** = the document's `source_doc_id`, always.

So the queries the product runs:
- **Cost per document** (no job id): group generations by `session_id` (= document_id).
- **Cost per document** (with job id): filter generations by `metadata.document_id`.
- **Cost per job**: group by `session_id` (= job_id).

Cost-per-document is one Langfuse query; no bespoke script, no engine read API.

---

## Pricing (product-owned)

Token counts and call volume are real as soon as generations emit. **Currency** for the `astream_text` bulk
needs price rows in the deployment's Langfuse (self-hosted Langfuse ships none for these models). Add two rows:

| model | input $/M tokens | output $/M tokens |
|---|---|---|
| `ibm-granite/granite-4.2-8b` | 0.10 | 0.15 |
| `google/gemma-4-31b-it` | 0.09 | 0.34 |

(OpenRouter list rates as of 2026-09-05; confirm against the deployment's provider.) The `litellm` path already
carries OpenRouter's actual cost via `cost_details`, so it is priced without a table.

---

## Coverage & caveats

- Covered: `astream_text` (the dominant path) + the docling-graph litellm path. Any remaining direct
  `build_model(...).invoke` caller is not yet instrumented (add the same `record_generation` pattern if one is
  found).
- Two token classes to expect: **per-chunk** calls (classification, party extraction — scale with chunk size,
  the 5–7k-token calls on real contracts) and **per-clause** calls (tag-parse groups + judge — small, ×clauses).
- Reference: `models/tracing.py` (emitter), `models/seam.py::astream_text`, `capabilities/dg_extraction.py`
  (litellm path), `subgraphs/contract_ingestion_pipeline.py::arun_corpus_ingestion` (correlation).
