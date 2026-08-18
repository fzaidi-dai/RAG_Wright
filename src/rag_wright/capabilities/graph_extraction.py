"""Graph extraction (FR-C.6, FR-I.4): the GP-1B docling-graph party/relational extractor over parsed chunks.

Re-backed per ADR-0035. The capability's job is unchanged -- a chunk -> ontology-conforming graph facts
(`ExtractionResult`) anchored to `chunk_id` with a confidence tag (FR-S.4), behind the T5 `Extractor` seam --
but the *implementation* is now the **GP-1B docling-graph extractor** (granite-4.1-8b), the entity/relational
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
from typing import Any, Callable

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
# The adopted graph-extraction model (GP-1B): granite-4.1-8b via OpenRouter; config-driven (SPEC §17).
DEFAULT_GRAPH_EXTRACT_MODEL = os.getenv("RAG_GRAPH_EXTRACT_MODEL", "ibm-granite/granite-4.1-8b")

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


class DoclingGraphExtractor:
    """GP-1B: docling-graph party extraction (granite-4.1-8b) -- the adopted graph extractor (ADR-0035).

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
