"""EP-API-4b (ADR-0117): embedding profiles -- the single place an embedding profile alias maps to its embedders.

A product selects a profile via `EngineConfig.embeddings["text"]`; the engine resolves it to the right QUERY-side
encoder (retrieval) and INGEST-side SPAN encoder (the index's `encode_batch`). `bge-m3` is the default BGE-M3
family (the query encoder + the batch SPAN encoder). Adding a profile is one entry in each registry here, invisible
to the product -- so BGE-M3 is no longer hardcoded in the workspace / the ingestion pipeline. Builders import their
heavy impls LAZILY, so importing this module (and passing a profile string around) stays cheap."""
from __future__ import annotations

from typing import Any, Callable


def _bge_m3_query() -> Any:
    from rag_wright.capabilities.remote_encoders import query_embedder

    return query_embedder()


def _bge_m3_ingest() -> Any:
    from rag_wright.capabilities.embedding import BGEM3Embedder

    return BGEM3Embedder()


# profile alias -> builder. A new embedder family = one entry in each (same alias).
_QUERY_BUILDERS: dict[str, Callable[[], Any]] = {"bge-m3": _bge_m3_query}
_INGEST_BUILDERS: dict[str, Callable[[], Any]] = {"bge-m3": _bge_m3_ingest}


def build_query_embedder(profile: str = "bge-m3") -> Any:
    """The retrieval-side embedder for `profile` (default BGE-M3). Raises `ValueError` for an unknown profile."""
    builder = _QUERY_BUILDERS.get(profile)
    if builder is None:
        raise ValueError(f"unsupported embedding profile {profile!r} (known: {sorted(_QUERY_BUILDERS)})")
    return builder()


def build_ingest_embedder(profile: str = "bge-m3") -> Any:
    """The ingest-side SPAN embedder for `profile` (default BGE-M3; `encode_batch`). Raises on an unknown profile."""
    builder = _INGEST_BUILDERS.get(profile)
    if builder is None:
        raise ValueError(f"unsupported embedding profile {profile!r} (known: {sorted(_INGEST_BUILDERS)})")
    return builder()
