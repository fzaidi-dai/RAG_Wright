# ADR-0124: A generic ingestion builder with domain hooks; the legal pipeline becomes the reference instantiation

- Status: Accepted (ING-1: contracts; ING-2..5 build on it)
- Date: 2026-10-07

## Context

The reference ingestion pipeline (`aproduction_document_ingest`) hardcodes three domain decisions inside its stage
functions: the segmenter (`segment_clause`: legal enumeration markers and abbreviations, fused with the contract
function classifier), the extraction UNIT (spans grouped into contract provisions by `Section/Article/Clause N`
headings, a contract-trained boundary decider, and a legal furniture filter), and the extractor. A new domain's
extractor therefore receives a contract provision as its unit, which is a domain leak. The only seam,
`abuild_document_ingest` (seven stage functions), is internal and forces a domain to rewrite indexing, provenance and
writing to change one decision (engine gaps G3/G4).

## Decision

1. **One public ingestion builder; the engine owns the mechanism.** Wiring, chunking, the span index, page
   provenance, ids, writing, `X/N` progress and dead-lettering are engine-owned. A domain overrides only the
   domain-shaped hooks, each with a domain-neutral default:
   `segmenter` (default: a docling-layout-based NLP segmenter), `span_tagger` (default: none), `unit_grouper`
   (default: layout-structural grouping, else the chunk), `boundary_decider` (default: none), `extractor`
   (required), `writer` (default: `kg_write`).
2. **The hook contracts live in `rag_wright.contracts.ingestion`, exported from `rag_wright.api`:** `LayoutItem`
   (an engine-owned closed set of layout kinds, so hooks never depend on docling's labels), `Span`, `TaggedSpan`,
   `Unit`, `UnitExtraction` (the extractor returns `KgNode`/`KgEdge` in the pack's schema), and the hook protocols.
3. **The engine enforces the contract on every hook output, default or override:** `check_tiling` (spans tile the
   chunk text byte-faithfully under `span_id = <chunk_id>#<index>`), `check_units` (known spans, each at most once,
   in order, indexed 0..k-1; dropping a span is allowed, it stays in the index), `check_extraction` (each record
   node cites a span of its unit and carries a `ConfidenceTag`, FR-S.4).
4. **The legal pipeline is the reference pack's instantiation** of the builder (`segment_clause`, provision
   grouping, the Jev decider, the function classifier, the clause extractor and its typed writer), proven
   byte-identical by a parity test. Its legal patterns move to the reference pack as data (ADR-0066).
5. **Hooks are passed as callables to the builder.** Registering a hook by name as a `function` capability is a
   possible later extension, not part of this decision.

## Consequences

- A new domain chooses its own span and unit and writes only its extractor (plus any hook it wants to swap); the
  default path needs no legal knowledge.
- `OperativeSpan` is now an alias of the generic `Span` (its OKF locator became optional), and `BoundaryDecider` is
  one shared contract, so the reference implementations satisfy the hooks by construction.
- The identifier schemes (`chunk_id`, `span_id`, `entity_id`) are unchanged.
