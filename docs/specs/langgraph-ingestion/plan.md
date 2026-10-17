# LangGraph ingestion: plan and ledger (LGI)

Status: approved 2026-10-17 (decisions: sequential index then extract; run graph + document subgraph; serialization measured in LGI-3). Runs before PS-48 (the harness tests onto the fabric fixture).

## Why

`build_ingestion` (ADR-0124) is the engine's generic ingestion path, and every product's ingestion capability is
built on it (TexWright's `texwright_ingestion` and `texwright_screen_ingestion`). It is plain asyncio: `aingest`
runs `IngestionStages` per document with `asyncio.gather` and a semaphore. No LangGraph graph exists on that path.
Yet the `authoring-a-capability` skill defines the `subgraph` kind as "a compiled LangGraph `StateGraph`", and
`building-an-ingestion-capability` tells a product to register its pipeline as a `subgraph`. So every product
registers a "subgraph" that has no graph, and gets none of what the graph gives: durable resume of a long ingest
from a checkpointer, per-node streaming, and the substrate for a later `interrupt()` review step. The graph
version (ADR-0050's async ingestion) exists only in the reference pack (`contract_ingestion_pipeline`,
`async_ingestion`), not on the generic path.

## Goal

`build_ingestion` builds a LangGraph graph, and `aingest` runs it. A product's ingestion capability is then a real
`subgraph`: its `production_<slug>` returns `pipeline.graph(...)`, its `ainvoke` stays `pipeline.aingest(...)`.

**Nothing breaks.** `build_ingestion`'s parameters, `aingest(ws, sources, *, cache_dir)`, `IngestionReport` /
`DocumentReport`, the `[ingest] i/N` progress lines, dead-letter semantics and `IngestionStages` (used directly by
RuleWright's and the reference pack's contract pipelines) are unchanged. Additions only, so a `feat:` (0.14.0).

## Design

```
run graph  (IngestionPipeline.graph(ws, *, cache_dir, checkpointer=None))
  START --Send per source--> document (the per-document subgraph) --> END        report = reducer over documents
                             at most tuning.document_concurrency documents in flight (a semaphore in the node)

document subgraph
  parse -> chunk -> segment -> index -> extract -> write -> document_hook -> document_node -> children --> END
     |any stage raises: the document's DocumentReport.dead_letter = "<Type>: <message>" --> END (never raised)
  children: each embedded file / attachment through the same document subgraph (depth cap 2, as today),
            then its EmbeddedIn / AttachedTo links
```

- **The nodes ARE `IngestionStages`.** One implementation of each stage, now with three drivers' worth of reuse:
  the generic graph, the reference pack's contract graph, and any product driving the stages itself.
- **Order and semantics match today's driver exactly**: stages sequential per document (index before extract),
  documents concurrent up to `document_concurrency`, children after their parent, same dead-letter strings, same
  progress lines. Parity is the acceptance gate, measured, not assumed.
- **State stays small and serializable.** The parse is not held in state: the `parse` node fills the
  content-hash parse cache and later nodes reload from it (they already do, through `IngestionStages.parsed`).
  State holds the chunks, tagged spans, units/extractions and the report fields.
- **Durable resume is opt-in.** `checkpointer=` (any LangGraph saver; the product supplies its own, e.g. Postgres,
  as with the harness: ADR-0139, no Postgres extra in the engine) plus a `run_id` used as the thread id. Re-running
  an interrupted ingest with the same `run_id` skips documents and stages already checkpointed. Without a
  checkpointer, behaviour is today's.
- **No retry policy added.** Today a stage failure dead-letters the document; that stays (a `RetryPolicy` would
  change behaviour and cost). The graph makes it a one-line addition later if a product asks.

## Open questions (decide before LGI-1)

1. **Parallel index and extract after segment?** The reference pack fans them out; on the generic path they are
   independent (embedding vs the extractor). A wall-clock gain on model-backed extractors, but a change of order
   in store writes. **Recommendation: keep sequential here (parity), measure the gain in a later task.**
2. **Run graph or per-document graph only?** The run graph (one `aingest` call over its sources; a "run", named by `run_id`; not a `corpus`, which is the database name) (Send fan-out) makes the whole ingest one
   compiled graph, so the capability is one graph and a checkpointer covers the run. **Recommendation: both, as
   designed (the per-document subgraph is a node of the run graph).**
3. **Checkpoint serialization of the stage values** (pydantic chunks/spans/units, dataclass `KgNode`/`KgEdge`):
   LangGraph's serializer must round-trip them. Measured in LGI-3; if a type does not round-trip, it is converted
   at the node boundary, never by changing the public contracts.

## Ledger

| Id | Task | Acceptance (verify) | Status |
|---|---|---|---|
| LGI-1 | The per-document subgraph over `IngestionStages` (nodes above, dead-letter route, children through the same subgraph); `aingest` drives it per document, documents still fanned out as today. Parity fixture recorded FIRST from the current driver: `IngestionReport`s and store contents for the existing hermetic ingestion fixtures (text, PDF, spreadsheet with embedded children and links, a failing extractor, a failing document) | the recorded parity fixture equal after the change; the whole existing ingestion suite green unchanged; ADR-0124 amended (and ADR-0050's note that the generic path is not a graph) | done 2026-10-17 (`document_graph`; parity 11/11 scenarios; suite 2297 passed) |
| LGI-2 | The run graph: `IngestionPipeline.graph(ws, *, cache_dir)` public, Send fan-out per source, at most `document_concurrency` documents in flight (a semaphore inside the `document` node, so a direct `graph()` caller gets it without passing a config), the report reduced from the documents; `aingest` = run the run graph | parity fixture equal; a concurrency test (peak documents in flight = `document_concurrency`); progress lines identical (the `[ingest] start N=` / `i/N` / `done` lines asserted); API docs regenerated | done 2026-10-17 (`graph()`; parity 11/11; source-order report; suite 2304 passed) |
| LGI-3 | Durable resume: `checkpointer=` and `run_id=` on `graph()` and `aingest`; state round-trips through the saver | hermetic, `InMemorySaver`: an ingest interrupted mid-run (a node raises a non-dead-letter interruption on document 2) resumed with the same `run_id` re-runs no completed stage (extractor and embedder call counts) and yields the same report as an uninterrupted run; without a checkpointer behaviour is unchanged | todo |
| LGI-4 | The capability shape and docs: `authoring-a-capability` (the `subgraph` kind: an ingestion capability's `production_<slug>` returns `pipeline.graph(...)`), `building-an-ingestion-capability` step 5 (resume with the product's checkpointer, the run id), `docs/architecture.md` / `concepts.md` ingestion sections; the product-starter templates if they show the ingest | doc-reference, shipped-skills and ingestion-skill-example guards green; the skill example runs the graph | todo |
| LGI-5 | Live parity and handover: a live `build_ingestion` run on real parses (the PS-47 workbook with an embedded PDF, and a PDF set) before and after: same `IngestionReport`, same store counts per type; a live resume with a real checkpointer; RuleWright and TexWright notes (nothing to change; the new optional resume) | live tests green; the migration notes written | todo |

Each task is one approval gate; one commit per approved task.
