"""EP-API-1 (ADR-0117): `open_workspace` + the opaque `WorkspaceHandle`.

The product names a `corpus` (the logical KG = the backend database name; the product maps its own tenant -> db-name,
so tenancy stays product-owned) and gets back an opaque handle that resolves + caches the store (and lazily the
embedder) from the `EngineConfig`. The handle is what the engine's per-kind invokers (EP-API-2) take as resources.
The product never imports `ArcadeDBStore`/`query_embedder` and the handle exposes no public store accessor."""
from __future__ import annotations

import logging
from typing import Any, Optional

from rag_wright.api.config import EngineConfig
from rag_wright.models.profiles import ModelRole, model_for

logger = logging.getLogger(__name__)

# Cached per (backend, host, port, corpus) -- one resolved workspace per customer DB (absorbs the product's
# per-customer graph/store cache). Process-local; the embedder is lazy + per-handle.
_WORKSPACES: dict[tuple, "WorkspaceHandle"] = {}


class WorkspaceHandle:
    """An opaque handle to a resolved engine workspace. Public surface: `model_id(role)`. The resolved store +
    embedder are engine-internal (`_store` / `_embedder`), used by the invokers -- NOT a product accessor."""

    def __init__(self, store: Any, config: EngineConfig, corpus: str) -> None:
        self._store = store
        self._config = config
        self._corpus = corpus
        self.__embedder: Any = None
        self.__embedder_built = False

    @property
    def _embedder(self) -> Any:
        """The query embedder for this workspace, built lazily on first use (so an ingest-only workspace -- or a
        test -- needs no embedder backend). Best-effort: unavailable -> None (the consumer surfaces it)."""
        if not self.__embedder_built:
            self.__embedder = _build_embedder(self._config)
            self.__embedder_built = True
        return self.__embedder

    def model_id(self, role: ModelRole) -> str:
        """Resolve a model role to its id: the `EngineConfig.models` override wins, else the profile default."""
        key = role.value if isinstance(role, ModelRole) else str(role)
        return self._config.models.get(key) or model_for(role)


def open_workspace(config: EngineConfig, *, corpus: str, reset: bool = False) -> WorkspaceHandle:
    """Resolve (and cache) the workspace for `corpus` (the backend database name) from `config`. Ensures the schema.
    Returns an opaque `WorkspaceHandle`. `reset=True` drops + recreates the database (test/clean-slate) and bypasses
    the cache."""
    sc = config.store
    cache_key = (sc.backend, sc.host, sc.port, corpus)
    if not reset and cache_key in _WORKSPACES:
        return _WORKSPACES[cache_key]
    store = _build_store(config, corpus, reset=reset)
    store.ensure_schema()
    handle = WorkspaceHandle(store, config, corpus)
    _WORKSPACES[cache_key] = handle
    return handle


def _build_store(config: EngineConfig, corpus: str, *, reset: bool) -> Any:
    sc = config.store
    if sc.backend != "arcadedb":
        raise ValueError(f"unsupported store backend: {sc.backend!r}")  # a new backend plugs in here (DD)
    from rag_wright.store.arcadedb import ArcadeDBStore

    return ArcadeDBStore.from_config(sc.host, sc.port, sc.user, sc.password, database=corpus,
                                     protocol=sc.protocol, reset=reset)


def _build_embedder(config: EngineConfig) -> Optional[Any]:
    profile = config.embeddings.get("text", "bge-m3")
    if profile != "bge-m3":
        raise ValueError(f"unsupported embedding profile: {profile!r}")  # pluggable embedders: EP-API-4
    try:
        from rag_wright.capabilities.remote_encoders import query_embedder

        return query_embedder()
    except Exception:  # noqa: BLE001 - embedder backend unavailable -> None; the retrieval consumer surfaces it
        logger.warning("engine_embedder_unavailable", exc_info=True)
        return None
