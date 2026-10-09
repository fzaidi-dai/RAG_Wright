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
| `pack_sdk/` | the pack-author tier (PS-8b): re-exports of the engine building blocks a domain pack's code needs beyond `api` (identifiers, the model seam, the LangGraph scaffold, the store protocol, the generic capabilities a pack composes); a pack imports only `api` and `pack_sdk` | **yes** |
| `capabilities/` | the capability catalog + ARD runtime — `CapabilityManifest`, `registry`, `manifests`, the adapter-free `invoke` client; plus the generic primitives (graph query, retrieval core, embedding, disambiguation, entity resolution, the decision model) | **yes** |
| `ingestion/` | the generic ingestion mechanism (ADR-0124): `build_ingestion` + the shared `IngestionStages`, the default layout segmenter and structural unit grouper, `table_rows`, `evaluate_ingestion` | **yes** |
| `subgraphs/` | the generic LangGraph scaffolding (`scaffold.py`), semantic chunking, graph extraction and observability; domain graphs live in their pack | **yes** |
| `models/` | the model-profile seam: `ModelRole`, `profiles`, `seam`, `tag_structured`, usage/tracing | **yes** |
| `store/` | the single ArcadeDB store behind the seam: `arcadedb` (only the engine types and generic reads/writes; a pack's own store methods live in the pack, e.g. `ContractKGStore`, `ComplianceStore`), `seam` (`KgNode`/`KgEdge`/`NOT_NULL`), `chunk_text` | **yes** |
| `ontology/` | the generic pack-schema reader (`pack_schema`) and the `EntityRegistry` derivation (`registry`) | **yes** |
| `spans/` | `page_map` (page/bbox positions of spans) | **yes** |
| `contracts/` | Pydantic contracts + the shared identifiers (`chunk_id`, `entity_id`) + the ingestion hook contracts | **yes** |
| `packs/` | the REFERENCE PACK, as two domain packs (ING-8c): `packs.contracts` (segmentation, the clause classifier fleet, judges, the boundary decider, the contract KG store (`ContractKGStore`) and the ingestion/retrieval/QA graphs, its ontology and loaders, the CUAD/EDGAR corpus adapters, its MCP servers) and `packs.compliance` (requirement and claim extraction, compliance judgment and checking, its `ComplianceStore`, its ontology and regulation packs, its MCP server), each with a `pack.py` (manifests, canonical slugs, `register()`); compliance builds on contracts. `packs.reference_seam` is a worked product seam over both | reference pack (domain) |
| `corpus/` | the generic document parser, embedded-file extraction, canonicalization and HTTP helpers | **yes** |
| `skills/` | authored `SKILL.md` content for the generic capabilities (a pack keeps its own under `packs/<pack>/skills/`) | **yes** |
| `util/` | shared capability-agnostic utilities | **yes** |

### The domain-free rule (enforced)

The **generic engine** — every package outside `rag_wright.packs` (`api`, `capabilities`, `contracts`, `corpus`,
`ingestion`, `models`, `ontology`, `pack_sdk`, `skills`, `spans`, `store`, `subgraphs`, `util`) — **must not import
any domain pack** (`rag_wright.packs`). A second contract keeps a pack on the public tiers: `rag_wright.packs` may
import `rag_wright.api` and `rag_wright.pack_sdk` (and itself) but no other engine package directly (PS-8c). A third
keeps the packs layered: `packs.contracts` must not import `packs.compliance`, which is built on it. All three are
`forbidden`
import-linter contracts in `pyproject.toml` (`[tool.importlinter]`), run inside the normal test suite as a pytest
test (`tests/arch/test_import_contracts.py`); a re-coupling import fails the build. The reference pack's own
modules (its domain graphs and stores) are the importers, not the imported: they build on the generic engine
through its two public tiers (`rag_wright.api` and `rag_wright.pack_sdk`), because a domain pipeline is domain-shaped
by construction. This is the primitives-vs-domain
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

## Known limitations

- **One capability catalog per process.** The runtime catalog that `register_capability` fills is shared by every
  workspace in the process, and registering a slug replaces it for all of them, including the later steps of a run
  already in progress. Workspaces in one process can differ in store, corpus and configuration, but not in which
  implementation a slug resolves to. A product whose tenants need different implementations of the same slug runs
  them in separate processes. There is no plan to change this; raise it if a product needs it.

## Pointers

- [`ARCHITECTURE_OVERVIEW.md`](ARCHITECTURE_OVERVIEW.md) — the detailed as-built pipeline (diagrams, model defaults,
  the wired capabilities).
- [`docs/specs/engine-platform/SPEC.md`](specs/engine-platform/SPEC.md) — the engine API + capability-runtime
  boundary spec (ADR-0117/0118).
- Key ADRs: 0052 (engine/product split), 0117/0118 (engine API + capability runtime), 0066 (ontology as truth),
  0033 (unified KG), 0057 (async engine), 0045 (tag-parse), 0124 (generic ingestion builder and hooks).
