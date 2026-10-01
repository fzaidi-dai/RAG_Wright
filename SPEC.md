# Spec: Hybrid RAG, Capability Spec

Version 0.1. This is the capability half of the Hybrid Retrieval-Augmented Generation (RAG) specification: the objective, the shared store and identifiers, and the capabilities the system is built from. It is what a coding agent builds through ordinary spec-driven development (plan, break into tasks, implement, test, register each capability). Its companion is the Orchestration Spec, which holds the two graph briefs dispatched to the compiler. The two documents share one vocabulary: the capability names defined here (the FR-C set) are what the orchestration briefs expect to bind, and what the built capabilities are registered under. This document defines and builds capabilities; it does not describe orchestration.

> **Active child spec (2026-10-02):** the engine-platform boundary workstream — publishing a stable engine API layer +
> capability runtime over a de-domained core (ADR-0117, extending ADR-0052/0066/0067) — is specced separately in
> **`docs/specs/engine-platform/SPEC.md`** with its own ledger **`docs/specs/engine-platform/TASKS.md`**. That child
> spec + tasks are the current active plan; this parent spec remains the overall source of truth for the FR-C/FR-S set.

## 1. Objective

We are building a generic hybrid RAG system: the documents knowledge layer that answers questions about an unstructured corpus (policies, standard operating procedures, supplier contracts, menus, recipes, manuals) and holds the relationship graph over that corpus. It is domain-neutral and model-neutral: realized by default through OpenRouter (the same model-serving path the orchestration engine uses), and deployable fully locally on open models (a Gemma 4 class model) where a client requires it, without changing the design. Local deployment is a supported mode, not the premise, which keeps the system reusable across engagements.

The user is the agentic layer, not a person directly. The component is exposed as a set of governed skills over a Model Context Protocol (MCP) interface; agents call it, they do not own it. It answers document and relationship questions; composing it with a structured-analytics path into a unified system is the job of a higher-level orchestration, not this component.

Success looks like correct, cited, abstention-willing answers across the range of question shapes, with end-to-end provenance and confidence, on a corpus that updates incrementally and cheaply after the first bulk load.

## 2. Assumptions (correct these in review)

1. The corpus is documents (text after parsing), not a live analytical database. Structured analytical data is out of scope here; the system only shares canonical entity identifiers with whatever external systems hold that data.
2. Models are served by default through OpenRouter, and the system deploys fully locally on open models where a client requires it (the private-by-design mode). The model-serving path is a profile, not a premise.
3. An enterprise Data Catalog (Nessie or equivalent) exists and can supply an ontology (entity and relationship types) and an entity registry (real entities with identifiers, names, and aliases). Where it does not, extraction degrades to the lightweight path plus an open-ended language model. For the validation corpus, the concrete source is the Contract Understanding Atticus Dataset (CUAD) plus U.S. Securities and Exchange Commission (SEC) Electronic Data Gathering, Analysis, and Retrieval (EDGAR) entity data (see the testing section, the Phase 0 foundation, and ADR-0002): CUAD's expert clause annotations supply the ontology, and EDGAR Central Index Key (CIK) identifiers populate the entity registry as canonical `entity_id`s.
4. The reasoning, generation, and recursive-language-model (RLM) work runs on a Gemma 4 class model (served through OpenRouter or locally); ingestion tiers models down per task where quality allows.
5. The embedding-free path (FR-K) assumes retrieval quality is limited by query representation, not evidence availability. This is corpus-specific and testable: the reachability spike (FR-K.8) measures the pattern per corpus before the path is enabled, so a corpus that does not show it gives FR-K no reason to outperform the hybrid path. Grounded, not assumed: T41 measured query-to-relevant cosine below the random-pair baseline alongside strong clause-clause structure.

## 3. Scope

### 3.1 In scope

The single ArcadeDB store holding both the hybrid retrieval index and the knowledge graph; the shared identifier scheme and provenance; and the capabilities the graphs bind (parsing, chunking, embedding, hybrid search, reranking, graph extraction and storage, entity disambiguation and resolution, reasoning and synthesis, RLM); and the golden evaluation set by archetype.

Multimodal note: multimodal here means vision-to-text at ingestion (a Gemma 4 class model turns images, scans, and diagrams into text), after which everything is text retrieval. True multimodal retrieval (a joint image-and-text embedding space, for example a CLIP-class embedder) is a property of the embedder, not the store, and is orthogonal to it; the store holds vectors of any modality. Adding a multimodal embedder is a deferred item that slots in at any time as an additional vector index in the same store.

Experimental, gated: an embedding-free knowledge-navigation path (FR-K) that compiles the chunk corpus into an Open Knowledge Format (OKF) bundle and retrieves by programmatic traversal of its indexes, frontmatter, and links rather than by vector similarity. It is an alternative retrieval path and a registered capability, never a default; the hybrid path (FR-C.3, FR-C.4, FR-Q.1, FR-Q.2) stays primary. Per-corpus enablement is decided by the reachability spike (FR-K.8); graduation or removal of the path is decided by GATE-3 (section 13).

### 3.2 Out of scope

Multi-vector (ColBERT) embeddings; a typed-functional RLM runtime; a CLIP-class multimodal embedder (the addition that would give true multimodal retrieval); and materializing the canonical entity skeleton, until cross-domain traversal is a real need. A domain Low-Rank Adaptation (LoRA) on top of the grounding, decided from evaluation results.

Note: this component may later serve as a durable memory backend for the orchestration engine. That is an engine-side integration (the engine adapts this component behind its own memory interface, and this component does not know it is being used as one, the same way a general-purpose database does not), and it is out of scope for this spec.

## 4. Tech stack

Parsing: Docling. Single store: ArcadeDB (Apache 2.0, free high availability and clustering), holding both the hybrid retrieval index and the knowledge graph in one multi-model database. ArcadeDB provides dense vector search (the `LSM_VECTOR` Hierarchical Navigable Small World (HNSW) index), sparse and lexical retrieval (the `LSM_SPARSE_VECTOR` posting-list index, which targets Best Match 25 (BM25) style and BGE-M3 sparse vectors), and server-side hybrid fusion (`vector.fuse` by Reciprocal Rank Fusion (RRF)), plus metadata filtering by Structured Query Language (SQL). The chunk record and the graph nodes live in the same database, so a chunk and its extracted entities are connected in one transaction and there is no cross-store join to keep consistent. Embeddings: BGE-M3 (dense plus native sparse from one model). Reranker: a cross-encoder in the BGE-reranker family. The store is reached behind a query-skill seam, so Graphify can serve a prototype corpus and, as a fallback, a separate hybrid vector store (LanceDB) can be substituted for the retrieval leg if ArcadeDB hybrid retrieval underperforms on the Phase 1 evaluation (section 13). Graph extraction: a hybrid stack (docling-graph Pydantic-contract extraction for schema entities, a lightweight path of Named Entity Recognition (NER) plus dependency parsing and Open Information Extraction (OpenIE) for the bulk, an open-ended language model for hard cases). Reasoning, generation, vision-to-text, and RLM: a Gemma 4 class model, served through OpenRouter by default or locally (Apache 2.0, native function calling, long context). Serving runtime and Graphics Processing Unit (GPU) footprint for the local mode are an open question (section 16).

## 5. Shared store, identifiers, and the capability catalog

### FR-S. Shared store and identifiers

- **FR-S.1** One ArcadeDB store holds the corpus: the hybrid retrieval index (one record per chunk, carrying the dense summary vector, the sparse full-text vector, keywords, entities, and metadata) and the knowledge graph (entities and relationships), in the same multi-model database. Both resolve to the same `chunk_id`, and because they share a database, a chunk record and its graph nodes are connected directly rather than joined across stores.
- **FR-S.2** `chunk_id` is the stable identifier for a chunk. Scheme: source-document identifier, plus chunk index, plus content hash. It is fixed before building, because a re-chunk that changes it breaks the link between a chunk and its extracted graph nodes.
- **FR-S.3** `entity_id` is the canonical entity identifier, sourced from the entity registry, shared between graph nodes and any external system that references the same entities (for example a structured data lake or warehouse). This spec does not assume any particular external system; it only guarantees the identifier is canonical and shareable.
- **FR-S.4** Every stored unit carries provenance: source document and chunk for text, and for graph-derived facts a confidence tag of `EXTRACTED`, `INFERRED`, or `AMBIGUOUS`.
- **FR-S.5** The component is reached only through governed skills over MCP. Retrieval and the graph are reached through a query-skill interface, so the store implementation is swappable behind it (Graphify for a prototype graph, and a separate hybrid vector store as the eval-gated fallback for the retrieval leg) with nothing else changing.

### FR-C. Capability catalog (the authoritative capability names; each is built and registered)

This is the shared vocabulary. Every capability an orchestration brief expects to bind is one of these, and the coding agent builds and registers each one. The list is the expected backlog; the authoritative, contract-level detail for each is finalized as it is built.

- **FR-C.1 Parsing (Docling).** Turns source documents (Portable Document Format (PDF), Office files, scans) into a clean structured representation (reading order, headings, sections, tables, Optical Character Recognition (OCR)), parsed once and reused by chunking, embedding, and extraction.
- **FR-C.2 Embedding (BGE-M3).** Produces dense vectors over summaries and native sparse vectors over full chunk text, from one model.
- **FR-C.3 Hybrid search (ArcadeDB).** Fuses dense-over-summary and sparse-over-full-text results server-side by RRF into one ranked candidate list, honoring metadata filters.
- **FR-C.4 Reranking (BGE-reranker).** A cross-encoder that reranks candidates, the precision gate before any expensive synthesis.
- **FR-C.5 Graph query (ArcadeDB).** Answers relational and multi-hop questions via graph query, returning an answer with cited `chunk_id`s, `entity_id`s, and confidence tags, from the same store as the retrieval index.
- **FR-C.6 Graph extraction.** A hybrid stack: docling-graph Pydantic-contract extraction for schema entities, a lightweight NER-plus-dependency-plus-OpenIE path for the bulk, and an open-ended language-model escalation for hard cases, all conforming to one ontology.
- **FR-C.7 Entity disambiguation, canonicalization, and resolution.** Two stages, so the graph does not fragment across surface-form variants. First, mention disambiguation and canonicalization (the `entity_disambiguation` capability): normalize surface forms (legal-suffix and whitespace or punctuation variants), reject non-entities (template placeholders, role artifacts, over-broad or degenerate matches such as a bare "Bank"), and cluster the mentions that denote one real-world entity into canonical clusters, shaped as proposals for human verification, not auto-committed merges. Second, closed-world linking (the `entity_resolution` capability): resolve each canonical cluster to the registry's canonical `entity_id` against the known set. Recognition (FR-C.6) detects mentions, disambiguation canonicalizes them, resolution links them. Full coreference (pronouns, definite descriptions) is deferred behind a real seam. Human name-to-CIK verification verifies the proposed clusters, which keeps it scaling with entity count, not mention count.
- **FR-C.8 Ontology and registry derivation.** Pulls the entity and relationship types (as Pydantic models) and the populated entity registry from the Data Catalog.
- **FR-C.9 Reasoning, generation, and vision-to-text (Gemma 4).** Two capabilities on the same Gemma 4 class model, registered under two slugs so discovery ranks each on its own intents (ADR-0014): `generation` — the answer generator (grounded, cited, confidence-aware, abstention-willing) — and `vision_to_text` — the image-and-scan-to-text step at ingestion. They have different inputs (retrieved evidence vs. an image), different callers (the query path vs. ingestion), and different failure modes, so a single bundled slug diluted discovery for both. The reasoning model inside RLM is the same GENERAL-role model, bound at whichever node needs it (not a separate slug).
- **FR-C.10 RLM skill.** An authored skill (a SKILL.md with instructions, built as ordinary software in Phase 1, not provided by any build tool) that teaches the divide-and-conquer method: load a working set into an interpreter, slice and dispatch the work in code, and synthesize the results. The method is applied by two registered capabilities: the RLM chunking capability (ingestion) and the RLM synthesis capability (query). The authored method may itself register as a shared skill that those two require (an `agent_skill` `requires` closure), so the method is loaded once and reused. It is a required capability wherever a graph binds it.

### FR-C canonical slugs (the cross-spec join key)

Each capability is registered under a canonical slug, and that slug is the join key: it is the name the built capability registers under, and the name an orchestration brief uses when it must bind a specific capability. The slug is the semantic capability name, not the requirement id (use `hybrid_search`, never `fr-c-3`), so it survives spec renumbering. The registered capabilities and their slugs:

`parsing` (FR-C.1), `embedding` (FR-C.2), `hybrid_search` (FR-C.3), `reranking` (FR-C.4), `graph_query` (FR-C.5), `graph_extraction` (FR-C.6), `entity_disambiguation` and `entity_resolution` (the two-stage canonicalize-then-link capabilities, FR-C.7), `ontology_registry_derivation` (FR-C.8), `generation` and `vision_to_text` (the answer generator and the scanned-image transcription, split from FR-C.9, ADR-0014), `fusion` (the union-and-deduplicate capability, FR-Q.4), `rlm_chunking` and `rlm_synthesis` (the two capabilities that apply the RLM skill, FR-C.10 with FR-I.1 and FR-Q.5). If the authored RLM method is registered as its own shared skill, its slug is `rlm_method`, required by `rlm_chunking` and `rlm_synthesis`.

ARD-manifest scope (which slugs the GraphWright compiler discovers by query, so which author an ARD manifest). A canonical slug and an ARD manifest are not the same thing; the rule is not "is it a slug" but "is it discovered by query at compile time." Three categories:

1. **Query-discovered capabilities** — canonical slug **and** an ARD manifest. The compiler discovers them by representative query and binds them into the ingestion/query graphs. All FR-C / FR-Q capabilities above, including the split `generation` and `vision_to_text`.
2. **Seam-bound pipeline nodes** — **no** canonical slug and **no** manifest. They bind the store seam and are wired into the ingestion graph by the compiler, not discovered by query: `chunk_write` (FR-I.3) and `graph_storage` (FR-I.4).
3. **Foundation derivations** — a canonical slug (a real capability with a contract and an internal registry entry) but **no** manifest, because they run **before** compilation and their output is an input to the graph rather than a node the compiler binds: `ontology_registry_derivation` (FR-C.8). The compiler never searches for it, because by the time it runs the ontology already exists.

The Orchestration Spec references most capabilities behaviorally (resolved by discovery against representative queries) and names only the must-bind capabilities explicitly; those explicit names must be exactly these slugs. Registration URNs embed the slug as the final segment (`urn:air:dreamai.io:rag_wright:<slug>`).

### FR-K. Embedding-free knowledge navigation (experimental, gated by GATE-3)

An alternative, embedding-free retrieval path, never a default: compile the chunk corpus into an Open Knowledge Format (OKF) bundle (Google's Apache 2.0 spec, a directory tree of markdown files with You Ain't Markup Language (YAML) frontmatter, one concept per file, cross-linked by standard markdown links, only a non-empty `type` field required) and retrieve by programmatic traversal of its indexes, frontmatter, and links instead of vector similarity. It is a general, corpus-neutral capability; whether it is enabled for a given corpus is decided per corpus by a cheap model-free reachability spike (FR-K.8, GATE-3a) before any traversal model call is spent. The Atticus Clause Retrieval Dataset (ACORD) is the first validation corpus, chosen because its measured query-representation gap (T41) is the condition this path bypasses and its gold is nearly free (pre-segmented clauses, graded query-clause pairs).

- **FR-K.1 Bundle compile.** One OKF-conformant markdown file per chunk: non-empty `type` frontmatter, body from the chunk-text sidecar (FR-I.3), root stamped with `okf_version` and the compile-recipe version. No re-chunk; `chunk_id` identity is unchanged (FR-S.2). The bundle is a gitignored, rebuildable data artifact.
- **FR-K.2 Signpost enrichment.** Populate the fields a traversal filters on without reading a body: `type`, `tags`, clause category, source document. The category signpost is manufactured by classifying each chunk into a **corpus-appropriate label set** through the model-profile seam (`graph_extraction`'s ontology where the corpus fits it, a corpus-supplied taxonomy otherwise), not assumed to ship with the corpus; index descriptions reuse the existing chunk summaries (FR-I.6). Category coverage and confidence are reported, since that quality is the retrieval ceiling and the control-arm mechanism. (ACORD finding, 2026-07-22: `graph_extraction`'s fixed 41 CUAD categories cover only 67% of ACORD's gold-clause mass, since Indemnification and Affirmative Covenants have no CUAD home, whereas a direct classifier into ACORD's own 9 attorney categories agrees with the qrels-induced labels 91.6%. The corpus-appropriate classifier, not the fixed CUAD ontology, is the signpost source; ADR-0022.)
- **FR-K.3 Cross-linking.** Standard markdown links (absolute from the bundle root, not double-bracket wikilinks) between related concepts, derived from measured relationship structure, bounded in density. Navigation affordances for a model reading signposts, not a similarity-expansion mechanism.
- **FR-K.4 Index and conformance.** `index.md` per directory (a real discriminating description per entry, drawn from the chunk summary), `log.md` for incremental recompiles, and a lint (parseable frontmatter, non-empty `type`, resolvable links) that reports orphan rate, description coverage, and broken-link ratio as numbers, not pass or fail alone. Index and description quality is the retrieval ceiling of the whole path.
- **FR-K.5 Navigation primitives.** Deterministic, model-free reads over the bundle (enumerate, read index, parse frontmatter, filter by predicate, resolve link), exercised from interpreter code by Programmatic Tool Calling (PTC).
- **FR-K.6 Traversal.** Progressive disclosure from the bundle root: filter subtrees by frontmatter and description, expand the frontier by links and index entries, dispatch one reader sub-agent per surviving body, deduplicate against a visited set, stop on convergence or the depth and frontier bounds. Returns a `chunk_id` shortlist plus the traversal trace; computes no query-to-chunk similarity anywhere. Each reader sub-agent receives its body and the query by enforcement, not orchestrator model choice (the FR-Q.5 lesson). The model binds through the existing model-profile seam; depth and frontier bounds and the PTC allowlist are run configuration, not a new seam. One interpreter session per traversal (ADR-0020).
- **FR-K.7 Bundle lifecycle.** Incremental recompile, update, and delete, sharing the FR-I.5 content-hash gate so the bundle cannot drift from the store; deleting a chunk repairs or records the links that pointed at it.
- **FR-K.8 Reachability analysis (evaluation-side, per-corpus spike).** Deterministic, model-free computation of whether a gold chunk is discoverable from the bundle root through signposts within the depth and frontier bounds, reporting connectivity reachability and signpost reachability separately and ablating one signpost channel at a time. It is both the compile-side ceiling (a scaffolding failure versus a policy failure) and the per-corpus enablement spike (GATE-3a): a ceiling below the grounded bar redirects the experiment for the cost of a compile and an analyzer, no traversal model call spent.
- **FR-K.9 Strategy memory (post-graduation).** Store verified navigation strategies (parameterized, keyed on structural features not query embeddings) with their compile-recipe and run-parameter versions, and reuse them on later queries of similar shape; offline admission is gold-verified, production admission is judge-approved. Memory state (cold or warm) is declared for any measurement. Built only if FR-K graduates at GATE-3.

Slugs and ARD scope (the section-5 three-category rule): `okf_compile` (FR-K.1 through FR-K.4, the compile pipeline as one capability) is **category 3** (foundation derivation, canonical slug and internal registration, no manifest, same shape as `ontology_registry_derivation`); `okf_navigate` (FR-K.6, the traversal) is **category 1** (query-discovered, canonical slug and an ARD manifest with representative queries). FR-K.5 registers nothing (the internal PTC surface `okf_navigate` uses); FR-K.8 and FR-K.9 are evaluation and infrastructure software and register nothing beyond `okf_navigate`.

## 6. Ingestion-side capability requirements

These detail the capabilities the ingestion graph binds. They describe capability behavior, not graph wiring.

- **FR-I.1** The RLM chunking capability reads the whole parsed document through an interpreter (so it is not bounded by a context window), using the RLM skill (FR-C.10), and splits along topic, section, or chapter boundaries into semantically coherent chunks (variable size, capped at roughly 20,000 tokens), writing a summary per chunk, a manifest per source document, and stable `chunk_id`s. It is deterministic and reliable: temperature zero or structured output, boundary validation, and a content-hash gate so an unchanged document is not re-chunked.
- **FR-I.2** A less reliable small chunking model handles the bulk for throughput; any document that fails boundary validation is re-run on a larger model.
- **FR-I.3** The chunk record holds `chunk_id`, the summary, the dense summary vector (BGE-M3 over the summary), the sparse full-text vector (BGE-M3 over the full text), extracted keywords and entities, and source metadata.
- **FR-I.4** Graph extraction (FR-C.6) runs over the parsed documents, canonicalizes and then resolves mentions to registry `entity_id`s (FR-C.7, disambiguation then linking), and writes nodes and edges that carry the originating `chunk_id` into the same store, conforming to one ontology, gated by content hash.
- **FR-I.5** The store updates incrementally: chunk records upsert by `chunk_id`; graph extraction and RLM chunking are each content-hash gated; the pipeline is resumable and idempotent, with per-document and per-chunk checkpoints and a dead-letter queue for failed documents.
- **FR-I.6** Ingestion tiers models per task (a smaller model for chunking and summarization, classical CPU methods for keyword and entity metadata, the BGE-M3 encoder for embeddings, the multimodal model only for the image and scan subset, a larger model for quality-sensitive extraction), decouples CPU interpreter loops from pooled GPU inference, and applies backpressure between stages. It runs in two modes over one pipeline: a bulk mode that takes the whole machine, and a low-priority background mode that yields to live serving.

## 7. Query-side capability requirements

These detail the capabilities the query graph binds.

- **FR-Q.1** Hybrid search (FR-C.3) fuses dense-over-summary and sparse-over-full-text by RRF server-side in ArcadeDB into one ranked candidate list, honoring metadata filters.
- **FR-Q.2** The reranker (FR-C.4) cuts the candidate list to a top set before any expensive work.
- **FR-Q.3** The graph answer (FR-C.5) returns cited `chunk_id`s, `entity_id`s, and confidence tags, treated as evidence, not truth.
- **FR-Q.4** A fusion capability unions and deduplicates on `chunk_id` between the reranked top set and the graph-cited chunks, capped, not a score fusion (the graph returns an answer, not a comparable ranked list).
- **FR-Q.5** The RLM synthesis capability, using the RLM skill (FR-C.10), loads the candidate chunks into an interpreter as data, slices and filters in code, and recursively calls sub-models on the small focused portions, so it never attends over the full chunk volume.
- **FR-Q.6** The answer generator (FR-C.9) produces grounded, cited answers (no citation, no claim), is confidence-aware, and abstains when the retrieved context does not support an answer.

## 8. What the knowledge graph holds

The graph is the relationship layer over the corpus. It earns its place on question shapes flat retrieval handles poorly: variable-depth or recursive traversal, pathfinding between entities, connection-shape pattern matching, and bridging document-derived relationships to canonical entities for multi-hop questions. What goes in the graph is document-derived relationships (the primary content) and, only if cross-entity traversal is needed, a thin canonical entity skeleton (entity nodes, identifiers, names, and types, no facts). Heavy structured data does not go in the graph; where it exists in an external system, it is referenced by the shared `entity_id`. Composing this graph with a structured-analytics path into a unified system is the job of a higher-level orchestration above this component, not a concern here.

## 9. Commands

To be finalized at Phase 0 with the environment. Placeholders: ingest a corpus, ingest or refresh a single document, run a query, build or refresh the ontology and registry, run the evaluation suite, and run the per-source ablation. Full executable commands with flags are recorded once the runtime is chosen.

## 10. Project structure

To be finalized at Phase 0. Expected shape: a package for the shared capabilities, the ontology and registry derivation, the evaluation suite and golden set, and the MCP skill surface. This application is a separate repository from the orchestration engine; it binds the engine, it is not part of it. The graph briefs live in the companion Orchestration Spec.

## 11. Code style

Practitioner voice in prose and comments; Pydantic models for the ontology, the extraction contracts, and the shared identifiers; deterministic capabilities (chunking, extraction gates) written so their determinism is testable (temperature zero or structured output, explicit validation). House style in all written artifacts: expand each acronym on first use, no em dashes, plain phrasing. One real snippet per capability contract is added at Phase 0.

## 12. Testing and evaluation strategy

Validation corpus (ADR-0002): the Contract Understanding Atticus Dataset (CUAD), a subset of roughly 100 to 150 contracts (about 100MB, deliberately including some scanned filings so the Docling parse and vision-to-text path is exercised), together with the SEC EDGAR entity data the contracts come from. The golden set is built by archetype from this corpus: CUAD's expert clause annotations give ground truth for the exact and lexical archetype, the semantic archetype, and clause-finding answer-and-citation questions; the EDGAR party-and-entity graph is used to construct the relational and multi-hop questions, since CUAD alone is single-document clause extraction and under-tests the archetype the graph layer exists for. Known limitation: this is single-domain legal, so the generic-RAG generality claim needs validation on a second domain later.

Two levels. Capability tests: each capability (FR-C) is a tested unit with its own contract, built and verified in isolation. End-to-end evaluation: a golden question-and-answer set split by the four archetypes, measuring retrieval recall at k per archetype (each leg, text and graph, measured separately since recall is bounded by their union); the summary-miss failure mode specifically (a detail dropped from the summary that the sparse and graph legs also miss); chunk-boundary quality and summary fidelity (A/B the RLM chunker against a simpler baseline so it earns its cost); entity-resolution quality (do mentions resolve to the right `entity_id`, and how badly does the graph fragment when they do not); end-to-end answer quality, faithfulness, and citation correctness; latency and its tail (to set routing thresholds from data); and a per-source ablation (turn off text retrieval, then graph, then RLM synthesis, and see what the evaluation set loses by archetype).

## 13. Phased build

Eval-justified, thin slice first, depth over breadth.

Phase 0, foundation: assemble the corpus (the CUAD contract subset of roughly 100 to 150 contracts including some scanned filings, plus the SEC EDGAR entity data; confirm the Creative Commons Attribution 4.0 license at download), fix the chunk and identifier scheme, derive the ontology (the 41 CUAD clause categories plus party and entity types) and the entity registry (EDGAR CIK identifiers as canonical `entity_id`s), and build the archetype-split golden evaluation set (CUAD expert annotations for the exact, semantic, and clause-finding archetypes; multi-hop questions constructed from the EDGAR party-and-entity graph for the relational archetype). Everything after is measured against it. See ADR-0002.

Phase 1, the strong baseline: build the parsing, RLM chunking (deterministic, gated), embedding, ArcadeDB hybrid index (dense summary vectors plus sparse full-text vectors, fused by RRF), and reranking capabilities. A/B the RLM chunker against a simpler chunker to confirm it earns its cost, and validate ArcadeDB hybrid retrieval against the archetype recall bar. If ArcadeDB hybrid retrieval underperforms the recall bar, substitute a separate hybrid vector store (LanceDB) for the retrieval leg behind the query-skill seam; this is the one eval-gated fallback, not a default.

Phase 2, the graph layer: build the graph extraction, entity disambiguation and canonicalization, entity resolution, and graph query capabilities, in the same store (Graphify usable behind the query-skill seam for a prototype corpus), and re-measure where the relational and multi-hop archetype shows gaps.

Phase 3, the RLM synthesis tier: build the RLM synthesis capability for the complex queries the cheaper paths miss, add prefix and result caching, and measure round-trip latency and its tail to inform routing thresholds.

Phase 4, case-by-case extras: multi-vector embeddings, a typed-functional RLM runtime, a CLIP-class multimodal embedder (added as an additional vector index in the same store, since the embedder is orthogonal to storage), and the canonical skeleton, each added only when a use case and the evaluation set justify it.

Phase 5, experimental retrieval recomposition: the T41 composition-experiment backlog (an LLM reranker, category label-retrieval, base-pool sizing) plus the embedding-free OKF path (FR-K), each measured against the corpus baseline as an add-or-remove-a-capability, recompile, new-number loop. The OKF path is built in two stages separated by a cheap model-free gate. **GATE-3a (reachability ceiling, per-corpus):** after the bundle compiles and the reachability analyzer and category-label control run, read the ceiling; if gold is not signpost-reachable within bounds, or the coarse-label control already captures the available lift, redirect for the cost of a compile and an analyzer with no traversal model call spent. **GATE-3 (graduate or remove):** after the traversal capability and the single-query trace-and-iterate loop run, adjudicate whether FR-K graduates from experimental, on evidence against the category-label control, not against the two-leg baseline alone.

Note: the phases build capabilities. The graphs that orchestrate these capabilities are compiled separately from the Orchestration Spec, once the capabilities a graph binds are built and registered.

## 14. Boundaries

- Always: carry provenance and confidence on every answer; gate expensive ingestion stages by content hash; resolve entities against the registry; treat the graph's answer as evidence to be verified, not truth; keep retrieval and the graph behind the query-skill seam; register each built capability under its FR-C name; keep FR-K a registered alternative retrieval path, never the default; keep `chunk_id` identity unchanged across a bundle compile; stamp the compile-recipe version into the bundle root; run the reachability spike before enabling FR-K for a new corpus; keep the compiled bundle a gitignored, rebuildable data artifact.
- Ask first: changing the `chunk_id` or `entity_id` scheme (it breaks the link between chunks and graph nodes); adding a model or a store; materializing the canonical entity skeleton; switching a client to local-only serving; making FR-K the default retrieval path for any deployment; adding a router that selects between retrieval paths; changing the signpost recipe for a corpus that already has a measured baseline.
- Never: let a claim go out without a citation; re-chunk an unchanged document; assume a specific external analytics system exists inside this component; let the OKF bundle drift from the store (share the content-hash gate); read a reachability number without its compile-recipe version, or compare an FR-K number against a baseline measured at a different evidence-feed cap.

## 15. Success criteria

The system answers correctly, with citations, and abstains when unsupported, across the four archetypes, at the evaluation bar set in Phase 0. Incremental updates are cheap: an unchanged corpus re-run does effectively no work, and a single changed document updates the store without a full rebuild. Ingestion saturates the available GPU in bulk mode and yields to serving in background mode. Every answer is auditable to specific chunks, and graph-derived facts carry their confidence. The system runs unchanged whether models are served through OpenRouter or locally, and supports a fully local deployment where a client requires it.

## 16. Open questions

1. The chunking skill internals: boundary heuristics, the max-token policy, summary structure, and manifest representation.
2. Ontology and registry derivation: resolved for the validation corpus (ADR-0002) — the ontology is the 41 CUAD clause categories plus party and entity types (as Pydantic models), and the entity registry is the set of EDGAR Central Index Key (CIK) identifiers used as canonical `entity_id`s. How the Pydantic models are generated from the clause categories, and how the CIK registry is pulled and refreshed, remain to be detailed at the ontology-derivation task.
3. Entity disambiguation and resolution: mentions are canonicalized (normalize, reject, cluster into proposed clusters) before closed-world linking against the EDGAR CIK registry (ADR-0002). The clustering approach (blocking plus similarity), the linking matching strategy (exact, fuzzy, embedding, language-model-assisted), and whether the canonical skeleton is materialized remain open, decided at their tasks. Full coreference is deferred behind a seam.
4. Store specifics: the ArcadeDB schema and indexes (the dense `LSM_VECTOR` and sparse `LSM_SPARSE_VECTOR` configurations, dimensions, and similarity), how chunk records and graph nodes and edges carry `chunk_id` and `entity_id`, the query-skill interface shape, and the fallback-store swap point.
5. Retrieval parameters: top-k per stage, the RRF and sparse-index parameters inside ArcadeDB (including the sparse vocabulary cap and IDF weighting), the rerank cutoff, and the union cap.
6. Routing thresholds between the cheap path and the RLM tier, set from latency data. (The routing itself is orchestration; the thresholds are a capability-tuning input.)
7. The caching design (prefix and prompt caching of the code scaffold and interpreter state, memoizing sub-call and graph results).
8. The ingestion pipeline build: batch sizes and worker counts, wave-based versus pipelined layout, per-task model-tier assignments, dead-letter handling, and the bulk-versus-background switch.
9. Serving and infrastructure: the OpenRouter model ladder for the default path, and for the local mode the serving runtime (Ollama versus vLLM), model sizes, and GPU footprint; refresh cadence and triggers; and evaluation-set size and maintenance.
10. Whether a domain LoRA is needed on top of the grounding, decided from evaluation results.
11. (FR-K) Whether within-branch full-text search (ArcadeDB full-text, no vectors) counts as a signpost channel in the reachability model, or is a traversal-side capability. It raises the ceiling and stays embedding-free either way; the choice changes what the ceiling means. Decided at the reachability task and recorded.
12. (FR-K) Whether the traversal depth and frontier bounds are fixed or swept in the first slice. Sweeping produces a reachability-versus-cost curve for a future router; fixed is cheaper for a go or no-go read.
13. (FR-K) Whether the headline reachability metric is any-reachable or all-reachable per question, which matters once a question's gold set has more than one clause.
14. (FR-K) Whether a concept layer (compiled synthesis above the chunk layer) is built at all, or the bundle stays one file per chunk with rich signposts. The chunk-only form is cheaper and lossless; the concept layer defeats chunk-boundary misses but is a lossy synthesis needing its own evidence first. The first slice builds the chunk-only form.
15. (FR-K) How much of the path's quality is recipe tuning versus mechanism. The path has more tunable surface than vector retrieval because the model reasons about the query at run time; that headroom is the reason to expect improvement and the reason the held-out split and run-variance floor are load-bearing (an apparent gain can be selection on noise). Resolved empirically at GATE-3.

## 17. Domain portability (swapping the dataset)

The domain lives in three artifacts, not in the pipeline. Swapping datasets away from CUAD and EDGAR is mostly swapping what those artifacts contain, which is why most of ingestion and retrieval does not move. This section is the procedure and the precondition to check.

### The three artifacts that carry the domain

- The ontology (entity and relationship types). Today: CUAD's 41 clause categories plus party and entity types.
- The entity registry (the canonical `entity_id` set with names and aliases). Today: EDGAR CIK identifiers.
- The golden evaluation set (the archetype-split ground truth). Today: CUAD expert annotations plus the EDGAR-derived relational set.

Everything mechanical consumes these three through seams and does not know their source.

### What changes, and where (four touchpoints)

1. Ontology and registry derivation (FR-C.8, T8). The one real code-touch, deliberately isolated here. A new dataset supplies a different ontology and a different canonical id scheme, so T8's derivation logic changes. The blast radius is T8; everything downstream binds to the derived ontology and the `entity_id` interface, not to CUAD or EDGAR (open question 2).
2. Entity resolution registry (FR-C.7, T24). Contents, not code. Linking stays closed-world against whatever registry T8 produced, and the matching strategy recorded in ADR-0005 is domain-general; only the target set changes.
3. Disambiguation rules (FR-C.7, T23b). Additive. New domain noise (different placeholder conventions, non-US legal forms, personal names rather than company names) surfaces new misses, so append normalization and reject rules to ADR-0004 with their triggering case. The capability's shape does not change; its rule table grows. A personal-name domain may add one new normalization family, still behind the same capability.
4. Graph extraction schema binding (FR-C.6, T23). Through the ontology seam. Extraction conforms to one ontology, and that ontology is now different, so the Pydantic contract it extracts against changes, because T8 handed it a different ontology, not because the extractor was rewritten. The hybrid extraction stack is domain-general.

### What must stay domain-blind (a swap that forces a change here is a design bug, not a swap)

- The ingestion spine: parsing (T16), RLM chunking (T17), embedding (T19), and chunk write (T20). Structure-level and text-level, no domain semantics.
- The retrieval path end to end: hybrid search (T21), reranking (T22), graph query (T26), fusion (T27), RLM synthesis (T28), and answer generation (T29). They operate over embeddings, chunks, and `chunk_id` and `entity_id` references.
- The store, the seams, and the ARD: ArcadeDB, the model-profile seam, the query-skill and store seams, the capability registry, and the ARD manifests (which describe capabilities, not data).

If a domain assumption leaks into any of these, it is a bug to hunt down, not a place to edit for the new dataset.

### The precondition to check before any swap (the one that can break the model)

The registry is closed-world (open question 3). That premise holds only if the new dataset comes with an authoritative entity-id source, which EDGAR's CIKs are today. So the question to ask of any candidate dataset is not "is it contracts" but "does it carry an authoritative canonical id scheme."

- If yes, the swap is as cheap as the four touchpoints above.
- If no, T8 has nothing to derive a registry from and T24's closed-world premise breaks. The entity leg becomes open-world resolution, which is a real design change, not a swap. Decide this before committing to the dataset.

### The work that is not cheap (do not under-scope it)

The evaluation set is not a small aside; it is most of the actual work. A new domain needs its archetype-split golden set rebuilt, and if it is graph-heavy, a new human-verified entity set (the T10 exercise, again). Budget for that, not for pipeline changes.

### One-line summary

Domain equals ontology plus registry plus evaluation set. A dataset swap touches T8 (derivation, real code), T24 (registry contents), T23b (rule table, additive), and T23 (ontology binding). Parsing, chunking, embedding, and the entire retrieval path stay unchanged. Precondition: the new dataset must carry an authoritative id scheme, or the closed-world entity leg is a rebuild.

## 18. Glossary of terms

- **Abstention.** Returning "the context does not support an answer" rather than fabricating one.
- **ArcadeDB.** The single multi-model store (Apache 2.0) holding both the hybrid retrieval index (dense and sparse vector indexes with server-side RRF fusion) and the knowledge graph, reached behind a query-skill seam.
- **BGE-M3.** The embedding model producing dense and native sparse vectors from one model.
- **BGE-reranker.** The cross-encoder that reranks candidates as a precision gate.
- **BM25 (Best Match 25).** The lexical ranking approach; here carried by BGE-M3 sparse vectors over full chunk text in ArcadeDB's sparse index, for exact-term and rare-token recall.
- **Capability catalog.** The authoritative FR-C list of capabilities built and registered; the shared vocabulary the Orchestration Spec references.
- **chunk_id.** The stable identifier for a chunk: source-document identifier, chunk index, content hash. Shared by the chunk record and its graph nodes in the same store.
- **Docling.** The parsing front-end that turns documents into a clean structured representation.
- **entity_id.** The canonical entity identifier from the registry, shared between graph nodes and any external system referencing the same entities.
- **Entity registry.** The populated list of real entities (identifiers, names, types, aliases) from the catalog; the resolution dictionary.
- **Gemma 4.** The reasoning, generation, vision-to-text, and RLM model, served through OpenRouter or locally.
- **Graphify.** A prototype graph implementation usable behind the query-skill seam for a modest corpus.
- **LanceDB.** A separate hybrid vector store, retained only as the eval-gated Phase 1 fallback for the retrieval leg if ArcadeDB hybrid retrieval underperforms.
- **OpenRouter.** The default model-serving path; the same seam supports local open-model serving for a private deployment.
- **Ontology.** The entity and relationship types the graph holds, expressed as Pydantic models, derived from the Data Catalog.
- **RLM (Recursive Language Model).** The divide-and-conquer pattern of loading a working set into an interpreter, dispatching slices in code, and synthesizing; here an authored skill used by the chunking and synthesis capabilities.
- **RRF (Reciprocal Rank Fusion).** The server-side fusion of dense and sparse result lists inside ArcadeDB into one ranked list.
- **Summary-miss.** The failure where a detail dropped from a chunk's summary, and absent from the sparse and graph legs, never reaches synthesis; the dense-over-summary plus sparse-over-full-text split is designed to prevent it.
- **OKF (Open Knowledge Format).** Google's open specification (Apache 2.0) representing knowledge as a directory tree of markdown files with YAML frontmatter, one concept per file, cross-linked by standard markdown links; only a non-empty `type` field is required for conformance. Reserved filenames `index.md` and `log.md` carry directory listing and change history.
- **Signpost.** Any navigational cue a traversal can act on without reading a body: an index entry, a frontmatter field, a link anchor, or a directory edge. In the embedding-free path, signpost quality is the retrieval ceiling.
- **Progressive disclosure.** Reading a bundle root index first, then descending only into subtrees the signposts justify, so the traversal reads a small fraction of the corpus.
- **Reachability.** Whether a gold chunk is discoverable from the bundle root through signposts within the bounds. The compile-side ceiling, measured without a model.
- **Reached.** Whether the live traversal actually returned the gold chunk. Realized recall, set by the navigation policy.
- **Reachability spike.** The per-corpus, model-free run of the reachability analyzer plus the category-label control (GATE-3a) that decides whether the OKF path is worth building or enabling for a corpus before any traversal model call is spent.
- **PTC (Programmatic Tool Calling).** Calling tools from interpreter code, enabled by an explicit allowlist on the interpreter middleware; used here for the deterministic sift before any sub-agent is dispatched.
- **ACORD (Atticus Clause Retrieval Dataset).** The clause-retrieval benchmark (CC-BY-4.0) with attorney-authored queries and graded query-clause pairs, the first corpus the FR-K path is validated against and the current retrieval baseline.
