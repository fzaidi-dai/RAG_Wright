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

from typing import Any

from rag_wright.capabilities.invoke import capability_impl
from rag_wright.api.workspace import WorkspaceHandle
from rag_wright.models.tracing import traced_step

_INDEX: dict[str, Any] | None = None


def _index() -> dict[str, Any]:
    """The light capability index (`slug -> manifest spec`), built once from the ARD manifest specs. Importing the
    specs pulls NO capability implementation -- just the metadata (kind/description/impl_ref/representative_queries)."""
    global _INDEX
    if _INDEX is None:
        from rag_wright.capabilities.manifests import _SPECS

        _INDEX = {s.slug: s for s in _SPECS}
    return _INDEX


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
    with traced_step(f"invoke:{name}"):
        return await factory(resources, inputs)


def invoke_model(name: str, inputs: dict, *, resources: WorkspaceHandle) -> Any:
    """Invoke a model-kind capability by name. Validated against the ARD catalog, then resolved via `impl_ref` and
    dispatched -- the SAME path the ingestion pipeline routes through (one production path, no second hand-built
    fleet). `resources` is accepted for API uniformity but model capabilities are store-independent (local fleets).
    Usage is the caller's `measure_usage()` scope (EP-API-5)."""
    _validate(name, "model")
    factory = capability_impl(name)
    with traced_step(f"invoke:{name}"):
        return factory(resources, inputs)


def _invocable_names() -> dict[str, str]:
    """{name: kind} for every capability that declares an `impl_ref` -- the drift guard asserts each resolves to a
    callable of the declared kind (replaces the old central-adapter-dict binding)."""
    return {slug: s.kind for slug, s in _index().items() if getattr(s, "impl_ref", None)}
