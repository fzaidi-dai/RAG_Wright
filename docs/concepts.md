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
  the embedding profile, ingest options (`EngineOptions` / `IngestOptions`), and `pack` (the path to a domain
  `.ttl`; `None` uses the reference contract pack).
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
| `subgraph` | a composite pipeline (ingestion, retrieval, QA, compliance) | `ainvoke_subgraph(name, inputs, resources=ws)` |
| `model` | a model-backed decision (a classifier, a typed-decision model, an LLM step) | `invoke_model` / `ainvoke_model(name, inputs, resources=ws)` |
| `function` | a deterministic function other capabilities compose | imported directly |
| `agent_skill` | authored SKILL.md knowledge loaded into an agent | loaded, not called |
| `mcp_tool` | a capability exposed over MCP | served by an MCP server |

Two things matter for a new domain:

- **Invocable-by-name vs composed-by-import.** A capability with an `impl_ref` ("module:attr" pointing at a
  `(resources, inputs) -> result` factory) is invoked **by name** through the engine — zero engine edits. Capabilities
  without one (most `function`s) are composed by direct import. In the reference pack, 9 of 36 capabilities are
  invocable-by-name; the rest are composed or served.
- **The catalog ships empty.** The runtime ARD catalog (`MANIFEST_SPECS`) is empty on install. A product populates
  it with `register_capability(manifest)`; the reference pack is opt-in via `load_reference_pack()`. The invoker
  resolves `impl_ref` lazily — there is no central adapter table.

Discovery is `capability_index()` (slug → kind + description). Authoring one is the
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
KG edge types), constraints (applicability, cardinality, deontic polarity — authored as SHACL), and
mappings/synonyms. **Code holds mechanism only** (the pipeline, the router, the judge). A new customer domain is a
new pack, not an engine edit — load it with `EngineConfig(pack=…)`. When a generated artifact must carry concerns
the ontology can't express (e.g. prompt engineering), the source of truth stays the ontology and the overlay is
*generated* from it with CI-enforced zero drift — never hand-edited into code.

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
