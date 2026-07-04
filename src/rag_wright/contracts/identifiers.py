"""Shared identifier contracts: `chunk_id` (FR-S.2) and `entity_id` (FR-S.3).

These schemes are load-bearing and fixed here before anything is built. A re-chunk that changes
a `chunk_id` breaks the link between a chunk and its extracted graph nodes, and a non-canonical
`entity_id` fragments the graph across surface-form variants. Changing either scheme is an
ask-first change (SPEC.md section 14; CLAUDE.md boundaries).

Both identifiers are frozen Pydantic models, so they are immutable and hashable and can serve as
dictionary keys and graph-node identity directly.
"""

from __future__ import annotations

import hashlib
import re

from pydantic import BaseModel, ConfigDict, field_validator

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_DOC_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_CANONICAL_CIK = re.compile(r"^\d{10}$")


class ChunkId(BaseModel):
    """The stable identifier for a chunk (FR-S.2).

    Scheme: source-document identifier, chunk index, and a content hash of the chunk text. The
    content hash is what makes the identifier change when (and only when) the chunk content
    changes, so an unchanged document re-chunks to the same ids (the content-hash gate in FR-I.1
    and FR-I.5 relies on this).
    """

    model_config = ConfigDict(frozen=True)

    source_doc_id: str
    chunk_index: int
    content_hash: str  # lowercase hex SHA-256 digest of the chunk content

    @field_validator("source_doc_id")
    @classmethod
    def _delimiter_safe_source(cls, v: str) -> str:
        v = v.strip()
        if not _SOURCE_DOC_ID.match(v):
            raise ValueError(
                "source_doc_id must be non-empty and use only [A-Za-z0-9._-], so the ':'-delimited "
                "value string stays unambiguous for provenance and citation lookup; assign a "
                "delimiter-safe id upstream (slugify the filename if needed)"
            )
        return v

    @field_validator("chunk_index")
    @classmethod
    def _nonnegative_index(cls, v: int) -> int:
        if v < 0:
            raise ValueError("chunk_index must be >= 0")
        return v

    @field_validator("content_hash")
    @classmethod
    def _valid_sha256(cls, v: str) -> str:
        v = v.strip().lower()
        if not _SHA256_HEX.match(v):
            raise ValueError("content_hash must be a 64-character lowercase hex SHA-256 digest")
        return v

    @classmethod
    def of(cls, source_doc_id: str, chunk_index: int, content: str | bytes) -> ChunkId:
        """Build a `ChunkId`, computing the content hash deterministically (SHA-256).

        This is where determinism lives (RAC-1): identical `(source_doc_id, chunk_index, content)`
        always yield an identical `ChunkId`.
        """
        data = content.encode("utf-8") if isinstance(content, str) else content
        return cls(
            source_doc_id=source_doc_id,
            chunk_index=chunk_index,
            content_hash=hashlib.sha256(data).hexdigest(),
        )

    @property
    def value(self) -> str:
        """The canonical string form: ``<source_doc_id>:<chunk_index>:<content_hash>``.

        Because ``source_doc_id`` is constrained to a delimiter-safe character set, this string is
        safe to string-match and to parse back with ``rsplit(":", 2)`` for provenance and citation
        lookup. Identity itself remains field-based (frozen-model equality and hashing), not
        string-based.
        """
        return f"{self.source_doc_id}:{self.chunk_index}:{self.content_hash}"

    def __str__(self) -> str:
        return self.value


class EntityId(BaseModel):
    """The canonical entity identifier (FR-S.3).

    For the validation corpus (ADR-0002) the canonical identifier is the U.S. Securities and
    Exchange Commission (SEC) Electronic Data Gathering, Analysis, and Retrieval (EDGAR) Central
    Index Key (CIK) in its 10-digit zero-padded form, e.g. ``"0000320193"``.

    This contract is deliberately strict: it accepts the canonical form only and rejects everything
    else (unpadded, ``"CIK"``-prefixed, integer, whitespace-padded). An identifier contract's job
    is to define the canonical invariant and reject anything that is not canonical, so every
    downstream holder of an `EntityId` can trust it. Normalizing the messy EDGAR forms into this
    canonical form is the registry loader's job (T8), where the world's mess actually arrives;
    keeping the contract strict surfaces upstream defects loudly at that boundary instead of
    silently minting wrong-but-normalizable identifiers and fragmenting the graph.
    """

    model_config = ConfigDict(frozen=True)

    cik: str  # canonical 10-digit zero-padded CIK, e.g. "0000320193"

    @field_validator("cik", mode="before")
    @classmethod
    def _canonical_only(cls, v: object) -> str:
        if not isinstance(v, str) or not _CANONICAL_CIK.match(v):
            raise ValueError(
                "cik must be the canonical EDGAR form: a string of exactly 10 digits, zero-padded "
                "(e.g. '0000320193'). Normalize upstream at the registry loader (T8)."
            )
        return v

    @classmethod
    def of(cls, cik: str) -> EntityId:
        """Build an `EntityId` from a CIK already in canonical 10-digit form.

        This does not normalize; non-canonical input is rejected (see the class docstring). The
        registry loader (T8) is where raw EDGAR forms are normalized before construction.
        """
        return cls(cik=cik)

    @property
    def value(self) -> str:
        """The canonical string form: the 10-digit zero-padded CIK."""
        return self.cik

    def __str__(self) -> str:
        return self.value
