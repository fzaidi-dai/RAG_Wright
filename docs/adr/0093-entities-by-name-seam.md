# ADR-0093: `entities_by_name` — a name→entity lookup on the Store seam (restores what `all_entities` removal broke)

**Status:** accepted · **Date:** 2026-09-09 · **Issue:** engine 0030 (RuleWright) · **Fixes a regression from:** ADR-0091 (PartyTo retirement) · **Related:** FR-S.2/FR-S.3 (identifiers), the entity-resolution clustering key (`normalize_entity_name`)

## Context

ADR-0091 (retire PartyTo) also deleted `ArcadeDBStore.all_entities()`. Its docstring tied it to KG-7 ("every party `Entity`'s node key, name, `chunk_id`"), so it looked PartyTo-specific — but it was the **only** route from a party *name* to a graph *entity*, and that need has nothing to do with PartyTo. RuleWright depended on it for a shipped feature (T-4.6 counterparty exposure) and, after the 0028/0029 bump, four of its live tests failed with `AttributeError: 'ArcadeDBStore' object has no attribute 'all_entities'`. My 0028 handoff's "nothing you call is affected" was wrong; the removal is a regression I introduced.

`graph_query`/`graph_neighbors` take an exact `start_entity_id` and match it verbatim — everything downstream is fine. The missing piece is the **first** step: a user types "Acme Corporation" and the node must be found. The obvious substitutes don't work: `graph_counts` returns a count not entities; reconstructing the `UNLINKED:<key>` surrogate couples the product to a private engine internal (`graph_storage._node_key`) and fails *silently* on a miss; and the product is forbidden from touching ArcadeDB or `_query` directly (engine ADR-0052 / product ADR-0001).

Crucially, `all_entities` was **never on the `Store` protocol** — it was an `ArcadeDBStore` implementation detail RuleWright leaned on as if it were seam surface. So the fix is not to restore the old method verbatim, but to put a name→entity capability *on the seam* and let the engine own the normalization.

## Decision

Add `entities_by_name(self, name: str) -> list[dict]` to the `Store` protocol and implement it on `ArcadeDBStore`.

- **Returns `[{entity_id, name, entity_type}]`** for every stored entity whose name normalizes to the same clustering key as `name`. `entity_id` is exactly the node key `graph_neighbors`/`graph_query` take as `start_entity_id`, so the caller chains the result straight into a traversal.
- **The engine owns the normalization.** Matching uses the SAME `normalize_entity_name` the ingestion side clusters on (the rule that makes "Acme Corp" / "Acme Corporation" / "ACME, Inc." one entity). The caller passes a **raw** name and never re-implements the rule; because `normalize_entity_name` is idempotent, an already-normalized name resolves identically too.
- **A name may resolve to several nodes** — e.g. a resolved (linked) node plus a not-yet-merged unlinked ref that share a clustering key — and **all** are returned, each a valid `start_entity_id`. This is correct: the caller should traverse from each.
- **Implementation is a scan + normalize** (`SELECT entity_id, name, entity_type FROM Entity`, filter in Python by normalized key). The stored `name` is a surface form for both linked and unlinked nodes and cannot be normalized inside ArcadeDB SQL, so the match must happen in the engine. This is the same cost profile as the retired `all_entities` (which scanned and left the caller to normalize); it just moves the normalization to where the rule lives.
- Empty/whitespace/non-entity names (normalized key empty) return `[]`, never an error.

## Consequences

- **The shipped feature is unblocked**, and the capability is now seam surface: any `Store` implementation must provide it (the in-memory test stub was extended to conform), so it is covered by the structural-conformance test and can no longer vanish invisibly.
- **Normalization is centralized.** Callers stop coupling to `_node_key`'s `UNLINKED:` format or re-implementing the clustering rule; a name lookup that matches nothing returns `[]` explicitly rather than silently, unlike a hand-built id.
- **Live-verified** end to end: writing an "Acme Corporation" party, then `entities_by_name("acme corp")` → the node's `entity_id`, fed into `graph_neighbors` → the counterparty. Hermetic tests cover variant/casing collapse, multi-node return, idempotent already-normalized input, unlinked resolution, and empty input.
- **A regression-class lesson:** removing a *public* store method is invisible to every test that does not touch a live store (RuleWright's fast suite stayed green; only `-m ingest` runs failed). The engine's own suite had no caller of `all_entities`, so its removal looked safe. When retiring a public method, its being off the protocol is a reason to check consumers harder, not softer — a seam gap is exactly where an external caller leans.
