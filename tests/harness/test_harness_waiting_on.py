"""A paused run says what it is waiting on (PS-83).

**A run that stopped for a person reported only that it had stopped.** `RunOutcome.WAITING_ON_A_PERSON` said the run
paused; nothing public said what it asked. A product could read the chat-manager's own `ask_the_user` from a tool
call's argument, but a worker's need, batched by the orchestrator, and a moved view are raised by middleware with no
tool call behind them: their question existed only in LangGraph's `__interrupt__` key (TexWright's request
`2026-11-25-a-batched-needs-question-is-not-readable-publicly.md`).

`RunResult.waiting_on` carries it, as a `Pending`: the `kind` of pause, the `question` to show, and the `needs` behind
it one by one, for a product that renders them as fields. Filled by every door: `start_run`, `resume_run`, and
`read_run` from the checkpoint alone; `stream_run`'s `translate` emits it as a `waiting` event.

**Two pauses can be pending at once** (measured: a model that calls `ask_the_user` twice in one reply), and
`resume_run` then raised LangGraph's "you must specify the interrupt id". They are one `Pending` of kind
`batched_needs`, asked as one question, and the person's one answer resumes them all.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Interrupt

from rag_wright.harness import ConfirmTheView, Pending, RunEvent, build_chat_agent, read_run, resume_run, start_run
from rag_wright.harness import clarification_tool, stream_run, translate
from tests.harness.fabric import FabricContext, FabricView, _a_context, _fabric, _resources, _settings
from tests.harness.fabric import scripted
from tests.harness.test_harness_answer_inside_a_dispatch import _worker
from tests.harness.test_harness_answered_by_dispatch import PrecedentSurface, _call, _delegate, recording

QUESTION = "Which earlier development is closest to SDF 3146?"
SEASON = "Which season -- SS26 or AW26?"
MILL = "Which mill -- Ludhiana or Tiruppur?"


def _context() -> FabricContext:
    return FabricContext(account="acme-mills", season="ss26", collection="resort")


def _asks(*questions: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "ask_the_user", "args": {"question": q}, "id": f"a{i}",
                                              "type": "tool_call"} for i, q in enumerate(questions)])


def _chat(*replies: AIMessage, streams: bool = False) -> Any:
    model = scripted(*replies)
    if streams:
        # The scripted model cannot stream a tool call; the run still streams its updates.
        model = model.model_copy(update={"disable_streaming": True})
    return create_deep_agent(model=model, tools=[clarification_tool()], context_schema=FabricContext,
                             checkpointer=InMemorySaver())


def _blocked_worker(*needs: str) -> Any:
    """Chat-manager -> orchestrator -> one worker per need, each reporting `blocked` (PS-81's shape)."""
    definition = dataclasses.replace(
        _fabric(), surface=PrecedentSurface(), planning={"product.find_precedents": "compares across cases"},
        workers=(_worker,), routing=None, repeatable=frozenset(), question_argument={})
    # One reply dispatching every worker at once, so their needs come back together and are batched together.
    dispatch = AIMessage(content="", tool_calls=[
        {"name": "task", "args": {"description": f"Match {i}.", "subagent_type": "composition_worker"},
         "id": f"t{i}", "type": "tool_call"} for i in range(len(needs))])
    reports = [_call("FabricFindings", {"status": "blocked", "need": need}, f"r{i}") for i, need in enumerate(needs)]
    replies = [_delegate(QUESTION), dispatch, *reports]
    model, _ = recording(*replies, AIMessage(content="SDF 3204 is the closest in SS26."),
                         AIMessage(content="SDF 3204, in SS26."))
    return build_chat_agent(_a_context(), _resources(), _settings(), definition=definition, model=model)


# --- the chat-manager's own question ---------------------------------------------------------------------


def test_A_QUESTION_THE_CHAT_MANAGER_ASKED_IS_READ_FROM_THE_RUN() -> None:
    agent = _chat(_asks("Which collection did you mean?"), AIMessage(content="unused"))

    run = asyncio.run(start_run(agent, _context(), message="compare them", run_id="ask"))

    assert run.waiting_on == Pending(kind="ask_the_user", question="Which collection did you mean?",
                                     needs=("Which collection did you mean?",))
    # Every pause's payload names its kind, this one too.
    assert run.state["__interrupt__"][0].value["kind"] == "ask_the_user"


def test_A_RELOAD_READS_THE_QUESTION_FROM_THE_CHECKPOINT_ALONE() -> None:
    agent = _chat(_asks("Which collection did you mean?"), AIMessage(content="unused"))
    asyncio.run(start_run(agent, _context(), message="compare them", run_id="reload"))

    found = asyncio.run(read_run(agent, _context(), run_id="reload"))

    assert found is not None
    assert found.waiting_on == Pending(kind="ask_the_user", question="Which collection did you mean?",
                                       needs=("Which collection did you mean?",))


def test_A_FINISHED_RUN_IS_WAITING_ON_NOTHING() -> None:
    agent = _chat(_asks("Which collection did you mean?"), AIMessage(content="The resort collection."))
    asyncio.run(start_run(agent, _context(), message="compare them", run_id="done"))

    resumed = asyncio.run(resume_run(agent, _context(), answer="resort", run_id="done"))
    found = asyncio.run(read_run(agent, _context(), run_id="done"))

    assert resumed.waiting_on is None
    assert found is not None and found.waiting_on is None


# --- a moved view ------------------------------------------------------------------------------------------


def test_A_VIEW_TO_CONFIRM_IS_READ_FROM_THE_RUN() -> None:
    agent = create_deep_agent(model=scripted(*[AIMessage(content="ok")] * 4), tools=[], middleware=[ConfirmTheView()],
                              context_schema=FabricContext, checkpointer=InMemorySaver())
    asyncio.run(start_run(agent, _context(), message="what matched?", run_id="view",
                          continuation=FabricView(swatch="cotton-poplin-120gsm")))

    moved = asyncio.run(start_run(agent, _context(), message="what about this one?", run_id="view",
                                  continuation=FabricView(swatch="linen-slub-180gsm")))

    assert moved.waiting_on is not None
    assert moved.waiting_on.kind == "confirm_view"
    assert moved.waiting_on.needs == (moved.waiting_on.question,)


# --- a worker's need, asked inside the chat-manager's dispatch ---------------------------------------------


def test_A_WORKERS_NEED_IS_READ_FROM_THE_RUN_THOUGH_IT_PAUSED_INSIDE_A_DISPATCH() -> None:
    agent = _blocked_worker(SEASON)

    run = asyncio.run(start_run(agent, _a_context(), message=QUESTION, run_id="need"))
    found = asyncio.run(read_run(agent, _a_context(), run_id="need"))

    assert run.waiting_on == Pending(kind="batched_needs", question=SEASON, needs=(SEASON,))
    assert found is not None and found.waiting_on == run.waiting_on


def test_SEVERAL_WORKERS_NEEDS_KEEP_THEIR_NEEDS_SEPARATE() -> None:
    """A form renders `needs` as fields; a chat shows the one numbered `question`."""
    agent = _blocked_worker(SEASON, MILL)

    run = asyncio.run(start_run(agent, _a_context(), message=QUESTION, run_id="needs"))

    assert run.waiting_on is not None
    assert run.waiting_on.kind == "batched_needs"
    # The workers run concurrently, so which need comes back first is theirs to decide.
    assert set(run.waiting_on.needs) == {SEASON, MILL}
    assert SEASON in run.waiting_on.question and MILL in run.waiting_on.question


# --- two pauses at once -----------------------------------------------------------------------------------


def test_TWO_QUESTIONS_IN_ONE_REPLY_ARE_ONE_PENDING_ASKED_ONCE() -> None:
    agent = _chat(_asks(SEASON, MILL), AIMessage(content="done"))

    run = asyncio.run(start_run(agent, _context(), message="go", run_id="two"))

    assert run.waiting_on is not None
    assert run.waiting_on.kind == "batched_needs"
    assert run.waiting_on.needs == (SEASON, MILL)


def test_ONE_ANSWER_RESUMES_TWO_QUESTIONS() -> None:
    """Before PS-83 this resume raised: LangGraph needs each pending interrupt's id when there are several."""
    agent = _chat(_asks(SEASON, MILL), AIMessage(content="done"))
    asyncio.run(start_run(agent, _context(), message="go", run_id="both"))

    resumed = asyncio.run(resume_run(agent, _context(), answer="SS26, Ludhiana", run_id="both"))

    answered = [m for m in resumed.state["messages"] if isinstance(m, ToolMessage) and m.name == "ask_the_user"]
    assert [str(m.content) for m in answered] == ["SS26, Ludhiana", "SS26, Ludhiana"]
    assert resumed.waiting_on is None


# --- the stream -------------------------------------------------------------------------------------------


def test_THE_STREAM_SAYS_WHAT_THE_RUN_STOPPED_FOR() -> None:
    agent = _chat(_asks("Which collection did you mean?"), AIMessage(content="unused"), streams=True)

    async def events() -> list[RunEvent]:
        return [event async for _, namespace, mode, chunk in stream_run(agent, _context(), message="go")
                for event in translate(namespace, mode, chunk)]

    waiting = [e for e in asyncio.run(events()) if e.name == "waiting"]

    assert [e.data for e in waiting] == [{"kind": "ask_the_user", "question": "Which collection did you mean?",
                                          "needs": ["Which collection did you mean?"]}]


def test_A_PAUSE_INSIDE_A_DISPATCH_IS_TOLD_ONCE() -> None:
    """LangGraph streams a subagent's interrupt in the subagent's namespace and again at the top: only the top one
    is the run's (measured on the fabric run that pauses inside its dispatch)."""
    paused = (Interrupt(value={"question": SEASON, "kind": "batched_needs", "needs": [SEASON]}, id="i1"),)

    inside = translate(("tools:call_1",), "updates", {"__interrupt__": paused})
    top = translate((), "updates", {"__interrupt__": paused})

    assert inside == []
    assert [e.name for e in top] == ["waiting"]
