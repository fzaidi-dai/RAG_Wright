"""EP-RT-7 (ADR-0117): the SINGLE binding of model-capability name -> implementation.

This is the ONE place the engine's model capabilities (`clause_function_classification`, `clause_property_classification`)
are bound to their fleets and dispatched. It lives at the `spans/` layer -- below BOTH the engine API invoker
(`api/invoke.py`) and the ingestion pipeline (`subgraphs/`) -- so every consumer routes through it by name. No
capability's fleet is constructed anywhere else: production goes THROUGH the capability layer, never around it
(see memory route-production-through-capability-layer). Subgraph capabilities stay bound in `api/invoke.py` because
they are workspace-bound (need the handle's store/models); model capabilities are store-independent, so they bind
here where both the API and the pipeline can reach them.

Usage/cost is captured by the caller's ambient `api.measure_usage()` scope (EP-API-5); this wraps each dispatch in a
trace span only."""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from rag_wright.pack_sdk import capability_impl
from rag_wright.api import traced_step

# --- cached fleets: loaded ONCE per process (heavy) ---

_DIM_REGISTRY: Any = None


def _dim_registry() -> Any:
    """The 29-dim property-classifier fleet (shared Laya group agents + SetFit + abstain heads)."""
    global _DIM_REGISTRY
    if _DIM_REGISTRY is None:
        from rag_wright.packs.contracts.spans.dim_classifier import load_dim_registry

        _DIM_REGISTRY = load_dim_registry()
    return _DIM_REGISTRY


_SETFIT_CLAUSE: Any = None


def _setfit_clause_classifier() -> Any:
    """The trained SetFit/LegalBERT clause-function soft-tagger."""
    global _SETFIT_CLAUSE
    if _SETFIT_CLAUSE is None:
        from rag_wright.packs.contracts.spans.clause_function_classifier import production_setfit_clause_classifier

        _SETFIT_CLAUSE = production_setfit_clause_classifier()
    return _SETFIT_CLAUSE


# --- the model-capability adapters: name -> (inputs -> output). Store-independent. ---

# The model-capability invoke factories: `(resources, inputs) -> result`, uniform with the subgraph factories
# (EP-CORE-2). Model capabilities are store-independent, so `resources` is ignored. These are the targets of the
# manifests' `impl_ref` ("rag_wright.packs.contracts.spans.model_capabilities:clause_function_classification", etc.).

def clause_function_classification(resources: Any, inputs: dict) -> Any:  # noqa: ARG001 - store-independent
    # `with_probabilities` (PS-R3, optional): each span as (its FunctionScores, {function: averaged probability}) for
    # the reference unit representative's vote; the default output (FunctionScores per span) is unchanged.
    clf = _setfit_clause_classifier()
    if inputs.get("with_probabilities"):
        return clf.classify_spans_with_probabilities(inputs["chunk_text"], inputs["span_texts"])
    return clf.classify_spans(inputs["chunk_text"], inputs["span_texts"])


def clause_property_classification(resources: Any, inputs: dict) -> Any:  # noqa: ARG001 - store-independent
    from rag_wright.packs.contracts.spans.property_extractor import HybridPropertyExtractor

    ext = HybridPropertyExtractor(_dim_registry(), runnable=object())  # classifier lane only; runnable unused
    return ext.classify_properties(inputs["text"], functions=tuple(inputs.get("functions", ())))


def dispatch_model(name: str, inputs: dict) -> Any:
    """Dispatch a model-capability by name, inside a trace span -- resolving the implementation via the manifest
    `impl_ref` (the SAME adapter-free path the engine API invoker uses, EP-CORE-2). This is the ONE production path
    the ingestion pipeline also routes through. Model factories are store-independent, so resources is `None`."""
    factory = capability_impl(name)  # resolves impl_ref; raises KeyError/NotImplementedError on an unknown/unwired name
    with traced_step(f"invoke:{name}"):
        return factory(None, inputs)


async def adispatch_model(name: str, inputs: dict, *, sem: asyncio.Semaphore | None = None) -> Any:
    """Async dispatch: run the (CPU-bound, in-process) model capability off the event loop; `sem` bounds in-flight
    work across concurrent callers (the ingestion segment stage passes one shared semaphore)."""
    if sem is None:
        return await asyncio.to_thread(dispatch_model, name, inputs)
    async with sem:
        return await asyncio.to_thread(dispatch_model, name, inputs)


# --- thin shims the ingestion pipeline wires its classify stages to (so production routes through the capability) ---

class CapabilityFunctionClassifier:
    """The ingestion segment stage's clause-function classifier, routed through the `clause_function_classification`
    capability. Same `(classify_spans / aclassify_spans(sem=))` shape the segment stage expects."""

    def classify_spans(self, chunk_text: str, span_texts: list[str]) -> Any:
        return dispatch_model("clause_function_classification",
                              {"chunk_text": chunk_text, "span_texts": span_texts})

    async def aclassify_spans(self, chunk_text: str, span_texts: list[str],
                              *, sem: asyncio.Semaphore | None = None) -> Any:
        return await adispatch_model("clause_function_classification",
                                     {"chunk_text": chunk_text, "span_texts": span_texts}, sem=sem)

    async def aclassify_spans_with_probabilities(self, chunk_text: str, span_texts: list[str],
                                                 *, sem: asyncio.Semaphore | None = None) -> Any:
        return await adispatch_model("clause_function_classification",
                                     {"chunk_text": chunk_text, "span_texts": span_texts,
                                      "with_probabilities": True}, sem=sem)


def capability_property_classifier_fn() -> Callable[[str, tuple], list[dict]]:
    """The classifier LANE for the ingestion extract stage, routed through the `clause_property_classification`
    capability. Returns `(text, functions) -> [{dimension, value, confidence}]`; the pipeline composes the residual
    LLM call + the semantic judge AROUND it (those are not part of this capability)."""
    def _classify(text: str, functions: tuple = ()) -> list[dict]:
        return dispatch_model("clause_property_classification", {"text": text, "functions": tuple(functions)})

    return _classify
