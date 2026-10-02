"""EP-API-1/4 (ADR-0117): the engine's typed configuration. The product constructs an `EngineConfig` and passes it
to `open_workspace`; the engine resolves backends behind an opaque handle. This is the ONLY place a store backend is
named -- everything else goes through the handle. EP-API-4a adds the `options` catalog (ingest knobs today;
retrieval/rerank groups land as needed), so a product tunes the engine through config, never environment variables."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


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
    """Ingest-time knobs, settable through config instead of environment variables (EP-API-4a). Every field defaults
    to `None` = "use the engine default", so the engine's existing env fallback is preserved (a non-API caller is
    unaffected) and an API caller that leaves these unset gets today's behavior exactly. Set a field to override."""

    classify_concurrency: Optional[int] = None    # function-classify parallelism (was CLASSIFY_CONCURRENCY)
    clause_concurrency: Optional[int] = None       # clause-extraction parallelism (was CLAUSE_CONCURRENCY)
    affiliations: Optional[bool] = None            # run affiliation extraction (was RAG_INGEST_AFFILIATIONS)
    function_classifier: Optional[str] = None      # "setfit" | "llm" (was RAG_FUNCTION_CLASSIFIER)
    list_model: Optional[str] = None               # secondary list-union model, "off" to disable (was RAG_INGEST_LIST_MODEL)
    clause_samples: Optional[int] = None           # multi-sample count for the list union (was RAG_INGEST_CLAUSE_SAMPLES)


@dataclass(frozen=True)
class EngineOptions:
    """The engine's options catalog. Ingest knobs today; retrieval / reranking / chunking groups are added here as
    they are promoted off environment variables."""

    ingest: IngestOptions = field(default_factory=IngestOptions)


@dataclass(frozen=True)
class EngineConfig:
    """The product's view of the engine: the store connection, chosen models (by role alias), the embedding profile,
    and the `options` catalog. Defaults just work; override only to trade quality/cost/latency. Implementation
    details (ArcadeDB, BGE) never cross this boundary."""

    store: StoreConfig
    models: dict[str, str] = field(default_factory=dict)  # ModelRole value -> engine-supported model alias (override)
    embeddings: dict[str, str] = field(default_factory=lambda: {"text": "bge-m3"})  # profile -> supported embedder
    options: EngineOptions = field(default_factory=EngineOptions)  # EP-API-4a: ingest (+ future) knobs via config
