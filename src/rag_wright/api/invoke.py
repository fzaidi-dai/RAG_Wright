"""EP-API-2 (ADR-0117): the engine's capability invoker -- an ARD *client* over the metadata catalog.

Progressive loading, like an agent holding skill name+description and loading the full skill on demand:
  * a LIGHT index (`slug -> kind/description`) is built ONCE from the ARD manifest specs (no capability
    IMPLEMENTATION is imported) -- the "cards" layer, for discovery + knowing what exists;
  * the capability's in-process adapter (and its heavy deps) is imported + resolved LAZILY on first invoke
    (only that capability's module loads).
The ARD registry stays metadata (no callables); the `name -> adapter` binding is the CLIENT's, keyed to the ARD
catalog and drift-guarded. The per-kind invokers take an opaque `WorkspaceHandle` as resources and wrap each call
with in-band usage accounting + a trace span; subgraph hardening (retry/dead-letter) comes from the LangGraph
scaffold the subgraph is built on."""
from __future__ import annotations

from typing import Any, Callable

from rag_wright.api.workspace import WorkspaceHandle
from rag_wright.models.profiles import ModelRole
from rag_wright.models.tracing import traced_step
from rag_wright.models.usage import usage_scope

_INDEX: dict[str, Any] | None = None


def _index() -> dict[str, Any]:
    """The light capability index (`slug -> manifest spec`), built once from the ARD manifest specs. Importing the
    specs pulls NO capability implementation -- just the metadata (kind/description/representative_queries)."""
    global _INDEX
    if _INDEX is None:
        from rag_wright.capabilities.manifests import _SPECS

        _INDEX = {s.slug: s for s in _SPECS}
    return _INDEX


def capability_index() -> dict[str, dict]:
    """Public discovery index: `{slug: {kind, description}}` for every catalogued capability (the 'cards')."""
    return {slug: {"kind": s.kind, "description": s.description} for slug, s in _index().items()}


# --- in-process adapters: the client's name -> adapter binding. Each lazily imports its implementation, so the
# index + binding stay light and only the invoked capability's module loads. More capabilities are wired here (or,
# later, co-registered per module); every adapter name MUST be in the ARD catalog with a matching kind (drift guard).

async def _sub_typed_property_retrieval(h: WorkspaceHandle, inputs: dict) -> Any:
    from rag_wright.capabilities.dg_extraction import default_extraction_model
    from rag_wright.subgraphs.typed_property_retrieval import production_typed_property_retrieval

    graph = production_typed_property_retrieval(
        store=h._store, embedder=h._embedder,
        extract_model=default_extraction_model(model=h.model_id(ModelRole.STRUCTURED_REASONING)),
        k=inputs.get("k", 8), pool_k=inputs.get("pool_k", 30), documents=inputs.get("documents"))
    return await graph.ainvoke({"query": inputs["query"]})


def _model_clause_function_classification(h: WorkspaceHandle, inputs: dict) -> Any:  # noqa: ARG001 - local model
    from rag_wright.spans.clause_function_classifier import production_setfit_clause_classifier

    clf = production_setfit_clause_classifier()
    return clf.classify_spans(inputs["chunk_text"], inputs["span_texts"])


_SUBGRAPH_ADAPTERS: dict[str, Callable] = {"typed_property_retrieval": _sub_typed_property_retrieval}
_MODEL_ADAPTERS: dict[str, Callable] = {"clause_function_classification": _model_clause_function_classification}


def _resolve(name: str, kind: str, adapters: dict[str, Callable]) -> Callable:
    idx = _index()
    if name not in idx:
        raise KeyError(f"unknown capability {name!r} (not in the ARD catalog)")
    if idx[name].kind != kind:
        raise ValueError(f"capability {name!r} is kind {idx[name].kind!r}, not {kind!r}")
    adapter = adapters.get(name)
    if adapter is None:
        raise NotImplementedError(f"no in-process invocation adapter wired for {kind} {name!r}")
    return adapter


async def ainvoke_subgraph(name: str, inputs: dict, *, resources: WorkspaceHandle) -> Any:
    """Invoke a subgraph-kind capability by name over the workspace. Retry/dead-letter comes from the LangGraph
    scaffold the subgraph is built on; this wraps in-band usage accounting + a trace span."""
    adapter = _resolve(name, "subgraph", _SUBGRAPH_ADAPTERS)
    with traced_step(f"invoke:{name}"), usage_scope():
        return await adapter(resources, inputs)


def invoke_model(name: str, inputs: dict, *, resources: WorkspaceHandle) -> Any:
    """Invoke a model-kind capability by name over the workspace, with usage accounting + a trace span."""
    adapter = _resolve(name, "model", _MODEL_ADAPTERS)
    with traced_step(f"invoke:{name}"), usage_scope():
        return adapter(resources, inputs)


def _invocable_names() -> dict[str, str]:
    """{name: kind} for every capability with a wired in-process adapter -- used by the drift guard."""
    return {**{n: "subgraph" for n in _SUBGRAPH_ADAPTERS}, **{n: "model" for n in _MODEL_ADAPTERS}}
