"""T28: RLM synthesis (FR-Q.5) — apply the recursive RLM method to the retrieved candidate chunks.

The rebuild (ADR-0015/0016) makes synthesis genuinely recursive dynamic sub-agents: a `SliceExtractor`
seam decomposes the candidate set through the T15 machinery (a fresh `rlm_decomposer` per over-large
group, an `rlm_slice_worker` extracting query-relevant facts per leaf) — the DESCENT — and the kept
Python `_reduce` fan-in combines the extracts — the ASCENT. Unlike chunking, recursion IS gated here
(ADR-0019): the fail-if-absent recursion discipline applies. Hermetic tests inject stubs and drive the
real machinery with scripted fake models; the live extract + synthesize is opt-in `-m model`.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.capabilities.rlm_synthesis import (
    RLM_SLICE_WORKER,
    SeamSliceExtractor,
    SliceOutput,
    SynthesisChunk,
    SynthesisResult,
    _reduce,
    register_rlm_synthesis,
    rlm_synthesize,
)
from rag_wright.skills.rlm.agent import RLM_WORKFLOW_JS


# --- stubs -----------------------------------------------------------------------------------------


class _StubExtractor:
    """A SliceExtractor that returns fixed per-slice extracts (stands in for the RLM machinery)."""

    def __init__(self, outputs: list[tuple[str, str]]) -> None:
        self._outputs = outputs
        self.calls = 0

    def extract(self, query: str, chunks: list[SynthesisChunk]) -> list[SliceOutput]:
        self.calls += 1
        return [SliceOutput(chunk_id=cid, extract=ex) for cid, ex in self._outputs]


class _StubSynthesizer:
    """A Synthesizer whose combine concatenates notes and whose extract covers one slice (counts calls)."""

    def __init__(self) -> None:
        self.combine_calls = 0
        self.extract_calls = 0

    def extract(self, query: str, text: str) -> str:
        self.extract_calls += 1
        return f"extracted({text})"

    def combine(self, query: str, extracts: list[str]) -> str:
        self.combine_calls += 1
        return " | ".join(extracts)


class FakeChat(BaseChatModel):
    responder: Any = None

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self.responder(messages))])

    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def _llm_type(self) -> str:
        return "fake-chat"


def _last_human(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


# --- the whole synthesis: descent (extractor) then ascent (_reduce) -------------------------------


def test_rlm_synthesize_extracts_then_reduces_and_cites_chunks():
    chunks = [SynthesisChunk(chunk_id=f"d:{i}:h", text=f"passage {i}") for i in range(3)]
    extractor = _StubExtractor([(f"d:{i}:h", f"fact {i}") for i in range(3)])
    synthesizer = _StubSynthesizer()

    result = rlm_synthesize("what are the facts?", chunks, extractor=extractor, synthesizer=synthesizer)

    assert isinstance(result, SynthesisResult)
    assert result.query == "what are the facts?"
    assert [o.extract for o in result.slice_outputs] == ["fact 0", "fact 1", "fact 2"]  # descent
    assert result.synthesis == "fact 0 | fact 1 | fact 2"  # ascent: _reduce combined the extracts
    assert result.chunk_ids == ["d:0:h", "d:1:h", "d:2:h"]  # no claim without a citation
    assert extractor.calls == 1 and synthesizer.combine_calls >= 1


def test_rlm_synthesize_on_empty_candidates_returns_empty():
    result = rlm_synthesize("q", [], extractor=_StubExtractor([]), synthesizer=_StubSynthesizer())
    assert result.slice_outputs == [] and result.synthesis == "" and result.chunk_ids == []


def test_coverage_guarantee_covers_a_candidate_the_descent_silently_missed():
    # T36 finding: out of context the recursive descent can miss a deep leaf -- here it returns extracts
    # for d:0 and d:2 but SILENTLY DROPS d:1. The code holds the whole candidate set, so it guarantees d:1
    # is extracted anyway (via synthesizer.extract), not dropped, and orders outputs to the candidates.
    chunks = [SynthesisChunk(chunk_id=f"d:{i}:h", text=f"passage {i}") for i in range(3)]
    extractor = _StubExtractor([("d:0:h", "fact 0"), ("d:2:h", "fact 2")])  # d:1 missed
    synthesizer = _StubSynthesizer()

    result = rlm_synthesize("q", chunks, extractor=extractor, synthesizer=synthesizer)

    assert [o.chunk_id for o in result.slice_outputs] == ["d:0:h", "d:1:h", "d:2:h"]  # every candidate covered
    assert result.slice_outputs[1].extract == "extracted(passage 1)"  # the missed leaf covered by the code
    assert synthesizer.extract_calls == 1  # ONLY the missed chunk was repaired (bounded, not a re-run)
    assert result.chunk_ids == ["d:0:h", "d:1:h", "d:2:h"]  # no claim without a citation, all present


# --- the kept _reduce fan-in (ascent, ADR-0016) --------------------------------------------------


def test_reduce_fans_in_recursively_over_groups():
    # 9 extracts with fanout 3: reduce combines in groups (3 groups -> 3 partials -> 1), recursing on the
    # way up. A model is never called over the whole set; the fan-in is the kept synthesis combine step.
    synthesizer = _StubSynthesizer()
    extracts = [f"e{i}" for i in range(9)]

    result = asyncio.run(_reduce("q", extracts, synthesizer, asyncio.Semaphore(4), fanout=3))

    assert "e0" in result and "e8" in result  # everything folded in
    assert synthesizer.combine_calls == 4  # 3 group combines + 1 over the partials (recursive ascent)


# --- the descent machinery: recursion is GATED for synthesis (ADR-0016/0019) ----------------------

_TOKEN = re.compile(r"C0(?:\.\d+)*")


def _deepest(text: str) -> str:
    tokens = _TOKEN.findall(text)
    return max(tokens, key=len) if tokens else "C0"


def _probe_decomposer(messages: list[BaseMessage]) -> AIMessage:
    """Reveals an opaque depth-2 candidate tree; the leaf ids only appear via recursion on this output."""
    sid = _deepest(_last_human(messages))
    if sid.count(".") >= 2:
        return AIMessage(content=json.dumps({"leaf": True}))
    return AIMessage(content=json.dumps({"leaf": False, "parts": [f"{sid}.0", f"{sid}.1"]}))


def _workflow_orchestrator():
    """An orchestrator that writes the recursive decompose() workflow into eval (code-driven fan-out).
    The working set arrives via `tools.workingSet()` (T36), not embedded in the code."""

    def respond(messages: list[BaseMessage]) -> AIMessage:
        if any(isinstance(m, ToolMessage) and m.name == "eval" for m in messages):
            return AIMessage(content="done")
        return AIMessage(content="", tool_calls=[{"name": "eval", "args": {"code": RLM_WORKFLOW_JS}, "id": "eval_1"}])

    return respond


def _stream_events(extractor: SeamSliceExtractor, chunks) -> list[dict]:
    from langchain_quickjs import CodeInterpreterMiddleware

    events: list[dict] = []
    # bind the working set as a PTC (T36), the same way extract() does, so the workflow reads it
    interpreter = CodeInterpreterMiddleware(subagents=True, ptc=[extractor._working_set_tool(chunks)])
    agent = extractor._build_agent(chunks, interpreter=interpreter)
    for mode, data in agent.stream(
        {"messages": [HumanMessage(content="run the workflow")]},
        stream_mode=["custom", "values"],
        config={"recursion_limit": 60},
    ):
        if mode == "custom" and isinstance(data, dict) and data.get("type") == "subagent":
            events.append(data)
    return events


def test_synthesis_descent_recurses_past_depth_one():
    # The candidate set is an opaque handle C0 whose sub-slices only the decomposer reveals; reaching the
    # leaves forces the interpreter to re-enter decompose(). A flat one-level split cannot reach them.
    extractor = SeamSliceExtractor(
        model=FakeChat(responder=_workflow_orchestrator()),
        decomposer_model=FakeChat(responder=_probe_decomposer),
        worker_model=FakeChat(responder=lambda m: AIMessage(content=f"extracted {_deepest(_last_human(m))}")),
        working_set="C0",  # delivered via tools.workingSet() (opaque handle; only the decomposer reveals leaves)
    )
    events = _stream_events(extractor, [SynthesisChunk(chunk_id="C0", text="opaque")])

    starts = [e for e in events if e.get("phase") == "start"]
    assert RLM_SLICE_WORKER in {e.get("subagent_type") for e in starts}  # leaves extracted by workers
    depths = {int(m.group(1)) for e in starts if (m := re.search(r"depth (\d+)", str(e.get("description", ""))))}
    assert len(depths) >= 2 and max(depths) >= 2, f"decomposer fired only at depths {depths} (no recursion)"
    assert all(e.get("eval_id") for e in starts)  # code-driven fan-out, not sequential (fail-if-sequential)


def test_a_slice_worker_can_use_a_tool_during_extraction():
    called: list[str] = []

    @tool
    def cite(chunk_id: str) -> str:
        """Record a citation for an extracted fact."""
        called.append(chunk_id)
        return "cited"

    def worker(messages):
        if any(isinstance(m, ToolMessage) and m.name == "cite" for m in messages):
            return AIMessage(content="extracted with citation")
        return AIMessage(content="", tool_calls=[{"name": "cite", "args": {"chunk_id": "c1"}, "id": "t1"}])

    def orchestrator(messages):
        if any(isinstance(m, ToolMessage) and m.name == "eval" for m in messages):
            return AIMessage(content="done")
        code = 'await task({description: "extract leaf: c1", subagentType: "rlm_slice_worker"});'
        return AIMessage(content="", tool_calls=[{"name": "eval", "args": {"code": code}, "id": "e1"}])

    extractor = SeamSliceExtractor(
        model=FakeChat(responder=orchestrator),
        worker_model=FakeChat(responder=worker),
        worker_tools=[cite],
    )
    _stream_events(extractor, [SynthesisChunk(chunk_id="c1", text="passage")])
    assert called == ["c1"]  # a slice worker used a tool mid-extraction (per-slice tool use)


# --- registration --------------------------------------------------------------------------------


def test_rlm_synthesis_registers_as_an_agent_skill():
    reg = CapabilityRegistry()
    register_rlm_synthesis(reg)
    registration = reg.get("rlm_synthesis")
    assert registration.name == "rlm_synthesis"
    assert registration.kind == "agent_skill"  # applies the RLM method (requires rlm_method)
    assert registration.contract is SynthesisResult


# --- opt-in live: real recursive extract + reduce -------------------------------------------------


@pytest.mark.model
def test_real_rlm_synthesize_answers_from_the_candidates_with_citations():
    chunks = [
        SynthesisChunk(chunk_id="deal:0:h", text="The purchase price under the agreement is $500 million."),
        SynthesisChunk(chunk_id="deal:1:h", text="The agreement is governed by the laws of Delaware."),
        SynthesisChunk(chunk_id="deal:2:h", text="Either party may terminate on 90 days written notice."),
    ]
    result = rlm_synthesize("What is the purchase price and the governing law?", chunks)

    assert result.synthesis.strip()
    assert "500" in result.synthesis and "Delaware" in result.synthesis  # facts drawn from the candidates
    assert set(result.chunk_ids) == {"deal:0:h", "deal:1:h", "deal:2:h"}  # citations preserved
