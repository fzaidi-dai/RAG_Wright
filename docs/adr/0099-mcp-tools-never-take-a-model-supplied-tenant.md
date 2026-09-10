# ADR-0099: an MCP tool never takes a model-supplied tenant; the store is caller-bound per session

**Status:** accepted · **Date:** 2026-09-11 · **Issue:** engine 0035 (RuleWright) · **Extends:** the seam discipline (never a hardcoded provider/flag) to the MCP surface · **Related:** RuleWright ADR-0008 (routing names the database), ADR-0019 §4 (tenancy is product policy)

## Context

All four MCP servers resolved their store from a **process env var** (`QA_DB`, `COMPLIANCE_DB`) at import. That is correct for a demo, an eval, or a single-tenant deployment, but a process env var is per-deployment while tenancy is per-request — so a multi-tenant agent would read whichever database the process happened to start with. The natural repair — adding `database`/`tenant`/`scope` as a tool argument — is the dangerous one: an MCP tool's arguments are filled by a language model, so a tenant argument puts a cross-tenant read one token-prediction away, silently, with a plausible-looking result. This is the highest-consequence failure in a multi-tenant deployment, and it would be controlled by sampling. The engine does not own tenancy (that is product policy), but it owns the rule that its tools refuse a model-supplied tenant, and the seam that lets a caller bind one.

## Decision

Adopt the principle and provide the seam, across all four servers and every server added after them.

**Principle (enforced):** an MCP tool never takes a tenant, database, or scope as a **model-supplied** argument. A build-failing guard test introspects every server's tool input schemas and fails if any exposes such a parameter (`assert_no_tenant_arguments` / `FORBIDDEN_TENANT_ARGS`). The guard's server list is **derived, not maintained**: it discovers every `rag_wright.mcp.*_server` module and its `build_*_mcp` builders (`pkgutil.iter_modules`), builds each with stubbed runners, and checks every registered tool. A server added later is covered the moment it exists — *forgetting* the principle is the failure, not the exemption (RuleWright's 0035 follow-up: a hand-maintained enumeration is disarmed by the same omission it exists to catch). This makes the unsafe shape un-expressible by accident, at server #5 as at server #1.

**Seam (`mcp/session_store.py`):**
- Each `build_*_mcp(...)` factory gains an optional **`store_resolver: Callable[[ctx], Store]`** (sync or async) and an `env_store` fallback.
- Per request, the tool handler calls `resolve_request_store(store_resolver, env_store)`: with a resolver it resolves against the **current MCP request context** (`get_context()`) — the context the caller populated out-of-band (init params / transport headers), opaque to the engine, from which the caller reads its own tenancy; without a resolver it returns the process-wide `env_store` (single-tenant / demo / eval). Neither path is a model argument.
- The injected runner is **store-parametric** (`run_fn(store, *tool_args)`). The tenant-independent pieces (embedder, model ids) are built once per process; only the **store** is per request, so one process serves many tenants without a per-process database.
- The engine defines no tenancy — it only hands the resolver the request context and calls it. Tenancy stays product policy.

## Consequences

- **The unsafe thing is un-expressible.** No engine MCP tool exposes a tenant/database/scope to the model, and the guard test keeps it that way for every future server. A `get_context()`-injected context is never in a tool's input schema, so it is correctly ignored by the guard.
- **A caller can use the engine's MCP servers directly in a multi-tenant deployment** by passing a `store_resolver` at build — no wrapper per server, one description of each capability. This is what RuleWright's agent harness (Phase 5a) needs.
- **No behavior change for single-tenant / demo / eval:** with no resolver, the env-store fallback is exactly today's behavior; the CLI `main()` of each server wires it. The demo runners ignore the store.
- **The engine implements no multi-tenancy.** It provides the rule and the binding seam; how a caller reads tenancy from the request context (init params, headers, an opaque scope token it resolves) is the caller's choice and is never model-visible.
- Cost: one store resolution per request (cheap); the embedder/models stay process-cached.
