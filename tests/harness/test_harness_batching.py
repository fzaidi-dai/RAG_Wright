"""Clarifications are batched by the orchestrator: one question, not five.

Ported from RuleWright under ADR-0139's handover (PS-48g); the substrate is `rag_wright.harness.batch`, driven
through the fabric product. RuleWright's copies of the roster guards (no worker holds `ask_the_user`, every worker
can say it is blocked) are ported once, in `test_harness_workers.py`.

**The channel between a worker and its parent is ONE STRING.** A subagent's state updates do not reach its parent,
so its final message, arriving as the `task` result, is all it can say; a structured `response_format` turns
"I need X" into JSON the orchestrator can act on.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import textwrap

import pytest
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from rag_wright.harness.agent import build_chat_agent
from rag_wright.harness.batch import PURE_BEFORE_THE_INTERRUPT, BatchTheQuestions, blocked_reports, question_for
from rag_wright.harness.tiers import build_orchestrator
from rag_wright.harness.workers import WorkerReport
from tests.harness.fabric import FabricContext, _a_context, _fabric, _resources, _settings, scripted


def test_THE_REPORT_DISTINGUISHES_BLOCKED_FROM_DONE() -> None:
    """`status` and `need` are what every report carries; a prose `summary` is gone deliberately, because the
    workflow spreads a worker's result and a paragraph cannot survive that."""
    fields = set(WorkerReport.model_fields)

    assert {"status", "need"} <= fields
    assert "summary" not in fields, "the prose field is back; a worker's result must be chainable"


def test_THE_REPORT_IS_MODEL_FACING_AND_NAMES_NO_SCOPE() -> None:
    """A `response_format` becomes a tool the worker's model calls, so a field on it is a field an injected
    passage can fill. The envelope every product extends must name no part of a scope."""
    forbidden = {f.name for f in dataclasses.fields(FabricContext)} | {"scope", "shared_scope"}

    assert not forbidden & set(WorkerReport.model_fields)


# --- reading the needs off a turn ----------------------------------------------------------------------


def _task_result(payload: dict, call_id: str = "1") -> ToolMessage:
    return ToolMessage(content=json.dumps(payload), name="task", tool_call_id=call_id)


def test_TWO_BLOCKED_WORKERS_IN_ONE_TURN_ARE_READ_AS_TWO_NEEDS() -> None:
    messages = [_task_result({"status": "blocked", "need": "Which season -- SS26 or AW26?"}, "a"),
                _task_result({"status": "done", "swatches": ["cotton-poplin-120gsm"]}, "b"),
                _task_result({"status": "blocked", "need": "Organic cotton only, or any?"}, "c")]

    found = blocked_reports(messages, already_handled=set())

    assert [call_id for call_id, _ in found] == ["a", "c"]
    assert "SS26 or AW26" in found[0][1]


def test_A_NON_JSON_TOOL_RESULT_IS_NOT_A_NEED() -> None:
    """**Most tool results are not reports.** A parser that threw would take the run down; one that read prose as
    a need would ask the user about a mill listing."""
    messages = [ToolMessage(content="mill-a, mill-b", name="list_mills", tool_call_id="x"),
                ToolMessage(content="", name="task", tool_call_id="y"),
                ToolMessage(content="[1, 2]", name="task", tool_call_id="w"),
                _task_result({"status": "done"}, "z")]

    assert blocked_reports(messages, already_handled=set()) == []


def test_A_NEED_ALREADY_ANSWERED_IS_NOT_ASKED_AGAIN() -> None:
    """The tool messages stay in history forever, so a scan with no memory re-asks on every later turn."""
    assert blocked_reports([_task_result({"status": "blocked", "need": "Which season?"}, "a")],
                           already_handled={"a"}) == []


def test_A_BLOCKED_REPORT_WITH_NO_NEED_IS_NOT_A_QUESTION() -> None:
    """A worker that says it is blocked and names nothing would produce an empty question."""
    assert blocked_reports([_task_result({"status": "blocked", "need": "   "}, "a")], already_handled=set()) == []


def test_SEVERAL_NEEDS_ARE_ONE_NUMBERED_QUESTION_AND_ONE_IS_ITSELF() -> None:
    """The person sees the whole cost of answering before they start, and answers in one reply."""
    assert question_for(["Which season?"]) == "Which season?"
    assert question_for(["Which season?", "Organic only?"]) == (
        "Before I continue, 2 things:\n1. Which season?\n2. Organic only?")


# --- one question, not five ----------------------------------------------------------------------------


def _blocked_worker(name: str, need: str) -> dict:
    return {"name": name, "description": name, "system_prompt": "w", "tools": [], "response_format": WorkerReport,
            "model": scripted(AIMessage(content="", tool_calls=[{
                "name": "WorkerReport", "id": f"r-{name}", "type": "tool_call",
                "args": {"status": "blocked", "need": need}}]))}


def _agent(*model_messages: AIMessage) -> object:
    return create_deep_agent(model=scripted(*model_messages), tools=[], middleware=[BatchTheQuestions()],
                             subagents=[_blocked_worker("w1", "Which season -- SS26 or AW26?"),
                                        _blocked_worker("w2", "Organic cotton only?")],
                             checkpointer=InMemorySaver())


def _fan_out() -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": "task", "args": {"description": "poplin", "subagent_type": "w1"}, "id": "t1", "type": "tool_call"},
        {"name": "task", "args": {"description": "twill", "subagent_type": "w2"}, "id": "t2", "type": "tool_call"}])


@pytest.mark.asyncio
async def test_TWO_BLOCKED_WORKERS_PRODUCE_ONE_QUESTION() -> None:
    """**The requirement itself, driven end to end.**"""
    state = await _agent(_fan_out(), AIMessage(content="done")).ainvoke(
        {"messages": [{"role": "user", "content": "check both"}]}, config={"configurable": {"thread_id": "b1"}})

    interrupts = state.get("__interrupt__")
    assert interrupts and len(interrupts) == 1, f"the user was interrupted {len(interrupts or [])} times, not once"
    asked = interrupts[0].value
    assert asked["kind"] == "batched_needs"
    assert asked["needs"] == ["Which season -- SS26 or AW26?", "Organic cotton only?"]


@pytest.mark.asyncio
async def test_THE_ANSWERS_REACH_THE_MODEL_IN_FRAMED_FORM() -> None:
    """The orchestrator must be able to tell a recorded answer from the user's own next sentence."""
    agent = _agent(_fan_out(), AIMessage(content="done"))
    config = {"configurable": {"thread_id": "b2"}}

    await agent.ainvoke({"messages": [{"role": "user", "content": "check both"}]}, config=config)
    state = await agent.ainvoke(Command(resume="SS26, and organic only"), config=config)

    said = " ".join(str(getattr(m, "content", "")) for m in state["messages"])
    assert "SS26, and organic only" in said, "the answer never reached the conversation"
    assert "<answers_to_your_questions>" in said, "the answer arrived unframed"


@pytest.mark.asyncio
async def test_A_TURN_WITH_NO_BLOCKED_WORKER_IS_NOT_INTERRUPTED() -> None:
    """**Anti-vacuity**: most turns block on nothing, and asking anyway would make every delegation cost a question."""
    done = {"name": "w", "description": "w", "system_prompt": "w", "tools": [], "response_format": WorkerReport,
            "model": scripted(AIMessage(content="", tool_calls=[{
                "name": "WorkerReport", "id": "r", "type": "tool_call", "args": {"status": "done", "need": ""}}]))}
    agent = create_deep_agent(
        model=scripted(AIMessage(content="", tool_calls=[{"name": "task", "id": "t1", "type": "tool_call",
                                                          "args": {"description": "x", "subagent_type": "w"}}]),
                       AIMessage(content="done")),
        tools=[], middleware=[BatchTheQuestions()], subagents=[done], checkpointer=InMemorySaver())

    state = await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]},
                                config={"configurable": {"thread_id": "b3"}})

    assert not state.get("__interrupt__")


@pytest.mark.asyncio
async def test_THE_SAME_QUESTION_IS_NOT_ASKED_ON_THE_NEXT_TURN() -> None:
    """**The loop, driven rather than unit-tested.** The filter test above passes `already_handled` by hand, so it
    says nothing about whether the middleware fills it; breaking exactly that left every other test green."""
    agent = _agent(_fan_out(), AIMessage(content="done"), AIMessage(content="and again"))
    config = {"configurable": {"thread_id": "b4"}}

    await agent.ainvoke({"messages": [{"role": "user", "content": "check both"}]}, config=config)
    await agent.ainvoke(Command(resume="SS26, and yes"), config=config)
    later = await agent.ainvoke({"messages": [{"role": "user", "content": "thanks -- now what?"}]}, config=config)

    assert not later.get("__interrupt__"), "the user was asked the same questions again on a later turn"


# --- nothing that runs twice happens before the interrupt -------------------------------------------------


def _before_the_interrupt(function: object) -> list[ast.stmt]:
    body = ast.parse(textwrap.dedent(inspect.getsource(function))).body[0].body
    before = []
    for statement in body:
        if any(isinstance(n, ast.Call) and getattr(n.func, "id", None) == "interrupt" for n in ast.walk(statement)):
            return before
        before.append(statement)
    raise AssertionError("`before_model` never interrupts; this guard is reading the wrong function")


def test_NOTHING_IS_AWAITED_BEFORE_THE_INTERRUPT() -> None:
    """Resume replays the node from the top, so anything awaited here happens twice."""
    awaited = [n for st in _before_the_interrupt(BatchTheQuestions.before_model)
               for n in ast.walk(st) if isinstance(n, ast.Await)]

    assert not awaited


def test_THE_SYNC_CALLS_BEFORE_THE_INTERRUPT_ARE_THE_ONES_MEANT() -> None:
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "?")
              for st in _before_the_interrupt(BatchTheQuestions.before_model)
              for n in ast.walk(st) if isinstance(n, ast.Call)}

    assert called <= PURE_BEFORE_THE_INTERRUPT, f"{sorted(called - PURE_BEFORE_THE_INTERRUPT)} is not allow-listed"


def test_THE_MIDDLEWARE_IS_ON_THE_ORCHESTRATOR_NOT_THE_CHAT_MANAGER() -> None:
    """The ORCHESTRATOR batches. The chat-manager runs no workers, so the only question it sees is its own."""
    context, resources = _a_context(), _resources()
    on_orchestrator = build_orchestrator(context, resources, _settings(), definition=_fabric(),
                                         model=scripted(AIMessage(content="x"))).get_input_jsonschema()["properties"]
    on_manager = build_chat_agent(context, resources, _settings(), definition=_fabric(),
                                  model=scripted(AIMessage(content="x"))).get_input_jsonschema()["properties"]

    assert "answered_needs" in on_orchestrator, "the orchestrator does not batch"
    assert "answered_needs" not in on_manager, "the chat-manager carries batching, with nothing to batch"
