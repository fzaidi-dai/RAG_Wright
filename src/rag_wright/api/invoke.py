"""EP-API-2 / EP-CORE-2 (ADR-0117, ADR-0118): the engine's capability invoker -- an adapter-free ARD *client*.

Progressive loading, like an agent holding skill name+metadata and loading the full thing on demand:
  * a LIGHT index (`slug -> manifest spec`) is built ONCE from the ARD manifest specs (no capability IMPLEMENTATION
    is imported) -- the "cards" layer, for discovery + knowing what exists;
  * on invoke, the capability's `impl_ref` (a "module:attr" vendor-extension pointer on its manifest) is imported
    LAZILY via `capability_impl` and called -- so there is NO central engine-owned adapter dict. A developer who
    registers a capability with an `impl_ref` makes it invocable with zero engine edits (ADR-0118).
The ARD registry stays metadata (ADR-0003: a string pointer, never a callable). Each per-kind invoker takes an
opaque `WorkspaceHandle` as resources and wraps the call in a trace span; model usage/cost is captured by the
CALLER's `api.measure_usage()` (EP-API-5). Subgraph hardening (retry/dead-letter) comes from the LangGraph scaffold."""
from __future__ import annotations

import asyncio
from typing import Any

from rag_wright.capabilities.invoke import capability_impl
from rag_wright.api.workspace import WorkspaceHandle, use_workspace_models
from rag_wright.models.tracing import traced_step

def _index() -> dict[str, Any]:
    """The light capability index (`slug -> manifest spec`): the LIVE runtime ARD catalog the developer populates
    (EP-CORE-3). Read fresh each call so a just-registered capability is seen; carries only metadata (kind/
    description/impl_ref/representative_queries), never an implementation."""
    from rag_wright.capabilities.manifests import MANIFEST_SPECS

    return MANIFEST_SPECS


def capability_index() -> dict[str, dict]:
    """Public discovery index: `{slug: {kind, description}}` for every catalogued capability (the 'cards')."""
    return {slug: {"kind": s.kind, "description": s.description} for slug, s in _index().items()}


def _validate(name: str, kind: str) -> None:
    """Validate a capability name against the ARD catalog (name known, kind matches). Raises KeyError/ValueError."""
    idx = _index()
    if name not in idx:
        raise KeyError(f"unknown capability {name!r} (not in the ARD catalog)")
    if idx[name].kind != kind:
        raise ValueError(f"capability {name!r} is kind {idx[name].kind!r}, not {kind!r}")


async def ainvoke_subgraph(name: str, inputs: dict, *, resources: WorkspaceHandle) -> Any:
    """Invoke a subgraph-kind capability by name over the workspace, inside a trace span. The implementation is
    resolved lazily from the manifest `impl_ref` (no central adapter dict). Retry/dead-letter comes from the
    LangGraph scaffold the subgraph is built on; usage is captured by the caller's `measure_usage()` (EP-API-5)."""
    _validate(name, "subgraph")
    factory = capability_impl(name)  # resolves impl_ref -> the co-located `ainvoke(resources, inputs)`
    with use_workspace_models(resources), traced_step(f"invoke:{name}"):  # PS-14: the workspace's models
        return await factory(resources, inputs)


def invoke_model(name: str, inputs: dict, *, resources: WorkspaceHandle) -> Any:
    """Invoke a model-kind capability by name (SYNCHRONOUSLY). Validated against the ARD catalog, then resolved via
    `impl_ref` and dispatched -- the SAME path the ingestion pipeline routes through (one production path, no second
    hand-built fleet). `resources` is accepted for API uniformity but model capabilities are store-independent.
    Usage is the caller's `measure_usage()` scope (EP-API-5). A model impl may be async (I/O-bound, e.g. an
    LLM-backed cap) -- those cannot be invoked here; call `ainvoke_model` instead (we refuse rather than silently
    return an un-awaited coroutine)."""
    _validate(name, "model")
    factory = capability_impl(name)
    if asyncio.iscoroutinefunction(factory):
        raise TypeError(
            f"capability {name!r} has an async impl; call ainvoke_model() instead of invoke_model()")
    with use_workspace_models(resources), traced_step(f"invoke:{name}"):
        return factory(resources, inputs)


async def ainvoke_model(name: str, inputs: dict, *, resources: WorkspaceHandle,
                        sem: asyncio.Semaphore | None = None) -> Any:
    """Invoke a model-kind capability by name, ASYNCHRONOUSLY -- the async surface for model caps (the subgraph
    legs already have `ainvoke_subgraph`). A model impl is one of two shapes, and this routes each honestly:
      * SYNC (CPU-bound local inference -- a classifier/XGBoost fleet): run OFF the event loop in a worker thread
        (`asyncio.to_thread`), so a big batch never blocks the loop;
      * ASYNC (I/O-bound -- an LLM-backed cap calling OpenRouter or a local vLLM client): AWAITED directly, so the
        I/O concurrency is real (not a thread wrapping a blocking call).
    `sem` (an `asyncio.Semaphore`) bounds total in-flight work when a caller fans out a batch -- the same
    backpressure the ingestion pipeline applies. Usage is the caller's `measure_usage()`
    scope (EP-API-5)."""
    _validate(name, "model")
    factory = capability_impl(name)

    async def _run() -> Any:
        with use_workspace_models(resources), traced_step(f"invoke:{name}"):
            if asyncio.iscoroutinefunction(factory):
                return await factory(resources, inputs)
            return await asyncio.to_thread(factory, resources, inputs)

    if sem is None:
        return await _run()
    async with sem:
        return await _run()


def _invocable_names() -> dict[str, str]:
    """{name: kind} for every capability that declares an `impl_ref` -- the drift guard asserts each resolves to a
    callable of the declared kind (replaces the old central-adapter-dict binding)."""
    return {slug: s.kind for slug, s in _index().items() if getattr(s, "impl_ref", None)}
