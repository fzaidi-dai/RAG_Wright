# KG construction (ingestion)

Ingestion turns a corpus into the two graphs + the hybrid retrieval index that the query capabilities read, all in
the one store. Your domain supplies an **ingestion graph** that composes the engine's generic steps; the
[reference pack](../reference-pack.md)'s `contract_ingestion_pipeline` is the worked example of that shape.

## The stages

```
parse → chunk → segment → ┬─ index   (embed each span → the hybrid retrieval index)
                          ├─ extract (your domain's typed records from each unit)      ┐
                          └─ graph   (entities + relationships)  → resolve (entity_id) ┴─→ write → one store
```

- **parse** — `api.parse_document` (PDF, via docling with tiered OCR) or `api.source_document` (already-text).
  Content-hash cached, so re-parsing a seen document is free.
- **chunk** — split the parsed document into chunks (a seam: a deterministic single-call default).
- **segment** — split each chunk into operative units (spans) — the citeable small unit.
- **index** — embed each span (dense + sparse) into the hybrid retrieval index (`Span`, generic infra).
- **extract** — produce your domain's **typed records** from each unit, targeting your pack's schema and value
  sets. This is the domain-specific step (the reference pack extracts clause records; your domain extracts its own).
- **graph** — extract entities and their relationships (the once-per-document structure pass).
- **resolve** — canonicalize each entity to its `entity_id` (see [entity resolution](entity-resolution.md)).
- **write** — persist the records, the entity graph, and the span index into the single ArcadeDB store.

Most of this is engine-generic; only **extract** (and your pack's schema) is domain-shaped. The steps fan out in
parallel per document and are composed by your ingestion subgraph.

## The two graphs (one store)

Ingestion populates two graphs in the same ArcadeDB database — no cross-store join to keep consistent:

- the **entity graph** — entity nodes and the relationships between them (who/what is in the corpus and how they
  connect); the substrate for relational/multi-hop queries.
- the **record (property) graph** — your domain's typed records, each linked to the operative span it was read
  from; the substrate for typed-property retrieval and scoped QA.

Both ride the generic infra types (`Chunk`, `Span`, `Entity`) plus the domain vertex/edge types your pack declared
([ontology authoring](ontology-authoring.md)).

## Load-bearing identifiers (fixed before building; ask-first to change)

- **`chunk_id` = `<source_doc_id>:<chunk_index>:<content_hash>`** (SHA-256 of the chunk content). This is where
  determinism lives: identical `(source_doc_id, chunk_index, content)` → identical id.
- **`span_id` = `<parent_chunk_id>#<span_index>`** — embeds its parent chunk.
- **`entity_id`** = the canonical registry id — resolves surface-form variants to one node, so the entity graph
  doesn't fragment.

These ids are the join between the index and the graph; changing either scheme breaks that link, so it is an
ask-first change.

## Provenance, confidence, and cheap re-ingest

- **Every fact carries provenance** — its source document and the chunk/span it came from — and a **`ConfidenceTag`**:
  `EXTRACTED` (read directly from a chunk), `INFERRED` (derived by reasoning, not verbatim), or `AMBIGUOUS`
  (supported but with competing readings / unresolved mentions). Downstream, **no claim is emitted without a
  citation**.
- **Expensive stages are content-hash gated**, so re-ingesting an unchanged corpus is near-free and ingestion is
  idempotent (write is create-if-absent). A changed document re-extracts only what changed.

## Building your own

Supply an ingestion graph (a `subgraph` capability) that composes the engine's parse/chunk/segment/index/graph
steps and plugs in your domain's extraction over your pack's schema. Register it with `register_capability`
([authoring capabilities](authoring-capabilities.md)); write its eval first (the `creating-evals` skill). Read the
reference pack's pipeline as the template — then: [entity resolution](entity-resolution.md).
