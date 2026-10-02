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

from rag_wright.models.tracing import traced_step

# --- cached fleets: loaded ONCE per process (heavy) ---

_DIM_REGISTRY: Any = None


def _dim_registry() -> Any:
    """The 29-dim property-classifier fleet (shared Laya group agents + SetFit + abstain heads)."""
    global _DIM_REGISTRY
    if _DIM_REGISTRY is None:
        from rag_wright.spans.dim_classifier import load_dim_registry

        _DIM_REGISTRY = load_dim_registry()
    return _DIM_REGISTRY


_SETFIT_CLAUSE: Any = None


def _setfit_clause_classifier() -> Any:
    """The trained SetFit/LegalBERT clause-function soft-tagger."""
    global _SETFIT_CLAUSE
    if _SETFIT_CLAUSE is None:
        from rag_wright.spans.clause_function_classifier import production_setfit_clause_classifier

        _SETFIT_CLAUSE = production_setfit_clause_classifier()
    return _SETFIT_CLAUSE


# --- the model-capability adapters: name -> (inputs -> output). Store-independent. ---

def _model_clause_function_classification(inputs: dict) -> Any:
    return _setfit_clause_classifier().classify_spans(inputs["chunk_text"], inputs["span_texts"])


def _model_clause_property_classification(inputs: dict) -> Any:
    from rag_wright.spans.property_extractor import HybridPropertyExtractor

    ext = HybridPropertyExtractor(_dim_registry(), runnable=object())  # classifier lane only; runnable unused
    return ext.classify_properties(inputs["text"], functions=tuple(inputs.get("functions", ())))


_MODEL_ADAPTERS: dict[str, Callable[[dict], Any]] = {
    "clause_function_classification": _model_clause_function_classification,
    "clause_property_classification": _model_clause_property_classification,
}


def model_adapter_names() -> list[str]:
    """The model-capability slugs with a wired adapter (for the invoker's drift guard)."""
    return list(_MODEL_ADAPTERS)


def dispatch_model(name: str, inputs: dict) -> Any:
    """Dispatch a model-capability by name through the single binding, inside a trace span. Raises
    `NotImplementedError` if no adapter is wired (name validation vs the ARD catalog is the API invoker's job)."""
    adapter = _MODEL_ADAPTERS.get(name)
    if adapter is None:
        raise NotImplementedError(f"no in-process model-capability adapter wired for {name!r}")
    with traced_step(f"invoke:{name}"):
        return adapter(inputs)


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


def capability_property_classifier_fn() -> Callable[[str, tuple], list[dict]]:
    """The classifier LANE for the ingestion extract stage, routed through the `clause_property_classification`
    capability. Returns `(text, functions) -> [{dimension, value, confidence}]`; the pipeline composes the residual
    LLM call + the semantic judge AROUND it (those are not part of this capability)."""
    def _classify(text: str, functions: tuple = ()) -> list[dict]:
        return dispatch_model("clause_property_classification", {"text": text, "functions": tuple(functions)})

    return _classify
