# RuleWright handoff: engine issue 0035 resolved — MCP tools never take a model-supplied tenant; store is caller-bound per session

Date: 2026-09-11 · on `origin/main` (commit `cf6c370`) · ADR-0099 · **No breaking change for single-tenant/demo. You can now point your agent at the engine's MCP servers directly in a multi-tenant deployment.**

---

## The principle (adopted, and enforced)

> **An MCP tool never takes a tenant, database, or scope as a model-supplied argument. The store is resolved from session context the caller establishes out-of-band.**

Exactly your ask. It's enforced by a **build-failing guard test** (`assert_no_tenant_arguments`) that introspects every server's tool input schemas — so the unsafe shape is un-expressible by accident, for the four servers today and every server we add after them. No engine MCP tool exposes `database`/`tenant`/`scope`/etc. to the model.

## The seam you asked for

Each `build_*_mcp(...)` factory now takes an optional **`store_resolver`** (and an `env_store` fallback):

```python
# multi-tenant: YOU bind the store per session, out-of-band. The resolver is handed the MCP request
# context (init params / transport headers you populated) -- never a model argument.
def my_resolver(ctx) -> Store:            # sync or async
    tenant = my_tenancy_from(ctx)         # you read your own tenancy from the context
    return ArcadeDBStore.from_env(database=databases_for(tenant).contract)

mcp = build_typed_property_retrieval_mcp(production_retrieval_fn(), store_resolver=my_resolver)
# ...same shape for build_intra_document_qa_mcp / build_relational_qa_mcp / build_compliance_mcp
```

- **With a resolver**, the tool handler resolves the store per request from the current MCP request context (`get_context()`), which the engine treats as opaque — you read tenancy from it however you established it. Sync or async resolver both work.
- **Without a resolver**, it falls back to the process `env_store` (`QA_DB`/`COMPLIANCE_DB`) — today's single-tenant/demo/eval behavior, unchanged. The server CLIs (`python -m rag_wright.mcp.*_server`) wire that fallback.
- The tenant-independent pieces (embedder, models) are built once per process; only the **store** is per request, so one server process serves many tenants — no per-tenant process required.

The engine implements **no tenancy** — it provides the rule and the binding seam only. How you read tenancy from the request context (MCP init params, transport headers, or an opaque scope token you resolve) is yours, and is never model-visible. That matches your placement test.

## What this means for your Phase 5a decision

You can **use the engine's MCP servers directly** in a multi-tenant deployment by passing a `store_resolver` at build — no wrapper per server, one description of each capability. The four affected servers: `typed_property_retrieval`, `intra_document_qa`, `relational_qa`, `compliance` (all three of its tools).

One note on `contract_id` / `start_entity_id`: those remain tool arguments, and correctly so — they are document/entity **keys within the already-resolved (tenant's) store**, not tenant selectors. The tenant boundary is the store, bound by your resolver; the model only names things inside it.

## Verification

Hermetic: the guard test asserts no tool across all four servers exposes a forbidden arg (and is proven to bite on a deliberately-leaky server); the resolver seam is unit-tested (resolver called with the request context, async resolver awaited, env fallback when no resolver). No live infra needed. Full suite 1540 passed. Single-tenant behavior is unchanged (env fallback); your existing CLI usage is unaffected.

Reference: ADR-0099, `mcp/session_store.py` (`resolve_request_store`, `StoreResolver`, `assert_no_tenant_arguments`, `FORBIDDEN_TENANT_ARGS`), the four `mcp/*_server.py` (`build_*_mcp(..., store_resolver=, env_store=)`), `docs/architecture/foundations-and-adding-a-domain.md`.
