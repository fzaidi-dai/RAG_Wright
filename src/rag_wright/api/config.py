"""EP-API-1 (ADR-0117): the engine's typed configuration. The product constructs an `EngineConfig` and passes it to
`open_workspace`; the engine resolves backends behind an opaque handle. This is the ONLY place a store backend is
named -- everything else goes through the handle. The `options` catalog (reranker/retrieval/ingest knobs) is added
in EP-API-4; EP-API-1 covers the connection + model aliases + embedding profile."""
from __future__ import annotations

from dataclasses import dataclass, field


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
class EngineConfig:
    """The product's view of the engine: the store connection, chosen models (by role alias), and the embedding
    profile. Defaults just work; override only to trade quality/cost/latency. Implementation details (ArcadeDB, BGE)
    never cross this boundary."""

    store: StoreConfig
    models: dict[str, str] = field(default_factory=dict)  # ModelRole value -> engine-supported model alias (override)
    embeddings: dict[str, str] = field(default_factory=lambda: {"text": "bge-m3"})  # profile -> supported embedder
