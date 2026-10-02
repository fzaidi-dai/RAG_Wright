"""EP-API-2 (ADR-0117): the engine's capability invoker -- an ARD *client* over the metadata catalog.

Progressive loading, like an agent holding skill name+description and loading the full skill on demand:
  * a LIGHT index (`slug -> kind/description`) is built ONCE from the ARD manifest specs (no capability
    IMPLEMENTATION is imported) -- the "cards" layer, for discovery + knowing what exists;
  * the capability's in-process adapter (and its heavy deps) is imported + resolved LAZILY on first invoke
    (only that capability's module loads).
The ARD registry stays metadata (no callables); the `name -> adapter` binding is the CLIENT's, keyed to the ARD
catalog and drift-guarded. The per-kind invokers take an opaque `WorkspaceHandle` as resources and wrap each call
in a trace span; model usage/cost is captured by the CALLER's `api.measure_usage()` (ambient + additive, EP-API-5),
not by the invoker. Subgraph hardening (retry/dead-letter) comes from the LangGraph scaffold the subgraph is built on."""
from __future__ import annotations

from typing import Any, Callable

from rag_wright.api.workspace import WorkspaceHandle
from rag_wright.models.profiles import ModelRole
from rag_wright.models.tracing import traced_step

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


async def _sub_relational_qa(h: WorkspaceHandle, inputs: dict) -> Any:
    from rag_wright.capabilities.answer_generator import answer_model_for
    from rag_wright.subgraphs.relational_qa import production_relational_qa

    graph = production_relational_qa(store=h._store, answer_model=answer_model_for(h.model_id(ModelRole.GENERAL)))
    return await graph.ainvoke({"query": inputs["query"], "start_entity_id": inputs["start_entity_id"],
                                "max_hops": inputs.get("max_hops", 1)})


async def _sub_intra_document_qa(h: WorkspaceHandle, inputs: dict) -> Any:
    from rag_wright.capabilities.answer_generator import answer_model_for
    from rag_wright.subgraphs.intra_document_qa import production_intra_document_qa

    graph = production_intra_document_qa(store=h._store, answer_model=answer_model_for(h.model_id(ModelRole.GENERAL)),
                                        top_k=inputs.get("top_k", 12))
    return await graph.ainvoke({"contract_id": inputs["contract_id"], "question": inputs["question"]})


async def _sub_contract_ingestion(h: WorkspaceHandle, inputs: dict) -> Any:
    """Ingest ONE document (a `SourceDocument`, build via `api.source_document`) through the async per-document ingest
    graph. `inputs`: `document` (required) + `cache_dir` (the parse cache, required to be stable across calls)."""
    from rag_wright.capabilities.dg_extraction import default_extraction_model
    from rag_wright.ontology.registry import EntityRegistry
    from rag_wright.subgraphs.contract_ingestion_pipeline import aproduction_document_ingest

    # `registry` is the ENTITY-RESOLUTION registry (resolve_entities), NOT the dim-classifier fleet -- the pipeline
    # builds its own dim fleet internally (classifier_property_extractor). The generic default is an empty,
    # closed-world surface-form registry: an unknown entity resolves to None/unlinked, never a fabricated id
    # (ADR-0013/DD-3). A product with its own canonical registry (e.g. the SEC/CIK pack) passes that instead.
    # embedder=None -> the pipeline builds its own BGE-M3 SPAN embedder (encode_batch); the handle's query embedder
    # is for retrieval, not ingest. (Pluggable ingest embedder via the config profile comes with EP-API-4.)
    graph = aproduction_document_ingest(
        h._store, cache_dir=inputs["cache_dir"], registry=EntityRegistry(),
        extract_model=default_extraction_model(model=h.model_id(ModelRole.STRUCTURED_REASONING)))
    return await graph.ainvoke({"document": inputs["document"]})


_SUBGRAPH_ADAPTERS: dict[str, Callable] = {
    "typed_property_retrieval": _sub_typed_property_retrieval,
    "relational_qa": _sub_relational_qa,
    "intra_document_qa": _sub_intra_document_qa,
    "contract_ingestion_pipeline": _sub_contract_ingestion,
}


def _validate(name: str, kind: str) -> None:
    """Validate a capability name against the ARD catalog (name known, kind matches). Raises KeyError/ValueError."""
    idx = _index()
    if name not in idx:
        raise KeyError(f"unknown capability {name!r} (not in the ARD catalog)")
    if idx[name].kind != kind:
        raise ValueError(f"capability {name!r} is kind {idx[name].kind!r}, not {kind!r}")


async def ainvoke_subgraph(name: str, inputs: dict, *, resources: WorkspaceHandle) -> Any:
    """Invoke a subgraph-kind capability by name over the workspace, inside a trace span. Retry/dead-letter comes
    from the LangGraph scaffold the subgraph is built on. Model usage is captured by the CALLER's `measure_usage()`
    (ambient + additive, EP-API-5), so the invoker does not open its own scope."""
    _validate(name, "subgraph")
    adapter = _SUBGRAPH_ADAPTERS.get(name)
    if adapter is None:
        raise NotImplementedError(f"no in-process invocation adapter wired for subgraph {name!r}")
    with traced_step(f"invoke:{name}"):
        return await adapter(resources, inputs)


def invoke_model(name: str, inputs: dict, *, resources: WorkspaceHandle) -> Any:  # noqa: ARG001 - store-independent
    """Invoke a model-kind capability by name. Validated against the ARD catalog, then dispatched through the SINGLE
    model-capability binding (`spans.model_capabilities`) that the ingestion pipeline ALSO routes through -- so there
    is one production path, never a second hand-built fleet. `resources` is accepted for API uniformity but model
    capabilities are store-independent (local fleets). Usage is the caller's `measure_usage()` scope (EP-API-5)."""
    from rag_wright.spans.model_capabilities import dispatch_model

    _validate(name, "model")
    return dispatch_model(name, inputs)


def _invocable_names() -> dict[str, str]:
    """{name: kind} for every capability with a wired adapter -- used by the drift guard. Model-capability adapters
    live in `spans.model_capabilities` (the single binding); subgraph adapters are workspace-bound and stay here."""
    from rag_wright.spans.model_capabilities import model_adapter_names

    return {**{n: "subgraph" for n in _SUBGRAPH_ADAPTERS}, **{n: "model" for n in model_adapter_names()}}
