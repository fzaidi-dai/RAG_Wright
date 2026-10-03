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
_SOURCE_DOC_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def canonical_source_doc_id(raw: str) -> str:
    """The ONE canonical filename/title -> `source_doc_id` slug (FR-S.2; HYG-1).

    Every ingestion path MUST derive a `source_doc_id` through this function so the same document gets the
    same id everywhere. Any run of characters outside the delimiter-safe set ``[A-Za-z0-9._-]`` (notably
    spaces, ``&``, commas) collapses to a single ``_``; leading/trailing ``_`` are stripped. Existing safe
    delimiters (``-``, ``.``, ``_`` -- e.g. inside ``EX-10.1`` / ``10-Q``) are preserved. Idempotent on an
    already-canonical id.

    The ``_`` replacement (never ``-``) is the fix for the HYG-1 divergence: two ingestion paths slugged the
    same title with different characters (``FLEET_MAINTENANCE`` vs ``FLEET-MAINTENANCE``), breaking the
    cross-graph join. An empty result raises rather than silently colliding every empty title into one id.
    """
    slug = _SOURCE_DOC_UNSAFE.sub("_", raw).strip("_") if isinstance(raw, str) else ""
    if not slug:
        raise ValueError(
            f"source_doc_id slug is empty for {raw!r}; supply a non-empty, sluggable document id"
        )
    return slug


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
    """The canonical entity identifier (FR-S.3): an opaque canonical-registry id string.

    The engine is domain-agnostic (DD-4, ADR-0067/0117), so the FORMAT of a canonical id is owned by
    the resolver / domain pack, NOT by this contract. The SEC pack resolves to a 10-digit zero-padded
    EDGAR Central Index Key (CIK), e.g. ``"0000320193"`` (shaped in ``corpus/edgar.normalize_cik``); a
    generic pack uses an exact-normalized surface-form key; another domain uses its own scheme. This
    contract's only invariant is therefore the domain-neutral one: a non-empty string. That is still a
    real invariant -- every downstream holder of an `EntityId` can trust it is a present, non-blank id --
    while the format check lives at the one boundary that knows the domain (the resolver/loader), where
    the world's mess actually arrives, per the "normalize at the boundary" rule.
    """

    model_config = ConfigDict(frozen=True)

    value: str  # an opaque canonical id; its FORMAT is the resolver/pack's concern, not this contract's

    @field_validator("value", mode="before")
    @classmethod
    def _nonempty(cls, v: object) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError(
                "EntityId.value must be a non-empty string. The canonical-id FORMAT is owned by the "
                "resolver / domain pack (e.g. corpus/edgar.normalize_cik for SEC CIKs), not this contract."
            )
        return v

    @classmethod
    def of(cls, value: str) -> EntityId:
        """Build an `EntityId` from an already-canonical id string.

        This does not normalize or format-check beyond non-emptiness; shaping the raw domain form into
        the canonical id is the resolver / domain pack's job (e.g. `corpus/edgar.normalize_cik`).
        """
        return cls(value=value)

    def __str__(self) -> str:
        return self.value
