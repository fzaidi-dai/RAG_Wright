"""The engine API layer (ADR-0117): the stable, domain-agnostic surface a product builds on.

EP-API-1 ships the typed config + the opaque workspace handle. Future increments add the per-kind invokers
(EP-API-2), generic `kg_read`/`kg_write` + id/format accessors (EP-API-3), and the options catalog + pluggable
embedders (EP-API-4). The product imports from here; it never reaches the store/embedder/model implementations."""
from __future__ import annotations

from rag_wright.api.config import EngineConfig, StoreConfig
from rag_wright.api.invoke import ainvoke_subgraph, capability_index, invoke_model
from rag_wright.api.workspace import WorkspaceHandle, open_workspace

__all__ = [
    "EngineConfig", "StoreConfig", "WorkspaceHandle", "open_workspace",
    "ainvoke_subgraph", "invoke_model", "capability_index",
]
