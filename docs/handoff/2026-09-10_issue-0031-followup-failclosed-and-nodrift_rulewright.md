# RuleWright handoff: engine issue 0031 follow-up — fail-closed validation (already shipped) + single-source edge write (added)

Date: 2026-09-10 · **Re:** engine-issue 0031 (two follow-up asks) · on `origin/main` (commit `9a2f041`) · extends ADR-0094 · **No API change.**

---

## Ask 1 — "fail closed, raise on an unknown document id (graph side too)": already shipped in 0031

This was already in the 0031 commit — `graph_query` validates exactly like the span entrypoint does:

- `graph_query(..., documents=[...])` calls `validate_documents(store, documents)` **before** the traversal and raises `UnknownDocumentError` if any id is absent from the store (`known_document_ids()` = union of `Span.contract_id` and `Relationship.source_doc_id`). Same for `production_typed_property_retrieval`.
- It also **fails closed on the edge filter itself**: an edge whose `source_doc_id` is NULL or absent (e.g. not yet backfilled) is *excluded* by `source_doc_id IN [...]`, so a scope can only ever match *fewer* edges, never all of them. Live-verified: clearing an edge's `source_doc_id` makes a scoped traversal return empty (not "everything").

So the boundary is: a bad scope returns nothing (survivable) or raises — it never silently returns everything. Nothing to change here; flagging it so you know it's covered.

## Ask 2 — "write source_doc_id and chunk_id from one source, so they can't drift": added

You're right that `source_doc_id` is a denormalization of `chunk_id` for index-backed scoping, and that a **drifted pair** (a `source_doc_id` disagreeing with its `chunk_id`) would be the invisible failure — the scoping filter would silently admit an out-of-scope edge (a real cross-matter leak, i.e. fail *open*). Fixed structurally:

- The two fields are now written as **one matched pair from a single source** via `_edge_provenance_assignments(chunk_id)`, which emits `chunk_id = …, source_doc_id = <derived from that same chunk_id>`. Both edge writers (`write_graph`, `add_affiliation_edges`) go through it — previously each had its own inline derivation (same input, but two sites that could drift if one were edited).
- The backfill script uses the **same** `_doc_id_of(chunk_id)` derivation, so a backfilled edge is identical to a freshly-written one.
- A test asserts the pair is always consistent, including a tricky `weird.doc-1:2:hash` id (dots/dashes in the doc prefix).

The rule is now documented in the helper: **every edge writer must emit the pair through `_edge_provenance_assignments` and never assign the two fields independently.** So `source_doc_id` cannot silently drift from `chunk_id` at write time.

Full suite 1511 passed; the live span-scope + graph-scope + backfill + unknown-raise check was re-run and still passes.

Reference: commit `9a2f041`, `store/arcadedb.py` (`_edge_provenance_assignments`, `_doc_id_of`, `write_graph`, `add_affiliation_edges`), `capabilities/graph_query.py`, `capabilities/document_scope.py`.
