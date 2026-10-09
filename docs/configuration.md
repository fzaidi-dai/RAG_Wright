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
    options=EngineOptions(),           # the knobs catalog: generic ingest knobs + each pack's options
    pack=None,                         # path to a domain .ttl; None = no domain pack (neutral engine schema)
)
ws = open_workspace(config, corpus="my_corpus", reset=False)   # corpus = the backend DB name
```

| field | type | default | meaning |
|---|---|---|---|
| `store` | `StoreConfig` | — (required) | how to reach the store |
| `models` | `dict[str,str]` | `{}` | override the model for a `ModelRole` (e.g. `{"general": "ibm-granite/granite-4.2-8b"}`) |
| `embeddings` | `dict[str,str]` | `{"text": "bge-m3"}` | the embedding profile → supported embedder |
| `options` | `EngineOptions` | defaults | the options catalog: `ingest` (an `IngestOptions`) and `packs` (each domain pack's options, keyed by pack name) |
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

`StoreConfig.from_env()` builds one from `ARCADEDB_HOST` (default `localhost`), `ARCADEDB_PORT` (`2480`),
`ARCADEDB_USER` (`root`), `ARCADEDB_PASSWORD` (required) and `ARCADEDB_PROTOCOL` (`http`), for a standalone
entrypoint (a script, an MCP server) that opens a workspace without its own config.

### `EngineOptions` / `IngestOptions` / pack options

`EngineOptions.ingest` (an `IngestOptions`) holds the engine's generic ingest knobs; `EngineOptions.packs` holds each
domain pack's own options object, keyed by the pack's name (ING-8d). The engine passes `packs` through untouched; a
pack reads its entry and falls back to its defaults when it is absent.

| `IngestOptions` field | type | env fallback | meaning |
|---|---|---|---|
| `tuning` | `IngestionTuning` | — | the structural thresholds of the generic ingestion hooks (unit size cap, record-table rule, identifier rule, extraction and document concurrency). `build_ingestion` uses its own `tuning=` argument, else this, else the defaults. Tune it from `evaluate_ingestion` runs on your own samples. |

The reference contracts pack reads `EngineOptions.packs["contracts"]`, a `ContractIngestOptions`
(`rag_wright.packs.contracts.options`); any other type under `"contracts"` raises a `TypeError`. Every field defaults
to `None` = the pack's env/default:

| `ContractIngestOptions` field | type | env fallback | meaning |
|---|---|---|---|
| `classify_concurrency` | `int` | `CLASSIFY_CONCURRENCY` | function-classify parallelism |
| `clause_concurrency` | `int` | `CLAUSE_CONCURRENCY` (default 8) | clause-extraction parallelism |
| `affiliations` | `bool` | `RAG_INGEST_AFFILIATIONS` (default on; `0` turns it off) | run affiliation extraction |
| `function_classifier` | `str` | `RAG_FUNCTION_CLASSIFIER` | `"setfit"` (default, the trained SetFit ensemble) \| `"llm"` |

```python
from rag_wright.api import EngineConfig, EngineOptions
from rag_wright.packs.contracts.options import ContractIngestOptions

cfg = EngineConfig(store=..., options=EngineOptions(packs={"contracts": ContractIngestOptions(clause_concurrency=8)}))
```

## Models and the model-profile seam

Models are chosen by **role** (`ModelRole`) and resolved to a concrete model by a **profile** keyed by model id —
never a hardcoded provider flag. Override a role per workspace via `EngineConfig.models`, or globally via env.

**Per workspace.** A role resolves to the active workspace's `EngineConfig.models` entry first, then
`RAG_MODEL_<ROLE>`, then `RAG_MODEL_ALL`, then the default. Every engine call that takes a workspace (ingestion
through `aingest`, the `invoke_*` / `ainvoke_*` calls and the MCP tools built on them, `agenerate_answer`,
`ajudge_spans`) runs under that workspace's models, and concurrent calls for different workspaces each see their
own. Wrap a call that takes no workspace in `with use_workspace_models(ws):`, for example `parse_document_bytes`
(its OCR escalation uses `VISION_OCR`) or a `default_chunk_discoverer` run. Not per workspace, set once for the
process: the serving backend and its endpoints (`RAG_SERVING`, `VLLM_BASE_URL`, `OPENROUTER_*`), the decision
model's profile (`RAG_DECISION_MODEL`; a call can name one in the `jev_decision` input `model`), the call timeouts,
the trained classifier weights (`RAG_MODELS_DIR`), and the reference pack's graph-extraction model
(`RAG_GRAPH_EXTRACT_MODEL`) and standalone MCP servers, which resolve their models from the environment when they
start.

- **Serving backend** — `RAG_SERVING` selects `openrouter` (default) or `vllm` (self-hosted). For vLLM set
  `VLLM_BASE_URL` and `VLLM_API_KEY`; for OpenRouter set `OPENROUTER_API_KEY` (optionally `OPENROUTER_PROVIDER`, a
  comma-separated provider list, with `OPENROUTER_ALLOW_FALLBACKS` to allow routing beyond it; the contracts pack's
  docling-graph extraction reads its own `OPENROUTER_PROVIDER_ORDER` / `OPENROUTER_SORT`).
- **Roles** (`ModelRole`): `STRUCTURED_REASONING` (extraction/grading/synthesis), `STRUCTURED_REASONING_SECONDARY`
  (the same call class, a selectable fallback), `GENERAL` (reasoning/generation/vision, the local-deployment
  default), `SUMMARIZATION` (chunking/summarization),
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
| `ARCADEDB_HOST` / `ARCADEDB_PORT` / `ARCADEDB_USER` / `ARCADEDB_PASSWORD` / `ARCADEDB_DATABASE` / `ARCADEDB_PROTOCOL` | store connection: `StoreConfig.from_env()` reads them (and the scripts do); `open_workspace` itself reads only the `StoreConfig` it is given |
| `OPENROUTER_API_KEY` / `OPENROUTER_BASE_URL` / `OPENROUTER_PROVIDER` / `OPENROUTER_ALLOW_FALLBACKS` | OpenRouter access + provider routing (`OPENROUTER_SORT` / `OPENROUTER_PROVIDER_ORDER`: the contracts pack's docling-graph extraction only) |
| `RAG_SERVING` / `VLLM_BASE_URL` / `VLLM_API_KEY` / `STACK_URL` | serving backend (openrouter \| vllm) + self-hosted endpoint |
| `RAG_MODEL_<ROLE>` (e.g. `RAG_MODEL_GENERAL`, `RAG_MODEL_VISION_OCR`) / `RAG_MODEL_ALL` / `RAG_GRAPH_EXTRACT_MODEL` / `RAG_DECISION_MODEL` | model overrides (one role / all roles / graph extraction / decision-model profile) |
| `RAG_SEMANTIC_JUDGE` / `RAG_RESIDUAL_EXTRACTOR` | set to `llm` to move the reference pipeline's extraction judge / residual property values off the decision model |
| `CLASSIFY_CONCURRENCY` / `CLAUSE_CONCURRENCY` / `RAG_INGEST_AFFILIATIONS` / `RAG_FUNCTION_CLASSIFIER` | contracts-pack ingest knobs (mirror `ContractIngestOptions`) |
| `RAG_INGEST_LIST_MODEL` / `RAG_INGEST_CLAUSE_SAMPLES` / `RAG_INGEST_CLAUSE_EXTRACTOR` | read only by the contracts pack's legacy tag-parse clause extractor, which the default pipeline does not use (it is classifier-first) |
| `RAG_STRUCTURED_TIMEOUT_S` / `RAG_JEV_TIMEOUT_S` / `RAG_JUDGE_TIMEOUT_S` / `RAG_RELEVANCE_TIMEOUT_S` | call timeouts (raise for reasoning-ON bulk work) |
| `RAG_SPACY_MODEL` | the spaCy model name for the optional NER extra |
| `RAG_MODELS_DIR` | the models root every trained classifier loads its weights from (default: the engine checkout's `data/models` when it exists, else `./data/models`); fill it with `scripts/fetch_reference_models.py` for the reference pack |
| `EMBED_DEVICE` / `RAG_SETFIT_DEVICE` / `RAG_SETFIT_CLAUSE_DIR` / `RAG_SETFIT_THRESHOLD` / `RAG_SETFIT_TOPK` | embedder / classifier device + fleet knobs (`RAG_SETFIT_CLAUSE_DIR` overrides `<models root>/setfit_clause`) |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` / `RAG_TRACE_LEVEL` | observability (Langfuse tracing; see [`OBSERVABILITY.md`](OBSERVABILITY.md)) |

Secrets belong only in a gitignored `.env`. See [`installation.md`](installation.md) for the minimal set and
[`concepts.md`](concepts.md) for the seams in context.
