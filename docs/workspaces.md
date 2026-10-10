# Workspaces, configuration and processes

How a product runs the engine for several customers, teams or projects at once: what a workspace is, what each
setting applies to (a workspace, one call, or the whole process), and how to manage workspaces.

## A workspace

```python
from rag_wright.api import EngineConfig, StoreConfig, open_workspace

ws = open_workspace(EngineConfig(store=StoreConfig.from_env(), models={"general": "qwen3.8-27b-modal-or"}),
                    corpus="acme_contracts")
```

A workspace is one **corpus** (the backend database: its documents, spans, records and graph) opened with one
**`EngineConfig`**. The handle is opaque: pass it to every engine call that takes `ws` or `resources=`. Data never
crosses corpora: every read and write goes through the handle's store.

## What applies where

| scope | what | how you set it |
|---|---|---|
| **per workspace** | the corpus and everything stored in it, including document metadata | `open_workspace(config, corpus=...)`; `IngestSource(metadata=...)` |
| per workspace | the model for each role (`ModelRole`), including a self-hosted server's `model_id` ([A self-hosted model server](configuration.md#a-self-hosted-model-server-qwen-on-modal)) | `EngineConfig.models`: ahead of `RAG_MODEL_<ROLE>` / `RAG_MODEL_ALL` for every engine call that takes the workspace (`aingest`, the invokers and the MCP tools on them, `agenerate_answer`, `ajudge_spans`) |
| per workspace | the embedding profile, the ingestion tuning, each pack's options | `EngineConfig.embeddings`, `EngineConfig.options` |
| **per call** | the caches (parse, chunk, decisions) | the `cache_dir` you pass to `aingest` / `evaluate_ingestion` |
| per call | usage metering and trace grouping | `measure_usage()`, `traced_run(...)` around the call |
| per call | the workspace's models for a call that takes no workspace | `with use_workspace_models(ws):` (e.g. around `parse_document_bytes`, whose OCR uses `VISION_OCR`) |
| per call | your own context (a user, a request id) inside your hooks and capabilities | a closure when you build the hook, or a `contextvars.ContextVar` you set around the call |
| **per process** | the capability catalog: which implementation each slug resolves to | `register_capability` / `load_pack`, once at startup |
| per process | the serving backend and endpoints, provider routing, call timeouts | `RAG_SERVING`, `VLLM_BASE_URL`, `OPENROUTER_*`, `RAG_*_TIMEOUT_S` |
| per process | the decision model's profile (one call can name another in the `jev_decision` input `model`) | `RAG_DECISION_MODEL` |
| per process | trained classifier weights and their knobs | `RAG_MODELS_DIR`, `RAG_SETFIT_*` |
| per process | one cached handle per corpus, replaced when the corpus is opened with a different config | see "Changing a workspace's configuration" |
| per process | the reference pack's graph-extraction model and its standalone MCP servers (they read the environment when they start) | `RAG_GRAPH_EXTRACT_MODEL`; one server process per deployment |

Concurrent calls for different workspaces in one process are safe: each call resolves its own workspace's models,
and metering and trace scopes belong to the call that opened them.

## Managing workspaces

1. **One corpus per workspace, and its own `EngineConfig`.** Map your tenant (a customer, a team, a project) to a
   corpus name and keep a config object per tenant: its models, embedding profile and options.
2. **Its own `cache_dir`.** Caches are keyed by content, so a shared cache cannot leak one tenant's text to another,
   but a cache per tenant keeps retention and deletion per tenant.
3. **Register capabilities once, at startup.** The catalog is per process (a known limitation, see
   [`architecture.md`](architecture.md#known-limitations)). Tenants that need different implementations of one slug
   run in separate processes, or use one slug per implementation and choose the slug per tenant in your code.
4. **Keep the product's context out of the engine's arguments.** Close over it when you build a hook, or set a
   context variable around the call; the engine carries context variables through its tasks and threads.
5. **Tag documents with your own fields** (`IngestSource(metadata={...})`), filter them with
   `kg_read(ws, "Document", where={...})`, and pass the matching document ids to a query's document scope. Spans
   and records do not carry the metadata. Two behaviours to know:
   - **A re-ingest only adds or updates the metadata keys it is given.** Keys you leave out keep their stored
     values. To remove a value, set it to `None`: `kg_update(ws, "Document", set={"owner": None},
     where={"doc_id": doc_id})`.
   - **Version:** `IngestSource.metadata`, per-workspace models (`EngineConfig.models` for every role-based call,
     `use_workspace_models`) and the labelling check in `evaluate_ingestion` arrive in the release after 0.3.1.
     Require that version or later before relying on them: on 0.3.1, `IngestSource(metadata=...)` raises no error
     but the metadata is silently dropped (an unknown field is ignored), so documents are ingested without it.
6. **One process, many workspaces, one set of process settings.** Where tenants need a different serving backend,
   decision model, timeouts or classifier weights, run a process per such set.

## Changing a workspace's configuration

Open the corpus again with the new `EngineConfig`; no restart is needed. `open_workspace` caches one handle per
store and corpus: an equal config returns that handle, and a different one (other models, options, credentials or
pack) returns a new handle that replaces it for every later call. The new handle reuses the store connection when
the store settings and the pack are unchanged, and the query embedder when the embedding profile is unchanged, so a
model change costs nothing to apply. Calls already holding the old handle finish on the old config.

Configs are compared by value, so build them from your tenant's stored settings on each request if that is
convenient; an unchanged config returns the cached handle. An option object of your own in `EngineOptions.packs`
should compare by value (a dataclass or a pydantic model does); one that compares by identity looks changed every
time, and each call then builds a new handle (still reusing the store and the embedder). `reset=True` drops and
recreates the database, so it is not a way to change configuration.
