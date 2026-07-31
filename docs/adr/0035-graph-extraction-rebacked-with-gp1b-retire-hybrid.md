# ADR-0035: Re-back `graph_extraction` with the GP-1B docling-graph extractor; retire the T23-27 hybrid

- Status: accepted
- Date: 2026-07-31
- Related: FR-C.6, ADR-0012 (structural CONTRACTS_WITH, no proximity edges), ADR-0033 (unified contract KG,
  three legs), GP-1B (docling-graph entity extraction recipe), LG-2c (`graph_extraction` LangGraph subgraph).

## Context

`graph_extraction` (FR-C.6) is a canonical capability: chunk -> ontology-conforming graph facts
(`ExtractionResult`) behind the `Extractor` seam. It shipped as a **T23-27 hybrid stack** -- spaCy NER (typed
mentions) + a Pydantic-contract LLM extractor (clause categories + signing-party `CONTRACTS_WITH`) + an LLM
escalation (hard-case relationships). That hybrid was built early to probe whether entity/relation extraction
would lift **inter-corpus recall** (the work that later became **Leg B**).

Two things settled since:
- **Leg B** (clause facts / inter-corpus recall) is served better by the **typed Clause KG**
  (`typed_clause_extraction` + the KG-5 retrieval core), not by extracted entity/relation facts.
- **Leg C** (party-to-party relational) is served by **GP-1B** -- docling-graph party extraction with
  granite-4.1-8b (`dg_extraction.extract_parties`) -- which populated the relational graph at **real recall
  0.991** (`eval/relational_eval`). The T23-27 hybrid was never used to populate a real corpus graph.

So the hybrid stack no longer earns its keep, while the capability slug `graph_extraction` is spec-mandated
(FR-C.6) and cannot simply be deleted.

## Decision

**Re-back `graph_extraction` with the GP-1B docling-graph extractor and retire the T23-27 hybrid
implementation.** The capability, its `ExtractionResult` contract, its `Extractor` seam, its
`capability_interface` (governed, T44), and the LG-2c subgraph are all unchanged; only the *implementation*
behind the seam changes.

- New default extractor: `DoclingGraphExtractor` -- extracts signing parties via docling-graph
  (`extract_parties`, granite-4.1-8b) and emits ORGANIZATION mentions + structural `CONTRACTS_WITH` facts
  (`parties_to_extraction`), EXTRACTED. Resolution (EDGAR CIK) and the graph write stay downstream
  (`entity_resolution` -> `write_graph`).
- Retired: `SpacyNerExtractor` / `SpacyPipeline` / `NerPipeline`, `ContractExtractor` (+ its clause-category
  `ClauseFact` emission -- now the typed Clause KG's job), `LlmEscalationExtractor` (+ its `AFTER`-style
  INFERRED relationships -- not part of the proven GP-1B path), and their prompts/schemas/tests.
- `extract_fn` is dependency-injected so the extractor stays hermetically testable with no docling-graph / no
  network; `production_extract_fn()` binds the real granite model.
- Observability: docling-graph is a raw-SDK call, so the LG-2c subgraph node now wraps each extractor in
  `raw_llm_span` (was `business_span`, which assumed the retired seam-based LLM extractors were auto-captured).

## Consequences

- One proven extractor instead of three probes; less code, and the capability now reflects what actually
  populates the graph. Clause-category facts are no longer produced here (they are covered by the typed Clause
  KG), and the escalation `AFFILIATE_OF` path is dropped (it was gold-only, not in the GP-1B path).
- The `graph_extraction` slug, contract, ports, and subgraph are preserved -- FR-C.6 stays satisfied and no
  downstream binding changes. The LG-2c subgraph remains generic over any injected extractor list.
- Sets up **KG-7** (the `Party <-> Clause` unifying link) and **LG-3d** (the ingestion pipeline), which compose
  this re-backed `graph_extraction` for the party/relational side of the one unified contract KG.
- `parties_to_extraction` is kept as the shared party fact shape (also the no-LLM CUAD-`Parties` path).
