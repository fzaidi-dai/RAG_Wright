"""Graph extraction (FR-C.6, FR-I.4): a hybrid extractor stack over parsed chunks.

Three extractors, all behind the T5 `Extractor` seam, all producing ontology-conforming facts (T4)
anchored to the chunk's `chunk_id` with a confidence tag (FR-S.4):

- `SpacyNerExtractor` — the lightweight NER bulk path. Emits typed entity *mentions* only
  (ORG -> ORGANIZATION, PERSON -> PERSON), confidence EXTRACTED. It never emits a relationship edge:
  co-occurrence of two organizations in legal text is frequently non-contractual, so a proximity edge
  is a false-edge generator, and nothing downstream filters edges (ADR-0012).
- `ContractExtractor` — Pydantic-contract structured extraction. Emits which of the 41 CUAD clause
  categories are present (ClauseFacts), the signing parties (mentions), and CONTRACTS_WITH edges
  between the parties (structural, near-perfect precision; the only source of CONTRACTS_WITH). EXTRACTED.
- `LlmEscalationExtractor` — open-ended structured extraction for hard cases (e.g. corporate
  affiliation). Emits RelationshipFacts, confidence INFERRED.

The two LLM extractors call the model only through the model-profile seam (T11) under the DeepSeek V4
Pro structured-reasoning profile (ADR-0006); no provider/model flag lives here. The capability is a
GPU/network-calling capability, so `extract_chunks` carries the FR-I.6 decoupling property: each chunk's
extraction runs in a thread (`asyncio.to_thread`), bounded by a semaphore (the T19/T-SUM pattern).
"""

from __future__ import annotations

import asyncio
import os
from typing import Callable, Protocol, runtime_checkable

from pydantic import BaseModel

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.extraction import (
    EntityMention,
    ExtractionResult,
    Extractor,
    run_extractors,
)
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.ontology import (
    ClauseCategory,
    ClauseFact,
    EntityType,
    RelationshipFact,
    RelationshipType,
)
from rag_wright.contracts.provenance import ConfidenceTag, Provenance
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_structured

DEFAULT_EXTRACT_CONCURRENCY = 4  # in-flight chunk extractions (backpressure); GPU/network-bound
DEFAULT_SPACY_MODEL = os.getenv("RAG_SPACY_MODEL", "en_core_web_sm")  # config-driven (SPEC §17)

# OntoNotes NER labels -> the T4 ontology entity types. Off-ontology labels (GPE, DATE, MONEY, ...)
# are dropped; the noise (e.g. a bare "LLC" tagged ORG) is expected and handled by T23b + the human gate.
_SPACY_LABEL_TO_TYPE = {"ORG": EntityType.ORGANIZATION, "PERSON": EntityType.PERSON}

_CONTRACT_PROMPT = (
    "From this contract chunk, extract: (1) parties — the organizations that are signing parties to "
    "the agreement (exact surface forms); (2) clause_categories — which of the listed clause types are "
    "present. Extract only what is stated in this chunk."
)
_ESCALATION_PROMPT = (
    "From this contract chunk, extract entity-to-entity relationships (e.g. corporate affiliation, "
    "parent/subsidiary). Return each as source_ref, relationship_type, target_ref using the surface "
    "forms. Extract only relationships supported by this chunk; return none if unsure."
)

StructuredFactory = Callable[[str, type], object]  # (model_id, schema) -> a Runnable with .invoke


@runtime_checkable
class NerPipeline(Protocol):
    """The NER seam: return (surface, label) pairs for a text. `SpacyPipeline` binds it; tests stub it."""

    def ner(self, text: str) -> list[tuple[str, str]]: ...


class SpacyPipeline:
    """The real NER pipeline: a spaCy model (config-driven name), loaded lazily."""

    def __init__(self, model_name: str = DEFAULT_SPACY_MODEL) -> None:
        self._model_name = model_name
        self._nlp = None

    def ner(self, text: str) -> list[tuple[str, str]]:
        if self._nlp is None:
            import spacy

            self._nlp = spacy.load(self._model_name)
        return [(ent.text, ent.label_) for ent in self._nlp(text).ents]


class SpacyNerExtractor:
    """The NER bulk path: typed entity mentions only, no edges (ADR-0012). Confidence EXTRACTED."""

    name = "spacy_ner"

    def __init__(self, pipeline: NerPipeline) -> None:
        self._pipeline = pipeline

    def extract(self, chunk_id: ChunkId, text: str) -> ExtractionResult:
        mentions: list[EntityMention] = []
        seen: set[tuple[str, EntityType]] = set()
        for surface, label in self._pipeline.ner(text):
            entity_type = _SPACY_LABEL_TO_TYPE.get(label)
            surface = surface.strip()
            if entity_type is None or not surface or (surface, entity_type) in seen:
                continue
            seen.add((surface, entity_type))
            mentions.append(
                EntityMention(text=surface, entity_type=entity_type, confidence=ConfidenceTag.EXTRACTED)
            )
        return ExtractionResult(chunk_id=chunk_id, entity_mentions=mentions)


class _ContractExtraction(BaseModel):
    """The contract extractor's structured output: signing parties + clause categories present."""

    parties: list[str] = []
    clause_categories: list[ClauseCategory] = []


class ContractExtractor:
    """Pydantic-contract extraction: clause facts + party mentions + party-structure CONTRACTS_WITH.

    CONTRACTS_WITH is emitted between the signing parties (a structural fact), the only source of that
    edge (ADR-0012). Everything is EXTRACTED (read from the chunk). Uses the model-profile seam.
    """

    name = "contract"

    def __init__(self, *, model_id: str | None = None,
                 structured_factory: StructuredFactory = build_structured) -> None:
        self._model_id = model_id or model_for(ModelRole.STRUCTURED_REASONING)
        self._factory = structured_factory

    def extract(self, chunk_id: ChunkId, text: str) -> ExtractionResult:
        out: _ContractExtraction = self._factory(self._model_id, _ContractExtraction).invoke(
            f"{_CONTRACT_PROMPT}\n\n{text}"
        )
        provenance = Provenance.of(chunk_id)
        parties = list(dict.fromkeys(p.strip() for p in out.parties if p.strip()))  # dedup, order-stable
        mentions = [
            EntityMention(text=p, entity_type=EntityType.ORGANIZATION, confidence=ConfidenceTag.EXTRACTED)
            for p in parties
        ]
        relationships = [
            RelationshipFact(
                provenance=provenance, confidence=ConfidenceTag.EXTRACTED,
                source_ref=parties[i], relationship_type=RelationshipType.CONTRACTS_WITH,
                target_ref=parties[j],
            )
            for i in range(len(parties))
            for j in range(i + 1, len(parties))
        ]
        clause_facts = [
            ClauseFact(provenance=provenance, confidence=ConfidenceTag.EXTRACTED, category=category)
            for category in dict.fromkeys(out.clause_categories)
        ]
        return ExtractionResult(
            chunk_id=chunk_id, entity_mentions=mentions,
            clause_facts=clause_facts, relationship_facts=relationships,
        )


class _EscalatedRelationship(BaseModel):
    source_ref: str
    relationship_type: RelationshipType
    target_ref: str


class _EscalatedRelationships(BaseModel):
    relationships: list[_EscalatedRelationship] = []


class LlmEscalationExtractor:
    """Open-ended structured extraction for hard cases (e.g. corporate affiliation). INFERRED."""

    name = "llm_escalation"

    def __init__(self, *, model_id: str | None = None,
                 structured_factory: StructuredFactory = build_structured) -> None:
        self._model_id = model_id or model_for(ModelRole.STRUCTURED_REASONING)
        self._factory = structured_factory

    def extract(self, chunk_id: ChunkId, text: str) -> ExtractionResult:
        out: _EscalatedRelationships = self._factory(self._model_id, _EscalatedRelationships).invoke(
            f"{_ESCALATION_PROMPT}\n\n{text}"
        )
        provenance = Provenance.of(chunk_id)
        relationships = []
        for relationship in out.relationships:
            source, target = relationship.source_ref.strip(), relationship.target_ref.strip()
            if not source or not target or source == target:
                continue  # skip empty or ref-level self-loop (RelationshipFact would reject it anyway)
            relationships.append(
                RelationshipFact(
                    provenance=provenance, confidence=ConfidenceTag.INFERRED,
                    source_ref=source, relationship_type=relationship.relationship_type,
                    target_ref=target,
                )
            )
        return ExtractionResult(chunk_id=chunk_id, relationship_facts=relationships)


def parties_to_extraction(chunk_id: ChunkId, parties: list[str]) -> ExtractionResult:
    """The no-LLM party path (GP-1(A)): known signing parties -> ORGANIZATION mentions + a CONTRACTS_WITH
    fact between each pair, EXTRACTED. Same fact shape as `ContractExtractor` minus the model call -- the
    cheap first-light edge source when the signatories are already known (e.g. the CUAD 'Parties'
    annotation). Strips + dedups (order-stable); a lone party yields a mention but no edge. ADR-0012:
    the parties are the actual signatories, so CONTRACTS_WITH is structural, not a proximity guess."""
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


def default_extractors() -> list[Extractor]:
    """The default hybrid stack: spaCy NER + contract + LLM escalation (the live wiring)."""
    return [SpacyNerExtractor(SpacyPipeline()), ContractExtractor(), LlmEscalationExtractor()]


async def extract_chunks(
    items: list[tuple[ChunkId, str]],
    *,
    extractors: list[Extractor],
    max_concurrency: int = DEFAULT_EXTRACT_CONCURRENCY,
) -> list[ExtractionResult]:
    """Extract facts from chunks concurrently (FR-I.6): each chunk's extractor stack runs in a thread,
    bounded by a semaphore so bulk mode saturates the GPU without unbounded in-flight work."""
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
    """Register graph extraction under FR-C.6 (`graph_extraction`, an in-process `function`)."""
    registry.register(
        "graph_extraction",
        contract=ExtractionResult,
        kind="function",
        display_name="Graph extraction (hybrid: spaCy NER + contract + LLM escalation)",
    )
