"""Per-capability ARD manifest authoring (the committed source; T15 onward).

Every discoverable capability (FR-C / FR-I / FR-Q) authors one ARD manifest, written to the shared
registry root as `<slug>.json` under `urn:air:dreamai.io:rag_wright:<slug>`. The authoring data that
cannot be derived at registration — above all the 2-5 representative queries discovery ranks on — is
committed here, one `CapabilityManifest` per capability, added at that capability's own task. The
registry root itself is regenerable (outside the repo, `~/.air/registry`); this module is the durable
source, and `scripts/publish_manifests.py` writes every spec into the root.

Authoring reuses the T6 seam: the canonical-slug set and URN emitter (`registry.py`), the media-type
map and callable-bounds rule (`ard.py`), and `ManifestSkeleton.author(...)` for the validated
`RegistryEntry`. A live `RegistryStore` load is a GraphWright-side step, not done here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rag_wright.capabilities.ard import (
    CALLABLE_KINDS,
    MEDIA_TYPE_BY_KIND,
    CapabilityInterface,
    EntryKind,
    RegistryEntry,
    ResponseBounds,
    SkillRuntime,
    write_manifest,
)
from rag_wright.capabilities.registry import (
    CANONICAL_CAPABILITY_SLUGS,
    ManifestSkeleton,
    capability_urn,
)
from rag_wright.skills.rlm.agent import GRANTED_SUBAGENTS

# The RLM sub-agent roster the three RLM skills dispatch to, as the skill itself declares them
# (single source of truth in `skills/rlm/agent.py`). Since the recursive rebuild (T15/T17/T28) these are
# real Deep Agents sub-agents, so `grantedSubagents` is populated (ADR-0015; was `[]` pre-rebuild). Bound
# from `GRANTED_SUBAGENTS` so the manifest roster cannot drift from the names the skill actually declares
# and dispatches — a conformance test asserts the two are identical (drift passes here, fails GraphWright's
# bind).
_RLM_GRANTED = list(GRANTED_SUBAGENTS)


@dataclass(frozen=True)
class CapabilityManifest:
    """The committed ARD authoring data for one capability (what registration cannot derive)."""

    slug: str
    kind: EntryKind
    display_name: str
    description: str
    representative_queries: tuple[str, ...]  # 2-5; the field discovery ranks on
    tags: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()  # closure; agent_skill only
    skill_runtime: Optional[SkillRuntime] = None  # intrinsic runtime; agent_skill only
    golden_eval_ref: Optional[str] = None
    response_bounds: Optional[ResponseBounds] = None  # callable kinds only; defaults if omitted
    # GraphWright vendor extension (ADR-0030): the governed typed I/O. Declared only for the query-graph
    # capabilities GraphWright's checker verifies (the 5 + graph_query + generation); None elsewhere.
    capability_interface: Optional[CapabilityInterface] = None


# One entry per capability, added at that capability's task. T15 registers the shared RLM method
# skill; the two RLM capabilities (rlm_chunking T17, rlm_synthesis T28) will `require` it.
_SPECS: tuple[CapabilityManifest, ...] = (
    CapabilityManifest(
        slug="rlm_method",
        kind="agent_skill",
        display_name="RLM divide-and-conquer method",
        description=(
            "The general recursive-language-model method (FR-C.10): load a working set into an "
            "interpreter, slice and dispatch the work in code, and synthesize the results, so the "
            "model never attends over the full volume. A shared skill required by the RLM chunking "
            "and RLM synthesis capabilities; it carries no determinism, boundary, or gating behavior "
            "of its own (those belong to the applying capability)."
        ),
        representative_queries=(
            "divide and conquer over a working set too large for a single prompt",
            "load a large working set into an interpreter and dispatch the work in code",
            "recursively call sub-models on small focused slices instead of the whole volume",
            "synthesize an answer from many partitioned sub-calls",
        ),
        tags=("rlm", "method", "divide-and-conquer"),
        # No capability_interface (T44): rlm_method is a shared METHOD skill `require`d by rlm_chunking and
        # rlm_synthesis (loaded knowledge), never bound as a data-processing node in a graph — it has no
        # pipeline data I/O of its own. The applying capability (chunking / synthesis) is what carries the
        # governed interface; governing the method here would be a type with no producer or consumer.
        # Intrinsic RLM runtime: the interpreter holds the working set and runs the code-side recursive
        # decompose(), dispatching the two real sub-agents (ADR-0015). granted_subagents is bound from the
        # skill's own roster so it cannot drift from what the skill declares/dispatches.
        skill_runtime=SkillRuntime(
            needs_interpreter=True, rlm=True, requires_dynamic_dispatch=True, granted_subagents=_RLM_GRANTED
        ),
    ),
    CapabilityManifest(
        slug="parsing",
        kind="function",  # an in-process graph-node call
        display_name="Document parsing (Docling)",
        description=(
            "Turn a source document (PDF, Office file, or scan) into a clean structured "
            "representation — reading order, headings, sections, tables, and OCR text — parsed once "
            "and reused by chunking, embedding, and extraction (FR-C.1)."
        ),
        representative_queries=(
            "parse a PDF contract into structured sections and tables",
            "extract reading order and headings from a source document",
            "OCR a scanned filing into machine-readable text",
            "turn an Office document into a clean structured representation",
        ),
        tags=("parsing", "docling", "ingestion"),
        capability_interface=CapabilityInterface(
            inputs={"source": "document"},
            outputs={"parsed": "parsed_doc"},  # a handle to the cached DoclingDocument; chunking consumes it
            success_criterion="parse a source document into a cached structured representation, parsed once",
        ),
    ),
    CapabilityManifest(
        slug="rlm_chunking",
        kind="agent_skill",  # applies the RLM method; loaded knowledge, requires rlm_method
        display_name="RLM chunking",
        description=(
            "Read a whole parsed document through an interpreter using the RLM method, split it along "
            "topic/section/chapter boundaries into semantically coherent chunks (capped ~20,000 "
            "tokens), and write a summary per chunk with a per-document manifest and stable "
            "chunk_ids. Deterministic and content-hash gated (FR-I.1)."
        ),
        representative_queries=(
            "chunk a long parsed document into semantically coherent sections",
            "split a document along topic and section boundaries within a token cap",
            "produce a summary per chunk and stable chunk ids",
            "re-chunk a document only when its content changes",
        ),
        requires=("rlm_method",),
        tags=("chunking", "rlm", "ingestion"),
        skill_runtime=SkillRuntime(  # LLM boundary discovery via the recursive machinery; real sub-agents
            needs_interpreter=True, rlm=True, requires_dynamic_dispatch=True, granted_subagents=_RLM_GRANTED
        ),
        capability_interface=CapabilityInterface(
            # Emits the ingestion `chunk` (id + text + summary + index) — NOT the query-side chunk_with_text;
            # embedding and graph_extraction consume this same `chunk`.
            inputs={"parsed": "parsed_doc"},
            outputs={"chunks": "chunk"},
            success_criterion="split a parsed document into semantically coherent, capped, summarized chunks with stable ids",
        ),
    ),
    CapabilityManifest(
        slug="semantic_chunking",
        kind="subgraph",  # single-call boundary discovery + deterministic repair + hash-gate (CU-B4); CAP-REG-1b
        display_name="Semantic chunking (single-call)",
        description=(
            "Split a parsed document into semantically coherent, token-capped chunks via a SINGLE-CALL "
            "boundary discoverer (non-agentic) plus deterministic boundary repair, a minimum-size floor, "
            "and a content-hash gate — the deterministic alternative to the RLM chunker (`rlm_chunking`). "
            "Emits the ingestion `chunk` (id + text + summary + index) with stable chunk_ids (FR-I.1, ADR-0031)."
        ),
        representative_queries=(
            "chunk a parsed document with a single boundary-discovery call plus deterministic repair",
            "split a document into capped, semantically coherent chunks without the RLM machinery",
            "produce stable chunk ids and a per-chunk summary deterministically",
            "re-chunk a document only when its content changes",
        ),
        tags=("chunking", "ingestion", "deterministic"),
        capability_interface=CapabilityInterface(
            # Same ingestion `chunk` output as rlm_chunking; embedding and graph_extraction consume it.
            inputs={"parsed": "parsed_doc"},
            outputs={"chunks": "chunk"},
            success_criterion="split a parsed document into semantically coherent, capped, summarized chunks with stable ids",
        ),
    ),
    CapabilityManifest(
        slug="embedding",
        kind="model",  # BGE-M3 inference (CAP-REG-1)
        display_name="Embedding (BGE-M3)",
        description=(
            "From one BGE-M3 model, produce a dense vector over the chunk summary and a native sparse "
            "vector over the full chunk text (the summary-miss mitigation). Output shapes match the "
            "chunk-record contract: dense length 1024, sparse token-id -> weight (FR-C.2, FR-I.3)."
        ),
        representative_queries=(
            "embed a chunk summary into a dense vector with BGE-M3",
            "produce a native sparse lexical vector over full chunk text",
            "generate dense and sparse embeddings from one model",
            "vectorize chunks for hybrid retrieval",
        ),
        tags=("embedding", "bge-m3", "ingestion"),
        capability_interface=CapabilityInterface(
            inputs={"chunks": "chunk"},  # needs the summary the ingestion `chunk` carries (dense-over-summary)
            outputs={"embeddings": "embedding"},
            success_criterion="produce a dense-over-summary and sparse-over-full-text vector per chunk (BGE-M3)",
        ),
    ),
    CapabilityManifest(
        slug="hybrid_search",
        kind="function",  # an in-process query-side node
        display_name="Hybrid search (RRF over dense + sparse)",
        description=(
            "Retrieve candidate chunks for a natural-language query by fusing a dense "
            "semantic leg and a sparse lexical leg server-side with Reciprocal Rank Fusion "
            "(ArcadeDB vector.fuse), honoring metadata filters, into one ranked candidate list "
            "(FR-C.3, FR-Q.1)."
        ),
        representative_queries=(
            "retrieve the most relevant chunks for a natural-language query",
            "hybrid dense and sparse search fused by reciprocal rank fusion",
            "find candidate passages combining semantic and lexical matching",
            "search the chunk index and filter candidates by source document",
        ),
        tags=("retrieval", "hybrid", "rrf", "query"),
        capability_interface=CapabilityInterface(
            inputs={"query": "text"},
            outputs={"candidates": "chunk_id"},  # id-only by design; rehydrate via chunk_read before any text consumer
            success_criterion="retrieve RRF-fused candidate chunk references for a natural-language query",
        ),
    ),
    CapabilityManifest(
        slug="reranking",
        kind="model",  # BGE cross-encoder inference (CAP-REG-1)
        display_name="Reranking (BGE cross-encoder precision gate)",
        description=(
            "Re-score retrieved candidate passages against the query with a BGE-reranker "
            "cross-encoder (which reads query and passage together, more precise than the "
            "bi-encoder retrieval legs) and cut the list to a top-k precision gate before "
            "synthesis (FR-C.4, FR-Q.2)."
        ),
        representative_queries=(
            "rerank retrieved passages by cross-encoder relevance to the query",
            "apply a precision gate that cuts candidates to the most relevant top-k",
            "reorder hybrid-search results with a BGE reranker before answering",
            "select the best passages to ground an answer on",
        ),
        tags=("reranking", "cross-encoder", "bge-reranker", "query"),
        capability_interface=CapabilityInterface(
            # Consumes chunk_with_text (only chunk_read produces it) — this is what forces a rehydrate
            # upstream of reranking. It never fetches text itself (reranking.py). Output carries the score.
            inputs={"query": "text", "passages": "chunk_with_text"},
            outputs={"ranked": "scored_chunk"},
            success_criterion="cross-encoder re-score candidate passages against the query and cut to top-k",
        ),
    ),
    CapabilityManifest(
        slug="graph_extraction",
        kind="subgraph",  # multi-step extractor workflow; CAP-REG-1
        display_name="Graph extraction (GP-1B docling-graph party/relational)",
        description=(
            "Extract ontology-conforming graph facts from a parsed chunk with the GP-1B docling-graph "
            "extractor (granite-4.1-8b): the signing parties as ORGANIZATION mentions plus the structural "
            "CONTRACTS_WITH edges between them, EXTRACTED. This is the entity/relational extractor that "
            "populated the relational (Leg C) graph at real recall 0.991; the earlier spaCy-NER + "
            "contract-LLM + escalation hybrid is retired (ADR-0035). Every fact carries chunk_id provenance "
            "and a confidence tag (FR-C.6, FR-I.4)."
        ),
        representative_queries=(
            "extract the signing parties and their relationships from a contract chunk",
            "identify the organizations party to an agreement and the CONTRACTS_WITH edges between them",
            "produce ontology-conforming party/relational graph facts with provenance and confidence",
        ),
        tags=("extraction", "graph", "parties", "relational", "docling-graph", "ingestion"),
        capability_interface=CapabilityInterface(
            inputs={"chunks": "chunk"},  # consumes the same ingestion `chunk` (uses its id + text)
            outputs={"facts": "extraction"},  # chunk-anchored entity mentions + relationship facts
            success_criterion="extract ontology-conforming entity mentions and relationship facts from a chunk, with provenance and confidence",
        ),
    ),
    CapabilityManifest(
        slug="entity_disambiguation",
        kind="function",  # an in-process graph-layer node
        display_name="Entity disambiguation (normalize / reject / cluster)",
        description=(
            "Turn the raw extracted entity-mention stream into canonical mention clusters: normalize "
            "surface-form variants to one key, reject non-entities (placeholders, role artifacts, bare "
            "generic tokens, alias prefixes), and cluster survivors — flagging ambiguous near-duplicates "
            "for human decision rather than merging. Clusters are human-verifiable proposals carrying "
            "chunk_id provenance and confidence (FR-C.7)."
        ),
        representative_queries=(
            "canonicalize entity surface-form variants into one entity",
            "reject template placeholders and role artifacts from extracted parties",
            "cluster contract party mentions that denote the same company",
            "flag parent/subsidiary near-duplicate entities for human review",
        ),
        tags=("entity", "disambiguation", "canonicalization", "graph"),
        capability_interface=CapabilityInterface(
            inputs={"facts": "extraction"},  # the extracted mention stream (graph_extraction's output)
            outputs={"clusters": "entity_cluster"},
            success_criterion="normalize, reject, and cluster extracted entity mentions into human-verifiable canonical clusters",
        ),
    ),
    CapabilityManifest(
        slug="entity_resolution",
        kind="function",  # an in-process graph-layer node
        display_name="Entity resolution (closed-world to EDGAR CIK)",
        description=(
            "Link canonical mention clusters to their EDGAR CIK entity_id in the registry, closed-world "
            "(exact normalized match; unknown -> unlinked, never fabricated). Resolves relationship "
            "endpoints as the same stream so an entity in both channels is one node, and drops "
            "post-resolution self-loops (FR-C.7)."
        ),
        representative_queries=(
            "link an extracted company mention to its EDGAR CIK",
            "resolve contract parties to canonical registry entities",
            "map entity surface forms to a canonical entity id, closed-world",
            "deduplicate relationship endpoints and standalone mentions to one entity node",
        ),
        tags=("entity", "resolution", "edgar", "cik", "graph"),
        capability_interface=CapabilityInterface(
            # Two inputs: the clusters to link AND the original extraction (to resolve relationship endpoints
            # as the same mention stream) — resolve_entities(clusters, results, ...).
            inputs={"clusters": "entity_cluster", "facts": "extraction"},
            outputs={"resolved": "resolved_entity"},  # entities + relationships linked to a canonical id (the graph)
            success_criterion="link mention clusters to canonical EDGAR ids (closed-world) and resolve relationship endpoints as one stream",
        ),
    ),
    CapabilityManifest(
        slug="graph_query",
        kind="function",  # an in-process query-side graph node
        display_name="Graph query (cited relational/multi-hop answer)",
        description=(
            "Answer relational and multi-hop questions by traversing the knowledge graph from a start "
            "entity over relationship edges, returning cited chunk_ids, entity_ids, and confidence tags "
            "as evidence for fusion (treated as evidence, not truth; confidence surfaced, not filtered) "
            "(FR-C.5, FR-Q.3)."
        ),
        representative_queries=(
            "who are the counterparties of this company in the contract graph",
            "find entities connected to a company within two hops",
            "answer a multi-hop relational question with cited graph evidence",
            "traverse contract relationships between organizations",
        ),
        tags=("graph", "query", "traversal", "multi-hop", "relational"),
        capability_interface=CapabilityInterface(
            inputs={"query": "text"},
            outputs={"graph": "graph_answer"},  # the distinct type fusion's graph leg consumes
            success_criterion="answer a relational/multi-hop question by graph traversal, returning cited evidence",
        ),
    ),
    CapabilityManifest(
        slug="fusion",
        kind="function",  # an in-process query-side node
        display_name="Fusion (union/dedup on chunk_id, capped)",
        description=(
            "Union and deduplicate the reranked retrieval top set and the graph-cited chunks on "
            "chunk_id into one capped, deterministic evidence set for synthesis. Not a score fusion "
            "(the graph returns an answer, not a comparable ranked list) (FR-Q.4)."
        ),
        representative_queries=(
            "combine retrieval results and graph evidence into one evidence set",
            "union and deduplicate cited chunks from the text and graph legs",
            "merge reranked passages with graph-cited chunks for synthesis",
            "build one capped evidence set from both retrieval and the knowledge graph",
        ),
        tags=("fusion", "union", "evidence", "query"),
        capability_interface=CapabilityInterface(
            # Two DISTINCT input types (retrieval leg vs graph leg), not a variadic id-set. Output is
            # id-only (the union carries id + sources[], no text/score) — typed `chunk_id` so the valid
            # fusion -> chunk_read -> synthesis tail type-checks (GraphWright's §3 provenance rule: the
            # `sources[]` sub-field does not fork the type name; no consumer gates on it). ADR-0021.
            inputs={"reranked": "scored_chunk", "graph": "graph_answer"},
            outputs={"fused": "chunk_id"},
            success_criterion="union and dedup the retrieval and graph evidence on chunk_id, capped",
        ),
    ),
    CapabilityManifest(
        slug="chunk_read",
        kind="function",  # an in-process query-side node
        display_name="Chunk read (rehydrate chunk_ids to full text)",
        description=(
            "Rehydrate a set of retrieved chunk_ids to their full chunk text — the text the retrieval "
            "index does not hold (it is dense-over-summary) — by reading the chunk-text sidecar, "
            "returning text per chunk_id in the requested order for synthesis. The governed "
            "text-rehydration step between fusion (FR-Q.4) and synthesis (FR-Q.5); no id is silently "
            "dropped."
        ),
        representative_queries=(
            "rehydrate retrieved chunk ids to their full text for synthesis",
            "fetch the full source text of chunks by chunk_id",
            "load the text behind a set of retrieved chunk ids before answering",
            "get the chunk text for the evidence set the retriever returned",
        ),
        tags=("rehydration", "chunk-text", "evidence", "query"),
        capability_interface=CapabilityInterface(
            # The ONLY producer of chunk_with_text — so the checker forces it in wherever a text consumer
            # (reranking, rlm_synthesis, generation) follows an id-only producer. Drops nothing.
            inputs={"chunk_ids": "chunk_id"},
            outputs={"chunks": "chunk_with_text"},
            success_criterion="rehydrate chunk_ids to their full chunk text, order-preserving, dropping nothing",
        ),
    ),
    CapabilityManifest(
        slug="rlm_synthesis",
        kind="agent_skill",  # applies the RLM method; loaded knowledge, requires rlm_method
        display_name="RLM synthesis",
        description=(
            "Apply the RLM divide-and-conquer method to the retrieved candidate chunks: load them into "
            "an interpreter as data, slice in code (one focused unit per chunk), sub-call a model once "
            "per unit, and combine the outputs in a recursive code-side reduce — so synthesis never "
            "attends over the full chunk volume (FR-Q.5)."
        ),
        representative_queries=(
            "synthesize an answer from many retrieved chunks without attending over all at once",
            "divide-and-conquer synthesis over a large candidate set",
            "reduce retrieved passages into a focused synthesis for a query",
            "recursively combine per-chunk extracts into one answer",
        ),
        requires=("rlm_method",),
        tags=("rlm", "synthesis", "query"),
        skill_runtime=SkillRuntime(  # recursive descent (real sub-agents) + kept _reduce ascent
            needs_interpreter=True, rlm=True, requires_dynamic_dispatch=True, granted_subagents=_RLM_GRANTED
        ),
        capability_interface=CapabilityInterface(
            # Takes chunk_with_text (already rehydrated; does not fetch text). Emits the answer AND the
            # citations: cited_chunk_ids (the cited set) + cited_extracts (per-slice extract, each cited).
            inputs={"query": "text", "chunks": "chunk_with_text"},
            outputs={"answer": "text", "cited_chunk_ids": "chunk_id", "cited_extracts": "cited_extract"},
            success_criterion="recursively extract per-slice then reduce the chunks into a cited synthesis",
        ),
    ),
    CapabilityManifest(
        slug="generation",
        kind="agent_skill",  # a single grounded/cited LLM act; loaded, not called (CAP-REG-1)
        display_name="Answer generation (grounded, cited, abstains)",
        description=(
            "Produce a grounded, cited answer from the retrieved evidence — no claim without a citation, "
            "confidence-aware, abstaining when the context does not support an answer (FR-C.9, FR-Q.6). "
            "Split from vision-to-text so discovery ranks it only on answer-generation intents (ADR-0014)."
        ),
        representative_queries=(
            "answer a question grounded in the retrieved evidence with citations",
            "abstain when the retrieved context does not support an answer",
            "generate a confidence-aware cited answer from contract evidence",
            "produce a cited answer or an abstention from retrieved passages",
        ),
        tags=("generation", "answer", "grounded", "cited", "abstention"),
        capability_interface=CapabilityInterface(
            # Alt answer step to rlm_synthesis; also consumes chunk_with_text (evidence, already rehydrated).
            # Abstain is a boolean `abstained` flag on the answer record (empty citations), not a distinct
            # typed channel and nothing downstream gates on it — so it stays in the payload, noted here.
            inputs={"query": "text", "evidence": "chunk_with_text"},
            outputs={"answer": "text", "cited_chunk_ids": "chunk_id"},
            success_criterion="produce a grounded cited answer, or abstain (abstained flag, empty citations) when evidence does not support one",
        ),
    ),
    CapabilityManifest(
        slug="vision_to_text",
        kind="agent_skill",  # a single grounded vision-language act; SKILL.md, applied via the seam (SKILL-SPLIT)
        display_name="Vision-to-text (scanned-image transcription; authored skill)",
        description=(
            "Transcribe a scanned filing's images to text at ingestion on the Gemma 4 class model (FR-C.9), "
            "authored as skills/vision_to_text/SKILL.md: transcribe all visible text exactly, preserving "
            "reading order, output only the text. A single grounded vision-language act -- the ingestion-side "
            "twin of answer generation (also an agent_skill). Split from generation (ADR-0014): different "
            "inputs (an image, not evidence), different failure modes, a different caller (ingestion). "
            "Model-neutral through the seam (product = self-hosted Gemma-class, ADR-0039)."
        ),
        representative_queries=(
            "transcribe a scanned filing image to text",
            "extract the text from a scanned or image-only document",
            "convert a contract page image into machine-readable text at ingestion",
            "read text off a rasterized document image",
        ),
        tags=("vision-to-text", "ocr", "transcription", "ingestion"),
        capability_interface=CapabilityInterface(
            inputs={"image": "image"},
            outputs={"text": "text"},  # standalone ingestion transcription for image-only sources
            success_criterion="transcribe a scanned image to text at ingestion (image-only filings)",
        ),
    ),
    # --- CAP-REG-3: the KG-primary retrieval core (packaged out of eval/kg_primary.py) ---
    CapabilityManifest(
        slug="candidate_routing",
        kind="function",
        display_name="Candidate routing (union combiner -> candidate pool)",
        description=(
            "Route a query to its candidate clause pool by unioning the ranked function predictions from each "
            "router (taxonomy-constrained LLM, LegalBERT classifier, dimension-prior), first-wins and "
            "recall-safe, then fetching the clauses of that function set (KG-5e). The union combiner that "
            "replaces the oracle function filter; the store pool lookup is an injected seam."
        ),
        representative_queries=(
            "select the candidate clause pool for a query from its predicted functions",
            "union several function-routing signals into one recall-safe candidate set",
            "route a query to clauses by its clause function(s)",
        ),
        tags=("retrieval", "routing", "query-side", "deterministic"),
    ),
    CapabilityManifest(
        slug="typed_constraint_match_rank",
        kind="function",
        display_name="Typed constraint match rank (graded, subsumption-aware)",
        description=(
            "Grade candidate clauses by how many of the query's typed (dimension, value) constraints their "
            "grounded props satisfy, under KG-5a canonicalization + subsumption, returning descending graded "
            "order. Recall-safe: a zero-match candidate keeps its place (stable), never dropped. The symbolic "
            "primary ranking of the KG-primary retrieval."
        ),
        representative_queries=(
            "rank clauses by how many typed query constraints they satisfy",
            "grade candidates by subsumption-aware constraint match",
            "order a candidate pool by typed-property match count",
        ),
        tags=("retrieval", "ranking", "matching", "deterministic"),
    ),
    CapabilityManifest(
        slug="dense_rank_tiebreak",
        kind="function",
        display_name="Dense rank tiebreak (cosine order)",
        description=(
            "Order candidate clauses by descending cosine similarity to the query's dense vector -- the "
            "embedding signal that breaks constraint-match ties meaningfully (KG-6 / V4). Pure: the vectors "
            "come from the embedding capability; no model or store call here."
        ),
        representative_queries=(
            "break ranking ties by embedding cosine similarity",
            "order candidates by dense similarity to the query",
            "rank clauses by cosine to the query vector",
        ),
        tags=("retrieval", "ranking", "embedding", "deterministic"),
    ),
    CapabilityManifest(
        slug="property_boosted_retrieval",
        kind="function",
        display_name="Property-boosted typed retrieval",
        description=(
            "Typed retrieval over the CUAD-full KG (SPAN-CLAUSE-RERANK, ADR-0033): a bounded BGE base pool "
            "(span_hybrid_search over the routed functions) is joined to each span's clause props via the "
            "operative-span edge.span_id link, then reranked by typed-constraint match (BGE order as the "
            "tiebreak). Returns top-k cited spans with the constraints each satisfied."
        ),
        representative_queries=(
            "retrieve clauses matching a typed constraint, ranked over a BGE pool",
            "find spans whose clause satisfies the query's typed properties",
            "property-boosted retrieval with citations over the contract KG",
        ),
        tags=("retrieval", "ranking", "typed", "citation"),
    ),
    # --- KG-7: the Party<->Contract unifying link over the one contract KG (ADR-0036) ---
    CapabilityManifest(
        slug="party_clause_linking",
        kind="function",
        display_name="Party-clause linking (PARTY_TO edges over the unified contract KG)",
        description=(
            "Add the missing Party<->Contract link over the one contract KG, joining the party graph to the "
            "typed Clause KG WITHOUT re-ingest: a pure pass over the already-populated Contract and Entity "
            "nodes that matches each contract's parties_json names to Entity nodes by normalized name and "
            "writes PARTY_TO edges (Entity -> Contract). Many-to-many, so an edge (not an id field); a party "
            "reaches its clauses via PARTY_TO then the clause_id key-range. Unmatched (private/unlinked) "
            "parties are counted, not dropped (ADR-0036)."
        ),
        representative_queries=(
            "link the parties of a contract to their Entity nodes",
            "connect the party graph to the clause KG so a party reaches its clauses",
            "add PARTY_TO edges between resolved parties and their contracts",
        ),
        tags=("graph", "linking", "parties", "unification", "deterministic"),
    ),
    CapabilityManifest(
        slug="clause_exception_linking",
        kind="function",
        display_name="Clause exception linking (IsExceptionTo carve-out edges over the contract KG)",
        description=(
            "Add the missing cap<->carve-out relationship over the contract KG WITHOUT re-ingest (ADR-0044): a "
            "pure pass over the already-populated clauses that writes IsExceptionTo edges (an Uncapped clause -> "
            "the Cap clause it excepts). The signal is symbolic co-occurrence + POSITIONAL PROXIMITY (an Uncapped "
            "clause whose operative span is within ~one section of a Cap clause is that cap's carve-out); distant "
            "co-occurrence is NOT linked. The link is INFERRED (a reasoned inference, not extracted; surfaced at "
            "query time and human-validatable, FR-S.4). Lets a query answer 'capped at X, except uncapped for "
            "[carve-outs]' from structured evidence instead of two contradictory fragments."
        ),
        representative_queries=(
            "link an uncapped-liability carve-out to the cap clause it excepts",
            "connect a contract's cap and its exceptions so a query sees the conditions",
            "derive the cap-to-carve-out relationship over the clause KG",
        ),
        tags=("graph", "linking", "carve-out", "neuro-symbolic", "inferred"),
    ),
    # --- CAP-REG-2: the built contract-KG capabilities (capability_interface added when GraphWright-governed) ---
    CapabilityManifest(
        slug="typed_value_normalization",
        kind="function",
        display_name="Typed value normalization",
        description=(
            "Normalize a typed property value to its canonical form for matching (KG-5a): jurisdiction "
            "canonicalization (England / England and Wales / English law -> england) and closed-value "
            "subsumption rollup, so a query constraint matches equivalent or more-specific clause values."
        ),
        representative_queries=(
            "canonicalize a jurisdiction surface form to its canonical value",
            "roll a more specific closed value up to the broader value it satisfies",
            "normalize a typed property value for subsumption-aware matching",
        ),
        tags=("normalization", "matching", "deterministic"),
    ),
    CapabilityManifest(
        slug="extraction_grounding_judge",
        kind="function",
        display_name="Extraction grounding judge",
        description=(
            "Deterministically gate an extracted typed record against its source text (ADR-0028): downgrade "
            "an EXTRACTED value to AMBIGUOUS when its lexical cue is absent from the text. A quality gate on "
            "the extraction subgraph and a permanent gate on the final graph; lexically-anchored dims only."
        ),
        representative_queries=(
            "flag an extracted property value whose cue is not in the source text",
            "downgrade ungrounded EXTRACTED assertions to AMBIGUOUS",
            "ground a typed clause record against its clause text",
        ),
        tags=("grounding", "quality-gate", "deterministic"),
    ),
    CapabilityManifest(
        slug="extraction_semantic_judge",
        kind="agent_skill",  # a single grounded LLM verify-or-refute reading; SKILL.md, applied via the seam
        display_name="Extraction semantic judge (clause property -> supported?; authored skill)",
        description=(
            "Layer 3 of the neuro-symbolic extraction-fidelity cascade (ADR-0040), authored as "
            "skills/extraction_semantic_judge/SKILL.md: the verify-or-refute reading METHOD for the closed "
            "SEMANTIC dimensions (mutuality, favorability, party_asymmetry, cap_basis, the consent regimes) that "
            "carry no surface form -- what the lexical and symbolic gates cannot reach. Given one property "
            "(dimension = value, with its meaning) and the clause text, returns whether a faithful reading of "
            "THIS clause supports it (strict: mere plausibility is not support). Model-neutral through the seam "
            "(product = self-hosted Granite, ADR-0039); ingestion-side only. The AMBIGUOUS downgrade and "
            "dimension selection are the applying extraction_semantic_gate function's job, not the skill's."
        ),
        representative_queries=(
            "verify whether a clause supports an extracted mutuality reading",
            "refute a semantic property that a faithful reading of the clause does not support",
            "LLM-audit the closed semantic dimensions the deterministic gates cannot check",
        ),
        tags=("grounding", "quality-gate", "semantic", "skill"),
    ),
    CapabilityManifest(
        slug="extraction_semantic_gate",
        kind="function",
        display_name="Extraction semantic gate (semantic-dimension AMBIGUOUS downgrade)",
        description=(
            "DETERMINISTIC gate (ADR-0040 Layer 3, SKILL-SPLIT): select the surviving (non-AMBIGUOUS) assertions "
            "on a closed SEMANTIC dimension, apply the extraction_semantic_judge SKILL to each concurrently "
            "(async + semaphore), and downgrade a refuted one to AMBIGUOUS (kept but flagged), exactly like "
            "reground / symbolic_validate. A judge that fails or returns no ruling leaves the assertion "
            "untouched (never downgrade on a judge error). No model of its own -- it composes the skill."
        ),
        representative_queries=(
            "downgrade refuted semantic-property readings on a clause to AMBIGUOUS",
            "apply the semantic faithfulness judge across a clause's surviving assertions",
            "run the ADR-0040 Layer-3 semantic quality gate over an extraction record",
        ),
        tags=("grounding", "quality-gate", "semantic", "deterministic"),
    ),
    CapabilityManifest(
        slug="operative_span_segmentation",
        kind="function",
        display_name="Operative span segmentation",
        description=(
            "Split a chunk into operative spans (the clause-level units the function classifier and property "
            "extraction operate on), deterministically, with stable span ids."
        ),
        representative_queries=(
            "split a chunk into operative clause-level spans",
            "segment a contract chunk into the units for function classification",
            "produce stable span ids for downstream extraction",
        ),
        tags=("segmentation", "ingestion", "deterministic"),
    ),
    CapabilityManifest(
        slug="intra_document_scoped_query",
        kind="function",
        display_name="Intra-document scoped query",
        description=(
            "Answer scoped questions over ONE contract's typed KG (intra-contract): the clause index, the "
            "clauses of a given function, and aggregation by property — each cited (clause_id + span_id + "
            "confidence). The intra-contract serving capability over the typed KG."
        ),
        representative_queries=(
            "list every clause in this contract with its function and citation",
            "return the clauses of a given function within one contract",
            "aggregate a contract's clauses by a typed property",
        ),
        tags=("serving", "intra-contract", "cited"),
    ),
    CapabilityManifest(
        slug="clause_disambiguation",
        kind="function",
        display_name="Clause disambiguation",
        description=(
            "Within one contract, select the specific clause matching a typed condition among several of the "
            "same function (e.g. the mutual cap; the covenant-not-to-sue that is unbounded), by typed property "
            "filter over the KG. Cited."
        ),
        representative_queries=(
            "find the mutual cap clause among several cap clauses in this contract",
            "select the clause matching a typed condition among same-function clauses",
            "disambiguate same-type clauses by a typed property",
        ),
        tags=("serving", "disambiguation", "cited"),
    ),
    CapabilityManifest(
        slug="clause_function_classification",
        kind="model",
        display_name="Clause function classification (LegalBERT)",
        description=(
            "Classify an operative span into its CUAD-type function label(s) with a fine-tuned LegalBERT "
            "sequence classifier (T56); supports top-k for confusable-sibling routing. Model inference "
            "(CPU/GPU)."
        ),
        representative_queries=(
            "classify a contract span into its CUAD function type",
            "predict the top-k function labels for an operative span",
            "route a span to its clause type with a fine-tuned classifier",
        ),
        tags=("classification", "legalbert", "model"),
    ),
    CapabilityManifest(
        slug="query_function_classification",
        kind="agent_skill",
        display_name="Query function classification",
        description=(
            "Classify a natural-language query into the closed FUNCTION taxonomy via a single "
            "taxonomy-constrained structured LLM call, normalized to canonical labels at the boundary "
            "(KG-5e). The in-distribution query-side counterpart to the clause classifier."
        ),
        representative_queries=(
            "map a query to the clause function(s) it is about, constrained to the taxonomy",
            "classify an attorney's question into the closed function taxonomy",
            "route a query to functions for candidate-pool selection",
        ),
        tags=("classification", "query-side", "routing"),
    ),
    # --- LG-1: hardened LangGraph subgraphs ---
    CapabilityManifest(
        slug="typed_clause_extraction",
        kind="subgraph",
        display_name="Typed clause extraction",
        description=(
            "Extract a clause's typed (dimension, value) property record from its text, hardened as a LangGraph "
            "subgraph: docling-graph + granite extraction (retry on transient), adapt to the record, "
            "grounding-judge gate (ADR-0028), a Flash->Pro escalation on low-confidence, an optional human gate, "
            "and a dead-letter terminal so one bad clause never kills a batch."
        ),
        representative_queries=(
            "extract the typed property record for a contract clause",
            "turn a clause's text into confidence-tagged (dimension, value) assertions",
            "run schema-driven clause extraction with grounding and escalation",
        ),
        tags=("extraction", "ingestion", "subgraph", "langgraph"),
    ),
    CapabilityManifest(
        slug="query_constraint_extraction",
        kind="subgraph",
        display_name="Query constraint extraction",
        description=(
            "Extract a query's typed (dimension, value) constraints with the SAME granite + clause_template "
            "extractor used on clauses (KG-5b), hardened as a LangGraph subgraph with graceful degradation: a "
            "failed extraction yields an empty constraint set (embedding-only fallback), so the query is never "
            "dropped. No reground on queries (KG-5d: it false-flags real constraints)."
        ),
        representative_queries=(
            "extract the typed constraints a retrieval query is asking for",
            "turn an attorney's query into (dimension, value) constraints for KG matching",
            "parse a query into typed property constraints, same schema as the clauses",
        ),
        tags=("extraction", "query-side", "subgraph", "langgraph"),
    ),
    # --- LG-3: composite pipeline subgraphs ---
    CapabilityManifest(
        slug="relational_qa",
        kind="subgraph",
        display_name="Relational QA (cited answer from graph traversal)",
        description=(
            "Answer a relational/multi-hop entity question with a grounded, cited answer, as a composite "
            "LangGraph subgraph: traverse the knowledge graph (graph_query, FR-C.5) for cited evidence, "
            "rehydrate the evidence chunk_ids to full text (chunk_read, T38), then generate a grounded, "
            "abstaining, cited answer (generate_answer, FR-Q.6). Query-side hardening: a transient traversal "
            "failure degrades to empty evidence (the generator abstains, the query survives); an orphaned "
            "chunk_id dead-letters rather than fabricating. Confidence tags surface graph->evidence->answer."
        ),
        representative_queries=(
            "answer a relational question about an entity with a cited answer",
            "who does this party contract with, and cite the clauses",
            "traverse the graph from an entity and generate a grounded answer",
        ),
        tags=("qa", "relational", "graph", "subgraph", "langgraph", "composite"),
    ),
    CapabilityManifest(
        slug="intra_document_qa",
        kind="subgraph",
        display_name="Intra-document QA (cited answer scoped to one contract)",
        description=(
            "Answer a question scoped to ONE contract with a grounded, cited answer, as a composite LangGraph "
            "subgraph: run the intra-contract scoped KG query (KG-4; classify the question to its clause "
            "function(s) and serve those clauses with their typed properties, each cited), rehydrate each "
            "clause's real operative-span text and append its typed facts as cited evidence (worst-case "
            "confidence surfaced, FR-S.4), then generate a grounded, abstaining, cited answer (generate_answer, "
            "FR-Q.6). Query-side hardening: a transient serve failure degrades to empty evidence (the generator "
            "abstains, the query survives); an orphaned span_id dead-letters rather than fabricating."
        ),
        representative_queries=(
            "answer a question about a single contract with cited clauses",
            "what does this contract say about the liability cap, with citations",
            "disambiguate and answer over one contract's typed clause KG",
        ),
        tags=("qa", "intra-document", "contract", "subgraph", "langgraph", "composite"),
    ),
    # cross_corpus_retrieval RETIRED (standardized on typed_property_retrieval / Leg B, which uses the correct
    # BGE+property pool via property_boosted_retrieval; cross_corpus's function-only pool was the inferior copy).
    CapabilityManifest(
        slug="typed_property_retrieval",
        kind="subgraph",
        display_name="Typed property-boosted retrieval (Leg B)",
        description=(
            "The property-boosted Leg B as a composite LangGraph subgraph (LEGB-SUBGRAPH, ADR-0033): extract the "
            "query's typed constraints and route its functions (granite + LegalBERT) in parallel, then run the "
            "property_boosted_retrieval capability -- a bounded BGE base pool joined to each span's clause props "
            "via the operative-span edge.span_id link, reranked by typed-constraint match (BGE tiebreak) -- and "
            "emit top-k spans cited by span_id with the constraints each satisfied. Query-side hardening: a "
            "transient failure degrades to empty, never a crash. Wraps the registered property_boosted_retrieval "
            "function so the WORKFLOW is a registered subgraph, not an imperative script."
        ),
        representative_queries=(
            "retrieve clauses matching the query's typed constraints, ranked over a BGE pool, cited",
            "property-boosted typed retrieval as a hardened LangGraph workflow",
            "route + constrain + property-boost rerank the contract clause corpus",
        ),
        tags=("retrieval", "typed", "ranking", "subgraph", "langgraph", "composite"),
    ),
    CapabilityManifest(
        slug="contract_ingestion_pipeline",
        kind="subgraph",
        display_name="Contract ingestion pipeline (corpus -> populated, connected KG)",
        description=(
            "Ingest a corpus of contracts into a populated, connected contract KG, as one GENERIC composite "
            "LangGraph subgraph: per document, chunk (semantic_chunking) -> extract clauses "
            "(typed_clause_extraction) and the party/relational graph (graph_extraction) in parallel -> resolve "
            "entities (entity_resolution) -> write (typed clause KG + entity graph), with a per-document "
            "dead-letter so one bad document never kills the ingest; then party_clause_linking (KG-7) runs once "
            "to connect parties to clauses. Corpus-agnostic: a CorpusAdapter supplies the documents (parsing + "
            "the one canonical source_doc_id + any corpus metadata), so adding a corpus is one adapter, never a "
            "re-implemented ingest_xyz()."
        ),
        representative_queries=(
            "ingest a corpus of contracts into the knowledge graph",
            "populate and connect the typed clause KG and party graph from source documents",
            "run the generic contract ingestion pipeline over a new corpus adapter",
        ),
        tags=("ingestion", "pipeline", "corpus", "subgraph", "langgraph", "composite"),
    ),
    # --- Compliance module rung 1 (roadmap §13): the ad-compliance engine ---
    CapabilityManifest(
        slug="requirement_extraction",
        kind="subgraph",  # extract(docling-graph, multi-call auto/dense) -> adapt; a workflow, not a single act
        display_name="Requirement extraction (regulatory section -> deontic rules; subgraph)",
        description=(
            "Extract the deontic rules a regulatory section states into typed Requirement nodes as a hardened "
            "LangGraph subgraph (CC-2, compliance §13.1, SKILL-SPLIT): extract (the docling-graph extraction act "
            "using the skills/requirement_extraction/ template, extraction_contract='auto' -> dense/multi-call "
            "on long sections) -> adapt (requirement_adaptation). A subgraph because the extraction is "
            "multi-LLM-call and the extract->adapt chaining is deterministic. A transient extraction failure "
            "retries then dead-letters the section. The regulatory side of the compliance module's ingestion."
        ),
        representative_queries=(
            "extract the rules a regulation section states as typed requirements",
            "turn FTC endorsement-guide text into cited deontic requirement nodes",
            "parse a regulatory corpus into obligation/prohibition/permission rules",
        ),
        tags=("compliance", "extraction", "deontic", "regulatory", "subgraph"),
    ),
    CapabilityManifest(
        slug="requirement_adaptation",
        kind="function",
        display_name="Requirement adaptation (extracted section -> validated Requirements)",
        description=(
            "DETERMINISTIC adaptation (CC-2, SKILL-SPLIT): map the requirement_extraction subgraph's raw "
            "ExtractedRegulationSection to validated CC-1 Requirement nodes -- deontic force coerced to the "
            "closed vocab (off-vocab -> AMBIGUOUS, kept not dropped), applicability_scope from the claim_types "
            "(off-vocab dropped), citation = the section, content-hash requirement_id. No model."
        ),
        representative_queries=(
            "adapt an extracted regulation section into validated requirements",
            "coerce extracted deontic force to the closed vocab with a conservative fallback",
            "attach citations and ids to extracted regulatory rules",
        ),
        tags=("compliance", "adaptation", "deontic", "deterministic"),
    ),
    CapabilityManifest(
        slug="claim_extraction",
        kind="agent_skill",  # a single docling-graph LLM extraction act, authored as skills/claim_extraction/
        display_name="Claim extraction (subject ad -> checkable claims; authored skill)",
        description=(
            "The subject-document claim-extraction METHOD (CC-3, compliance §13.1), authored as an agent skill "
            "(skills/claim_extraction/: SKILL.md + the template.py schema asset ExtractedAd/ExtractedClaim) and "
            "applied through the docling-graph + model seam (Granite, ADR-0039): read an ad and pull out its "
            "distinct CHECKABLE assertions, each with its kind, the disclosures present near it, and whether the "
            "ad references evidence. One 'direct' call (ads are short). The deterministic mapping to the closed "
            "Claim vocab is the claim_adaptation FUNCTION's job, not the skill's."
        ),
        representative_queries=(
            "extract the checkable claims an ad makes",
            "turn a marketing campaign into claims with disclosures and evidence flags",
            "identify the health/efficacy/endorsement claims in a subject document",
        ),
        tags=("compliance", "extraction", "claims", "advertising", "skill"),
    ),
    CapabilityManifest(
        slug="claim_adaptation",
        kind="function",
        display_name="Claim adaptation (extracted ad -> validated Claims)",
        description=(
            "DETERMINISTIC adaptation (CC-3, SKILL-SPLIT): map the claim_extraction skill's raw ExtractedAd to "
            "validated CC-1 Claim nodes -- claim_type coerced to the closed vocab (an off-vocab value kept but "
            "flagged AMBIGUOUS, a checkable assertion is never dropped), disclosures/evidence/medium carried, "
            "the content-hash claim_id + span provenance attached. No model."
        ),
        representative_queries=(
            "adapt an extracted ad into validated typed claims",
            "coerce extracted claim types to the closed vocab with a conservative fallback",
            "attach span provenance and ids to extracted ad claims",
        ),
        tags=("compliance", "adaptation", "claims", "deterministic"),
    ),
    CapabilityManifest(
        slug="compliance_judgment",
        kind="agent_skill",  # a single grounded LLM judgment act, authored as skills/compliance_judgment/SKILL.md
        display_name="Compliance judgment (claim x requirement -> verdict; authored skill)",
        description=(
            "The advertising-compliance judgment METHOD (CC-4, compliance §13.2), authored as an agent skill "
            "(skills/compliance_judgment/SKILL.md) and applied through the model seam (Granite, ADR-0039): given "
            "one claim + one requirement and ONLY the ad text, decide violation (clearly wrong from the text -- "
            "overclaimed proof without a cited study, missing disclosure, fake review), needs_review (an "
            "objective claim whose substantiation cannot be verified from the text -- escalate), or compliant "
            "(puffery / disclosure present / evidence cited). Reserves violation for clear breaches and escalates "
            "the unverifiable; never a silent pass. The deterministic vocab/citation is the "
            "compliance_finding_assembly FUNCTION's job, not the skill's."
        ),
        representative_queries=(
            "judge whether an ad claim violates a regulatory requirement",
            "decide compliant / violation / needs-review for a claim against a rule",
            "audit a marketing claim against an FTC endorsement requirement",
        ),
        tags=("compliance", "judgment", "verdict", "skill", "human-in-the-loop"),
    ),
    CapabilityManifest(
        slug="compliance_finding_assembly",
        kind="function",
        display_name="Compliance finding assembly (verdict + inputs -> cited finding)",
        description=(
            "DETERMINISTIC assembly (CC-4, SKILL-SPLIT): map the compliance_judgment skill's raw verdict string "
            "to the closed Verdict vocab (an unreadable or missing verdict -> needs_review, the conservative "
            "default), and attach the BOTH-SIDED citation (the exact claim span + the exact requirement clause) "
            "FROM THE INPUTS -- the model never authors a citation -- returning a ComplianceFinding. No model: "
            "the trust guarantees the LLM judgment must not own live here."
        ),
        representative_queries=(
            "assemble a cited compliance finding from a raw judgment verdict",
            "map a verdict string to the closed vocab with a conservative default",
            "attach both-sided citations to a compliance verdict from the inputs",
        ),
        tags=("compliance", "assembly", "deterministic", "citation", "conservative-default"),
    ),
    CapabilityManifest(
        slug="compliance_ingestion",
        kind="subgraph",
        display_name="Compliance ingestion (regulatory corpus -> Requirement KG)",
        description=(
            "Ingest a regulatory corpus into a Requirement KG as a hardened LangGraph subgraph (CC-5, "
            "compliance §13): per section, extract the deontic rules (requirement_extraction) and write them "
            "as Requirement nodes, with a per-section retry -> dead-letter so one bad section never kills the "
            "ingest. Reuses the generic corpus driver (SourceDocument + run_corpus_ingestion) via a thin "
            "RegulationAdapter; the Requirement KG lives in its own database so the contract KG stays clean. "
            "The regulatory-corpus side of the compliance module's ingestion."
        ),
        representative_queries=(
            "ingest a regulation into a requirement knowledge graph",
            "load the FTC endorsement guides as typed deontic requirement nodes",
            "build the requirements KG from a regulatory corpus",
        ),
        tags=("compliance", "ingestion", "regulatory", "subgraph", "langgraph"),
    ),
    CapabilityManifest(
        slug="compliance_check",
        kind="subgraph",
        display_name="Compliance check (subject doc x requirements -> cited findings + gap matrix)",
        description=(
            "Check a subject advertisement against a regulatory Requirement KG as a hardened LangGraph subgraph "
            "(CC-6, compliance §13.3, the headline composite): extract the ad's claims (claim_extraction) -> "
            "retrieve the applicable requirements (claim scope <-> requirement applicability, with a "
            "section->claim_type map backfilling empty scopes) -> judge each (claim, requirement) pair "
            "(compliance_judgment, concurrent, with ad-level disclosure context) -> assemble cited findings + a "
            "per-requirement gap matrix + a verdict summary. Query-side: degrades to empty on failure; every "
            "violation/needs_review is human-gated. Both-sided cited -- the trust product."
        ),
        representative_queries=(
            "check whether an ad campaign complies with the FTC endorsement guides",
            "produce a cited compliance gap matrix for a marketing document",
            "audit a subject document against a regulatory requirements KG",
        ),
        tags=("compliance", "check", "verdict", "gap-matrix", "subgraph", "langgraph"),
    ),
    CapabilityManifest(
        slug="compliance_check_mcp",
        kind="mcp_tool",  # the discoverable MCP-tool surface of the compliance_check subgraph (MCP-PROTO)
        display_name="Ad compliance check (MCP tool)",
        description=(
            "The compliance_check capability exposed as a single MCP tool (`check_ad_compliance`, served by "
            "rag_wright.mcp.compliance_server via FastMCP): screen one advertisement against the FTC 16 CFR 255 "
            "endorsement rules and return a cited ComplianceReport (ad-level verdict, per-claim findings with "
            "both-sided citations, verdict summary, per-requirement gap matrix). An external agent discovers "
            "this via ARD search and calls it as ONE tool -- saving context tokens and inter-agent coordination "
            "vs. embedding the compliance_check subgraph. Same output contract as the subgraph; distinct ARD "
            "identity because the callable surface is a deployed MCP server, not an in-process graph node."
        ),
        representative_queries=(
            "check whether an advertisement complies with the FTC endorsement rules",
            "screen ad copy for unsubstantiated claims and missing disclosures",
            "get a cited compliance report for a marketing claim as a tool call",
        ),
        tags=("compliance", "mcp-tool", "ad-screening", "cited", "discoverable"),
    ),
    CapabilityManifest(
        slug="intra_document_qa_mcp",
        kind="mcp_tool",  # the discoverable MCP-tool surface of the intra_document_qa subgraph (MCP-PROTO B1)
        display_name="Intra-document contract QA (MCP tool)",
        description=(
            "The intra_document_qa capability exposed as a single MCP tool (`answer_contract_question`, served "
            "by rag_wright.mcp.intra_document_qa_server via FastMCP): answer a natural-language question about "
            "ONE known contract from its clause knowledge graph and return a cited GeneratedAnswer (grounded "
            "answer, chunk_id citations, abstained flag). An external agent discovers this via ARD search and "
            "calls it as ONE tool -- saving context tokens and inter-agent coordination vs. embedding the "
            "intra_document_qa subgraph. Same output contract as the subgraph; distinct ARD identity because the "
            "callable surface is a deployed MCP server, not an in-process graph node."
        ),
        representative_queries=(
            "answer a question about a known contract with cited clauses as a tool call",
            "what does this contract say about the liability cap, with citations",
            "get a cited answer for one contract's terms over MCP",
        ),
        tags=("qa", "intra-document", "contract", "mcp-tool", "cited", "discoverable"),
    ),
    CapabilityManifest(
        slug="relational_qa_mcp",
        kind="mcp_tool",  # the discoverable MCP-tool surface of the relational_qa subgraph (MCP-PROTO B2)
        display_name="Relational contract QA (MCP tool)",
        description=(
            "The relational_qa capability exposed as a single MCP tool (`answer_relational_question`, served by "
            "rag_wright.mcp.relational_qa_server via FastMCP): answer a relational question about a known entity "
            "by traversing the contract entity graph and return a cited GeneratedAnswer whose facts are cited by "
            "their SOURCE CONTRACT (graph-structural evidence, no chunk text). An external agent discovers this "
            "via ARD search and calls it as ONE tool -- saving context tokens and inter-agent coordination vs. "
            "embedding the relational_qa subgraph. Same output contract as the subgraph; distinct ARD identity "
            "because the callable surface is a deployed MCP server, not an in-process graph node."
        ),
        representative_queries=(
            "which parties does this company contract with, as a tool call",
            "traverse the entity graph for a company's contracting relationships with citations",
            "get a cited relational answer over MCP from a start entity",
        ),
        tags=("qa", "relational", "entity-graph", "mcp-tool", "cited", "discoverable"),
    ),
    CapabilityManifest(
        slug="typed_property_retrieval_mcp",
        kind="mcp_tool",  # the discoverable MCP-tool surface of the typed_property_retrieval subgraph (MCP-PROTO B3)
        display_name="Typed property-boosted retrieval (MCP tool)",
        description=(
            "The typed_property_retrieval capability (Leg B) exposed as a single MCP tool "
            "(`retrieve_typed_property_spans`, served by rag_wright.mcp.typed_property_retrieval_server via "
            "FastMCP): retrieve the most relevant contract clauses for a query from across the corpus, "
            "property-boosted and cited, returning a TypedPropertyRetrieval (the query + ranked spans each with "
            "its span_id citation, text, function, match score, and satisfied constraints). Corpus-wide "
            "RETRIEVAL (ranked evidence), NOT a written answer -- distinct from the intra_document_qa / "
            "relational_qa answer tools. An external agent discovers this via ARD search and calls it as ONE "
            "tool vs. embedding the subgraph. Same output contract as the subgraph; distinct ARD identity "
            "because the callable surface is a deployed MCP server, not an in-process graph node."
        ),
        representative_queries=(
            "find clauses across the corpus that cap liability at a multiple of the fees paid",
            "retrieve ranked cited clauses matching a typed condition as a tool call",
            "corpus-wide property-boosted clause search over MCP",
        ),
        tags=("retrieval", "typed-property", "leg-b", "mcp-tool", "cited", "ranked", "discoverable"),
    ),
)

MANIFEST_SPECS: dict[str, CapabilityManifest] = {spec.slug: spec for spec in _SPECS}


def author(slug: str) -> RegistryEntry:
    """Author a capability's committed spec into a complete, validated `RegistryEntry`."""
    if slug not in MANIFEST_SPECS:
        raise KeyError(f"no ARD manifest spec for {slug!r}; add one in capabilities/manifests.py")
    spec = MANIFEST_SPECS[slug]
    if slug not in CANONICAL_CAPABILITY_SLUGS:
        raise ValueError(f"{slug!r} is not a canonical capability slug (SPEC.md section 5)")

    # callable kinds must declare response bounds; agent_skill is loaded, not called (carries none).
    bounds = (spec.response_bounds or ResponseBounds()) if spec.kind in CALLABLE_KINDS else None

    skeleton = ManifestSkeleton(
        name=spec.slug,
        kind=spec.kind,
        identifier=capability_urn(spec.slug),
        media_type=MEDIA_TYPE_BY_KIND[spec.kind],
        display_name=spec.display_name,
        response_bounds=bounds,
        description=spec.description,
        tags=list(spec.tags),
    )
    return skeleton.author(
        list(spec.representative_queries),
        requires=list(spec.requires) or None,
        skill_runtime=spec.skill_runtime,
        capability_interface=spec.capability_interface,
        golden_eval_ref=spec.golden_eval_ref,
    )


def publish(slug: str, *, root: Optional[Path] = None) -> Path:
    """Author `slug` and write its manifest to `<root>/<slug>.json` (default: the shared root)."""
    return write_manifest(author(slug), root=root)


def publish_all(*, root: Optional[Path] = None) -> list[Path]:
    """Author and write every specified manifest into the root. Returns the written paths."""
    return [publish(slug, root=root) for slug in MANIFEST_SPECS]
