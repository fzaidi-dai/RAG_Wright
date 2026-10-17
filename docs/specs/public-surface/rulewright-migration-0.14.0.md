# RuleWright: moving to rag-wright 0.14

What 0.14.0 changes for RuleWright (the LangGraph ingestion workstream, `docs/specs/langgraph-ingestion/plan.md`,
LGI-1 to LGI-5; ADR-0124 amended). Do the 0.13 guide first. Nothing here is edited from the engine repo, and nothing
breaks an existing call.

## 1. `build_ingestion` is a LangGraph graph (nothing to change)

`IngestionPipeline.aingest` now runs a compiled LangGraph graph: one run graph sends each source to a per-document
graph of the stages (parse, chunk, segment, index, extract, write, the document hook, the `Document` node, then the
embedded files through the same graph). `pipeline.graph(ws, cache_dir=...)` returns it. Arguments, the
`IngestionReport`, dead-lettering and the progress lines are unchanged: a recorded parity fixture (11 scenarios) and
a live before/after ingest (7 sources, 10 documents, 394 spans) are identical.

Two small differences:

- `IngestionReport.documents` is in source order (it was completion order, which varied run to run).
- Hooks and the writer receive equal, freshly built objects between stages (the graph state holds plain values).

**Your contract pipeline is unaffected**: it drives `IngestionStages` directly (`pack_sdk.IngestionStages`), which did
not change.

## 2. Resuming a long ingest (optional)

`aingest(ws, sources, cache_dir=..., checkpointer=<your LangGraph saver>, run_id="<batch id>")` checkpoints every
stage of every document. If the process stops, the same call with the same sources and `run_id` carries on from each
document's last finished stage; no finished stage runs again. A finished `run_id`, or different sources, raise
`ValueError`. The state is built-in values only, so it works under LangGraph's strict msgpack mode; a tuple in a
record's free-form `props` comes back as a list after a resume. The engine ships no saver: pass yours (the
Postgres saver you use for the harness fits).

## 3. The skills that changed

Re-read these if you link them (ADR-0134; from your installed engine):

- **`building-an-ingestion-capability`**: section 6 step 5 (the pipeline IS a LangGraph graph; a capability's
  `production_<slug>` returns `pipeline.graph(...)`), and a new item, "Resume a long ingest instead of restarting
  it" (your checkpointer, the `run_id`, what a resume skips, the `ValueError` cases, strict msgpack mode).
- **`authoring-a-capability`**: the `subgraph` section's new bullet, "An ingestion capability already has its graph".
- **`using-the-rag-wright-engine`**: the ingestion-surface line names `IngestionPipeline.graph` and
  `checkpointer=` / `run_id=`.

## Verify

Your fast and db suites green on `>=0.14.0`; bump the floor.
