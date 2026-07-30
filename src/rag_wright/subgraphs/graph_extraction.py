"""LG-2: `graph_extraction` as a granular, PARALLEL LangGraph subgraph.

The hybrid extractor stack (FR-C.6) -- spaCy NER + contract-LLM + LLM-escalation, all behind the `Extractor`
seam -- expressed as a fan-out/fan-in graph rather than a sequential `run_extractors` loop:

        START
       /  |  \\           (one node per extractor, run in parallel)
    ner contract escalate
       \\  |  /
        merge            (ExtractionResult.merge over all results)
          |
         END

- **parallel fan-out**: the extractors are independent (each reads the same chunk text), so they run in one
  superstep; each appends its `ExtractionResult` to a reducer-accumulated `results` list (no write conflict).
- **graceful degradation**: an LLM extractor that fails after retries contributes an EMPTY `ExtractionResult`
  (via runtime.execution_info.node_attempt) rather than dropping the whole chunk -- partial facts beat none,
  and the deterministic NER path still lands.
- **observability**: the two LLM extractors call the model through the model-profile seam
  (`build_structured` -> ChatOpenAI), so they are auto-captured -- there is NO raw-SDK gap here (unlike
  docling-graph). Each extractor node is wrapped in a `business_span` for per-extractor visibility.
- **merge**: `ExtractionResult.merge` combines mentions + clause/relationship facts, deterministically.

`extractors` is injected (defaulting to `default_extractors()`), so the graph is hermetically testable with
stub extractors -- no live LLM or spaCy model.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from rag_wright.capabilities.graph_extraction import Extractor, default_extractors
from rag_wright.contracts.extraction import ExtractionResult
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, business_span
from rag_wright.subgraphs.typed_clause_extraction import TransientExtraction  # retryable-blip signal


class GraphExtractionState(TypedDict, total=False):
    chunk_id: ChunkId
    text: str
    results: Annotated[list, operator.add]  # each extractor node appends its ExtractionResult (reducer)
    result: ExtractionResult  # the merged output


def _extractor_node(extractor: Extractor, max_attempts: int):
    name = getattr(extractor, "name", extractor.__class__.__name__)

    def node(state: GraphExtractionState, runtime: Runtime) -> GraphExtractionState:
        attempt = runtime.execution_info.node_attempt
        with business_span(f"graph_extraction.{name}"):
            try:
                result = extractor.extract(state["chunk_id"], state["text"])
            except Exception as exc:  # noqa: BLE001 - retry (as a transient), or degrade to empty on exhaustion
                if attempt >= max_attempts:
                    return {"results": [ExtractionResult(chunk_id=state["chunk_id"])]}  # graceful: no facts
                raise TransientExtraction(str(exc)) from exc  # normalize so the RetryPolicy retries it
        return {"results": [result]}

    return node


def build_graph_extraction(
    extractors: Optional[list[Extractor]] = None,
    *,
    retry_policy: Any = DEFAULT_RETRY,
):
    """Compile the `graph_extraction` subgraph (per chunk). `extractors` is injected (defaulting to the live
    hybrid stack). `retry_policy` is each extractor node's policy (overridable for fast tests)."""

    extractors = extractors if extractors is not None else default_extractors()
    max_attempts = int(getattr(retry_policy, "max_attempts", 3))

    def merge(state: GraphExtractionState) -> GraphExtractionState:
        return {"result": ExtractionResult.merge(state["chunk_id"], state.get("results", []))}

    g = StateGraph(GraphExtractionState)
    g.add_node("merge", merge)
    for i, extractor in enumerate(extractors):
        name = f"extract_{i}_{getattr(extractor, 'name', 'x')}"
        g.add_node(name, _extractor_node(extractor, max_attempts), retry_policy=retry_policy)
        g.add_edge(START, name)   # fan-out: all extractors run in parallel
        g.add_edge(name, "merge")  # fan-in: merge runs after all complete
    g.add_edge("merge", END)
    return g.compile()


def register_graph_extraction_subgraph(registry) -> None:
    """LG-2: register `graph_extraction` (subgraph). The manifest/slug already exist (reclassified in
    CAP-REG-1); this binds the LangGraph runnable's contract (ExtractionResult)."""
    registry.register(
        "graph_extraction",
        contract=ExtractionResult,
        kind="subgraph",
        display_name="Graph extraction (hybrid: spaCy NER + contract + LLM escalation)",
    )
