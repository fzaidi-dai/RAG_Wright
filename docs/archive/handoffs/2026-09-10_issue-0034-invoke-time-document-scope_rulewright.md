# RuleWright handoff: engine issue 0034 resolved — `documents` is now per-invoke on the retrieval leg

Date: 2026-09-10 · on `origin/main` (commit `5b17d4a`) · ADR-0098 · **No breaking change. Your build-time `documents` stays valid as a default; pass the per-request scope in the invoke state.**

---

## TL;DR

You were right — a build-time `documents` on a per-customer cached graph is a cross-matter leak, and every workaround is worse than waiting. The retrieval leg now accepts `documents` **at invoke time**, matching `graph_query`:

```python
graph = _corpus_graph_for(tenant)              # build ONCE per customer, cache as you do today
await graph.ainvoke({"query": ..., "clause_type": ..., "value_condition": ...,
                     "documents": workspace_docs})   # per-request scope, no rebuild
```

Semantics are exactly what you asked for and match what shipped in 0031:

- **Invoke-time value wins** over the build-time one. So the build-time `documents=` argument can stay as a default (or be omitted), and **nothing that already calls it changes**.
- **Absent from the invoke state → the build-time value** (today's behavior; `None` = whole corpus).
- **`[]` = scope-to-nothing**, still failing closed.
- **An unknown id still RAISES `UnknownDocumentError`** — and validation now runs at **invoke time** against the effective scope (moved from build time, which is the correct place: an unknown *selection* is caught per request, not once per process when a cached graph happened to be built).
- **A bad selection fails closed *loudly*.** `UnknownDocumentError` propagates out of `ainvoke` — it is **not** retried or degraded to empty results by the retrieval node's graceful-degrade wrapper. So you get an exception, never a silent empty sweep that looks like "no matches".

## What to change on your side

Stop passing `documents` into `build_corpus_retrieval` / the cached-graph constructor as the operative scope (leave it `None`, or as a customer-wide default if you have one), and pass the workspace selection in each `ainvoke` state dict instead — the same place `clause_type` and `value_condition` already arrive. Your `_CORPUS_GRAPHS` cache stays keyed by customer database, unchanged; no per-selection cache key, no per-request rebuild.

`graph_query` is unchanged — it was already call-time and correct. So the counterparty-exposure operation (PR-17) that calls both legs can now scope both the same way.

## Verification note

The store-side filter itself (the `contract_id IN [...]` cut, out-of-scope spans never pooled/embedded/judged) is unchanged from issue 0031, which was live-verified. This change is the invoke-time wiring on top of it, covered by hermetic tests: invoke-time value threads to the store; absent falls back to the build default; `[]` scopes to nothing; an unknown id raises and is not degraded; and a production graph built with a default resolves invoke-over-default correctly. **Worth a quick confirm on your cached-graph path**: build one corpus graph, then `ainvoke` it twice with two different `documents` lists and check the coverage/results scope to each matter (and that a bogus id raises) — that's the exact per-customer-cache scenario this fixes.

Reference: ADR-0098, `subgraphs/typed_property_retrieval.py` (`_State.documents`, the `retrieve` node, `production_typed_property_retrieval`), `capabilities/document_scope.py::validate_documents`.
