"""LG-2: `graph_extraction` as a granular, PARALLEL LangGraph subgraph.

The extractor stack (FR-C.6), behind the `Extractor` seam, expressed as a fan-out/fan-in graph rather than a
sequential `run_extractors` loop. The default stack is now the single GP-1B docling-graph party extractor
(ADR-0035; the retired T23-27 hybrid was spaCy NER + contract-LLM + escalation), but the graph is generic over
any list of extractors:

        START
       /  |  \\           (one node per extractor, run in parallel)
     ex0 ex1 ...
       \\  |  /
        merge            (ExtractionResult.merge over all results)
          |
         END

- **parallel fan-out**: the extractors are independent (each reads the same chunk text), so they run in one
  superstep; each appends its `ExtractionResult` to a reducer-accumulated `results` list (no write conflict).
- **graceful degradation**: an extractor that fails after retries contributes an EMPTY `ExtractionResult`
  (via runtime.execution_info.node_attempt) rather than dropping the whole chunk -- partial facts beat none.
- **observability**: the GP-1B extractor calls docling-graph (a raw-SDK call that bypasses LangChain), so it
  is INVISIBLE to tracing unless wrapped -- each extractor node is wrapped in `raw_llm_span` per the
  GraphWright observability contract.
- **merge**: `ExtractionResult.merge` combines mentions + clause/relationship facts, deterministically.

`extractors` is injected (defaulting to `default_extractors()`), so the graph is hermetically testable with
stub extractors -- no live docling-graph / no network.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from rag_wright.capabilities.graph_extraction import Extractor, default_extractors
from rag_wright.contracts.extraction import ExtractionResult
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, raw_llm_span
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
        # GP-1B docling-graph is a raw-SDK call (ADR-0035); the model id is carried by the extractor.
        with raw_llm_span(f"graph_extraction.{name}", model=getattr(extractor, "model_id", "docling-graph")):
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
        display_name="Graph extraction (GP-1B docling-graph party/relational)",
    )
