# RuleWright handoff: engine issue 0030 resolved — `entities_by_name` is now on the Store seam (and I own the 0028 regression)

Date: 2026-09-09 · **Re:** engine-issue 0030 · on `origin/main` (commit `e7065d9`) · ADR-0093 · **New method on the `Store` protocol. This unbreaks T-4.6. My 0028 handoff was wrong — see below.**

---

## First, plainly: this was my regression

My 0028 change deleted `all_entities`, and my 0028 handoff said *"No query-API change … Nothing you call is affected."* That was wrong — you called it, and it broke your shipped counterparty-exposure feature (T-4.6). You were right that `all_entities` had nothing to do with `PartyTo`; I over-read its KG-7 docstring and removed it with the retirement. Apologies for the broken bump.

## The fix (better than restoring the old method)

There is now a **name→entity lookup on the `Store` protocol** — seam surface, not an ArcadeDB detail — with the engine owning the normalization, exactly as you asked:

```python
store.entities_by_name(name: str) -> list[dict]   # [{entity_id, name, entity_type}, ...]
```

- **Pass a raw name.** It normalizes internally with the **same `normalize_entity_name`** the ingestion side clusters on, so "Acme Corp", "Acme Corporation", and "ACME, Inc." all resolve to the same entity. You never re-implement the rule. (It's idempotent, so if you happen to pass an already-normalized string it still works.)
- **`entity_id` is exactly the `start_entity_id`** that `graph_neighbors` / `graph_query` take — chain it straight in.
- **A name can return several nodes** — e.g. a resolved (linked) node *and* a not-yet-merged unlinked ref that share a clustering key. **All** are returned; traverse from each. (This is why the return is a list, not one.)
- **No match → `[]`**, explicitly — not a silent empty like a hand-reconstructed `UNLINKED:<key>` id would give.

Live-verified end to end: write an "Acme Corporation" party → `entities_by_name("acme corp")` → its `entity_id` → `graph_neighbors(...)` → the counterparty.

## What to change on your side

Replace your `all_entities()` + client-side normalization with a single call:

```python
for ent in store.entities_by_name(user_typed_name):     # was: scan all_entities() + normalize yourself
    for nbr in store.graph_neighbors(ent["entity_id"], relationship_type="Contracts With", max_hops=1):
        ...
```

Drop any `UNLINKED:<key>` id-reconstruction and any local copy of the normalization rule — the engine owns it now. If you were doing a full `all_entities()` scan for some *other* reason (not name resolution), tell me what that reader needs and I'll put the right method on the seam rather than have you re-scan.

## The deeper lesson (so it doesn't recur)

You flagged the sharp edge exactly right: **removing a public store method is invisible to every test that doesn't touch a live store.** Your fast suite stayed green; only `-m ingest` caught it — and the engine's own suite had no caller, so removal looked safe. `all_entities` being *off the protocol* was the actual warning sign, and I read it backwards. `entities_by_name` is on the protocol now and covered by the structural-conformance test, so it can't silently vanish again. I've also recorded this as a standing check: when retiring a public store method, its absence from the seam is a reason to look *harder* for external consumers.

Reference: ADR-0093, `store/seam.py` (`Store.entities_by_name`), `store/arcadedb.py` (`ArcadeDBStore.entities_by_name`), `tests/store/test_entities_by_name.py`.
