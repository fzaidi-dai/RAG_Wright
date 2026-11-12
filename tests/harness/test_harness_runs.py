"""The run's entry points: a scope that cannot be forgotten, and a verdict on how a run ended.

Ported from RuleWright under ADR-0139's handover (PS-48c); the substrate is `rag_wright.harness.runs`. RuleWright's
tests of its own context type and of the route that derives it from a session stay in RuleWright.

**The failure this exists to prevent is silent and happens at the worst moment.** Runtime context is
per-INVOCATION, not per-thread: a resume that omits `context=` wakes every tool with `runtime.context is None`, and a
store namespace built from it falls back to one global namespace -- so a forgetful resume reads and writes another
scope's memory, at the moment a person has just answered. So the entry points make a context-free invocation
impossible rather than asking callers to remember.
"""

from __future__ import annotations

import inspect

import pytest
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import interrupt

from rag_wright.harness.runs import RunOutcome, read_run, resume_run, start_run
from tests.harness.fabric import FabricContext, _fabric, scripted, slow_from


def _context() -> FabricContext:
    return FabricContext(account="acme-mills", season="ss26", collection="resort")


def calls(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def _agent(*messages: AIMessage, tools: list) -> object:
    return create_deep_agent(model=scripted(*messages), tools=tools, context_schema=FabricContext,
                             checkpointer=InMemorySaver())


# --- the entry point ---------------------------------------------------------------------------------


async def test_start_run_supplies_the_context_the_caller_never_passes() -> None:
    """The caller hands over a scope; the entry point turns it into `context=`."""
    seen: list = []

    @tool
    def look(subject: str, runtime: ToolRuntime[FabricContext]) -> str:
        """Look something up."""
        seen.append(runtime.context)
        return "found"

    agent = _agent(calls("look", {"subject": "s"}, "1"), AIMessage(content="done"), tools=[look])

    await start_run(agent, _context(), message="go")

    assert seen and seen[0] is not None, "the tool woke with no context -- the entry point did not pass it"
    assert seen[0].scope == _context().scope


async def test_a_RESUME_through_the_entry_point_carries_the_scope() -> None:
    """**The test the entry points exist for.** A bare `agent.ainvoke(Command(resume=...))` loses the context;
    going through `resume_run` must not, because this is the moment a person has just answered and the memory
    namespace is about to be chosen."""
    seen: list = []

    @tool
    def ask(question: str, runtime: ToolRuntime[FabricContext]) -> str:
        """Ask the user and wait."""
        answer = interrupt({"question": question})
        seen.append(runtime.context)
        return f"ok {answer}"

    agent = _agent(calls("ask", {"question": "q"}, "1"), AIMessage(content="done"), tools=[ask])

    run = await start_run(agent, _context(), message="go")
    await resume_run(agent, _context(), answer="A", run_id=run.run_id)

    assert seen, "the tool never resumed"
    assert seen[-1] is not None, (
        "the resumed tool woke with `runtime.context is None` -- the cross-scope memory read the entry points "
        "exist to prevent, and it means `resume_run` dropped the context"
    )
    assert seen[-1].scope == _context().scope


async def test_a_run_has_an_IDENTITY_that_outlives_the_call_that_started_it() -> None:
    """A run is addressable: the entry point mints the id when the caller has none, so a caller cannot end up
    with a run it cannot resume or re-render."""
    @tool
    def look(subject: str) -> str:
        """Look something up."""
        return "found"

    run = await start_run(_agent(AIMessage(content="done"), tools=[look]), _context(), message="go")

    assert run.run_id, "a run with no identity cannot be resumed, re-rendered or listed"
    assert run.context.scope == _context().scope


async def test_an_agent_built_WITHOUT_the_context_schema_is_REFUSED() -> None:
    """An agent compiled with `context_schema=None` accepts `context=` and silently drops it: the same leak as a
    forgetful resume, one step earlier, and invisible from the call site."""
    unschooled = create_deep_agent(model=scripted(AIMessage(content="done")), checkpointer=InMemorySaver())

    with pytest.raises(TypeError, match="context_schema"):
        await start_run(unschooled, _context(), message="go")


def test_the_entry_point_is_ASYNC() -> None:
    """Concurrency belongs on the event loop, not on OS threads."""
    assert inspect.iscoroutinefunction(start_run)
    assert inspect.iscoroutinefunction(resume_run)


def _public_entry_points() -> dict[str, inspect.Signature]:
    """Every public coroutine and async generator `runs.py` exposes. **Derived, not enumerated**, so an entry
    point added next year is covered the day it is declared."""
    import rag_wright.harness.runs as module

    return {
        name: inspect.signature(obj)
        for name, obj in vars(module).items()
        if not name.startswith("_")
        and (inspect.iscoroutinefunction(obj) or inspect.isasyncgenfunction(obj))
        and getattr(obj, "__module__", None) == module.__name__
    }


def test_the_derivation_SEES_the_entry_points() -> None:
    """Anti-vacuity: an empty mapping makes every assertion below pass by checking nothing."""
    found = _public_entry_points()

    assert {"start_run", "resume_run", "stream_run", "read_run"} <= set(found), (
        f"the entry points moved or were renamed; found {sorted(found)}. Confirm the new shape is meant -- the "
        "guards below assert on exactly these."
    )


def test_the_entry_point_CANNOT_BE_CALLED_WITHOUT_A_CONTEXT() -> None:
    """**Omission made impossible, not discouraged.** The context is a REQUIRED positional parameter on every
    entry point -- no default, not keyword-only. A defaulted `context=None` would be the forgetful resume,
    wearing a convenience."""
    for name, sig in _public_entry_points().items():
        assert "context" in sig.parameters, f"{name} takes no context at all"
        parameter = sig.parameters["context"]
        assert parameter.default is inspect.Parameter.empty, (
            f"{name} defaults its context. A caller that omits it wakes every tool with `runtime.context is "
            "None`, and a store namespace then falls back to one global namespace -- another scope's memory."
        )
        assert parameter.kind is not inspect.Parameter.KEYWORD_ONLY, (
            f"{name}'s context is keyword-only, so it reads as optional at every call site"
        )


def test_NO_ENTRY_POINT_TAKES_A_TENANT() -> None:
    """**The substrate must not know what authenticated a run** (ADR-0139). An entry point taking a product's
    identity rows would make a second product supply the first one's types to start a run; it takes the scope
    the substrate actually reads."""
    offenders = {name: sorted(sig.parameters) for name, sig in _public_entry_points().items()
                 if "tenant" in sig.parameters}

    assert not offenders, f"these entry points still take a tenant: {offenders}"


# --- what a cut-off run shows ----------------------------------------------------------------------------


def _slow_agent(model: object, tools: list | None = None) -> object:
    """An agent built the way a product builds one: the fabric's own error stack, and a model that is slow."""
    from rag_wright.harness.errors import error_middleware

    definition = _fabric()
    return create_deep_agent(model=model, tools=tools or [], context_schema=FabricContext,
                             checkpointer=InMemorySaver(),
                             middleware=error_middleware(retry_on=definition.retry_on,
                                                         message_for=definition.message_for))


async def test_A_RUN_CUT_OFF_AFTER_REAL_WORK_READS_BACK_AS_PARTIAL(monkeypatch) -> None:
    """**The gap this closes.** A run that was cut off and a run that finished were indistinguishable to every
    reader -- including the route that renders a reload. When the whole-turn clock fires, the finished work is
    still in the checkpoint, so there is nothing to store, only something to name."""
    monkeypatch.setattr("rag_wright.harness.models.MODEL_ATTEMPT_DEADLINE_S", 10.0)
    monkeypatch.setattr("rag_wright.harness.models.MODEL_TURN_DEADLINE_S", 0.2)

    @tool
    def match_compositions(requirements: str) -> dict:
        """Find compositions matching a requirement set."""
        return {"matched": ["cotton-poplin-120gsm"], "mills": "3 of 3"}

    agent = _slow_agent(slow_from(2, 2.0, calls("match_compositions", {"requirements": "outerwear"}, "a"),
                                  AIMessage(content="never reached")),
                        tools=[match_compositions])

    with pytest.raises(Exception):  # noqa: B017 -- the type is not this test's subject
        await start_run(agent, _context(), message="which blends suit outerwear?", run_id="cut-off")

    found = await read_run(agent, _context(), run_id="cut-off")

    assert found is not None, "the run is unaddressable after being cut off, so nothing can be shown"
    assert found.outcome is RunOutcome.PARTIAL, (
        f"the run reads back as {found.outcome}; work finished and no answer was produced, which is the partial case"
    )
    assert "cotton-poplin-120gsm" in str(found.state["messages"]), "the finished work is not in what is shown"


async def test_A_RUN_THAT_PRODUCED_NOTHING_IS_NOT_CALLED_PARTIAL(monkeypatch) -> None:
    """**When the retries are exhausted the model never answered**, and the checkpoint holds only the person's own
    question. Reporting an empty gap as a partial answer would be worse than failing."""
    monkeypatch.setattr("rag_wright.harness.models.MODEL_ATTEMPT_DEADLINE_S", 0.05)
    monkeypatch.setattr("rag_wright.harness.models.MODEL_TURN_DEADLINE_S", 30.0)

    agent = _slow_agent(slow_from(None, 1.0, AIMessage(content="never reached")))

    with pytest.raises(Exception):  # noqa: B017
        await start_run(agent, _context(), message="blends?", run_id="nothing-came-back")

    found = await read_run(agent, _context(), run_id="nothing-came-back")

    assert found is not None
    assert found.outcome is RunOutcome.NOTHING_CAME_BACK, f"a run that produced nothing reads back as {found.outcome}"


async def test_A_RUN_THAT_FINISHED_IS_STILL_CALLED_COMPLETE() -> None:
    """**The negative half, and the one that would do real damage.** A verdict that called every run partial
    would put a caveat on every correct answer the product gives."""
    agent = _agent(AIMessage(content="Two mills stock it: mill-a and mill-b."), tools=[])

    await start_run(agent, _context(), message="who stocks it?", run_id="finished")
    found = await read_run(agent, _context(), run_id="finished")

    assert found is not None
    assert found.outcome is RunOutcome.COMPLETE, f"a finished run reads back as {found.outcome}"


async def test_A_RUN_WAITING_ON_A_PERSON_IS_NEITHER_COMPLETE_NOR_PARTIAL() -> None:
    """A turn that stopped to ask has not failed and has not finished -- it is waiting, and `resume_run` continues
    it. Calling it partial would tell the person work was lost while a question is outstanding."""
    from rag_wright.harness.clarify import clarification_tool

    ask = clarification_tool()
    agent = _agent(calls(ask.name, {"question": "which collection did you mean?"}, "q"), AIMessage(content="unused"),
                   tools=[ask])

    await start_run(agent, _context(), message="compare them", run_id="waiting")
    found = await read_run(agent, _context(), run_id="waiting")

    assert found is not None
    assert found.outcome is RunOutcome.WAITING_ON_A_PERSON, f"a suspended run reads back as {found.outcome}"
