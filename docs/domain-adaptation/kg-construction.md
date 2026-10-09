# KG construction (ingestion)

Ingestion turns a corpus into the two graphs + the hybrid retrieval index that the query capabilities read, all in
the one store. The engine owns the pipeline: `build_ingestion` (in `rag_wright.api`, ADR-0124) runs every stage and
calls your domain's **hooks** for the domain-shaped decisions. You pass a required `extractor` and override any
other hook you need; every other hook has a domain-neutral default.

```python
from rag_wright.api import IngestSource, build_ingestion

pipeline = build_ingestion(
    extractor,                 # required: Unit -> UnitExtraction (your typed records)
    segmenter=None,            # default: layout-based segmenter (headings, paragraphs, list items, table rows,
                               #          sentences)
    span_tagger=None,          # default: none (no soft tags)
    unit_grouper=None,         # default: layout-structural grouping (a heading owns its content; a table is one unit,
                               #          or one unit per row for a database-style table)
    boundary_decider=None,     # default: none (an uncertain boundary does not split)
    unit_representative=None,  # default: none (a unit is labelled and cited by its first member, often a heading);
                               #          your rule picks the member span that represents each unit (its anchor and
                               #          leading tag), e.g. the operative sentence
    writer=None,               # default: the store's kg_write of your KgNode/KgEdge
    tuning=None,               # IngestionTuning; else EngineConfig.options.ingest.tuning; else the defaults
    document_hook=None,        # async (ws, source_document, chunks), once per document after its records are written
                               #          (e.g. an entity graph); its return value is not used
    embedder=None,             # default: the workspace's ingest embedder (an object with encode_batch)
    chunk_discoverer=None,     # default: structural boundaries, a model refining only over-cap sections; pass
                               #          default_chunk_discoverer(guidance="...") to add your domain's wording
)
report = await pipeline.aingest(ws, ["a.pdf", IngestSource(path="b.xlsx", table_mode="record")], cache_dir="cache/")
```

The reference pack's contract pipeline is one instantiation of the same stages (`IngestionStages`): it passes a
legal segmenter, a function tagger, a provision grouper, a decision-model boundary decider, its clause extractor and
its typed writer. Read it as an example of hooks, not as the engine's definition of ingestion.

## The stages

```
per document:
parse → chunk → segment (+ optional tag) → index  (embed each span → the hybrid retrieval index)
                                         → group  (spans → units; Unit.table_row for a one-row unit)
                                         → extract (your records, per unit, concurrently) → write
      → document_hook (optional: e.g. entity graph + resolve) → Document node
      → embedded files / PDF attachments: the same pipeline as child documents (EmbeddedIn / AttachedTo)
```

- **parse**: `aparse_document`: PDF (docling, with tiered OCR), DOCX, HTML, Markdown/text, and spreadsheets (XLSX,
  XLSM, CSV). Content-hash cached, so re-parsing a seen document is free. Hidden spreadsheet sheets are included by
  default (`IngestSource(include_hidden_sheets=False)` skips them and reports them); files embedded in an Office
  document and files attached to a PDF are extracted into the parse's `.embedded` children.
- **chunk**: split the parsed document into chunks (structural boundaries; a model only refines an over-cap
  section). Cached. A document with no text is recorded with its children, not dead-lettered.
- **segment**: split each chunk into spans, the citeable small unit; a table yields one span per row. An optional
  `span_tagger` adds soft tags per span. To try another model behind it (a trained classifier, the decision model),
  see [swapping the decision behind a hook](classification-and-decision-models.md#5-swapping-the-decision-behind-a-hook).
- **index**: embed each span (dense + sparse) into the hybrid retrieval index (`Span`, generic infra).
- **group**: group the document's spans into extraction **units** (`Unit`: its spans, an anchor span, its text).
  A unit holding exactly one data row of a parsed table carries that row as `Unit.table_row` (exact cells).
- **extract**: your `extractor` turns each unit into **typed records** (`UnitExtraction`: `KgNode`s and `KgEdge`s
  in your pack's schema). This is the domain-specific step.
- **write**: persist the records (your `writer`, else `kg_write`), then a generic `Document` node per document.
- **entity graph + resolve**: not run by default. Run your entity extraction and resolution in a `document_hook`
  (see [entity resolution](entity-resolution.md)).

Documents run concurrently (`IngestionTuning.document_concurrency`) and units within a document run concurrently
(`extract_concurrency`). Progress streams as `[ingest] i/N ...` lines (and `child j/M` per embedded file).

## What the engine enforces on your hooks

Every hook's output is checked, default or override alike (`rag_wright.api` exports the checks so you can test your
hooks with them):

- **`check_tiling`**: a segmenter's spans tile the chunk text in order, byte for byte, with
  `span_id = <chunk_id>#<index>`.
- **`check_units`**: a grouper's units use known spans, each at most once, in document order, indexed 0..k-1.
  Dropping a span (page furniture) is allowed; it stays in the span index.
- **`check_extraction`**: provenance (FR-S.4): any node or edge that carries a `span_id` must cite a span of its
  unit; any `confidence` must be `EXTRACTED`, `INFERRED` or `AMBIGUOUS`; a non-empty extraction must cite at least
  once. Nodes without a `span_id` are shared vocabulary (a value or taxonomy node), allowed beside a cited fact.

A unit whose extraction raises or fails a check is recorded and skipped; a document that fails is dead-lettered;
the run goes on. Every node and edge type your extractor returns must be declared in your pack
([ontology authoring](ontology-authoring.md)).

## Tuning, table modes, and checking on your own samples

- **`IngestionTuning`** holds every structural threshold of the default hooks: the unit cap (`max_unit_chars`,
  6,000), the fragment floor, the database-table rule (`RecordTableRule`), the identifier rule for linking embedded
  files to rows (`IdentifierRule`), and the two concurrency limits. Set it on the builder or on
  `EngineConfig.options.ingest.tuning`.
- **`IngestSource.table_mode`**: `auto` (default) decides per table whether it is a database (one unit per row) or a
  form (one unit); `record` and `block` force one mode for that source.
- **`evaluate_ingestion(sources, cache_dir=..., tuning=..., table_labels=...)`** runs the structural stages (parse,
  deterministic chunk, segment, group; no model, no store) on your own samples and reports `passed` / `failures`. Use
  it to set `IngestionTuning` before a full run.
- **`table_rows(source_document)`** returns every parsed table's data rows (`TableRow`: `columns`, `values`,
  `cell(name)`, table ref, sheet or page, row index), read from the parse's cell grid, so a table split across chunks
  comes back whole. What a column means is your domain's decision.

## The report

`aingest` returns an `IngestionReport` (`succeeded`, `failed`, `documents`). Each `DocumentReport` has the counts
(`chunks`, `spans`, `units`, `records`), `extraction_failures`, `span_failures`, `dead_letter` (the reason, if the
document failed), `skipped_hidden_sheets`, `embedded_skipped`, `children`, and the `links` / `unmapped_links` of its
embedded files. Nothing is skipped silently.

## The two graphs (one store)

Ingestion populates two graphs in the same ArcadeDB database — no cross-store join to keep consistent:

- the **record (property) graph**: your domain's typed records, each linked to the span it was read from; the
  substrate for typed-property retrieval and scoped QA. Written by the extract and write stages.
- the **entity graph**: entity nodes and the relationships between them (who/what is in the corpus and how they
  connect); the substrate for relational/multi-hop queries. Written only if your `document_hook` builds it.

Both ride the generic infra types (`Chunk`, `Span`, `Entity`, `Document`, ...) plus the domain vertex/edge types
your pack declared ([ontology authoring](ontology-authoring.md)).

## Load-bearing identifiers (fixed before building; ask-first to change)

- **`chunk_id` = `<source_doc_id>:<chunk_index>:<content_hash>`** (SHA-256 of the chunk content). This is where
  determinism lives: identical `(source_doc_id, chunk_index, content)` → identical id.
- **`span_id` = `<parent_chunk_id>#<span_index>`** — embeds its parent chunk.
- **`entity_id`** = the canonical registry id — resolves surface-form variants to one node, so the entity graph
  doesn't fragment.
- **document id**: by default derived from the file name including its type (`Report v3.xlsm` becomes
  `Report_v3_xlsm`), or set with `IngestSource(doc_id=...)`; an embedded child is `<parent_id>.emb.<sha12>`.

These ids are the join between the index and the graph; changing either scheme breaks that link, so it is an
ask-first change.

## Provenance, confidence, and re-ingest

- **Every fact carries provenance** — its source document and the chunk/span it came from — and a **`ConfidenceTag`**:
  `EXTRACTED` (read directly from a chunk), `INFERRED` (derived by reasoning, not verbatim), or `AMBIGUOUS`
  (supported but with competing readings / unresolved mentions). Downstream, **no claim is emitted without a
  citation**.
- **What is cached on a re-ingest:** in `build_ingestion`, parse and chunk are content-hash cached; spans,
  record nodes and the `Document` node are upserted by key; the `EmbeddedIn` / `AttachedTo` edges are ensured (never
  duplicated). Extraction runs again on every ingest (cache inside your extractor if it is expensive), and the
  default writer creates record edges rather than ensuring them, so re-ingesting a document whose extractor returns
  edges duplicates them unless your `writer` is idempotent (engine gap G8). A boundary decider is not cached either
  (engine gap G6).

## Building your own

Write your extractor (and any hook you want to swap) against the contracts in `rag_wright.api` (`Unit`,
`UnitExtraction`, `KgNode`, `KgEdge`, the hook protocols), test it with the `check_*` functions, measure the
structure with `evaluate_ingestion`, and write its eval first (the `creating-evals` skill). The
`building-an-ingestion-capability` skill is the step-by-step playbook for this page. If other capabilities
should invoke your ingestion by name, wrap it as a `subgraph` capability and register it
([authoring capabilities](authoring-capabilities.md)). Then: [entity resolution](entity-resolution.md).
