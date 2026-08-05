"""CC-2 (compliance §13), SKILL-SPLIT: the `requirement_extraction` SUBGRAPH.

`requirement_extraction` is a SUBGRAPH, not a single-shot skill, because its docling-graph `auto/dense`
extraction is MULTI-LLM-call (skeleton-then-fill) and the extract -> adapt chaining is a deterministic workflow
(the rubric's rationale for a subgraph). A hardened LangGraph on `scaffold.py`:

    START --> extract [RetryPolicy]   (the extraction ACT: docling-graph fills the skill's template.py schema)
                |                       transient failure -> retry, exhaustion -> dead_letter
                v
              adapt --> END            (the requirement_adaptation FUNCTION: raw section -> validated Requirement[])

`extract`/`adapt` are DI'd for hermetic tests. `run_requirement_extraction` invokes the compiled graph for a
single section (used by the `compliance_ingestion` corpus driver, CC-5).
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from rag_wright.capabilities.requirement_extraction import extract_regulation_section, to_requirements
from rag_wright.contracts.compliance import Requirement
from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, business_span, dead_letter
from rag_wright.subgraphs.typed_clause_extraction import TransientExtraction

# extract_fn: section text -> raw ExtractedRegulationSection | None; adapt_fn: (extracted, source, section) -> Requirement[]
ExtractSectionFn = Callable[[str], Any]
AdaptFn = Callable[[Any, str, str], list]


class ReqExtractState(TypedDict, total=False):
    text: str
    source: str
    section: str
    extracted: Any
    requirements: list
    dead_letter: Optional[dict]


def build_requirement_extraction(
    extract_fn: ExtractSectionFn, adapt_fn: AdaptFn, *, retry_policy: Any = DEFAULT_RETRY
):
    """Compile the requirement-extraction subgraph: extract[retry] -> adapt -> END. Both nodes DI'd for tests.
    A transient extraction failure retries, then dead-letters the section (never raised) -> an empty result."""
    max_attempts = int(getattr(retry_policy, "max_attempts", 3))

    def extract(state: ReqExtractState, runtime: Runtime) -> ReqExtractState:
        section = state.get("section", "")
        attempt = runtime.execution_info.node_attempt
        with business_span("requirement_extraction.extract", section=section):
            try:
                return {"extracted": extract_fn(state["text"])}
            except Exception as exc:  # noqa: BLE001 - transient -> retry, or dead-letter on exhaustion
                if attempt >= max_attempts:
                    return {"dead_letter": dead_letter(
                        "extract_failed", section=section, stage="extract", error=str(exc))}
                raise TransientExtraction(str(exc)) from exc

    def adapt(state: ReqExtractState) -> ReqExtractState:
        extracted = state.get("extracted")
        if state.get("dead_letter") or extracted is None:
            return {"requirements": []}
        with business_span("requirement_extraction.adapt"):
            return {"requirements": adapt_fn(extracted, state["source"], state["section"])}

    g = StateGraph(ReqExtractState)
    g.add_node("extract", extract, retry_policy=retry_policy)
    g.add_node("adapt", adapt)
    g.add_edge(START, "extract")
    g.add_edge("extract", "adapt")  # adapt handles the dead-letter/None case (-> [])
    g.add_edge("adapt", END)
    return g.compile()


def production_requirement_extraction(
    *, model: Any, extraction_contract: str = "auto", extract_override: Optional[ExtractSectionFn] = None
):
    """Wire the real capabilities: extract = the docling-graph extraction ACT (skills/requirement_extraction/),
    adapt = the `to_requirements` FUNCTION. `extract_override` injects a stub for hermetic driver tests."""
    def _extract(text: str) -> Any:
        return extract_regulation_section(text, model=model, extraction_contract=extraction_contract)

    return build_requirement_extraction(
        extract_override or _extract,
        lambda extracted, source, section: to_requirements(extracted, source=source, section=section),
    )


def run_requirement_extraction(
    text: str, *, model: Any, source: str, section: str, extract_override: Optional[ExtractSectionFn] = None
) -> list[Requirement]:
    """Invoke the requirement-extraction subgraph for one § section -> its `Requirement[]` ([] on dead-letter)."""
    graph = production_requirement_extraction(model=model, extract_override=extract_override)
    return graph.invoke({"text": text, "source": source, "section": section}).get("requirements", [])


def register_requirement_extraction(registry) -> None:
    """Register `requirement_extraction` as a SUBGRAPH (CC-2): the extract -> adapt workflow over one section.
    Contract = `Requirement`."""
    registry.register(
        "requirement_extraction",
        contract=Requirement,
        kind="subgraph",
        display_name="Requirement extraction (regulatory section -> deontic rules; subgraph)",
    )
