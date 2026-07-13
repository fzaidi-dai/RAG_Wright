"""The graph extraction contract and extractor seam (FR-C.6, FR-I.4).

Graph extraction is a hybrid stack (FR-C.6): docling-graph contract extraction for schema entities,
a lightweight NER-plus-dependency path for the bulk, an open-ended language-model escalation for
hard cases, and later Open Information Extraction (OpenIE). This module is the *contract* those
extractors conform to, not the extractors themselves (those are the graph-extraction capability,
T23).

The load-bearing part is the seam: `Extractor` is a real interface, and `run_extractors` iterates a
list of extractors and merges their results. Adding a new extractor (the deferred OpenIE path) is
just appending an `Extractor` to that list; neither `run_extractors` nor `ExtractionResult` is
reopened. Every extractor yields an `ExtractionResult` whose facts conform to the ontology (T4) and
are anchored to the originating `chunk_id` (FR-I.4).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, field_validator, model_validator

from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.ontology import ClauseFact, EntityType, RelationshipFact
from rag_wright.contracts.provenance import ConfidenceTag


class EntityMention(BaseModel):
    """A pre-resolution entity mention: a surface form, its ontology type, and a confidence tag.

    Graph extraction produces typed mentions (NER labels); entity resolution (FR-C.7 / T24) later
    maps the surface form to a canonical `entity_id` and creates the canonical `EntityNode`.

    A mention IS an ontology-conforming graph fact (FR-S.4): it is read from the text, so a spaCy NER
    or contract-extracted mention carries a `confidence` tag like any other fact (ADR-0012). Its
    `chunk_id` provenance is the containing `ExtractionResult.chunk_id` (mentions are anchored by the
    result, not individually provenanced, since resolution collapses many mentions to one node). This
    is deliberately how the spaCy path satisfies "each path produces facts carrying chunk_id +
    confidence" — by emitting confidence-bearing mentions, NOT by inventing edges: co-occurrence of
    two organizations in legal text is frequently non-contractual (a non-compete, a governing-law or
    payment-clause reference), so a proximity edge is a false-edge generator, and nothing downstream
    filters edges (T26 surfaces confidence, it does not gate on it — FR-C.5/FR-Q.3). CONTRACTS_WITH
    comes from signing-party structure (the contract extractor), never proximity (ADR-0012).

    `text` here is the *same notion* as a `RelationshipFact`'s `source_ref` / `target_ref`: both are
    pre-resolution entity surface forms. Standalone mentions and relationship endpoints are two
    channels for the same entities, so entity resolution (T24) must resolve them as one mention
    stream; an entity appearing as both must resolve to a single node, not a duplicate.
    """

    text: str
    entity_type: EntityType
    confidence: ConfidenceTag

    @field_validator("text")
    @classmethod
    def _text_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("entity mention text must be non-empty")
        return v


class ExtractionResult(BaseModel):
    """What one extractor produces for one chunk: ontology-conforming facts plus typed mentions.

    Every fact is anchored to `chunk_id`: its provenance must point at this chunk, so the extraction
    result carries the originating `chunk_id` end to end (FR-I.4). Facts already conform to the
    ontology (their type fields are the T4 enums), so a non-ontology fact cannot be built at all.
    """

    chunk_id: ChunkId
    entity_mentions: list[EntityMention] = []
    clause_facts: list[ClauseFact] = []
    relationship_facts: list[RelationshipFact] = []

    @model_validator(mode="after")
    def _facts_anchored_to_chunk(self) -> ExtractionResult:
        for fact in (*self.clause_facts, *self.relationship_facts):
            if fact.provenance.chunk_id != self.chunk_id:
                raise ValueError(
                    "every fact in an ExtractionResult must be anchored to the result's chunk_id "
                    "(fact provenance chunk_id does not match)"
                )
        return self

    @classmethod
    def merge(cls, chunk_id: ChunkId, results: Sequence[ExtractionResult]) -> ExtractionResult:
        """Merge several extractors' results for one chunk into a single result.

        All results must be for `chunk_id`; a result for another chunk is a defect and is rejected.
        Merging is a union (dedup, if any, is entity resolution's and graph storage's concern).
        """
        for result in results:
            if result.chunk_id != chunk_id:
                raise ValueError("cannot merge extraction results from different chunks")
        return cls(
            chunk_id=chunk_id,
            entity_mentions=[m for r in results for m in r.entity_mentions],
            clause_facts=[f for r in results for f in r.clause_facts],
            relationship_facts=[f for r in results for f in r.relationship_facts],
        )


@runtime_checkable
class Extractor(Protocol):
    """The extractor seam. Each extractor in the hybrid stack (FR-C.6) implements this, and the
    graph-extraction capability (T23) iterates over a list of them. The deferred OpenIE path is a
    future `Extractor` added to that list, behind this same contract, with no change here.

    Note: `@runtime_checkable` makes `isinstance(x, Extractor)` a *presence* check only (it verifies
    `extract` and `name` exist, not their signatures or return type). Signature and output
    conformance are enforced downstream by `ExtractionResult` validation, which is what the
    result-validation tests exercise, not `isinstance`.
    """

    name: str

    def extract(self, chunk_id: ChunkId, text: str) -> ExtractionResult: ...


def run_extractors(
    extractors: Iterable[Extractor], chunk_id: ChunkId, text: str
) -> ExtractionResult:
    """Run every extractor over one chunk and merge into a single anchored `ExtractionResult`.

    This is the seam the capability drives: registering a new extractor means adding it to
    `extractors`, nothing here changes.
    """
    results = [extractor.extract(chunk_id, text) for extractor in extractors]
    return ExtractionResult.merge(chunk_id, results)
