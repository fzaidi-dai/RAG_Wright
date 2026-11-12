"""One model turn must finish, or be abandoned.

Ported from RuleWright under ADR-0139's handover (PS-48b); the substrate is `rag_wright.harness.errors`
(`error_middleware`, `ADeadlineForOneModelTurn`, `ModelAttemptTooSlow`, `ModelTurnTooSlow`) and the clocks in
`rag_wright.harness.models`. Driven through the fabric product's own retry set.

**A socket timeout is not a wall clock.** `httpx.Timeout(60.0)` bounds the idle gap between bytes, so a provider
that keeps a connection warm while it thinks defeats it, and a live run sat 445 seconds on one model turn with
the timeout correctly applied. **And two retry layers multiplied whatever ceiling existed.** So the deadline is
explicit, wall clock, and sits OUTSIDE the retry middleware where it bounds the whole sequence; a second,
per-attempt clock sits inside it. The arithmetic between them is asserted here, because it is the safety
property.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage

from rag_wright.harness.errors import ModelAttemptTooSlow, ModelTurnTooSlow, error_middleware
from rag_wright.harness.models import (
    MODEL_ATTEMPT_DEADLINE_S,
    MODEL_REQUEST_TIMEOUT_S,
    MODEL_RETRIES,
    MODEL_TURN_DEADLINE_S,
)
from tests.harness.fabric import _fabric, fails_transiently, never_answers, scripted


def _stack() -> list[Any]:
    definition = _fabric()
    return error_middleware(retry_on=definition.retry_on, message_for=definition.message_for)


def _agent(model: Any) -> Any:
    return create_agent(model, tools=[], system_prompt="s", middleware=_stack())


GO = {"messages": [{"role": "user", "content": "go"}]}


# --- the arithmetic ------------------------------------------------------------------------------------


def test_THE_DEADLINE_DOES_NOT_SILENTLY_PRE_EMPT_THE_RETRY_POLICY() -> None:
    """**A ceiling below the retry budget turns retries off without saying so.** Both numbers are tuned
    independently and nothing else connects them."""
    from rag_wright.harness.errors import MODEL_ATTEMPTS, RETRY_BACKOFF_BUDGET_S

    needed = MODEL_ATTEMPT_DEADLINE_S * MODEL_ATTEMPTS + RETRY_BACKOFF_BUDGET_S

    assert MODEL_TURN_DEADLINE_S >= needed, (
        f"the {MODEL_TURN_DEADLINE_S}s turn deadline cuts off a retry budget that needs {needed}s "
        f"({MODEL_ATTEMPTS} attempts of {MODEL_REQUEST_TIMEOUT_S}s plus backoff). Raise the deadline or lower the "
        "attempts -- do not leave retries configured that can never run."
    )


def test_THE_SDK_DOES_NOT_RETRY_BEHIND_THE_MIDDLEWARES_BACK() -> None:
    """**One retry layer, not two.** The SDK's own `max_retries` is invisible to `ModelRetryMiddleware` and
    multiplies with it."""
    assert MODEL_RETRIES == 0, (
        f"the SDK is retrying {MODEL_RETRIES} time(s) inside each middleware attempt. Retries belong to "
        "ModelRetryMiddleware, which is configured and observable."
    )


def test_THE_PER_ATTEMPT_BREACH_IS_RETRYABLE_AND_THE_WHOLE_TURN_IS_NOT() -> None:
    """**Two ceilings, opposite retry semantics, and they may not share an exception type.**

    A slow ATTEMPT is transient: abandoning it and trying again is usually faster than waiting. A breached TURN
    escapes, and to a parent it is a failed `task` tool call -- a retry there re-runs the worker's whole job.
    They shared one type once, and a live run recorded SEVEN identical dispatches of one worker.
    """
    assert not issubclass(ModelTurnTooSlow, ModelAttemptTooSlow), (
        "a breached TURN is an attempt breach by inheritance, so any product retrying attempts re-runs workers"
    )
    assert not issubclass(ModelAttemptTooSlow, ModelTurnTooSlow)

    stack = _stack()
    names = [getattr(m, "name", "") for m in stack]
    built = [type(m).__name__ for m in stack]
    retry_at = built.index("ModelRetryMiddleware")
    whole = next(i for i, n in enumerate(names) if "whole turn" in n)
    attempt = next(i for i, n in enumerate(names) if "one attempt" in n)

    assert whole < retry_at < attempt, f"the two clocks are not either side of the retry middleware: {names}"


def test_THE_ATTEMPT_CLOCK_IS_CALIBRATED_AGAINST_THE_COMPILED_PATH() -> None:
    """**The lesson is the population, not the number.** A 40s attempt clock calibrated on raw call latencies
    abandoned nearly every compiled-worker call (127-201 s per run) and retried into the turn ceiling. What can
    be asserted is that the budget fits inside the ceiling it sits in."""
    from rag_wright.harness.errors import MODEL_ATTEMPTS, RETRY_BACKOFF_BUDGET_S

    budget = MODEL_ATTEMPT_DEADLINE_S * MODEL_ATTEMPTS + RETRY_BACKOFF_BUDGET_S

    assert budget <= MODEL_TURN_DEADLINE_S, (
        f"{MODEL_ATTEMPTS} attempts of {MODEL_ATTEMPT_DEADLINE_S}s plus backoff is {budget}s, past the "
        f"{MODEL_TURN_DEADLINE_S}s turn ceiling -- the last attempt could never finish"
    )
    assert MODEL_ATTEMPTS >= 2, "a single attempt is not a retry policy"


# --- driven --------------------------------------------------------------------------------------------


async def test_A_MODEL_THAT_NEVER_ANSWERS_IS_ABANDONED(monkeypatch: Any) -> None:
    """**The live failure, reproduced in under a second.** No read timeout can fire on a provider that holds the
    connection and never replies, so only a wall clock ends it."""
    monkeypatch.setattr("rag_wright.harness.models.MODEL_TURN_DEADLINE_S", 0.5)

    began = time.monotonic()
    with pytest.raises(ModelTurnTooSlow):
        await _agent(never_answers()).ainvoke(GO)
    elapsed = time.monotonic() - began

    assert elapsed < 5.0, f"the deadline took {elapsed:.1f}s to fire; it is not bounding anything"


async def test_THE_DEADLINE_BOUNDS_THE_WHOLE_RETRY_SEQUENCE_NOT_EACH_ATTEMPT(monkeypatch: Any) -> None:
    """**Placement, driven rather than asserted about.** Inside `ModelRetryMiddleware` the deadline would bound
    each attempt, so the ceiling would silently be triple what it says. This fails on the wrong placement,
    which reading the list cannot."""
    monkeypatch.setattr("rag_wright.harness.models.MODEL_TURN_DEADLINE_S", 1.0)

    attempts: list = []
    began = time.monotonic()
    with pytest.raises(ModelTurnTooSlow):
        await _agent(fails_transiently(attempts)).ainvoke(GO)
    elapsed = time.monotonic() - began

    assert attempts, "the model was never called; this proves nothing about the deadline's placement"
    assert elapsed < 2.0, (
        f"{len(attempts)} attempts took {elapsed:.1f}s against a 1.0s deadline -- the deadline is inside the "
        "retry middleware, so every attempt gets its own"
    )


async def test_A_PROMPT_ANSWER_IS_LEFT_ALONE() -> None:
    """A ceiling that fires on healthy work teaches people to raise it without reading it."""
    state = await _agent(scripted(AIMessage(content="done"))).ainvoke(GO)

    assert state["messages"][-1].content == "done"


def test_A_SYNCHRONOUS_CALLER_STILL_WORKS() -> None:
    """**The regression adding this deadline caused, guarded so it cannot return.** A middleware that defines
    only `awrap_model_call` makes `invoke()` raise `NotImplementedError` from inside the model node. The deadline
    cannot be enforced on the sync path -- a blocking call cannot be abandoned from its own thread -- so the
    sync hook passes through, and this asserts that it works."""
    state = _agent(scripted(AIMessage(content="done"))).invoke(GO)

    assert state["messages"][-1].content == "done"


async def test_A_SLOW_ATTEMPT_IS_RETRIED_RATHER_THAN_LOSING_THE_TURN(monkeypatch: Any) -> None:
    """**The whole point of the per-attempt clock, driven.** A live run spent its outer ceiling on one turn and
    discarded three completed worker reports, having never retried, because the only deadline sat outside the
    retry layer. Here the first attempt hangs and the second answers; the turn must succeed."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    monkeypatch.setattr("rag_wright.harness.models.MODEL_ATTEMPT_DEADLINE_S", 0.3)
    monkeypatch.setattr("rag_wright.harness.models.MODEL_TURN_DEADLINE_S", 30.0)

    attempts: list = []

    class SlowThenFine(GenericFakeChatModel):
        def bind_tools(self, tools: Any, **kwargs: Any):
            return self

        async def _agenerate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any):
            attempts.append(1)
            if len(attempts) == 1:
                await asyncio.sleep(30)
            return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    state = await _agent(SlowThenFine(messages=iter([AIMessage(content="answered")]))).ainvoke(GO)

    assert len(attempts) >= 2, f"the slow attempt was not retried ({len(attempts)} attempt(s)); the turn was lost"
    assert state["messages"][-1].content == "answered", "the retry ran but its answer was discarded"


async def test_A_TURN_THAT_NEVER_ANSWERED_DOES_NOT_LOOK_LIKE_ONE_THAT_DID(monkeypatch: Any) -> None:
    """**`on_failure="error"`, and this is what pays for it.** `ModelRetryMiddleware` defaults to returning an
    `AIMessage` carrying the error when its retries are exhausted, so a turn that never answered looks like one
    that did. The outer ceiling is generous here, so only the retry layer giving up can end the turn, and it
    must raise -- as the attempt breach it is."""
    monkeypatch.setattr("rag_wright.harness.models.MODEL_ATTEMPT_DEADLINE_S", 0.3)
    monkeypatch.setattr("rag_wright.harness.models.MODEL_TURN_DEADLINE_S", 60.0)

    began = time.monotonic()
    with pytest.raises(ModelAttemptTooSlow):
        await _agent(never_answers()).ainvoke(GO)
    elapsed = time.monotonic() - began

    assert elapsed < 30.0, (
        f"the turn took {elapsed:.1f}s against a 0.3s per-attempt clock; it is the OUTER ceiling ending this, not "
        "the retry layer, so this guard is not testing what it claims"
    )


async def test_A_BREACHED_TURN_DOES_NOT_RE_RUN_THE_WHOLE_DISPATCH(monkeypatch: Any) -> None:
    """**One `task` call became three whole worker runs, measured live.** The whole-turn breach escapes the
    worker and arrives at the parent as a failed `task` call; if its type were retryable, the parent's
    `ToolRetryMiddleware` would re-run the worker's entire job. It must be terminal."""
    from deepagents import create_deep_agent
    from langchain.agents.middleware.types import AgentMiddleware

    monkeypatch.setattr("rag_wright.harness.models.MODEL_ATTEMPT_DEADLINE_S", 0.2)
    monkeypatch.setattr("rag_wright.harness.models.MODEL_TURN_DEADLINE_S", 0.8)

    dispatches: list = []

    class CountDispatch(AgentMiddleware):
        @property
        def name(self) -> str:
            return "CountDispatch"

        async def abefore_agent(self, state: Any, runtime: Any) -> None:
            dispatches.append(1)

    worker = {"name": "w", "description": "does a thing", "system_prompt": "s", "model": never_answers(),
              "tools": [], "middleware": [CountDispatch(), *_stack()]}
    parent = create_deep_agent(
        model=scripted(
            AIMessage(content="", tool_calls=[{"name": "task", "id": "t1", "type": "tool_call",
                                               "args": {"description": "go", "subagent_type": "w"}}]),
            AIMessage(content="done")),
        tools=[], system_prompt="orchestrate", middleware=_stack(), subagents=[worker])

    with contextlib.suppress(Exception):
        await asyncio.wait_for(parent.ainvoke(GO), timeout=90)

    assert len(dispatches) == 1, (
        f"one `task` call re-ran the worker {len(dispatches)} times. A whole-turn breach must be terminal: "
        "retrying it repeats every tool call and every token the worker already spent."
    )


async def test_AN_ATTEMPT_BREACH_ESCAPES_AS_ITSELF_NOT_AS_A_TURN_BREACH(monkeypatch: Any) -> None:
    """**The static split above is worthless if the runtime relabels one as the other.** `ModelTooSlow`
    subclasses `TimeoutError`, and the outer clock catches `TimeoutError` -- so an attempt breach escaping the
    retry middleware was once reissued as a turn breach, naming a bound that was never reached."""
    monkeypatch.setattr("rag_wright.harness.models.MODEL_ATTEMPT_DEADLINE_S", 0.05)
    monkeypatch.setattr("rag_wright.harness.models.MODEL_TURN_DEADLINE_S", 30.0)

    with pytest.raises(ModelAttemptTooSlow) as breach:
        await _agent(never_answers()).ainvoke(GO)

    assert "whole turn" not in str(breach.value), f"the attempt breach is reported as a turn breach: {breach.value}"


async def test_A_REAL_TURN_BREACH_STILL_REPORTS_ITSELF(monkeypatch: Any) -> None:
    """**The negative half.** A fix that stopped relabelling by never relabelling would leave the whole-turn
    clock unable to name itself -- and it is the backstop the retry sequence cannot be retried past."""
    monkeypatch.setattr("rag_wright.harness.models.MODEL_ATTEMPT_DEADLINE_S", 30.0)
    monkeypatch.setattr("rag_wright.harness.models.MODEL_TURN_DEADLINE_S", 0.2)

    with pytest.raises(ModelTurnTooSlow) as breach:
        await _agent(never_answers()).ainvoke(GO)

    assert "whole turn" in str(breach.value), f"the turn clock no longer says which bound fired: {breach.value}"
