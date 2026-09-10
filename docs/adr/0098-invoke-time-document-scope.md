# ADR-0098: the `documents` workspace scope is per-invoke on typed-property retrieval (not build-time)

**Status:** accepted · **Date:** 2026-09-10 · **Issue:** engine 0034 (RuleWright) · **Fixes a design flaw in:** ADR-0094 (issue 0031's `documents` scope) · **Aligns with:** `graph_query(documents=...)` (already call-time)

## Context

ADR-0094 added the `documents` workspace scope, but placed it as a **build-time** argument on `production_typed_property_retrieval` — baked into the compiled graph's `retrieve_fn` closure. The compiled retrieval graph is expensive to build (embedder + extraction model), so consumers cache it **per customer** for the process lifetime (RuleWright's `_CORPUS_GRAPHS`, keyed by the customer database). A workspace selection, by contrast, is **per request**. Baking `documents` into the cached graph means the first matter to warm the cache imposes its filter on every later matter for that customer — a silent *wrong* filter that re-introduces exactly the cross-matter leak issue 0031 was raised to fix. Every workaround (key the cache by `(db, documents)`; build per request; filter after the fact) is worse than no scope. Meanwhile `graph_query` already takes `documents` at **call time** and is correct; one product operation (counterparty exposure, PR-17) calls both, and could scope only one.

## Decision

Accept `documents` at **invoke time**, in the graph input state, with the build-time argument demoted to a default. Same semantics as already shipped.

- **`_State` gains `documents`.** The `retrieve` node reads `state["documents"]` when the key is present and passes it to `retrieve_fn`; when the key is **absent** it passes a sentinel (`_UNSET_DOCUMENTS`).
- **Invoke-time wins; absent → build default.** `production_typed_property_retrieval(documents=...)` stays as the build-time DEFAULT. Its `retrieve_fn(query, constraints, documents_override)` resolves `docs = default if override is _UNSET_DOCUMENTS else override`. So existing callers that pass a build-time default (or nothing) are unchanged, and a per-request `graph.ainvoke({"query": ..., "documents": [...]})` overrides it.
- **`[]` = scope-to-nothing, `None` = whole corpus** — unchanged, distinguished from "absent" because the state key's presence is what selects override-vs-default.
- **Validation moves to invoke time.** `validate_documents(store, docs)` now runs inside `retrieve_fn` (per invoke) against the *effective* scope, raising `UnknownDocumentError` on an unknown id — the correct place, matching `graph_query`. The build-time validate is removed.
- **A bad selection fails closed, loudly.** `UnknownDocumentError` is re-raised immediately by the retrieve node's degrade wrapper (`_degrading_io`), never retried or degraded to empty results — so an unknown id surfaces as an error, not a silent empty sweep.

## Consequences

- **The retrieval leg is usable with a cached per-customer graph.** RuleWright builds the graph once per customer and passes the workspace `documents` per `ainvoke`, so AC-1.2 (a sweep/workspace question scoped to a matter) holds without a rebuild, an unbounded cache key, or a post-filter. The two legs are now symmetric: `graph_query(documents=...)` at call time and `typed_property_retrieval` `documents` at invoke time.
- **No behavior change for existing callers** — absent-from-state falls back to the build-time default (verified: a graph built with `documents=["docA"]` and invoked with no `documents` still scopes to `docA`; invoked with `["docB"]` scopes to `docB`; invoked with an unknown id raises).
- **Validation cost is per invoke** — one `known_document_ids()` DISTINCT query per scoped sweep. Negligible, and it is what makes an unknown *selection* (not just an unknown build config) raise at the point of use.
- The store-side filter itself is unchanged from ADR-0094 (live-proven there); this ADR is the invoke-time wiring on top of it.
