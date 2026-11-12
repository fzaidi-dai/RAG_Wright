"""Faults a product's evals can cause on demand.

Ported from RuleWright's eval tooling under ADR-0139's handover (PS-48h), with the injector itself, which now lives
in the engine as `rag_wright.harness.faults`; the agents it is attached to carry the fabric product's retry set and
scope. RuleWright's check that its wheel ships no eval code stays in RuleWright; the engine's half is that nothing
in the engine constructs an injector.

**The injector causes the CONDITION -- a slow, failing, empty or truncated call -- deterministically, and cannot
make a model take a wrong DECISION.** The rows below (`A1`-`A4`, `D3`) are failures that were each seen once, live,
and are now reproducible on demand.
"""

from __future__ import annotations

import ast
import asyncio
import time
from itertools import cycle
from pathlib import Path
from typing import Any

import pytest
from deepagents import create_deep_agent
from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from rag_wright.harness.errors import MODEL_ATTEMPTS, error_middleware
from rag_wright.harness.faults import Fault, InjectFaults
from tests.harness.fabric import FabricContext, _a_context, _fabric, _fabric_message_for, scripted

ENGINE = Path(__file__).resolve().parents[2] / "src" / "rag_wright"


class TheMillFeedGaveUp(RuntimeError):
    """A failure the fabric product does not retry, so it surfaces."""


def _bounds() -> list:
    """The fabric product's error model, as its tiers carry it."""
    return [*error_middleware(retry_on=_fabric().retry_on, message_for=_fabric_message_for)]


def _turns(*faults: Fault, replies: int = 4, seed: int = 0) -> Any:
    said = [AIMessage(content=f"turn {n}") for n in range(replies)]
    agent = create_agent(scripted(*said), tools=[], system_prompt="s", middleware=[InjectFaults(*faults, seed=seed)])
    return agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config={"configurable": {"thread_id": "run-1"}})


def _calls(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


# --- addressing: the fault lands where it was aimed -----------------------------------------------------


def test_A_DELAY_LANDS_ON_THE_NTH_CALL_AND_NO_OTHER() -> None:
    """**Ordinal addressing is what makes this deterministic.** Three model calls, because a reply with no tool
    call ends the run, and a two-reply script would have left `fired` empty."""
    injector = InjectFaults(Fault(nth_model_call=2, delay_s=0.05))

    @tool
    def list_mills() -> dict:
        """Something to call, so the run takes more than one model call."""
        return {"mills": []}

    agent = create_agent(scripted(_calls("list_mills", {}, "a"), _calls("list_mills", {}, "b"),
                                  AIMessage(content="done")),
                         tools=[list_mills], system_prompt="s", middleware=[injector])
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config={"configurable": {"thread_id": "run-1"}})

    assert injector.fired == [2], f"the delay landed on calls {injector.fired}, not only the 2nd"


def test_EACH_RUN_COUNTS_ITS_OWN_CALLS() -> None:
    """Keyed by `run_key`: one run's ordinals never continue into another, or "the 1st call" would mean the 1st
    call of the process."""
    injector = InjectFaults(Fault(nth_model_call=1, empty=True))
    agent = create_agent(scripted(AIMessage(content="a"), AIMessage(content="b")), tools=[], system_prompt="s",
                         middleware=[injector])
    for thread in ("run-1", "run-2"):
        agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config={"configurable": {"thread_id": thread}})

    assert injector.fired == [1, 1]


def test_A_CHOSEN_EXCEPTION_IS_RAISED_WHERE_ASKED() -> None:
    """To test a failure apart from a bad answer, a real failure has to be causable at a chosen point."""
    with pytest.raises(TheMillFeedGaveUp):
        _turns(Fault(nth_model_call=1, raises=TheMillFeedGaveUp))


def test_AN_EMPTY_RESPONSE_IS_CAUSABLE() -> None:
    """Measured live: reasoning on returned empty content with 57 of 67 tokens spent reasoning."""
    assert str(_turns(Fault(nth_model_call=1, empty=True))["messages"][-1].content) == ""


def test_A_RESPONSE_CAN_BE_TRUNCATED() -> None:
    """A capped or cut-off generation, reproduced."""
    assert str(_turns(Fault(nth_model_call=1, truncate_to=3))["messages"][-1].content) == "tur"


def test_A_NAMED_TOOL_CAN_BE_MADE_TO_FAIL() -> None:
    """Work that does not complete happens at tools as well as at the model."""
    @tool
    def match_compositions(requirements: str) -> dict:
        """Match compositions."""
        return {"matched": ["cotton-poplin-120gsm"]}

    agent = create_agent(scripted(_calls("match_compositions", {"requirements": "poplin"}, "a"),
                                  AIMessage(content="done")),
                         tools=[match_compositions], system_prompt="s",
                         middleware=[InjectFaults(Fault(tool="match_compositions", raises=TheMillFeedGaveUp))])

    with pytest.raises(TheMillFeedGaveUp):
        agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config={"configurable": {"thread_id": "run-1"}})


def test_A_NAMED_TOOL_CAN_BE_MADE_SLOW() -> None:
    """A slow return is the other tool fault, and the one a bound is tested with."""
    @tool
    def list_mills() -> dict:
        """List the mills."""
        return {"mills": []}

    agent = create_agent(scripted(_calls("list_mills", {}, "a"), AIMessage(content="done")), tools=[list_mills],
                         system_prompt="s", middleware=[InjectFaults(Fault(tool="list_mills", delay_s=0.2))])
    started = time.monotonic()
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config={"configurable": {"thread_id": "run-1"}})

    assert time.monotonic() - started >= 0.2


def test_A_TOOL_NOBODY_AIMED_AT_IS_UNTOUCHED() -> None:
    """**The negative half.** An injector that broke everything would prove nothing about the one thing under test."""
    ran: list[str] = []

    @tool
    def list_mills() -> dict:
        """List the mills."""
        ran.append("listed")
        return {"mills": []}

    agent = create_agent(scripted(_calls("list_mills", {}, "a"), AIMessage(content="done")), tools=[list_mills],
                         system_prompt="s",
                         middleware=[InjectFaults(Fault(tool="match_compositions", raises=TheMillFeedGaveUp))])
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config={"configurable": {"thread_id": "run-1"}})

    assert ran == ["listed"]


# --- determinism, which is the whole property -----------------------------------------------------------


def _pattern(seed: int, runs: int) -> list[int]:
    injector = InjectFaults(Fault(probability=0.5, empty=True), seed=seed)
    agent = create_agent(scripted(*[AIMessage(content=f"t{n}") for n in range(runs)]), tools=[], system_prompt="s",
                         middleware=[injector])
    for turn in range(runs):
        agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config={"configurable": {"thread_id": f"run-{turn}"}})
    return injector.fired


def test_THE_SAME_SEED_GIVES_THE_SAME_FAILURES() -> None:
    """**A probabilistic fault is only useful if it is reproducible.**"""
    assert _pattern(7, 12) == _pattern(7, 12)
    assert _pattern(7, 12), "no probabilistic fault ever fired, so this proves nothing"


def test_A_DIFFERENT_SEED_GIVES_A_DIFFERENT_PATTERN() -> None:
    """**Anti-vacuity for the test above.** If the seed were ignored, both would trivially match. The patterns are
    compared by which RUNS fired, since each run counts from 1."""
    def which_runs(seed: int) -> list[int]:
        injector = InjectFaults(Fault(probability=0.5, empty=True), seed=seed)
        agent = create_agent(scripted(*[AIMessage(content=f"t{n}") for n in range(16)]), tools=[],
                             system_prompt="s", middleware=[injector])
        fired = []
        for turn in range(16):
            before = len(injector.fired)
            agent.invoke({"messages": [{"role": "user", "content": "go"}]},
                         config={"configurable": {"thread_id": f"run-{turn}"}})
            if len(injector.fired) > before:
                fired.append(turn)
        return fired

    assert which_runs(1) != which_runs(99), "the seed is not being used at all"


# --- the rows, as regression tests rather than anecdotes --------------------------------------------------


def test_A1_A_CORRECT_BUT_SLOW_RETURN_IS_ABANDONED_THE_SAME_WAY_EVERY_TIME(monkeypatch) -> None:
    """**`A1`: a slow-but-correct return is indistinguishable from a poor one.** The injector causes the condition --
    a correct answer, slow past the attempt bound -- and ten identical runs give the identical outcome.

    **The deadline is OUTSIDE the injector** (listed first), or the delay happens outside the timeout it is meant to
    trip. **Driven with `ainvoke`**: the deadline's sync hook is a deliberate pass-through."""
    monkeypatch.setattr("rag_wright.harness.models.MODEL_ATTEMPT_DEADLINE_S", 0.05)
    monkeypatch.setattr("rag_wright.harness.models.MODEL_TURN_DEADLINE_S", 0.5)

    async def one(attempt: int) -> str:
        agent = create_agent(scripted(AIMessage(content="cotton poplin is in stock at two mills")), tools=[],
                             system_prompt="s",
                             middleware=[*_bounds(), InjectFaults(Fault(nth_model_call=1, delay_s=0.3))])
        try:
            await agent.ainvoke({"messages": [{"role": "user", "content": "poplin?"}]},
                                config={"configurable": {"thread_id": f"a1-{attempt}"}})
            return "completed"
        except Exception as failed:  # noqa: BLE001 -- the TYPE is the measurement
            return type(failed).__name__

    outcomes = [asyncio.run(one(attempt)) for attempt in range(10)]

    assert len(set(outcomes)) == 1, f"ten identical runs gave {set(outcomes)}"
    assert outcomes[0] != "completed", "a correct-but-slow return was not abandoned"


def _a_worker_that_answers(reply: str) -> dict:
    """A compiled subagent that returns a correct result on every dispatch, so the run has real work to lose."""
    model = scripted()
    object.__setattr__(model, "messages", cycle([AIMessage(content=reply)]))
    return {"name": "composition_worker", "description": "matches compositions",
            "runnable": create_agent(model, tools=[], system_prompt="s")}


async def _a_parent_whose_second_call_is_slow(delay: float, answer: str, *, ceiling: float | None,
                                              thread: str) -> tuple[str, Any]:
    dispatch = _calls("task", {"description": "match the poplin", "subagent_type": "composition_worker"}, "a")
    parent = create_deep_agent(model=scripted(dispatch, AIMessage(content="compiled")), tools=[], system_prompt="s",
                               context_schema=FabricContext, subagents=[_a_worker_that_answers(answer)],
                               checkpointer=InMemorySaver(),
                               middleware=[*_bounds(), InjectFaults(Fault(nth_model_call=2, delay_s=delay))])
    config = {"configurable": {"thread_id": thread}}
    running = parent.ainvoke({"messages": [{"role": "user", "content": "poplin?"}]}, context=_a_context(),
                             config=config)
    try:
        await (asyncio.wait_for(running, timeout=ceiling) if ceiling is not None else running)
        return "completed", None
    except Exception as died:  # noqa: BLE001 -- the type IS the observation
        # **By exact type**: our own bounds subclass `TimeoutError`, so catching that would relabel them as the
        # caller's ceiling -- the very confusion the bounds' split exists to prevent.
        said = "the caller's ceiling fired" if type(died) is TimeoutError else type(died).__name__
        return said, await parent.aget_state(config)


def _persisted(state: Any) -> str:
    return str([getattr(m, "content", "") for m in (state.values or {}).get("messages", [])])


THE_ANSWER = "cotton-poplin-120gsm is stocked by mill-a and mill-b; coverage 2 of 2 mills"


def test_A2_THE_TURN_DEADLINE_ENDS_A_RUN_THAT_ALREADY_HAD_A_CORRECT_RESULT(monkeypatch) -> None:
    """**`A2`: the whole-turn deadline kills a run whose worker had already answered correctly.** The attempt bound
    must NOT fire, or this is `A3`.

    **Pins today's behaviour**: the run raises and the caller gets nothing. **And the worker's answer survives in
    the checkpoint**, so the work is unread rather than lost, and a policy for it is a read, not a store."""
    monkeypatch.setattr("rag_wright.harness.models.MODEL_ATTEMPT_DEADLINE_S", 5.0)
    monkeypatch.setattr("rag_wright.harness.models.MODEL_TURN_DEADLINE_S", 0.2)

    outcome, state = asyncio.run(_a_parent_whose_second_call_is_slow(1.0, THE_ANSWER, ceiling=None, thread="a2"))

    assert outcome == "ModelTurnTooSlow", f"the run ended as {outcome}"
    assert THE_ANSWER[:40] in _persisted(state), "the worker's correct answer is not in the checkpoint"


def _exhaust_the_retries(monkeypatch, *, attempt: float, turn: float, delay: float) -> tuple[str, list[int], Any, float]:
    """Delay EVERY model call past the attempt deadline. An unaimed fault fires on every call, so `fired` counts the
    attempts the retry middleware actually made -- the measurement, not an inference from elapsed time."""
    monkeypatch.setattr("rag_wright.harness.models.MODEL_ATTEMPT_DEADLINE_S", attempt)
    monkeypatch.setattr("rag_wright.harness.models.MODEL_TURN_DEADLINE_S", turn)
    injector = InjectFaults(Fault(delay_s=delay))

    async def run() -> tuple[str, Any, float]:
        agent = create_agent(scripted(AIMessage(content="an answer nobody will see")), tools=[], system_prompt="s",
                             context_schema=FabricContext, checkpointer=InMemorySaver(),
                             middleware=[*_bounds(), injector])
        config = {"configurable": {"thread_id": "a3"}}
        started = time.monotonic()
        try:
            await agent.ainvoke({"messages": [{"role": "user", "content": "poplin?"}]}, context=_a_context(),
                                config=config)
            outcome = "completed"
        except Exception as died:  # noqa: BLE001 -- the type IS the observation
            outcome = type(died).__name__
        return outcome, await agent.aget_state(config), time.monotonic() - started

    outcome, state, elapsed = asyncio.run(run())
    return outcome, injector.fired, state, elapsed


def test_A3_A_MODEL_THAT_TIMED_OUT_IS_RE_ASKED_UNTIL_THE_BUDGET_IS_GONE(monkeypatch) -> None:
    """**`A3`: the budget is spent re-asking a model that has just shown it will not answer in time.** Pins today's
    behaviour: every attempt is made."""
    outcome, attempts, _state, _elapsed = _exhaust_the_retries(monkeypatch, attempt=0.05, turn=30.0, delay=0.3)

    assert attempts == list(range(1, MODEL_ATTEMPTS + 1)), f"the model was asked {attempts}"
    assert outcome != "completed"


def test_D3_AN_EXHAUSTED_RETRY_RAISES_INSTEAD_OF_ANSWERING(monkeypatch) -> None:
    """**`D3`: a failure returned as message content is indistinguishable from a bad answer.** The error model sets
    the retry to raise; this measures that it does, rather than trusting a reading of the library."""
    outcome, _attempts, state, _elapsed = _exhaust_the_retries(monkeypatch, attempt=0.05, turn=30.0, delay=0.3)

    assert outcome.endswith("TooSlow"), f"the run ended as {outcome}"
    said = [str(getattr(m, "content", "")) for m in (state.values or {}).get("messages", [])]
    assert not any("too slow" in s.lower() or "deadline" in s.lower() for s in said), (
        f"the failure came back as message CONTENT: {said}"
    )


def test_A3_LEAVES_NOTHING_TO_PROCEED_WITH_UNLIKE_A2(monkeypatch) -> None:
    """`A2`'s worker result survived in the checkpoint; `A3`'s model never answered, so there is nothing to read.
    A policy that assumed every row had partial progress would report an empty gap as an answer."""
    _outcome, _attempts, state, _elapsed = _exhaust_the_retries(monkeypatch, attempt=0.05, turn=30.0, delay=0.3)

    said = [str(getattr(m, "content", "")) for m in (state.values or {}).get("messages", [])]
    assert not [s for s in said if s.strip() and "poplin?" not in s]


def test_THE_OUTER_BOUND_DOES_NOT_RELABEL_THE_INNER_ONE(monkeypatch) -> None:
    """**A defect the injector found and the fix it guards.** `ModelAttemptTooSlow` is transient and
    `ModelTurnTooSlow` terminal; an attempt breach escaping the retries used to be caught by the turn deadline and
    relabelled as a turn breach -- after 3.6 s against a 30 s turn -- inverting exactly that distinction."""
    outcome, attempts, _state, elapsed = _exhaust_the_retries(monkeypatch, attempt=0.05, turn=30.0, delay=0.3)

    assert attempts, "no attempt was made, so this proves nothing"
    assert elapsed < 30.0, "the fixture must keep the turn bound clear, or a real breach cannot be told apart"
    assert outcome == "ModelAttemptTooSlow", f"the escaping error is {outcome}"


def test_A4_AN_EXTERNAL_CEILING_LEAVES_THE_WORK_IN_THE_CHECKPOINT(monkeypatch) -> None:
    """**`A4`: a caller's ceiling cuts a working run.** Our own bounds are generous on purpose: if either fired this
    would be `A2` or `A3` under `A4`'s label."""
    monkeypatch.setattr("rag_wright.harness.models.MODEL_ATTEMPT_DEADLINE_S", 60.0)
    monkeypatch.setattr("rag_wright.harness.models.MODEL_TURN_DEADLINE_S", 190.0)

    outcome, state = asyncio.run(_a_parent_whose_second_call_is_slow(5.0, THE_ANSWER, ceiling=0.5, thread="a4"))

    assert outcome == "the caller's ceiling fired", f"the run ended as {outcome}"
    assert THE_ANSWER[:30] in _persisted(state), "the worker's result is not in the checkpoint after a cancellation"


def test_THE_ROWS_ARE_NOT_ONE_BUG_WITH_ONE_POLICY() -> None:
    """**The finding the rows above add up to**: `A2` and `A4` leave the work in the checkpoint (a read), `A3` leaves
    nothing. Referenced directly, so deleting the evidence is a `NameError` here."""
    evidence = (test_A2_THE_TURN_DEADLINE_ENDS_A_RUN_THAT_ALREADY_HAD_A_CORRECT_RESULT,
                test_A3_LEAVES_NOTHING_TO_PROCEED_WITH_UNLIKE_A2,
                test_A4_AN_EXTERNAL_CEILING_LEAVES_THE_WORK_IN_THE_CHECKPOINT)

    assert len({test.__name__ for test in evidence}) == 3


# --- never installed by the engine ------------------------------------------------------------------------


def _constructs_an_injector(source: str) -> bool:
    return any(isinstance(n, ast.Call) and (getattr(n.func, "id", None) or getattr(n.func, "attr", None)) == "InjectFaults"
               for n in ast.walk(ast.parse(source)))


def test_NOTHING_IN_THE_ENGINE_CONSTRUCTS_AN_INJECTOR() -> None:
    """**It ships, so the guarantee is that nothing installs it.** A fault injector in a product's served agent
    would fail real users' runs on purpose. It asserts it read the source, because a guard over a moved folder
    passes on nothing."""
    modules = [m for m in ENGINE.rglob("*.py") if "__pycache__" not in m.parts]
    assert any(m.name == "faults.py" for m in modules), f"the guard is not reading the engine's source: {ENGINE}"

    offenders = [m.relative_to(ENGINE).as_posix() for m in modules
                 if _constructs_an_injector(m.read_text(encoding="utf-8"))]

    assert not offenders, f"these construct a fault injector: {offenders}"


def test_THE_GUARD_CATCHES_A_PLANTED_CONSTRUCTION() -> None:
    assert _constructs_an_injector("m = [InjectFaults(Fault(delay_s=1))]\n")
    assert _constructs_an_injector("m = faults.InjectFaults()\n")
    assert not _constructs_an_injector("from rag_wright.harness.faults import InjectFaults\n")
