# RuleWright handoff: engine issue 0031 follow-up — validate against ingested documents + name unknown ids

Date: 2026-09-10 · **Re:** engine-issue 0031 (two more follow-ups) · on `origin/main` (commit `d8aa8d1`) · amends ADR-0094 · **No API change.**

---

## 1. Validation now uses the ingested-document set (so "ingested but indexed nothing" passes)

`store.known_document_ids()` — the set a `documents` scope is validated against — is now the **ingested-document registry** (the `Contract` nodes, one per ingested source document), **not** the narrower union of documents that produced spans or edges.

Concretely: a document that was **ingested but indexed nothing** (unreadable / empty → no spans, no edges) is now a **known** document and **passes** validation. It contributes nothing to the sweep — which you report through your coverage line — instead of raising and taking the whole matter's sweep down with it. This is exactly your point: since you pre-filter your selection to queryable documents before calling, the more permissive set is safe, not sloppy, and one unreadable document should surface in coverage, not as an exception that breaks every sweep in that matter.

Fail-closed is unchanged: an id that was **never ingested** still raises, and the edge filter still excludes any edge whose `source_doc_id` is NULL/absent, so a scope can only ever match *fewer* documents, never all.

Live-verified: a doc with a `Contract` node but zero spans and zero edges passes `validate_documents`; an un-ingested id raises.

**One thing to confirm on your side:** the known set is now keyed on `Contract` nodes. If your data can ever have spans for a document that has **no** `Contract` node (an unusual ingest path), that doc would validate as "unknown". In the normal pipeline every ingested document gets a `Contract` node, so this shouldn't arise — but if your queryable set can exceed the `Contract` set, tell us and we'll widen `known_document_ids` to a union.

## 2. The error names the unknown ids

`UnknownDocumentError` carries the offending ids and the known set as structured attributes — same shape as `UnknownComplianceSourceError`:

- `err.unknown` — the list of ids that were not ingested.
- `err.present` — the **full** known-document set (for programmatic inspection).
- The message **names every unknown id** and **samples** `present` (`[...first 20...] ... (+N more)`), since a real store holds thousands of documents and dumping them all would bury the actual problem.

So a caller can catch it and show precisely which selected documents aren't in the store, without parsing a giant string.

Full suite 1512 passed; the live scope + backfill + ingested-but-empty + unknown-raise checks all pass.

Reference: commit `d8aa8d1`, `store/arcadedb.py::known_document_ids` (now `Contract`-based), `capabilities/document_scope.py::UnknownDocumentError`, ADR-0094 (amended).
