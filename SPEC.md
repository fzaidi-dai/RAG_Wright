# Spec: Hybrid RAG, Capability Spec

Version 0.1. This is the capability half of the Hybrid Retrieval-Augmented Generation (RAG) specification: the objective, the shared store and identifiers, and the capabilities the system is built from. It is what a coding agent builds through ordinary spec-driven development (plan, break into tasks, implement, test, register each capability). Its companion is the Orchestration Spec, which holds the two graph briefs dispatched to the compiler. The two documents share one vocabulary: the capability names defined here (the FR-C set) are what the orchestration briefs expect to bind, and what the built capabilities are registered under. This document defines and builds capabilities; it does not describe orchestration.

## 1. Objective

We are building a generic hybrid RAG system: the documents knowledge layer that answers questions about an unstructured corpus (policies, standard operating procedures, supplier contracts, menus, recipes, manuals) and holds the relationship graph over that corpus. It is domain-neutral and model-neutral: realized by default through OpenRouter (the same model-serving path the orchestration engine uses), and deployable fully locally on open models (a Gemma 4 class model) where a client requires it, without changing the design. Local deployment is a supported mode, not the premise, which keeps the system reusable across engagements.

The user is the agentic layer, not a person directly. The component is exposed as a set of governed skills over a Model Context Protocol (MCP) interface; agents call it, they do not own it. It answers document and relationship questions; composing it with a structured-analytics path into a unified system is the job of a higher-level orchestration, not this component.

Success looks like correct, cited, abstention-willing answers across the range of question shapes, with end-to-end provenance and confidence, on a corpus that updates incrementally and cheaply after the first bulk load.

## 2. Assumptions (correct these in review)

1. The corpus is documents (text after parsing), not a live analytical database. Structured analytical data is out of scope here; the system only shares canonical entity identifiers with whatever external systems hold that data.
2. Models are served by default through OpenRouter, and the system deploys fully locally on open models where a client requires it (the private-by-design mode). The model-serving path is a profile, not a premise.
3. An enterprise Data Catalog (Nessie or equivalent) exists and can supply an ontology (entity and relationship types) and an entity registry (real entities with identifiers, names, and aliases). Where it does not, extraction degrades to the lightweight path plus an open-ended language model. For the validation corpus, the concrete source is the Contract Understanding Atticus Dataset (CUAD) plus U.S. Securities and Exchange Commission (SEC) Electronic Data Gathering, Analysis, and Retrieval (EDGAR) entity data (see the testing section, the Phase 0 foundation, and ADR-0002): CUAD's expert clause annotations supply the ontology, and EDGAR Central Index Key (CIK) identifiers populate the entity registry as canonical `entity_id`s.
4. The reasoning, generation, and recursive-language-model (RLM) work runs on a Gemma 4 class model (served through OpenRouter or locally); ingestion tiers models down per task where quality allows.

## 3. Scope

### 3.1 In scope

The single ArcadeDB store holding both the hybrid retrieval index and the knowledge graph; the shared identifier scheme and provenance; and the capabilities the graphs bind (parsing, chunking, embedding, hybrid search, reranking, graph extraction and storage, entity disambiguation and resolution, reasoning and synthesis, RLM); and the golden evaluation set by archetype.

Multimodal note: multimodal here means vision-to-text at ingestion (a Gemma 4 class model turns images, scans, and diagrams into text), after which everything is text retrieval. True multimodal retrieval (a joint image-and-text embedding space, for example a CLIP-class embedder) is a property of the embedder, not the store, and is orthogonal to it; the store holds vectors of any modality. Adding a multimodal embedder is a deferred item that slots in at any time as an additional vector index in the same store.

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
- **FR-C.9 Reasoning, generation, and vision-to-text (Gemma 4).** The answer generator, the image-and-scan-to-text step at ingestion, and the model inside RLM. Registered as one capability (the vision-to-text and answer-generation uses share the same model capability and are bound at whichever node needs them).
- **FR-C.10 RLM skill.** An authored skill (a SKILL.md with instructions, built as ordinary software in Phase 1, not provided by any build tool) that teaches the divide-and-conquer method: load a working set into an interpreter, slice and dispatch the work in code, and synthesize the results. The method is applied by two registered capabilities: the RLM chunking capability (ingestion) and the RLM synthesis capability (query). The authored method may itself register as a shared skill that those two require (an `agent_skill` `requires` closure), so the method is loaded once and reused. It is a required capability wherever a graph binds it.

### FR-C canonical slugs (the cross-spec join key)

Each capability is registered under a canonical slug, and that slug is the join key: it is the name the built capability registers under, and the name an orchestration brief uses when it must bind a specific capability. The slug is the semantic capability name, not the requirement id (use `hybrid_search`, never `fr-c-3`), so it survives spec renumbering. The registered capabilities and their slugs:

`parsing` (FR-C.1), `embedding` (FR-C.2), `hybrid_search` (FR-C.3), `reranking` (FR-C.4), `graph_query` (FR-C.5), `graph_extraction` (FR-C.6), `entity_disambiguation` and `entity_resolution` (the two-stage canonicalize-then-link capabilities, FR-C.7), `ontology_registry_derivation` (FR-C.8), `generation` (FR-C.9), `fusion` (the union-and-deduplicate capability, FR-Q.4), `rlm_chunking` and `rlm_synthesis` (the two capabilities that apply the RLM skill, FR-C.10 with FR-I.1 and FR-Q.5). If the authored RLM method is registered as its own shared skill, its slug is `rlm_method`, required by `rlm_chunking` and `rlm_synthesis`.

The Orchestration Spec references most capabilities behaviorally (resolved by discovery against representative queries) and names only the must-bind capabilities explicitly; those explicit names must be exactly these slugs. Registration URNs embed the slug as the final segment (`urn:air:dreamai:rag_wright:<slug>`).

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

Note: the phases build capabilities. The graphs that orchestrate these capabilities are compiled separately from the Orchestration Spec, once the capabilities a graph binds are built and registered.

## 14. Boundaries

- Always: carry provenance and confidence on every answer; gate expensive ingestion stages by content hash; resolve entities against the registry; treat the graph's answer as evidence to be verified, not truth; keep retrieval and the graph behind the query-skill seam; register each built capability under its FR-C name.
- Ask first: changing the `chunk_id` or `entity_id` scheme (it breaks the link between chunks and graph nodes); adding a model or a store; materializing the canonical entity skeleton; switching a client to local-only serving.
- Never: let a claim go out without a citation; re-chunk an unchanged document; assume a specific external analytics system exists inside this component.

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

## 17. Glossary of terms

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
