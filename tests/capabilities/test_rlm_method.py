"""ADR-0016 fail-if-absent tests for the RLM method machinery (T15, FR-C.10).

RLM is defined by what it must DO, not by markers (ADR-0016). These four tests each fail if the
assembled RLM agent lacks one required capability, so a stateless one-level splitter cannot pass:

  1. recursive input decomposition (the interpreter re-enters `decompose()` past depth one),
  2. per-slice tool use (a leaf worker can invoke a tool mid-handling),
  3. per-slice skill and behavior (a leaf worker can load a skill),
  4. dynamic sub-agents via code-driven fan-out (the "workflow" fired `task()` from `eval`, not a
     sequential tool-by-tool fallback).

They run the REAL Deep Agents machinery (`build_rlm_agent` + the shipped `RLM_WORKFLOW_JS`) with scripted
fake chat models per role, so they exercise genuine `task()` dispatch through the QuickJS interpreter
without a network call, and assert on the observable dispatch (`subagent` custom-stream events + the
`eval` result). Design B' (ADR-0015 Q2, corrected): the recursion lives in the interpreter, which
re-dispatches a fresh `rlm_decomposer` per level; no agent dispatches itself.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from rag_wright.skills.rlm.agent import (
    RLM_DECOMPOSER,
    RLM_SLICE_WORKER,
    RLM_WORKFLOW_JS,
    build_rlm_agent,
    rlm_interpreter_session,
)

_PROBE_SKILL = str((Path(__file__).parent / "_fixtures" / "rlm_probe_skill" / "SKILL.md").resolve())


# --- scripted fake models (hermetic; drive real dispatch) ----------------------------------------


class FakeChat(BaseChatModel):
    """A content-aware fake chat model: a responder maps the received messages to one AIMessage.

    Stateless per call (the responder inspects the transcript), so concurrent sub-agent dispatch order
    does not matter. Optionally records the messages each call received (for the skill test).
    """

    responder: Any = None  # Callable[[list[BaseMessage]], AIMessage]
    received: Any = None  # Optional[list] to append each call's messages to

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if self.received is not None:
            self.received.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.responder(messages))])

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001 - the agent binds eval/task; the script is fixed
        return self

    @property
    def _llm_type(self) -> str:
        return "fake-chat"


def _last_human(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def _ws_items(n: int) -> list[dict]:
    """A working set of `n` identifiable items (the shape the coverage tail diffs against)."""
    return [{"id": i, "text": f"item {i}"} for i in range(n)]


def _decomposer_responder(leaf_size: int = 1) -> Callable[[list[BaseMessage]], AIMessage]:
    """A decomposer that splits an item-list in half (returns `cuts`) until it is <= `leaf_size` items,
    then marks it a leaf. Reads the item count from the description RLM_WORKFLOW_JS sends."""

    def respond(messages: list[BaseMessage]) -> AIMessage:
        match = re.search(r"over (\d+) items", _last_human(messages))
        n = int(match.group(1)) if match else 1
        if n <= leaf_size:
            return AIMessage(content=json.dumps({"leaf": True}))
        return AIMessage(content=json.dumps({"leaf": False, "cuts": [n // 2]}))

    return respond


def _orchestrator_responder(workflow_js: str = RLM_WORKFLOW_JS):
    """An orchestrator that writes the workflow into `eval` once, then finishes (code-driven fan-out).
    The working set is delivered via `tools.workingSet()` (T36), so the workflow reads it — not the prompt."""

    def respond(messages: list[BaseMessage]) -> AIMessage:
        if any(isinstance(m, ToolMessage) and m.name == "eval" for m in messages):
            return AIMessage(content="done")
        return AIMessage(content="", tool_calls=[{"name": "eval", "args": {"code": workflow_js}, "id": "eval_1"}])

    return respond


def _ws_tool(value: Any):
    @tool
    def working_set() -> object:
        """Return the working set (delivered as a PTC value; never enters the model's context)."""
        return value

    return working_set


def _build_rlm(ws_value: Any, **roles: Any):
    """Build an RLM agent with `working_set` bound as a PTC returning `ws_value` (the T36 delivery seam)."""
    from langchain_quickjs import CodeInterpreterMiddleware

    interpreter = CodeInterpreterMiddleware(subagents=True, ptc=[_ws_tool(ws_value)])
    return build_rlm_agent(interpreter=interpreter, **roles)


def _run(agent, human: str = "run the workflow"):
    """Stream one run, returning (custom subagent events, final messages)."""
    events: list[dict] = []
    final: dict = {}
    for mode, data in agent.stream(
        {"messages": [HumanMessage(content=human)]}, stream_mode=["custom", "values"]
    ):
        if mode == "custom" and isinstance(data, dict) and data.get("type") == "subagent":
            events.append(data)
        elif mode == "values":
            final = data
    return events, final.get("messages", [])


def _eval_result(messages: list[BaseMessage]) -> dict:
    """Parse the JSON the workflow returned from the `eval` tool message (console/result stripped)."""
    for message in messages:
        if isinstance(message, ToolMessage) and message.name == "eval":
            body = str(message.content)
            start, end = body.find("{"), body.rfind("}")
            if start != -1 and end != -1:
                return json.loads(body[start : end + 1])
    return {}


def _starts(events: list[dict], subagent_type: str) -> list[dict]:
    return [e for e in events if e.get("phase") == "start" and e.get("subagent_type") == subagent_type]


# --- 1. recursive input decomposition ------------------------------------------------------------


def _decompose_depths(events: list[dict]) -> set[int]:
    return {int(e["description"].split("depth ", 1)[1].split(" ", 1)[0]) for e in _starts(events, RLM_DECOMPOSER)}


def test_recursion_forced_the_interpreter_reenters_decompose_past_depth_one():
    # 4 items split in half to singleton leaves: [0,1,2,3] -> [0,1],[2,3] -> [0],[1],[2],[3]. The leaves
    # are reachable ONLY by re-entering decompose() past depth 0. A one-level splitter fails the checks.
    agent = _build_rlm(
        _ws_items(4),
        reasoning_model=FakeChat(responder=_orchestrator_responder()),
        decomposer_model=FakeChat(responder=_decomposer_responder(leaf_size=1)),
        worker_model=FakeChat(responder=lambda m: AIMessage(content="handled")),
    )
    events, messages = _run(agent)
    result = _eval_result(messages)

    # (a) the interpreter re-entered decompose() past the first level (a split occurred at depth >= 1)
    assert result.get("maxSplitDepth", -1) >= 1
    # (b) every item covered — the coverage tail confirms it in-interpreter (covered == total, none missed)
    assert result.get("covered") == 4 and result.get("total") == 4 and result.get("missed") == 0
    # (c) the decomposer actually fired at MORE THAN ONE level, not a flat batch at depth 0
    depths = _decompose_depths(events)
    assert len(depths) >= 2 and max(depths) >= 2, f"decomposer fired only at depths {depths}"


def test_a_flat_one_level_workflow_fails_the_recursion_assertion():
    # Teeth: the recursion assertion is not vacuous. A non-recursive workflow (split once, dispatch the
    # parts as leaves) produces maxSplitDepth 0 and cannot satisfy the depth check above.
    flat_js = r"""
    const workingSet = await tools.workingSet();
    const handled = new Set();
    await task({description: "decompose depth 0 over " + workingSet.length + " items", subagentType: "rlm_decomposer"});
    for (const it of workingSet) { await task({description: "handle leaf depth 0", subagentType: "rlm_slice_worker"}); handled.add(it.id); }
    JSON.stringify({leafCount: workingSet.length, covered: handled.size, total: workingSet.length, maxSplitDepth: 0});
    """.strip()
    agent = _build_rlm(
        _ws_items(4),
        reasoning_model=FakeChat(responder=_orchestrator_responder(flat_js)),
        decomposer_model=FakeChat(responder=_decomposer_responder()),
        worker_model=FakeChat(responder=lambda m: AIMessage(content="handled")),
    )
    _events, messages = _run(agent)
    assert _eval_result(messages).get("maxSplitDepth", -1) < 1  # a flat batch never recurses


def test_coverage_tail_covers_an_incomplete_descent_in_the_interpreter():
    # T37: the completeness guarantee lives in the WORKFLOW, so it runs wherever the skill runs (the node
    # AND this harness). Even a workflow whose descent is INCOMPLETE (handles only the first half) reaches
    # full coverage: the tail diffs the working set (held in code) against `handled` and re-dispatches the
    # missed -- proven in-interpreter, the property a Python wrapper could not give GraphWright's node.
    incomplete_js = r"""
    const workingSet = await tools.workingSet();
    const handled = new Set();
    for (const it of workingSet.slice(0, Math.floor(workingSet.length / 2))) {
      await task({description: "handle leaf", subagentType: "rlm_slice_worker"}); handled.add(it.id);
    }
    for (const it of workingSet.filter((it) => !handled.has(it.id))) {   // COVERAGE TAIL
      handled.add(it.id); await task({description: "cover missed", subagentType: "rlm_slice_worker"});
    }
    JSON.stringify({covered: handled.size, total: workingSet.length});
    """.strip()
    agent = _build_rlm(
        _ws_items(6),
        reasoning_model=FakeChat(responder=_orchestrator_responder(incomplete_js)),
        decomposer_model=FakeChat(responder=_decomposer_responder()),
        worker_model=FakeChat(responder=lambda m: AIMessage(content="ok")),
    )
    events, messages = _run(agent)
    result = _eval_result(messages)

    assert result.get("covered") == 6 and result.get("total") == 6  # full coverage despite the incomplete descent
    assert len(_starts(events, RLM_SLICE_WORKER)) == 6  # 3 handled by the descent + 3 by the tail = every item


def test_skill_md_canonical_workflow_is_byte_identical_to_rlm_workflow_js():
    # The REAL model follows SKILL.md; the hermetic tests run RLM_WORKFLOW_JS. If they drift, the tests
    # validate a different artifact than what ships — exactly how the missing item count in the SKILL.md
    # decomposer dispatch (which makes a literal-following model fall flat, no recursion) slipped past a
    # green test. Assert the canonical SKILL.md workflow is byte-identical to RLM_WORKFLOW_JS.
    skill = (Path(__file__).parents[2] / "src" / "rag_wright" / "skills" / "rlm" / "SKILL.md").read_text()
    blocks = re.findall(r"```javascript\n(.*?)\n```", skill, re.DOTALL)
    assert blocks, "no javascript block found in SKILL.md"
    assert blocks[0].strip() == RLM_WORKFLOW_JS, "SKILL.md canonical workflow drifted from RLM_WORKFLOW_JS"
    assert "over " in RLM_WORKFLOW_JS and "items" in RLM_WORKFLOW_JS  # the decomposer dispatch carries the count


# --- 2. per-slice tool use -----------------------------------------------------------------------


def test_a_slice_worker_can_invoke_a_tool_mid_handling():
    called: list[str] = []

    @tool
    def record_fact(fact: str) -> str:
        """Record a fact extracted from the slice."""
        called.append(fact)
        return "recorded"

    def worker_responder(messages: list[BaseMessage]) -> AIMessage:
        if any(isinstance(m, ToolMessage) and m.name == "record_fact" for m in messages):
            return AIMessage(content="leaf handled")
        return AIMessage(content="", tool_calls=[{"name": "record_fact", "args": {"fact": "f1"}, "id": "t1"}])

    agent = _build_rlm(
        _ws_items(1),  # a single-item working set -> one leaf -> the worker uses its tool
        reasoning_model=FakeChat(responder=_orchestrator_responder()),
        decomposer_model=FakeChat(responder=_decomposer_responder()),
        worker_model=FakeChat(responder=worker_responder),
        worker_tools=[record_fact],
    )
    _run(agent)
    assert called == ["f1"]  # the leaf worker used its tool mid-handling


# --- 3. per-slice skill and behavior -------------------------------------------------------------


def test_a_slice_worker_can_load_a_skill():
    worker_seen: list[list[BaseMessage]] = []
    agent = _build_rlm(
        _ws_items(1),
        reasoning_model=FakeChat(responder=_orchestrator_responder()),
        decomposer_model=FakeChat(responder=_decomposer_responder()),
        worker_model=FakeChat(responder=lambda m: AIMessage(content="leaf handled"), received=worker_seen),
        worker_skills=[_PROBE_SKILL],
    )
    _run(agent)
    system_text = "\n".join(
        str(m.content) for run in worker_seen for m in run if isinstance(m, SystemMessage)
    )
    assert worker_seen, "the worker was never dispatched"
    # deepagents attaches `skills=` as higher-priority skill sources loaded into the agent's context;
    # the source is present in THIS worker's system context (and only because it was wired to the worker).
    assert "rlm_probe_skill/SKILL.md" in system_text


# --- 4. dynamic sub-agents via code-driven fan-out (not sequential) ------------------------------


def _is_code_driven(events: list[dict]) -> bool:
    """Code-driven fan-out: dispatches carry the parent `eval_id` (they ran inside an `eval` call).
    A sequential fallback calls `task` as a top-level tool, one per turn, with no parent `eval`."""
    starts = [e for e in events if e.get("phase") == "start"]
    return bool(starts) and all(e.get("eval_id") for e in starts)


def test_the_workflow_fires_code_driven_fanout_not_sequential_dispatch():
    agent = _build_rlm(
        _ws_items(4),
        reasoning_model=FakeChat(responder=_orchestrator_responder()),
        decomposer_model=FakeChat(responder=_decomposer_responder()),
        worker_model=FakeChat(responder=lambda m: AIMessage(content="handled")),
    )
    events, messages = _run(agent)
    assert any(isinstance(m, ToolMessage) and m.name == "eval" for m in messages)  # an eval ran
    assert _is_code_driven(events)  # every dispatch came from code-driven fan-out; FAILS if sequential


def test_sequential_dispatch_is_detected_as_a_fallback():
    # Teeth: a run that dispatches `task` as a top-level tool (no eval) is NOT code-driven fan-out, so
    # the detector rejects it. This is the failure the fail-if-sequential test guards against.
    def sequential_orchestrator(messages: list[BaseMessage]) -> AIMessage:
        if any(isinstance(m, ToolMessage) and m.name == "task" for m in messages):
            return AIMessage(content="done")
        return AIMessage(
            content="",
            tool_calls=[{"name": "task", "args": {"description": "handle leaf depth 0: x", "subagent_type": RLM_SLICE_WORKER}, "id": "task_1"}],
        )

    agent = build_rlm_agent(
        reasoning_model=FakeChat(responder=sequential_orchestrator),
        decomposer_model=FakeChat(responder=_decomposer_responder()),
        worker_model=FakeChat(responder=lambda m: AIMessage(content="handled")),
    )
    events, messages = _run(agent)
    assert not any(isinstance(m, ToolMessage) and m.name == "eval" for m in messages)  # no eval ran
    assert not _is_code_driven(events)  # sequential dispatch is correctly rejected


# --- KI-1: the process-wide interpreter-session serialization guard (ADR-0020) -------------------


def test_rlm_interpreter_session_serializes_across_threads():
    # KI-1 (ADR-0020): two QuickJS runtimes must never be alive at once in one process, because they race
    # on shared Rust state and silently drop ~half their dispatches. The guard makes the interpreter
    # session mutually exclusive process-wide, so concurrent callers serialize on it (the always-on
    # correctness floor). Prove no two sessions overlap even under concurrency.
    import threading
    import time

    inside = 0
    max_inside = 0
    probe = threading.Lock()

    def worker() -> None:
        nonlocal inside, max_inside
        with rlm_interpreter_session():
            with probe:
                inside += 1
                max_inside = max(max_inside, inside)
            time.sleep(0.03)  # hold the session so overlap would be observed if it were allowed
            with probe:
                inside -= 1

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert max_inside == 1  # never two interpreter sessions alive at once (would be >1 without the guard)


def test_rlm_interpreter_session_yields_an_interpreter_and_tears_it_down():
    # the session yields the middleware to build the agent with, and closes its registry on exit
    # (deterministic teardown inside the lock, not GC), so the runtime's full lifetime is exclusive.
    from langchain_quickjs import CodeInterpreterMiddleware

    with rlm_interpreter_session() as interpreter:
        assert isinstance(interpreter, CodeInterpreterMiddleware)
        registry = interpreter._registry
    assert registry._slots == {}  # close() cleared every REPL slot on exit (runtime torn down in-lock)


# --- opt-in: does a REAL model, given the RLM method, FOLLOW it (recurse on decomposer output)? ---
#
# The hermetic tests above scripted every model to emit the recursion, so they prove the machinery. This
# one proves what they cannot: that a REAL orchestrator, handed the RLM method (its system prompt) and a
# "workflow"-triggered request, WRITES the recursive decompose() and recurses on the decomposer's returned
# parts, rather than the flatten-and-hardcode degeneration (dispatch the decomposer once, then invent the
# slice list in JS) that a real model was observed to fall into when the method was only a lazy skill
# source it never read (fixed: the method is now the orchestrator's system prompt, ADR-0018).
#
# The trap this design avoids: on a small VISIBLE working set a faithful RLM correctly flattens (it fits
# one context), and derivation-from-the-decomposer is unobservable from outside. So the working set here is
# an OPAQUE handle whose sub-slices only the decomposer can reveal: the leaf ids (N0.0.0 ...) are unknowable
# in advance, so the orchestrator can reach them ONLY by recursing on the decomposer's output. Full leaf
# coverage is therefore direct proof of derivation, not a lowered bar. The decomposer/worker are probes
# (they make the opaque tree and the received leaves observable); the ORCHESTRATOR is the real model under
# test. A RED result is FIRST a SKILL.md-quality signal (the machinery is proven hermetically): a real
# model read the method and did not follow it. Measured 10/10 on google/gemma-4-31b-it, 2026-07-15.

_OPAQUE_LEAVES = {"N0.0.0", "N0.0.1", "N0.1.0", "N0.1.1"}
_OPAQUE_TOKEN = re.compile(r"N0(?:\.\d+)*")


def _deepest_token(text: str) -> str:
    tokens = _OPAQUE_TOKEN.findall(text)
    return max(tokens, key=len) if tokens else "N0"


def _probe_decomposer(messages: list[BaseMessage]) -> AIMessage:
    """Reveals an opaque depth-2 tree: a slice splits into two children until it is 2 levels deep (a leaf).
    The leaf ids are generated here, so the orchestrator cannot know them without recursing on this output."""
    slice_id = _deepest_token(_last_human(messages))
    if slice_id.count(".") >= 2:
        return AIMessage(content=json.dumps({"leaf": True}))
    return AIMessage(content=json.dumps({"leaf": False, "parts": [f"{slice_id}.0", f"{slice_id}.1"]}))


def _probe_worker(messages: list[BaseMessage]) -> AIMessage:
    return AIMessage(content=f"handled {_deepest_token(_last_human(messages))}")


_OPAQUE_REQUEST = (
    "Run this as a workflow. Get the working set with `const workingSet = await tools.workingSet();` — it "
    "is an opaque handle whose contents and sub-slices you cannot see; only the rlm_decomposer can reveal "
    "them. Apply the RLM method from your instructions: decompose the working set by dispatching "
    "rlm_decomposer and recursing on the parts it returns, and hand each leaf slice to an rlm_slice_worker. "
    "Report the leaves you handled."
)


@pytest.mark.model
def test_a_real_model_recurses_on_the_decomposers_output_not_a_hardcoded_split():
    """Live (opt-in, `-m model`): a real orchestrator, given the RLM method, must recurse on the
    decomposer's returned parts to reach opaque leaves it cannot hardcode.

    A RED result is FIRST a SKILL.md-quality signal, not a machinery failure (the machinery is proven
    hermetically above): it means a real model read the method and did not follow it (flatten-and-hardcode,
    wrong sub-agents, or no code-driven fan-out). Fix the skill, not the harness. See ADR-0018.
    """
    agent = _build_rlm(
        "N0",
        decomposer_model=FakeChat(responder=_probe_decomposer),
        worker_model=FakeChat(responder=_probe_worker),
    )
    events = []
    for mode, data in agent.stream(
        {"messages": [HumanMessage(content=_OPAQUE_REQUEST)]},
        stream_mode=["custom", "values"],
        config={"recursion_limit": 60},
    ):
        if mode == "custom" and isinstance(data, dict) and data.get("type") == "subagent":
            events.append(data)

    types = {e.get("subagent_type") for e in events if e.get("phase") == "start"}
    assert RLM_DECOMPOSER in types, f"the model did not dispatch the decomposer (dispatched: {types})"
    assert RLM_SLICE_WORKER in types, f"the model did not dispatch the leaf worker (dispatched: {types})"
    assert _is_code_driven(events), "the model did not use code-driven fan-out (sequential fallback)"

    worker_leaves = {
        _deepest_token(str(e.get("description", "")))
        for e in events
        if e.get("phase") == "start" and e.get("subagent_type") == RLM_SLICE_WORKER
    }
    # Every opaque leaf was handled. The orchestrator started knowing only "N0" and could reach these ids
    # ONLY by recursing on the decomposer's returned parts, so full coverage proves derivation, not a
    # hardcoded split. A flatten-and-hardcode run cannot produce leaves it never saw.
    assert _OPAQUE_LEAVES.issubset(worker_leaves), (
        f"workers did not cover the decomposer-generated leaves (got {sorted(worker_leaves)}); the model "
        "did not recurse on the decomposer's output"
    )
