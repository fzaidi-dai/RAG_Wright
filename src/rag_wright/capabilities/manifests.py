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
        kind="subgraph",  # multi-step LLM extractor stack (NER + OpenIE + LLM); CAP-REG-1
        display_name="Graph extraction (hybrid: spaCy NER + contract + LLM escalation)",
        description=(
            "Extract ontology-conforming graph facts from a parsed chunk with a hybrid stack: spaCy "
            "NER for typed entity mentions, Pydantic-contract extraction for clause categories and "
            "signing-party CONTRACTS_WITH edges, and an LLM escalation for hard-case relationships. "
            "Every fact carries chunk_id provenance and a confidence tag (FR-C.6, FR-I.4)."
        ),
        representative_queries=(
            "extract entities and relationships from a contract chunk",
            "identify the signing parties and which clause types a chunk contains",
            "produce ontology-conforming graph facts with provenance and confidence",
            "recognize the organizations and people mentioned in a document",
        ),
        tags=("extraction", "graph", "ner", "spacy", "ingestion"),
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
        kind="function",
        display_name="Vision-to-text (scanned-image transcription)",
        description=(
            "Transcribe a scanned filing's images to text at ingestion on the Gemma 4 class model "
            "(FR-C.9). Split from answer generation (ADR-0014): different inputs (an image, not evidence), "
            "different failure modes, and a different caller (ingestion, not the query path)."
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
