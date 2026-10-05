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
| `subgraphs/` | the composite LangGraph pipelines (ingestion, retrieval, QA, compliance) built on `scaffold.py` | **domain graphs** (may import domain) |
| `models/` | the model-profile seam: `ModelRole`, `profiles`, `seam`, `tag_structured`, usage/tracing | yes |
| `store/` | the single ArcadeDB store behind the seam: `arcadedb`, `seam` (`KgNode`/`KgEdge`), `chunk_text` | generic subset **yes** |
| `ontology/` | the `.ttl` packs + loaders + the `EntityRegistry` derivation (knowledge lives here) | `registry` **yes**; contract taxonomy is domain |
| `spans/` | operative-span segmentation + the classifier fleet dispatch | — |
| `contracts/` | Pydantic contracts + the shared identifiers (`chunk_id`, `entity_id`) | generic subset yes; `compliance`/`contract_meta` are domain |
| `reference/` | the reference-pack facades (thin worked-example wrappers over the API) | reference pack |
| `corpus/` | reference corpus adapters (EDGAR, CUAD) | domain (reference) |
| `mcp/` | MCP tool surfaces over registered capabilities | — |
| `skills/` | authored capability `SKILL.md` content | — |
| `okf/` | the OKF bundle-compile path | — |
| `util/` | shared capability-agnostic utilities | yes |

### The domain-free rule (enforced)

The **generic engine foundation** — the `api` layer, the generic store modules, and the generic capabilities
(graph query, retrieval core, the invoker, the registry, embedding, disambiguation, entity resolution) — **must
not import the reference contract/compliance domain pack**. This is a `forbidden` import-linter contract in
`pyproject.toml`, run as a pytest test (`tests/arch/test_import_contracts.py`); a re-coupling import fails the
build. The **domain graphs** under `subgraphs/` (and the domain stores) are deliberately excluded — they are
*allowed* to import domain, because a pipeline is domain-shaped by construction. This is the primitives-vs-domain
distinction: generic primitives stay pure; domain graphs compose them.

## Data flow

### Ingestion (document → KG)

```
parse → chunk → segment → ┬─ extract (typed facts, tag-parse)  ┐
                          ├─ index  (BGE-M3 dense + sparse)     ├─→ write → ArcadeDB (graph + hybrid index)
                          └─ graph  (entities, resolve → id)    ┘
```

Parse is tiered (text-layer-first, per-page VLM escalation). Chunking is a seam (deterministic single-call default).
Segmentation yields operative spans. Extraction produces typed facts carrying provenance + a `ConfidenceTag`;
expensive stages are content-hash gated so re-ingest is cheap. Entity extraction resolves to a canonical
`entity_id`. Everything lands in the one store. The reference pipeline is `subgraphs/contract_ingestion_pipeline.py`
(+ `compliance_ingestion.py`); a new domain supplies its own graph that composes the same primitives.

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
  0033 (unified KG), 0057 (async engine), 0045 (tag-parse).
