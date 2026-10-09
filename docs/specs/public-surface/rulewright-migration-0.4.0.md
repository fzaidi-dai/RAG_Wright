# RuleWright: moving to rag-wright 0.4 (customer workspaces)

What the release after 0.3.1 adds for RuleWright, written from a read-only scan of the RuleWright repo
(2026-10-09). Do the 0.3 guide first (`docs/specs/public-surface/rulewright-migration-0.3.0.md`); nothing here is
edited from the engine repo. The engine-wide picture is `docs/workspaces.md`: what applies per workspace, per call
and per process.

**Short version.** Nothing RuleWright does today breaks. The release lets RuleWright give each customer an engine
workspace with its own models, tag documents with its own fields, and measure unit labelling before ingesting. Use
of these is optional; section 2 is the recommended shape for customer workspaces.

## 1. Nothing breaks

- **Models.** A role now resolves to the active workspace's `EngineConfig.models` first. RuleWright opens no
  workspace yet (its seam builds `ArcadeDBStore.from_env(database=...)`), so no workspace scope is active and its
  models resolve exactly as before: the environment, then the defaults. Its explicit `answer_model_id` keeps
  working.
- **`kg_read`** no longer returns ArcadeDB's `@props` projection hint. RuleWright reads no `@` keys.
- **Threads the engine starts** now carry the caller's context, so usage metering in those paths now counts calls
  it used to miss. Totals measured with `measure_usage()` can rise for the same work.

## 2. Customer workspaces (recommended)

Today each customer has its own databases (`databases_for(customer)`: contract and compliance), the seam builds a
store per database from the environment, and `export_engine_env` republishes the settings into `os.environ`.

1. **One engine workspace per customer database, with the customer's config.** For each database,
   `open_workspace(EngineConfig(store=StoreConfig(host=..., port=..., user=..., password=...), models=...),
   corpus=databases.contract)` (and the same for `databases.compliance`), then `pack_store(ws, ContractKGStore)`
   for the pack store (0.3 guide, section 3). The store settings come from RuleWright's `Settings`, so the
   `ARCADEDB_*` exports are not needed for the workspaces you open; keep them while any path still builds a store
   from the environment.
2. **Models per customer.** Put the customer's model choices in its `EngineConfig.models`. Every engine call that
   takes that workspace uses them, and concurrent requests for different customers each get their own. One
   difference from today's `answer_model_id`: `{"general": ...}` applies to every `GENERAL` call in that
   workspace, ingestion included. To change only the answer model, keep passing `answer_model_id`.
3. **A cache per customer.** RuleWright passes one `DEFAULT_CACHE_DIR` for every customer. Caches are keyed by
   content, so this cannot leak one customer's text to another, but a `cache_dir` per customer keeps retention and
   deletion per customer.
4. **A config change takes effect on the next open.** Open the customer's databases again with the new
   `EngineConfig`: a different config replaces the cached handle (reusing the connection and the embedder when
   only models or options changed), and requests already running finish on the old one. Building the config from
   the customer's stored settings on each request is fine, since an unchanged config returns the cached handle.
5. **The capability catalog is per process.** RuleWright already registers once (`register_engine_capabilities`),
   which is right; every customer in a process shares one implementation per slug.
6. **RuleWright's own workspaces are not engine workspaces.** A RuleWright workspace is a selection inside a
   customer's database. Its current edge-based selection keeps working. To tag documents instead, ingest with
   `IngestSource(metadata={"workspace": slug})` and select with
   `kg_read(ws, "Document", where={"workspace": slug}, fields=["doc_id"])`, then pass those ids to the query's
   document scope.

## 3. Document metadata: two behaviours

- **A re-ingest only adds or updates the metadata keys it is given.** Keys left out keep their stored values. To
  remove a value: `kg_update(ws, "Document", set={"workspace": None}, where={"doc_id": doc_id})`.
- **Version.** Require this release or later before relying on `IngestSource.metadata`: on 0.3.1 it raises no
  error but the metadata is silently dropped (an unknown field is ignored).

## 4. Also available

- `evaluate_ingestion(..., span_tagger=, unit_representative=, unit_labels=)` measures how units are labelled
  against a small gold set, without a knowledge graph (the `creating-evals` skill, "Unit labelling").
- `use_workspace_models(ws)` scopes a call that takes no workspace (for example `parse_document_bytes`, whose OCR
  uses `VISION_OCR`) to the workspace's models.

## 5. Verify

Run RuleWright's suite against the engine checkout (editable path) and its live engine-seam test. If you move to
customer workspaces: open two customers with different `general` models, run a question for each concurrently
inside `measure_usage()`, and check each call was metered on its own customer's model.
