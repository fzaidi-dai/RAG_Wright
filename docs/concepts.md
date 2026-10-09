# Concepts

The mental model behind RAG_Wright, in the order it helps to learn it. Everything here is live in the current
engine; see [`architecture.md`](architecture.md) for how the pieces fit and [`docs/adr/README.md`](adr/README.md)
for the decisions.

## The engine in one paragraph

RAG_Wright turns an unstructured corpus into cited, abstention-willing answers. It parses documents, chunks and
embeds them into a hybrid (dense + sparse) index, extracts a knowledge graph with resolved entities, and answers
questions by retrieving, reranking, and reasoning over that index and graph — always with provenance and
confidence. It is **domain-neutral**: what a document *means* (the vocabulary, the schema, the constraints) lives
in an ontology pack, so the same engine serves contracts, textiles, or policies without a code change.

## Engine vs product (ADR-0052)

This repository is the **engine**. A **product** (a user-facing app) is a separate repository that depends on it,
one way only: **Product → Engine, never the reverse**, enforced in CI by an import-linter rule. The engine stays
domain-retargetable; the product owns UI, orchestration, and domain packs. A **reference pack** (a
contract/compliance worked example) ships in the engine so the open-core is runnable and demoable — it is an
example, not the product.

## Workspace — how you talk to the engine

A product configures the engine once and opens a workspace:

- **`EngineConfig`** — the whole view of the engine: a `StoreConfig` (how to reach ArcadeDB), model aliases by role,
  the embedding profile, the options catalog (`EngineOptions`: the generic ingest knobs in `IngestOptions`, plus
  each domain pack's own options under `EngineOptions.packs`, keyed by pack name), and `pack` (the path to a domain
  `.ttl`; `None` means no domain pack: only the neutral engine schema -- the vertex types `Chunk`, `Entity`, `Span`,
  `Document` and the edge types `Relationship`, `Mentions`, `EmbeddedIn`, `AttachedTo`. The reference contract
  pipeline ensures its own pack schema when it runs).
- **`open_workspace(config, *, corpus, reset=False) -> WorkspaceHandle`** — resolves and caches a workspace and
  ensures the schema. `corpus` is the backend database name (tenancy is the product's concern). The returned
  `WorkspaceHandle` is opaque: the store and embedder are private; you pass the handle to the invokers and the KG
  accessors. The whole public surface is imported from **`rag_wright.api`**.

## Capabilities and ARD — the unit of work

A **capability** is a named, discoverable unit of work (ARD = Agentic Resource Discovery). Each is declared by a
**`CapabilityManifest`** (`slug`, `kind`, `display_name`, `description`, `representative_queries`, optional
`impl_ref`, …) and registered under a canonical slug. There are five kinds a developer uses:

| kind | what it is | how it runs |
|---|---|---|
| `subgraph` | a composite pipeline (e.g. an ingestion, retrieval, or QA graph) | `ainvoke_subgraph(name, inputs, resources=ws)` |
| `model` | a model-backed decision (a classifier, a typed-decision model, an LLM step) | `invoke_model` / `ainvoke_model(name, inputs, resources=ws)` |
| `function` | a deterministic function other capabilities compose | imported directly |
| `agent_skill` | authored SKILL.md knowledge loaded into an agent | loaded, not called |
| `mcp_tool` | a capability exposed over MCP | served by an MCP server |

Three things matter for a new domain:

- **Invocable-by-name vs composed-by-import.** A capability with an `impl_ref` ("module:attr" pointing at a
  `(resources, inputs) -> result` factory) is invoked **by name** through the engine — zero engine edits. Capabilities
  without one (most `function`s) are composed by direct import. In the reference pack, 9 of 36 capabilities are
  invocable-by-name; the rest are composed or served.
- **The catalog ships empty, and the product owns it.** The runtime ARD catalog (`MANIFEST_SPECS`) is empty on
  install and import; nothing in the engine registers capabilities on a product's behalf. A product registers what it
  invokes, once, at startup in its seam: its own pack (`load_pack`), the engine's domain-neutral capabilities it uses
  (`engine_capabilities()` offers their definitions), and the reference pack only if it builds on that worked example
  (`load_reference_pack()`). The contract/compliance capabilities belong to the reference pack, not to the engine. The invoker
  resolves `impl_ref` lazily — there is no central adapter table.
- **Packs and canonical slugs.** A registration is accepted only for a canonical slug. The engine's own generic
  capabilities (generation, the RLM skills, vision-to-text, the `jev_decision` decision model, span relevance
  judgment) are listed by `engine_capabilities()`; a pack adds its own slugs with `register_canonical_slugs(...)`
  (the current set is `canonical_capability_slugs()`). A capability pack is a module that exposes `register()`, and
  `load_pack("<module>")` imports it and calls `register()`; `load_reference_pack()` is just
  `load_pack("rag_wright.packs.compliance.pack")` (the compliance pack registers the contracts pack it builds on first). All of these are imported from `rag_wright.api`.

Discovery is `capability_index()` (the flat `slug → kind + description` listing) or `discover(task, resources=ws)`
(embedding-ranked selection over the live catalog, for an agent planning over the engine). Authoring one is the
[`authoring-a-capability`](domain-adaptation/authoring-capabilities.md) workflow.

## The two seams

The engine isolates its two external dependencies behind seams, so neither leaks into capability code:

- **The store seam.** One store, **ArcadeDB**, holds *both* the hybrid retrieval index and the knowledge graph — no
  cross-store join to keep consistent. Capabilities read and write it through `kg_read` / `kg_write` / `kg_edges` /
  `entities_by_name` over the workspace handle (nodes/edges are `KgNode` / `KgEdge`), never by touching the store
  class.
- **The model-profile seam.** Models are selected by **role** (`ModelRole`: e.g. `STRUCTURED_REASONING` for
  extraction/grading, `GENERAL` for reasoning/generation/vision, `SUMMARIZATION` for chunking) and resolved to a
  concrete model by a **profile**, keyed by model id. OpenRouter is the default; a self-hosted open-model endpoint
  (vLLM) is a supported mode. Structured output and any provider flags live in the profile, never hardcoded in a
  node. A decision model (e.g. Jev) is reached through a `DecisionModelProfile`.

## Knowledge lives in the ontology (ADR-0066)

Domain **knowledge** is declarative and lives in a `.ttl` pack: closed value sets, the schema (classes, properties,
KG edge types), constraints (e.g. applicability, cardinality — authored as SHACL), and
mappings/synonyms. **Code holds mechanism only** (the pipeline, the router, the judge). A new customer domain is a
new pack, not an engine edit — load it with `EngineConfig(pack=…)`. When a generated artifact must carry concerns
the ontology can't express (e.g. prompt engineering), the source of truth stays the ontology and the overlay is
*generated* from it with CI-enforced zero drift — never hand-edited into code.

## Ingestion: the engine's pipeline, the domain's extractor (ADR-0124)

A new domain does not write an ingestion pipeline; it passes hooks to the engine's:

```python
pipeline = build_ingestion(extractor, segmenter=None, span_tagger=None, unit_grouper=None,
                           boundary_decider=None, unit_representative=None, writer=None, tuning=None,
                           document_hook=None)
report = await pipeline.aingest(ws, sources, cache_dir="cache/")
```

- **The engine owns the mechanism:** parse (PDF, Office, spreadsheets incl. hidden sheets), chunk, segment each chunk
  into spans that tile it, index the spans (dense + sparse), group spans into extraction **units**, run the
  extractor on each unit concurrently, write, and record one `Document` node per document. Embedded files and PDF
  attachments are ingested through the same pipeline as child documents, linked with `EmbeddedIn` (child to parent)
  and `AttachedTo` (child to the table-row span it belongs to). Every hook's output is checked against its contract.
- **The domain supplies the `extractor`** (a `Unit` in, a `UnitExtraction` of typed `KgNode`/`KgEdge` records out;
  each fact node or edge carries a `span_id` from that unit and a `confidence`, while a node without a `span_id` is
  shared vocabulary such as a value or taxonomy node). Every other hook is optional: the default segmenter follows the
  docling layout, the default unit grouper is structural (headings start units, record tables become one unit per
  row), and the default writer uses `kg_write`. `document_hook(ws, source_document, chunks)` runs once per document
  after its records are written (for example, a domain's entity graph).
- **Inputs and tuning.** `sources` are paths or `IngestSource` objects (a per-document `doc_id`, `table_mode` of
  `auto`/`record`/`block`, `include_hidden_sheets`). Every structural threshold is in `IngestionTuning`; set it from
  `evaluate_ingestion(sources, cache_dir=...)`, which scores tiling, layout respect, table-row integrity and coverage
  on your own samples without any model call. `table_rows(source_document)` reads a parsed table's rows from the
  cell grid, whole even when the chunker split the table.

The reference contract pipeline (`contract_ingestion_pipeline`) drives these same stages with legal hooks. Hook signatures are in the
[API reference](api/README.md) ("Hook protocols"); the decision record is
[ADR-0124](adr/0124-generic-ingestion-builder-and-hooks.md); the new-domain walkthrough is
[KG construction](domain-adaptation/kg-construction.md).

## The knowledge graph

Ingestion produces two graphs in the one store: an **entity KG** (who/what and how they connect) and a **property
graph** (the typed facts read from each unit). Two identifiers are load-bearing and fixed:

- **`chunk_id`** = source-document id + chunk index + content hash.
- **`entity_id`** = the canonical registry id.

They link chunks to graph nodes, so changing either scheme is an ask-first change. Every fact carries
**provenance** (source document + chunk/span) and a **confidence** tag (`EXTRACTED`, `INFERRED`, or `AMBIGUOUS`);
expensive ingestion stages are content-hash gated so re-ingest is cheap. The rule downstream is absolute: **no
claim without a citation.**

## The reference pack

The engine ships a contract/compliance worked example (the ontology bridges, the ingestion/retrieval/compliance
subgraphs, demo fixtures) so the open-core is runnable. Opt in with `load_reference_pack()`; read it as a template
for your own pack in [`reference-pack.md`](reference-pack.md). Restrictively-licensed evaluation corpora (CUAD/ACORD)
are *not* shipped — they have a separate acquisition path.

## Next

- [Architecture](architecture.md) — the layers and data flow, and the domain-free import boundary.
- [Building a new domain](domain-adaptation/) — ontology, KG construction, entity resolution, capabilities, evals.
- [Quickstart](quickstart.md) — open a workspace, ingest, and ask a question.
