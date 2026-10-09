"""The CONTRACTS pack (the engine's worked reference domain): its capability slugs and ARD manifests (ING-8c; moved
from `rag_wright.reference.pack`). `register()` adds the pack's canonical slugs, then registers the engine
capabilities it builds on plus its own manifests. A product's pack follows the same shape (`load_pack("<module>")`)."""

from __future__ import annotations

from rag_wright.capabilities.ard import ResponseBounds  # noqa: F401 - used by some specs
from rag_wright.capabilities.manifests import (  # noqa: F401 - the spec dataclass + shared helpers the specs use
    RLM_GRANTED_SUBAGENTS,
    CapabilityManifest,
    engine_capabilities,
    register_capability,
)
from rag_wright.capabilities.registry import register_canonical_slugs

CONTRACT_CAPABILITY_SLUGS: frozenset[str] = frozenset({
    "clause_disambiguation",
    "clause_exception_linking",
    "clause_function_classification",
    "clause_property_classification",
    "contract_ingestion_pipeline",
    "extraction_grounding_judge",
    "extraction_semantic_gate",
    "extraction_semantic_judge",
    "intra_document_qa",
    "intra_document_qa_mcp",
    "intra_document_scoped_query",
    "query_constraint_extraction",
    "query_function_classification",
    "relational_qa",
    "relational_qa_mcp",
    "typed_clause_extraction",
    "typed_property_retrieval",
    "typed_property_retrieval_mcp",
    "typed_value_normalization",
})

CONTRACT_SPECS: tuple[CapabilityManifest, ...] = (
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
        impl_ref="rag_wright.packs.contracts.spans.model_capabilities:clause_function_classification",
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
        slug="clause_property_classification",
        impl_ref="rag_wright.packs.contracts.spans.model_capabilities:clause_property_classification",
        kind="model",
        display_name="Clause property classification (29-dim fleet)",
        description=(
            "Classify a provision's closed-vocab property dimensions (cap basis, mutuality, IP ownership, dispute "
            "method, royalty basis, ...) with the best-of-both Laya/SetFit fleet (ADR-0115/0116), soft-scoped to the "
            "clause's likely functions; abstaining dims say 'not present'. Returns (dimension, value, confidence) "
            "soft tags -- the classifier lane of Step-3a, no LLM. Model inference (CPU/GPU)."
        ),
        representative_queries=(
            "classify the closed-vocab property dimensions of a contract provision",
            "tag a clause with its cap basis / mutuality / IP ownership values",
            "get the soft property tags for a provision without an LLM call",
        ),
        tags=("classification", "laya", "setfit", "model"),
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
    CapabilityManifest(
        slug="relational_qa",
        impl_ref="rag_wright.packs.contracts.subgraphs.relational_qa:ainvoke",
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
        impl_ref="rag_wright.packs.contracts.subgraphs.intra_document_qa:ainvoke",
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
    CapabilityManifest(
        slug="typed_property_retrieval",
        impl_ref="rag_wright.packs.contracts.subgraphs.typed_property_retrieval:ainvoke",
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
        impl_ref="rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline:ainvoke",
        kind="subgraph",
        display_name="Contract ingestion pipeline (corpus -> populated, connected KG)",
        description=(
            "Ingest a corpus of contracts into a populated, connected contract KG, as one GENERIC composite "
            "LangGraph subgraph: per document, chunk (semantic_chunking) -> extract clauses "
            "(typed_clause_extraction) and the party/relational graph (graph_extraction) in parallel -> resolve "
            "entities (entity_resolution) -> write (typed clause KG + entity graph), with a per-document "
            "dead-letter so one bad document never kills the ingest. Corpus-agnostic: a CorpusAdapter supplies the documents (parsing + "
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
    CapabilityManifest(
        slug="intra_document_qa_mcp",
        kind="mcp_tool",  # the discoverable MCP-tool surface of the intra_document_qa subgraph (MCP-PROTO B1)
        display_name="Intra-document contract QA (MCP tool)",
        description=(
            "The intra_document_qa capability exposed as a single MCP tool (`answer_contract_question`, served "
            "by rag_wright.packs.contracts.mcp.intra_document_qa_server via FastMCP): answer a natural-language question about "
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
            "rag_wright.packs.contracts.mcp.relational_qa_server via FastMCP): answer a relational question about a known entity "
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
            "(`retrieve_typed_property_spans`, served by rag_wright.packs.contracts.mcp.typed_property_retrieval_server via "
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


def register() -> None:
    """Register the contracts pack: its canonical slugs, then the engine capabilities it uses, then its manifests."""
    register_canonical_slugs(CONTRACT_CAPABILITY_SLUGS)
    for manifest in (*engine_capabilities(), *CONTRACT_SPECS):
        register_capability(manifest)
