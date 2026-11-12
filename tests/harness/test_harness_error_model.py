"""Which failures a model is told about, which are retried, and which halt the run: the wiring.

Ported from RuleWright under ADR-0139's handover (PS-48b); the substrate is `rag_wright.harness.errors.error_middleware`,
driven with the fabric product's own sets. RuleWright's tests of its own taxonomy (which of its exceptions are
recoverable, which transient, which never recoverable) stay in RuleWright: the sets are the product's, the wiring is
the harness's.

**What the product names is all that is handled.** `message_for` returns a message for a failure the model can act
on and `None` for everything else, which propagates and halts the run; `retry_on` names what is re-attempted, never
`Exception`, because the library's default would re-attempt a scope violation with backoff.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool as make_tool

from rag_wright.harness.errors import error_middleware
from rag_wright.harness.memory import MemoryWithoutTenancy
from tests.harness.fabric import MillIsOffline, UnknownComposition, _fabric, scripted


def _stack(**overrides: Any) -> list[Any]:
    definition = _fabric()
    return error_middleware(**{"retry_on": definition.retry_on, "message_for": definition.message_for, **overrides})


def _tool_errors(stack: list[Any]) -> Any:
    return next(m for m in stack if type(m).__name__ == "ToolErrorMiddleware")


def _call(name: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "1", "type": "tool_call"}])


async def test_A_TRANSIENT_TOOL_FAILURE_IS_ACTUALLY_RETRIED_THROUGH_THE_REAL_STACK() -> None:
    """**Driven, because a positional assertion proves nothing about behaviour.** A mill feed offline twice
    then fine: the run must get the real answer, not an error message."""
    from langchain.agents import create_agent

    attempts: list = []

    @make_tool
    def flaky() -> str:
        """Offline twice, then fine."""
        attempts.append(1)
        if len(attempts) < 3:
            raise MillIsOffline("the mill feed went quiet")
        return "the real answer"

    agent = create_agent(scripted(_call("flaky"), AIMessage(content="done")),
                         tools=[flaky], system_prompt="s", middleware=_stack())

    state = await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]})
    answered = [m for m in state["messages"] if getattr(m, "name", "") == "flaky"]

    assert len(attempts) == 3, f"the transient failure was not retried: {len(attempts)} attempt(s)"
    assert answered and "the real answer" in str(answered[-1].content), (
        f"the retry ran but the model got an error instead of the answer: {answered}"
    )


def test_the_error_middleware_uses_THE_PRODUCTS_policy() -> None:
    """A `ToolErrorMiddleware` wired to anything else -- or to a lambda returning `str(e)` -- would convert every
    exception, including the ones that must halt the run. **Asserted on behaviour, not identity**: the library
    calls the handler with `(exc, request)` and the taxonomy takes one argument, so an adapter sits between."""
    definition = _fabric()
    tool_errors = _tool_errors(_stack())

    recoverable = UnknownComposition("no such composition; try: cotton-poplin-120gsm")
    assert tool_errors.on_error(recoverable, object()) == definition.message_for(recoverable), (
        "the middleware is wired to something other than the product's taxonomy"
    )
    assert tool_errors.on_error(MemoryWithoutTenancy("leak"), object()) is None, (
        "a boundary failure is being converted to a message the model can reason around"
    )


def test_the_run_is_CAPPED() -> None:
    """A cap is not a judgement about the work, it is a bound on the bill -- and `exit_behavior="end"` stops
    with what the run has rather than raising, so a partial answer still reaches the user."""
    from rag_wright.harness.errors import MODEL_CALLS_PER_RUN, TOOL_CALLS_PER_RUN

    names = {type(m).__name__ for m in _stack()}

    assert {"ModelCallLimitMiddleware", "ToolCallLimitMiddleware"} <= names
    assert 0 < MODEL_CALLS_PER_RUN < 100
    assert 0 < TOOL_CALLS_PER_RUN < 200


@pytest.mark.parametrize("error", [MemoryWithoutTenancy("no scope"), RuntimeError("a bug in the seam")],
                         ids=["boundary", "unexpected"])
def test_the_POLICY_is_what_the_middleware_would_actually_call(error: BaseException) -> None:
    """Hand the configured middleware's own handler a failure the product does not name, the way the LIBRARY
    calls it, and confirm it declines. **The arity is derived from the library**, so the day its callback grows
    an argument this fails here rather than at the first tool failure in production -- which is how a handler
    taking one argument once raised `TypeError` from inside the error handler and the taxonomy never ran."""
    import inspect

    from langchain.agents.middleware import ToolErrorMiddleware

    tool_errors = _tool_errors(_stack())
    wanted = len(inspect.signature(ToolErrorMiddleware._on_error_type()).parameters) if hasattr(
        ToolErrorMiddleware, "_on_error_type") else 2

    assert len(inspect.signature(tool_errors.on_error).parameters) == wanted, (
        "the configured handler does not take the arguments the middleware passes it"
    )
    assert tool_errors.on_error(error, object()) is None


async def test_A_RECOVERABLE_FAILURE_REACHES_THE_MODEL_AS_A_MESSAGE() -> None:
    """**Every test that calls `message_for` directly misses the WIRING**, and that gap was once total: the
    "tool failure -> the model" level was dead until an eval drove a failure through the real middleware. So
    this builds a real agent with a real failing tool and reads what the model was handed."""
    from deepagents import create_deep_agent

    @make_tool
    def match_composition(composition: str) -> str:
        """Match one composition."""
        raise UnknownComposition("no such composition; try: cotton-poplin-120gsm, linen-slub-180gsm")

    agent = create_deep_agent(
        model=scripted(_call("match_composition", composition="flibbertigibbet"),
                       AIMessage(content="I'll try linen-slub-180gsm instead.")),
        tools=[match_composition], middleware=_stack())

    state = await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]})
    results = [m for m in state["messages"] if getattr(m, "type", "") == "tool"]

    assert results, "the tool failure never became a message the model could read"
    assert "UnknownComposition" in str(results[-1].content), (
        f"the model was told {results[-1].content!r}, which is not the failure it has to recover from"
    )
    assert "linen-slub-180gsm" in str(results[-1].content), (
        "the detail was stripped, so the model cannot pick a real composition"
    )


async def test_A_BOUNDARY_FAILURE_STILL_HALTS_THE_RUN_THROUGH_THE_REAL_MIDDLEWARE() -> None:
    """**On the wiring rather than on the list.** The adapter must not convert what the product refuses to
    convert: a boundary failure has to surface, not become a message the model reasons around."""
    from deepagents import create_deep_agent

    @make_tool
    def recall(key: str) -> str:
        """Recall a note."""
        raise MemoryWithoutTenancy("this run carries no scope")

    agent = create_deep_agent(model=scripted(_call("recall", key="theirs"), AIMessage(content="never reached")),
                              tools=[recall], middleware=_stack())

    with pytest.raises(MemoryWithoutTenancy):
        await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]})


async def test_A_FAILURE_A_PRODUCT_BOTH_RETRIES_AND_EXPLAINS_IS_RETRIED() -> None:
    """**Found by this port** (PS-48b). The fabric product first told the model about its transient failure as
    well as retrying it, and the error middleware, which sits inside the retry, converted the failure to a
    message before the retry ever saw an exception: one attempt, and "try again" handed to the model. A
    product's retry setting silently did nothing. Retry wins: a failure the product names in `retry_on` is
    never converted, so it reaches the retry middleware."""
    from langchain.agents import create_agent

    attempts: list = []

    @make_tool
    def flaky() -> str:
        """Offline twice, then fine."""
        attempts.append(1)
        if len(attempts) < 3:
            raise MillIsOffline("the mill feed went quiet")
        return "the real answer"

    def says_everything(error: BaseException) -> str | None:
        return f"{type(error).__name__}: {error}. Try again."

    agent = create_agent(scripted(_call("flaky"), AIMessage(content="done")), tools=[flaky], system_prompt="s",
                         middleware=_stack(message_for=says_everything))

    state = await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]})
    answered = [m for m in state["messages"] if getattr(m, "name", "") == "flaky"]

    assert len(attempts) == 3, f"a failure named in retry_on was explained instead of retried: {len(attempts)}"
    assert "the real answer" in str(answered[-1].content)
