"""Provenance and confidence contracts (FR-S.4).

Every stored unit carries provenance: for text, the source document and the chunk it came from;
for graph-derived facts, additionally a confidence tag. Provenance is what makes "no claim without
a citation" (FR-Q.6) enforceable, and the confidence tag is what marks a graph fact as evidence to
be verified, not truth (SPEC.md section 14).

`Provenance` and `ConfidenceTag` are the reusable primitives; `GraphFact` is the base that graph
extraction (T5) and graph storage (T24) build their nodes, edges, and facts on.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator

from rag_wright.contracts.identifiers import ChunkId


class ConfidenceTag(str, Enum):
    """The confidence a graph-derived fact carries (FR-S.4). A closed set, no other value.

    - ``EXTRACTED``: read directly from a source chunk.
    - ``INFERRED``: derived by reasoning over one or more chunks, not stated verbatim.
    - ``AMBIGUOUS``: supported but with competing readings or unresolved mentions.
    """

    EXTRACTED = "EXTRACTED"
    INFERRED = "INFERRED"
    AMBIGUOUS = "AMBIGUOUS"


class Provenance(BaseModel):
    """The source document and chunk a stored text unit came from (FR-S.4).

    The `chunk_id` (FR-S.2) already carries its source-document identifier; `source_doc_id` is kept
    as an explicit, denormalized field so a citation is self-describing, so records can be filtered
    and indexed by source document at the store level (metadata filters, FR-Q.1), and so downstream
    code never has to parse `chunk_id` to recover the document. The redundancy is safe only because
    the two fields cannot disagree: the `_source_matches_chunk` validator runs on every construction
    and deserialization path (raw constructor, `model_validate`, `model_validate_json`), and
    `Provenance.of(chunk_id)` derives `source_doc_id` from the chunk so callers cannot create an
    inconsistent one. Store deserialization must therefore use a validating path
    (`model_validate` / `model_validate_json`), not `model_construct`, which bypasses all validation.
    """

    model_config = ConfigDict(frozen=True)

    source_doc_id: str
    chunk_id: ChunkId

    @model_validator(mode="after")
    def _source_matches_chunk(self) -> Provenance:
        if self.source_doc_id != self.chunk_id.source_doc_id:
            raise ValueError(
                "source_doc_id must match chunk_id.source_doc_id "
                f"({self.source_doc_id!r} != {self.chunk_id.source_doc_id!r}); "
                "use Provenance.of(chunk_id) to derive it"
            )
        return self

    @classmethod
    def of(cls, chunk_id: ChunkId) -> Provenance:
        """Build a `Provenance` from a chunk id, deriving `source_doc_id` from it (no drift)."""
        return cls(source_doc_id=chunk_id.source_doc_id, chunk_id=chunk_id)


class GraphFact(BaseModel):
    """The base for a graph-derived fact: it carries provenance and a confidence tag (FR-S.4).

    Graph nodes and edges (FR-I.4) carry the originating `chunk_id` (through `provenance`) and a
    `confidence` tag. Graph extraction (T5) and graph storage (T24) extend this base with their own
    ontology-conforming fields.
    """

    provenance: Provenance
    confidence: ConfidenceTag
