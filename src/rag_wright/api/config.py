"""EP-API-1/4 (ADR-0117): the engine's typed configuration. The product constructs an `EngineConfig` and passes it
to `open_workspace`; the engine resolves backends behind an opaque handle. This is the ONLY place a store backend is
named -- everything else goes through the handle. EP-API-4a adds the `options` catalog (ingest knobs today;
retrieval/rerank groups land as needed), so a product tunes the engine through config, never environment variables."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from rag_wright.contracts.ingestion import IngestionTuning


@dataclass(frozen=True)
class StoreConfig:
    """How to reach the KG/retrieval store. `backend` selects the implementation (only `arcadedb` today; a new
    backend -- e.g. Neo4j -- is added here, invisibly to the product)."""

    host: str
    port: str
    user: str
    password: str
    backend: str = "arcadedb"
    protocol: str = "http"


@dataclass(frozen=True)
class IngestOptions:
    """The engine's generic ingest knobs, settable through config instead of environment variables (EP-API-4a).
    ING-8d: only engine mechanism lives here; a domain pack's own knobs travel in `EngineOptions.packs`."""

    tuning: Optional[IngestionTuning] = None     # ING-4b: the generic ingestion thresholds (build_ingestion default)


@dataclass(frozen=True)
class EngineOptions:
    """The engine's options catalog: the generic ingest knobs, plus `packs` -- each domain pack's own options
    object keyed by the pack's name (ING-8d), e.g. `packs={"contracts": ContractIngestOptions(...)}`. The engine
    passes `packs` through untouched; a pack reads its entry and falls back to its defaults when absent."""

    ingest: IngestOptions = field(default_factory=IngestOptions)
    packs: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EngineConfig:
    """The product's view of the engine: the store connection, chosen models (`models`: `ModelRole` value -> model
    alias), the embedding profile, the `options` catalog (generic `ingest` knobs + each pack's options under
    `packs`), and `pack`: the path to the domain pack `.ttl` whose KG types `open_workspace` creates (None = only the
    neutral engine types: Chunk, Entity, Relationship, Mentions, Span, Document, EmbeddedIn, AttachedTo). Defaults
    just work; override only to trade quality/cost/latency. Implementation details (ArcadeDB, BGE) never cross this
    boundary."""

    store: StoreConfig
    models: dict[str, str] = field(default_factory=dict)  # ModelRole value -> engine-supported model alias (override)
    embeddings: dict[str, str] = field(default_factory=lambda: {"text": "bge-m3"})  # profile -> supported embedder
    options: EngineOptions = field(default_factory=EngineOptions)  # EP-API-4a: ingest (+ future) knobs via config
    # AC-journey / ING-8a: the DOMAIN pack `.ttl` declaring this domain's KG vertex/edge types (open_workspace creates
    # them). None = no domain pack: the neutral engine schema only (Chunk, Entity, Span, Document + their edges). A
    # domain points this at its own `.ttl` -- "config + .ttl", no engine edit -- and `ensure_schema` creates that
    # schema on top of the engine types. (The reference contract pipeline ensures its own pack schema on use.)
    pack: Optional[str] = None
