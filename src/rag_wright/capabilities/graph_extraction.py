"""Graph extraction (FR-C.6, FR-I.4): the GP-1B docling-graph party/relational extractor over parsed chunks.

Re-backed per ADR-0035. The capability's job is unchanged -- a chunk -> ontology-conforming graph facts
(`ExtractionResult`) anchored to `chunk_id` with a confidence tag (FR-S.4), behind the T5 `Extractor` seam --
but the *implementation* is now the **GP-1B docling-graph extractor** (granite-4.2-8b), the entity/relational
extractor that populated Leg C at real recall 0.991. The earlier T23-27 hybrid stack (spaCy NER +
Pydantic-contract extraction + LLM escalation) is retired: Leg B (clause facts / inter-corpus recall) is served
by the typed Clause KG (typed_clause_extraction), and Leg C (party-to-party relational) by GP-1B, so the hybrid
stack -- built to probe inter-corpus recall -- no longer earns its keep.

`DoclingGraphExtractor` extracts the signing parties from the chunk text via docling-graph
(`dg_extraction.extract_parties`) and emits ORGANIZATION mentions + structural `CONTRACTS_WITH` facts between
them (`parties_to_extraction`), EXTRACTED. Resolution to canonical ids (EDGAR CIK) and the graph write are
downstream (`entity_resolution` -> `write_graph`), unchanged. The docling-graph call is a raw-SDK call, so the
LG-2c subgraph wraps it in `raw_llm_span` (the observability contract); `extract_fn` is dependency-injected so
the extractor is hermetically testable with no docling-graph / no network.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Callable

from pydantic import BaseModel

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.extraction import (
    EntityMention,
    ExtractionResult,
    Extractor,
    run_extractors,
)
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.ontology import EntityType, RelationshipFact, RelationshipType
from rag_wright.contracts.provenance import ConfidenceTag, Provenance

DEFAULT_EXTRACT_CONCURRENCY = 4  # in-flight chunk extractions (backpressure); GPU/network-bound
# The adopted graph-extraction model (GP-1B): granite-4.2-8b via OpenRouter; config-driven (SPEC §17).
DEFAULT_GRAPH_EXTRACT_MODEL = os.getenv("RAG_GRAPH_EXTRACT_MODEL", "ibm-granite/granite-4.2-8b")

# extract_fn: contract/chunk text -> a `ContractParties` (docling-graph output) or None when nothing extracted.
PartyExtractFn = Callable[[str], Any]


def parties_to_extraction(chunk_id: ChunkId, parties: list[str]) -> ExtractionResult:
    """Known signing parties -> ORGANIZATION mentions + a `CONTRACTS_WITH` fact between each pair, EXTRACTED.

    Strips + dedups (order-stable); a lone party yields a mention but no edge. ADR-0012: the parties are the
    actual signatories, so `CONTRACTS_WITH` is structural, not a proximity guess. This is the fact shape both
    the GP-1B extractor (below) and the no-LLM CUAD-`Parties` path share.
    """
    provenance = Provenance.of(chunk_id)
    names = list(dict.fromkeys(p.strip() for p in parties if p.strip()))
    mentions = [
        EntityMention(text=name, entity_type=EntityType.ORGANIZATION, confidence=ConfidenceTag.EXTRACTED)
        for name in names
    ]
    relationships = [
        RelationshipFact(
            provenance=provenance, confidence=ConfidenceTag.EXTRACTED,
            source_ref=names[i], relationship_type=RelationshipType.CONTRACTS_WITH, target_ref=names[j],
        )
        for i in range(len(names))
        for j in range(i + 1, len(names))
    ]
    return ExtractionResult(chunk_id=chunk_id, entity_mentions=mentions, relationship_facts=relationships)


# --- issue 0027: corporate AFFILIATION extraction (AFFILIATE_OF) ---------------------------------
# The ontology declares `RelationshipType.AFFILIATE_OF` and both `write_graph` and `graph_query` handle it, but
# nothing ever PRODUCED the edge, so corporate affiliation was unanswerable. Affiliation is stated in the text
# ("Acme Holdings Ltd, an affiliate of Acme Corp"), so -- unlike the structural `CONTRACTS_WITH` -- it needs a
# text-reading extraction. This runs once per contract on the preamble (like party extraction), gated by a lexical
# pre-filter so contracts that state no affiliation cost no LLM call. Entities are NOT merged (an affiliate is a
# separate legal entity); only the edge between the two org nodes is added.

_AFFIL_PREAMBLE_CHARS = 8000  # affiliations, like parties, are named in the preamble; bound the LLM input

# Lexical pre-filter: no affiliation cue in the text -> no LLM call. A miss is a false-negative (missed
# affiliation); a spurious hit just costs a call that returns nothing. Prompt-engineering overlay (ADR-0066: the
# relationship TYPE is ontology-declared; these cue words are mechanism, not the closed vocabulary).
_AFFILIATION_CUE_RE = re.compile(
    r"\b(affiliate|affiliated|subsidiar|parent\s+compan|wholly[\s-]?owned|under\s+common\s+control|"
    r"a\s+division\s+of|owned\s+by)\b", re.IGNORECASE)

_AFFILIATION_PROMPT = (
    "From this contract text, extract statements of CORPORATE AFFILIATION -- where one organization is stated to "
    "be an affiliate, subsidiary, parent, division of, or under common control with ANOTHER organization. For each, "
    "give the organization and the organization it is affiliated with. Do NOT treat two organizations merely "
    "signing the same contract as an affiliation. If none is stated, return no items.\n\nTEXT:\n{text}")


class Affiliation(BaseModel):
    """One corporate-affiliation statement (issue 0027): `organization` is stated to be an affiliate/subsidiary/
    parent of / under common control with `affiliate_of`. Both are ORGANIZATION surface forms."""

    organization: str = ""
    affiliate_of: str = ""


class Affiliations(BaseModel):
    """The tag-parse output of the affiliation extraction: zero or more `Affiliation` pairs."""

    affiliations: list[Affiliation] = []


def affiliations_to_extraction(chunk_id: ChunkId, affiliations: list) -> ExtractionResult:
    """Corporate-affiliation pairs -> ORGANIZATION mentions for BOTH orgs (so both endpoints resolve to nodes) +
    an `AFFILIATE_OF` fact between them, EXTRACTED (issue 0027). Entities are NOT merged; only the edge is added.
    Each `affiliations` item is a 2-tuple/list `(organization, affiliate_of)`. Strips/dedups; a pair with an empty
    or self-referential side is dropped."""
    provenance = Provenance.of(chunk_id)
    mentions: list[EntityMention] = []
    facts: list[RelationshipFact] = []
    seen: set[str] = set()
    for pair in affiliations:
        org = (pair[0] or "").strip()
        affil_of = (pair[1] or "").strip()
        if not org or not affil_of or org.lower() == affil_of.lower():
            continue
        for name in (org, affil_of):
            if name.lower() not in seen:
                seen.add(name.lower())
                mentions.append(EntityMention(
                    text=name, entity_type=EntityType.ORGANIZATION, confidence=ConfidenceTag.EXTRACTED))
        facts.append(RelationshipFact(
            provenance=provenance, confidence=ConfidenceTag.EXTRACTED,
            source_ref=org, relationship_type=RelationshipType.AFFILIATE_OF, target_ref=affil_of))
    return ExtractionResult(chunk_id=chunk_id, entity_mentions=mentions, relationship_facts=facts)


async def aextract_affiliations(text: str, *, model_id: str = DEFAULT_GRAPH_EXTRACT_MODEL) -> list[tuple[str, str]]:
    """Extract corporate-affiliation pairs from `text` (issue 0027). LEXICAL PRE-FILTER first: no affiliation cue
    word -> no LLM call (near-zero added ingestion cost, since most contracts state none). On a cue hit, a lean
    client-side tag-parse extraction over the preamble (ADR-0045). Degrades to no affiliations on any parse
    failure (never raised)."""
    if not _AFFILIATION_CUE_RE.search(text or ""):
        return []
    from rag_wright.models.tag_structured import build_tag_structured

    try:
        out = await build_tag_structured(model_id, Affiliations, label="affiliations").ainvoke(
            _AFFILIATION_PROMPT.format(text=text[:_AFFIL_PREAMBLE_CHARS]))
    except Exception:  # noqa: BLE001 - a persistent client-side parse failure -> no affiliations, not a crash
        return []
    if out is None:
        return []
    return [(a.organization, a.affiliate_of) for a in out.affiliations
            if (a.organization or "").strip() and (a.affiliate_of or "").strip()]


class DoclingGraphExtractor:
    """GP-1B: docling-graph party extraction (granite-4.2-8b) -- the adopted graph extractor (ADR-0035).

    Extracts the signing parties from the chunk text via the injected `extract_fn` (docling-graph
    `extract_parties`), then emits ORGANIZATION mentions + structural `CONTRACTS_WITH` facts between them. A
    chunk that yields no parties returns an empty `ExtractionResult` (never raised). `extract_fn` is injected so
    the extractor is hermetic in tests (a stub returning a `ContractParties`-shaped object with `.parties[].name`).
    """

    name = "docling_graph"

    def __init__(self, extract_fn: PartyExtractFn) -> None:
        self._extract_fn = extract_fn

    def extract(self, chunk_id: ChunkId, text: str) -> ExtractionResult:
        extracted = self._extract_fn(text)
        names = [p.name for p in extracted.parties] if extracted is not None else []
        return parties_to_extraction(chunk_id, names)


def production_extract_fn(*, model_id: str = DEFAULT_GRAPH_EXTRACT_MODEL) -> PartyExtractFn:
    """Bind the real docling-graph party extractor to `(text) -> ContractParties | None` (granite via OpenRouter).
    Lazy import so the module stays import-light and hermetic (tests inject a stub instead)."""
    from rag_wright.capabilities.dg_extraction import extract_parties, openrouter_model

    model = openrouter_model("graph-extract", model_id)
    return lambda text: extract_parties(text, model)


def aproduction_extract_fn(*, model_id: str = DEFAULT_GRAPH_EXTRACT_MODEL):
    """ASYNC-B2c (ADR-0057): the async twin of `production_extract_fn` -- party extraction on the async
    docling-graph seam (`aextract_parties`, true wall-clock deadline). Returns an async `(text) -> ContractParties
    | None`."""
    from rag_wright.capabilities.dg_extraction import aextract_parties, openrouter_model

    model = openrouter_model("graph-extract", model_id)

    async def _afn(text: str):
        return await aextract_parties(text, model)

    return _afn


def default_extractors() -> list[Extractor]:
    """The default extractor: the GP-1B docling-graph party extractor (the live wiring; ADR-0035)."""
    return [DoclingGraphExtractor(production_extract_fn())]


async def extract_chunks(
    items: list[tuple[ChunkId, str]],
    *,
    extractors: list[Extractor],
    max_concurrency: int = DEFAULT_EXTRACT_CONCURRENCY,
) -> list[ExtractionResult]:
    """Extract facts from chunks concurrently (FR-I.6): each chunk's extractor stack runs in a thread,
    bounded by a semaphore so bulk mode saturates the GPU/provider without unbounded in-flight work."""
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _one(chunk_id: ChunkId, text: str) -> ExtractionResult:
        async with semaphore:  # backpressure
            return await asyncio.to_thread(run_extractors, extractors, chunk_id, text)

    return list(await asyncio.gather(*(_one(chunk_id, text) for chunk_id, text in items)))


def extract_chunks_sync(
    items: list[tuple[ChunkId, str]],
    *,
    extractors: list[Extractor],
    max_concurrency: int = DEFAULT_EXTRACT_CONCURRENCY,
) -> list[ExtractionResult]:
    """Synchronous convenience for callers not already in an event loop."""
    return asyncio.run(extract_chunks(items, extractors=extractors, max_concurrency=max_concurrency))


def register_graph_extraction(registry: CapabilityRegistry) -> None:
    """Register graph extraction under FR-C.6 (`graph_extraction`, a `subgraph`; GP-1B docling-graph, ADR-0035)."""
    registry.register(
        "graph_extraction",
        contract=ExtractionResult,
        kind="subgraph",  # multi-step extractor workflow (CAP-REG-1)
        display_name="Graph extraction (GP-1B docling-graph party/relational)",
    )
