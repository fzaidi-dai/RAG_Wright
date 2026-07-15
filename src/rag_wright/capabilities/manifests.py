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
    ),
    CapabilityManifest(
        slug="embedding",
        kind="function",
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
    ),
    CapabilityManifest(
        slug="reranking",
        kind="function",  # an in-process query-side node
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
    ),
    CapabilityManifest(
        slug="graph_extraction",
        kind="function",  # an in-process ingestion-side node
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
    ),
    CapabilityManifest(
        slug="generation",
        kind="function",  # in-process callable; the compiler binds it, not over MCP (T31 note)
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
        golden_eval_ref=spec.golden_eval_ref,
    )


def publish(slug: str, *, root: Optional[Path] = None) -> Path:
    """Author `slug` and write its manifest to `<root>/<slug>.json` (default: the shared root)."""
    return write_manifest(author(slug), root=root)


def publish_all(*, root: Optional[Path] = None) -> list[Path]:
    """Author and write every specified manifest into the root. Returns the written paths."""
    return [publish(slug, root=root) for slug in MANIFEST_SPECS]
