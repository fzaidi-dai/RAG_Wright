# Architecture

How the engine is structured: the layers, the engine/product boundary, the data flow, and the two seams. For the
mental model read [`concepts.md`](concepts.md) first; for the detailed *as-built* pipeline (the reference-pack
contract/compliance flow, with diagrams) see [`ARCHITECTURE_OVERVIEW.md`](ARCHITECTURE_OVERVIEW.md); decisions are
indexed in [`docs/adr/README.md`](adr/README.md).

## The engine/product boundary (ADR-0052)

```
   ┌─────────────────────────────┐        ┌──────────────────────────────────────────┐
   │  Product (separate repo)     │        │  Engine (this repo, open-core)             │
   │  UI · orchestration ·        │ ─────▶ │  rag_wright.api  (the only public surface) │
   │  domain packs · seam         │ import │  capabilities + ARD runtime · subgraphs    │
   └─────────────────────────────┘  one   │  models seam · store seam · ontology · KG  │
                                     way   └──────────────────────────────────────────┘
```

The dependency is **strictly one way — Product → Engine, never Engine → Product** — so the engine stays
domain-retargetable. A product imports only `rag_wright.api`; it never reaches the store, embedder, or model
implementations. The engine ships a **reference pack** (a contract/compliance worked example) so the open-core is
runnable, but the reference pack is not the product.

## Layers

Everything a product touches is re-exported from `rag_wright.api`; the packages below are the implementation.

| package | responsibility | domain-free? |
|---|---|---|
| `api/` | the stable, domain-agnostic public surface: config, workspace, invokers, KG accessors, ids, usage | **yes** (in the enforced source set) |
| `capabilities/` | the capability catalog + ARD runtime — `CapabilityManifest`, `registry`, `manifests`, the adapter-free `invoke` client; plus the generic primitives (graph query, retrieval core, embedding, disambiguation, entity resolution) | generic parts **yes**; reference-pack caps are domain |
| `ingestion/` | the generic ingestion mechanism (ADR-0124): `build_ingestion` + the shared `IngestionStages`, the default layout segmenter and structural unit grouper, `table_rows`, `evaluate_ingestion` | **yes** |
| `subgraphs/` | the composite LangGraph pipelines, built on `scaffold.py` (the reference pack's ingestion/retrieval/QA graphs live here; a new domain adds its own) | generic scaffolding **yes**; **domain graphs** are reference pack |
| `models/` | the model-profile seam: `ModelRole`, `profiles`, `seam`, `tag_structured`, usage/tracing | yes |
| `store/` | the single ArcadeDB store behind the seam: `arcadedb`, `seam` (`KgNode`/`KgEdge`), `chunk_text` | generic subset **yes** |
| `ontology/` | the `.ttl` packs + loaders + the `EntityRegistry` derivation (knowledge lives here) | `pack_schema` and `registry` **yes**; `loader`, the contract taxonomy and the clause template are reference pack |
| `spans/` | the reference pack's legal segmentation, clause classifier fleet, judges and boundary decider | reference pack, except `page_map` (**yes**) |
| `contracts/` | Pydantic contracts + the shared identifiers (`chunk_id`, `entity_id`) + the ingestion hook contracts | generic subset yes; `compliance`/`contract_meta`/`property` etc. are reference pack |
| `reference/` | the reference pack's registration (`pack.py`: its manifests and canonical slugs) + facades (thin worked-example wrappers over the API) | reference pack |
| `corpus/` | the generic document parser and embedded-file extraction, plus reference corpus adapters (EDGAR, CUAD) | parser/embedded/canonicalize/http **yes**; adapters are reference pack |
| `mcp/` | MCP tool surfaces over registered capabilities | — |
| `skills/` | authored capability `SKILL.md` content | — |
| `okf/` | the OKF bundle-compile path | — |
| `util/` | shared capability-agnostic utilities | yes |

### The domain-free rule (enforced)

The **generic engine foundation** — every generic module: the `api` layer, `store`, `ingestion`, `models`, the
generic contracts, `ontology.pack_schema`/`ontology.registry`, the generic capabilities (the ARD runtime and
invoker, parsing, chunking, embedding, hybrid search, reranking, graph query, retrieval core, disambiguation, entity
resolution, the decision model, and more), the generic subgraph scaffolding, the generic `corpus` parser modules
and `spans.page_map` — **must not import any part of the reference contract/compliance pack**. Since ING-8b the
forbidden list is the whole reference pack (its `spans` modules, domain subgraphs, contract/compliance
capabilities and contracts, ontology loader and taxonomy, and `rag_wright.reference`). This is a `forbidden`
import-linter contract in `pyproject.toml` (`[tool.importlinter]`), run inside the normal test suite as a pytest
test (`tests/arch/test_import_contracts.py`); a re-coupling import fails the build. The reference pack's own
modules (its domain graphs and stores) are the importers, not the imported: they are *allowed* to import the
generic engine, because a domain pipeline is domain-shaped by construction. This is the primitives-vs-domain
distinction: generic primitives stay pure; domain graphs compose them.

## Data flow

### Ingestion (document → KG)

```
parse → chunk → segment → ┬─ extract (typed facts, per unit)   ┐
                          ├─ index  (BGE-M3 dense + sparse)     ├─→ write → ArcadeDB (graph + hybrid index)
                          └─ graph  (entities, resolve → id)    ┘
```

Parse is tiered (text-layer-first, per-page VLM escalation). Chunking is a seam (deterministic single-call default).
Segmentation yields operative spans. Extraction produces typed facts carrying provenance + a `ConfidenceTag`;
expensive stages are content-hash gated so re-ingest is cheap. Entity extraction resolves to a canonical
`entity_id`. Everything lands in the one store, with one `Document` node per document (embedded files and PDF
attachments become child documents linked by `EmbeddedIn` / `AttachedTo`).

A new domain does not build this pipeline: it calls `build_ingestion(extractor, ...)` (in `rag_wright.ingestion`,
re-exported from `rag_wright.api`) with its hooks. The engine owns parse, chunk, segment, index, group and write;
the domain supplies the extractor and overrides only the hooks it needs. The default segmenter follows the docling
layout and the default unit grouper is structural. The reference `contract_ingestion_pipeline` drives the same
shared `IngestionStages` with legal hooks (a legal segmenter, a clause-function span tagger, a provision grouper
with a decision-model boundary decider, the clause extractor and writer); `compliance_ingestion` is the reference
pack's other ingestion graph. See [`concepts.md`](concepts.md#ingestion-the-engines-pipeline-the-domains-extractor-adr-0124).

### Query (question → cited answer)

```
question → embed (dense+sparse) → hybrid fuse → (+ property/metadata boost) → rerank → generate (cited)
                                   │
         graph traversal ─────────┘  (relational answers from entities/edges)
```

Retrieval fuses dense and sparse legs in the store, applies a soft property boost, reranks, and generates an answer
in which **every claim carries a citation** (or the capability abstains). Relational questions are answered from
graph structure. The reference query capabilities are `intra_document_qa`, `typed_property_retrieval`,
`relational_qa`, and `compliance_check`, each a hardened subgraph that can also be exposed over MCP.

## The two seams

- **Store seam** — one ArcadeDB database holds both the hybrid retrieval index and the knowledge graph; capabilities
  use it only through `kg_read`/`kg_write`/`kg_edges`/`entities_by_name` (nodes/edges are `KgNode`/`KgEdge`). LanceDB
  is not a default — it is an eval-gated fallback for the retrieval leg behind the query-skill seam.
- **Model-profile seam** — models are chosen by `ModelRole` and resolved by a profile keyed by model id; OpenRouter
  by default, self-hosted vLLM as a supported mode (`RAG_SERVING`). Structured output uses client-side tag-parse
  (ADR-0045); any provider flags live in the profile, never in a node.

See [`concepts.md`](concepts.md) for both seams in context, and [`configuration.md`](configuration.md) for the
knobs.

## Pointers

- [`ARCHITECTURE_OVERVIEW.md`](ARCHITECTURE_OVERVIEW.md) — the detailed as-built pipeline (diagrams, model defaults,
  the wired capabilities).
- [`docs/specs/engine-platform/SPEC.md`](specs/engine-platform/SPEC.md) — the engine API + capability-runtime
  boundary spec (ADR-0117/0118).
- Key ADRs: 0052 (engine/product split), 0117/0118 (engine API + capability runtime), 0066 (ontology as truth),
  0033 (unified KG), 0057 (async engine), 0045 (tag-parse), 0124 (generic ingestion builder and hooks).
