"""EP-API-1 (ADR-0117): `open_workspace` + the opaque `WorkspaceHandle`.

The product names a `corpus` (the logical KG = the backend database name; the product maps its own tenant -> db-name,
so tenancy stays product-owned) and gets back an opaque handle that resolves + caches the store (and lazily the
embedder) from the `EngineConfig`. The handle is what the engine's per-kind invokers (EP-API-2) take as resources.
The product never imports `ArcadeDBStore`/`query_embedder` and the handle exposes no public store accessor; a pack's
store extension is built over the workspace store with `pack_store(ws, cls)` (PS-7)."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional, TypeVar

from rag_wright.api.config import EngineConfig
from rag_wright.models.profiles import ModelRole, model_for, scoped_models

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# Cached per (backend, host, port, corpus) -- one resolved workspace per customer DB (absorbs the product's
# per-customer graph/store cache). Process-local; the embedder is lazy + per-handle. A different config for a cached
# corpus replaces the entry (PS-16), reusing the store and the embedder when their settings are unchanged.
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

    def _share_embedder(self, other: "WorkspaceHandle") -> None:
        """Reuse `other`'s query embedder (same embedding profile) instead of building a second one."""
        if other.__embedder_built:
            self.__embedder, self.__embedder_built = other.__embedder, True

    def model_id(self, role: ModelRole) -> str:
        """Resolve a model role to its id: the `EngineConfig.models` override wins, else the profile default."""
        key = role.value if isinstance(role, ModelRole) else str(role)
        return self._config.models.get(key) or model_for(role)


@contextmanager
def use_workspace_models(ws: Any) -> Iterator[None]:
    """Resolve every model role through `ws`'s `EngineConfig.models` inside the block (PS-14), ahead of the
    `RAG_MODEL_*` environment. The engine's own entry points that take a workspace (`aingest`, the invokers) do this
    for you; use it around engine calls that take no workspace, such as `parse_document_bytes` (OCR) or a
    `default_chunk_discoverer` run, when the workspace overrides those roles. Per call, never shared: concurrent
    blocks for different workspaces each see their own models."""
    config = getattr(ws, "_config", None)
    with scoped_models(getattr(config, "models", None)):
        yield


def open_workspace(config: EngineConfig, *, corpus: str, reset: bool = False) -> WorkspaceHandle:
    """Resolve (and cache) the workspace for `corpus` (the backend database name) from `config`. Ensures the schema:
    the neutral engine types, plus `config.pack`'s declared types when set. Raises `RuntimeError` on a database whose
    `Span` type still has the pre-ING-8d field names (migrate it with `scripts/migrate_span_fields.py`). Returns an
    opaque `WorkspaceHandle`. `reset=True` drops + recreates the database (test/clean-slate) and bypasses the cache.

    One handle per store and corpus is cached for the process. An equal `config` returns it; a different one (new
    models, options, credentials or pack) returns a new handle that replaces it from then on, so a configuration
    change takes effect without a restart (PS-16). The new handle reuses the store when the store settings and the
    pack are unchanged, and the query embedder when the embedding profile is unchanged; a call still holding the old
    handle finishes on the old config."""
    sc = config.store
    cache_key = (sc.backend, sc.host, sc.port, corpus)
    cached = None if reset else _WORKSPACES.get(cache_key)
    if cached is not None:
        if cached._config == config:
            return cached
        if (cached._config.store, cached._config.pack) == (config.store, config.pack):
            handle = WorkspaceHandle(cached._store, config, corpus)
            if cached._config.embeddings == config.embeddings:
                handle._share_embedder(cached)
            _WORKSPACES[cache_key] = handle
            return handle
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
                                     protocol=sc.protocol, reset=reset, pack_ttl=config.pack)


def _build_embedder(config: EngineConfig) -> Optional[Any]:
    """The workspace's QUERY embedder, selected by the `text` embedding profile (EP-API-4b; default bge-m3). An
    unknown profile raises; an unavailable backend degrades to None (the retrieval consumer surfaces it)."""
    from rag_wright.capabilities.embedding_profiles import build_query_embedder

    profile = config.embeddings.get("text", "bge-m3")
    try:
        return build_query_embedder(profile)
    except ValueError:
        raise  # an unknown profile is a config error, not a transient backend failure
    except Exception:  # noqa: BLE001 - embedder backend unavailable -> None; the retrieval consumer surfaces it
        logger.warning("engine_embedder_unavailable", exc_info=True)
        return None


def pack_store(ws: WorkspaceHandle, cls: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    """Build a pack's store extension (or any object that wraps the workspace store) over the workspace's store:
    `cls(<store>, *args, **kwargs)`, e.g. `pack_store(ws, MyPackStore)`. The store it receives implements the
    engine's `Store` protocol (`kg_read` / `kg_write` / `kg_edges` / `kg_count` / `kg_delete` / `kg_update` and the
    rest); the workspace keeps the store itself private, so this is the one way a product hands it to a pack."""
    return cls(ws._store, *args, **kwargs)
