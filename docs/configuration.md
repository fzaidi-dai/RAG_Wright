# Configuration

The engine is configured through a typed **`EngineConfig`** passed to `open_workspace` — not through environment
variables. Defaults just work; override a field only to trade quality, cost, or latency. Environment variables
exist as a fallback for non-API callers and for secrets; the config always wins when set.

## `EngineConfig`

```python
from rag_wright.api import EngineConfig, StoreConfig, EngineOptions, IngestOptions, open_workspace

config = EngineConfig(
    store=StoreConfig(host="localhost", port="2480", user="root", password="<DEV_PASSWORD>"),
    models={},                         # ModelRole value -> model alias override (empty = engine defaults)
    embeddings={"text": "bge-m3"},     # embedding profile -> supported embedder
    options=EngineOptions(),           # the knobs catalog (ingest today)
    pack=None,                         # path to a domain .ttl; None = no domain pack (neutral engine schema)
)
ws = open_workspace(config, corpus="my_corpus", reset=False)   # corpus = the backend DB name
```

| field | type | default | meaning |
|---|---|---|---|
| `store` | `StoreConfig` | — (required) | how to reach the store |
| `models` | `dict[str,str]` | `{}` | override the model for a `ModelRole` (e.g. `{"general": "ibm-granite/granite-4.2-8b"}`) |
| `embeddings` | `dict[str,str]` | `{"text": "bge-m3"}` | the embedding profile → supported embedder |
| `options` | `EngineOptions` | defaults | the options catalog (ingest knobs today) |
| `pack` | `str \| None` | `None` | path to the domain `.ttl` pack, whose types are created on top of the engine's; `None` = the neutral engine schema only (`Chunk`, `Entity`, `Span`, `Document` vertices; `Relationship`, `Mentions`, `EmbeddedIn`, `AttachedTo` edges). The reference contract pipeline ensures its own pack schema on first use. |

`open_workspace(config, *, corpus, reset=False)` resolves and caches a workspace and ensures the schema. `corpus`
is the backend database name (tenancy is the product's concern); `reset=True` drops and recreates it and bypasses
the cache.

### `StoreConfig`

| field | type | default | meaning |
|---|---|---|---|
| `host` | `str` | — | store host |
| `port` | `str` | — | store port (ArcadeDB HTTP: `"2480"`) |
| `user` | `str` | — | user |
| `password` | `str` | — | password |
| `backend` | `str` | `"arcadedb"` | store implementation (only `arcadedb` today) |
| `protocol` | `str` | `"http"` | `http` locally; `https` for a remote/Modal store |

### `EngineOptions` / `IngestOptions`

`EngineOptions.ingest` holds the ingest knobs. Every field defaults to `None` = "use the engine default", so an API
caller that leaves them unset gets the engine's current behavior; set a field to override. Each mirrors an env var
(the env is the fallback when the config field is `None`). All but `tuning` are read by the reference contract
pipeline only.

| `IngestOptions` field | type | env fallback | meaning |
|---|---|---|---|
| `classify_concurrency` | `int` | `CLASSIFY_CONCURRENCY` | function-classify parallelism |
| `clause_concurrency` | `int` | `CLAUSE_CONCURRENCY` | clause-extraction parallelism |
| `affiliations` | `bool` | `RAG_INGEST_AFFILIATIONS` | run affiliation extraction |
| `function_classifier` | `str` | `RAG_FUNCTION_CLASSIFIER` | `"setfit"` (default, the trained SetFit ensemble) \| `"llm"` |
| `list_model` | `str` | `RAG_INGEST_LIST_MODEL` | **no-op**: accepted but unused since clause extraction became classifier-first; pending removal (ING-8d) |
| `clause_samples` | `int` | `RAG_INGEST_CLAUSE_SAMPLES` | **no-op**: accepted but unused; pending removal (ING-8d) |
| `tuning` | `IngestionTuning` | — | the structural thresholds of the generic ingestion hooks (unit size cap, record-table rule, identifier rule, extraction and document concurrency). `build_ingestion` uses its own `tuning=` argument, else this, else the defaults. Tune it from `evaluate_ingestion` runs on your own samples. |

## Models and the model-profile seam

Models are chosen by **role** (`ModelRole`) and resolved to a concrete model by a **profile** keyed by model id —
never a hardcoded provider flag. Override a role per workspace via `EngineConfig.models`, or globally via env.

- **Serving backend** — `RAG_SERVING` selects `openrouter` (default) or `vllm` (self-hosted). For vLLM set
  `VLLM_BASE_URL` and `VLLM_API_KEY`; for OpenRouter set `OPENROUTER_API_KEY` (optionally
  `OPENROUTER_PROVIDER`/`OPENROUTER_SORT`/`OPENROUTER_PROVIDER_ORDER` for provider routing).
- **Roles** (`ModelRole`): `STRUCTURED_REASONING` (extraction/grading/synthesis), `STRUCTURED_REASONING_SECONDARY`
  (the same call class, a selectable fallback), `GENERAL` (reasoning/generation/vision, the local-deployment
  default), `SUMMARIZATION` (chunking/summarization), `OKF_ENRICHMENT` (OKF signpost classify + description),
  `FUNCTION_CLASSIFY`, `VISION_OCR` (the scanned-page OCR escalation; must be a vision model). Every role
  defaults to the one product LLM (Qwen3.8-27B, profile `qwen3.8-27b-modal-or`, which accepts images), so a
  deployment serves a single model; OCR resolves its endpoint and flags through the same profile as every other
  call. Override one role with `RAG_MODEL_<ROLE>` (e.g. `RAG_MODEL_VISION_OCR`, `RAG_MODEL_GENERAL`), or every
  role at once with `RAG_MODEL_ALL`; the role-specific variable wins.
- **Structured output** is client-side tag-parse (ADR-0045); any provider flags live in the profile.
- **Decision models** (e.g. Jev) are reached through a `DecisionModelProfile` (default `jev-1.13`;
  `RAG_DECISION_MODEL` only overrides which profile). The reference contract pipeline uses the decision model
  whenever its key (`OPENROUTER_API_KEY`) is set **and** the `jev_decision` capability is registered
  (`load_reference_pack()` registers it): for the uncertain provision boundaries, the extraction judge, and the
  numeric/open property values, so there is no per-provision LLM call. Without either, those steps fall back
  (deterministic boundaries; the LLM judge and residual call). `RAG_SEMANTIC_JUDGE=llm` / `RAG_RESIDUAL_EXTRACTOR=llm`
  switch the judge / the residual values back to the LLM.

## Environment variables (reference)

Prefer config; use env for secrets and for non-API callers. The common ones the engine reads:

| variable | purpose |
|---|---|
| `ARCADEDB_HOST` / `ARCADEDB_PORT` / `ARCADEDB_USER` / `ARCADEDB_PASSWORD` / `ARCADEDB_DATABASE` / `ARCADEDB_PROTOCOL` | store connection (the `StoreConfig` fallback) |
| `OPENROUTER_API_KEY` / `OPENROUTER_BASE_URL` / `OPENROUTER_PROVIDER` / `OPENROUTER_SORT` / `OPENROUTER_PROVIDER_ORDER` / `OPENROUTER_ALLOW_FALLBACKS` | OpenRouter access + provider routing |
| `RAG_SERVING` / `VLLM_BASE_URL` / `VLLM_API_KEY` / `STACK_URL` | serving backend (openrouter \| vllm) + self-hosted endpoint |
| `RAG_MODEL_<ROLE>` (e.g. `RAG_MODEL_GENERAL`, `RAG_MODEL_VISION_OCR`) / `RAG_MODEL_ALL` / `RAG_GRAPH_EXTRACT_MODEL` / `RAG_DECISION_MODEL` | model overrides (one role / all roles / graph extraction / decision-model profile) |
| `RAG_SEMANTIC_JUDGE` / `RAG_RESIDUAL_EXTRACTOR` | set to `llm` to move the reference pipeline's extraction judge / residual property values off the decision model |
| `CLASSIFY_CONCURRENCY` / `CLAUSE_CONCURRENCY` / `RAG_INGEST_AFFILIATIONS` / `RAG_FUNCTION_CLASSIFIER` | ingest knobs (mirror `IngestOptions`) |
| `RAG_INGEST_LIST_MODEL` / `RAG_INGEST_CLAUSE_SAMPLES` / `RAG_INGEST_CLAUSE_EXTRACTOR` | **no-ops** for the reference pipeline (clause extraction is classifier-first); pending removal (ING-8d) |
| `RAG_STRUCTURED_TIMEOUT_S` / `RAG_JEV_TIMEOUT_S` / `RAG_JUDGE_TIMEOUT_S` / `RAG_RELEVANCE_TIMEOUT_S` | call timeouts (raise for reasoning-ON bulk work) |
| `RAG_SPACY_MODEL` | the spaCy model name for the optional NER extra |
| `EMBED_DEVICE` / `RAG_SETFIT_DEVICE` / `RAG_SETFIT_CLAUSE_DIR` / `RAG_SETFIT_THRESHOLD` / `RAG_SETFIT_TOPK` | embedder / classifier device + fleet knobs |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `RAG_TRACE_LEVEL` | observability (tracing/usage) |

Secrets belong only in a gitignored `.env`. See [`installation.md`](installation.md) for the minimal set and
[`concepts.md`](concepts.md) for the seams in context.
