"""What a run spent, tier by tier, and the run-wide budget each tier is held to (PS-80).

**A product could not see a worker's turns.** `run.state` holds the chat-manager's conversation only, so "how many
calls did that tier take?" was answerable from a trace after the fact and from nothing a test can read. The tally here
is kept per run, keyed by `run_key` as the decision ledger is, and counts each tier: how often it was started, its
model and tool calls, its tokens, and whether the run's budget stopped it.

**The per-invocation caps bound one tier's one invocation, not the run** (`errors.MODEL_CALLS_PER_INVOCATION`): each
dispatch starts afresh, so a run with several dispatches had no bound on its bill at all. `RunBudget` is that bound,
and each tier keeps back a reserve for the tiers above it, so a worker that spends all it may never leaves the
orchestrator and the chat-manager without the calls to compose.

**Innermost on every tier**, so it counts the calls that reached the model and the tools that ran: a turn the router
settled without a model call, or a repeat served from the record, costs nothing and is not counted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AIMessage

from rag_wright.harness.definition import RunBudget
from rag_wright.harness.seen import run_key

#: How deep each kind of tier sits, which is how many tiers above it keep a reserve.
CHAT_MANAGER, ORCHESTRATOR, WORKER = 0, 1, 2


@dataclass
class TierSpend:
    """What one tier spent in one run, every invocation of it together."""

    #: Times the tier was started: the chat-manager per run, the orchestrator and a worker per dispatch.
    invocations: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    #: Whether the run's budget stopped this tier (its model calls ended, or its tools were taken away).
    stopped_by_budget: bool = False
    #: The workers (or, from the chat-manager, the orchestrator) this tier dispatched, by name, in order (PS-86).
    dispatched: list[str] = field(default_factory=list)
    #: The dispatches its bound refused, by name: each never ran.
    refused: list[str] = field(default_factory=list)


@dataclass
class RunSpend:
    """One run's spend, by tier name: `chat-manager`, `orchestrator`, or a worker's own name."""

    tiers: dict[str, TierSpend] = field(default_factory=dict)

    def of(self, tier: str) -> TierSpend:
        return self.tiers.setdefault(tier, TierSpend())

    @property
    def model_calls(self) -> int:
        return sum(tier.model_calls for tier in self.tiers.values())

    @property
    def tool_calls(self) -> int:
        return sum(tier.tool_calls for tier in self.tiers.values())


@dataclass
class WhatTheRunSpent:
    """One tally per run, and one object that holds them all: passed to `build_chat_agent(spent=)` to be read, as the
    decision ledger is. Keyed by `run_key`, so `spent.of(thread_for(context, run.run_id))` is a run's."""

    runs: dict[str, RunSpend] = field(default_factory=dict)

    def of(self, key: str) -> RunSpend:
        return self.runs.setdefault(key, RunSpend())


def _thread(runtime: Any) -> str:
    """The run a `before_agent` hook belongs to: the same thread `run_key` reads off a model request."""
    return str(getattr(getattr(runtime, "execution_info", None), "thread_id", None) or "")


class SpendTheRunBudget(AgentMiddleware):
    """Count this tier's spend into the run's tally, and hold it to the run's budget less its reserve.

    Past its share of model calls a tier's next call is not made: it ends with a message saying so. Past its share
    of tool calls it is called with no tools, so it answers from what it has.
    """

    def __init__(self, spent: WhatTheRunSpent, budget: RunBudget, *, tier: str, depth: int) -> None:
        super().__init__()
        self._spent = spent
        self._tier = tier
        self._model_calls = budget.model_calls - depth * budget.reserve
        self._tool_calls = budget.tool_calls - depth * budget.reserve

    @property
    def name(self) -> str:
        """Distinct per tier, as `create_agent` refuses two middlewares sharing a name."""
        return f"{type(self).__name__}({self._tier})"

    def before_agent(self, state: Any, runtime: Any) -> None:
        self._spent.of(_thread(runtime)).of(self._tier).invocations += 1

    async def abefore_agent(self, state: Any, runtime: Any) -> None:
        self.before_agent(state, runtime)

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        run, stop = self._held(request)
        if stop is not None:
            return stop
        response = handler(self._within_tools(request, run))
        self._count(run, response)
        return response

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        run, stop = self._held(request)
        if stop is not None:
            return stop
        response = await handler(self._within_tools(request, run))
        self._count(run, response)
        return response

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        self._spent.of(run_key(request)).of(self._tier).tool_calls += 1
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        self._spent.of(run_key(request)).of(self._tier).tool_calls += 1
        return await handler(request)

    def _held(self, request: Any) -> tuple[RunSpend, AIMessage | None]:
        """This run's tally, and the message the tier ends with when its share of model calls is spent."""
        run = self._spent.of(run_key(request))
        if run.model_calls < self._model_calls:
            return run, None
        run.of(self._tier).stopped_by_budget = True
        return run, AIMessage(content=(f"This run has used the {run.model_calls} model calls its budget allows "
                                       f"this tier, so it stops here with what it has."))

    def _within_tools(self, request: Any, run: RunSpend) -> Any:
        """The request, with no tools once the run's tool calls reach this tier's share."""
        if run.tool_calls < self._tool_calls or not request.tools:
            return request
        run.of(self._tier).stopped_by_budget = True
        # a worker's report is unaffected: the framework adds its tools after this, from the response format
        return request.override(tools=[], tool_choice=None)

    def _count(self, run: RunSpend, response: Any) -> None:
        here = run.of(self._tier)
        here.model_calls += 1
        for message in getattr(response, "result", None) or [response]:
            usage = getattr(message, "usage_metadata", None) or {}
            here.input_tokens += usage.get("input_tokens", 0)
            here.output_tokens += usage.get("output_tokens", 0)


def run_budget(spent: WhatTheRunSpent, definition: Any, *, tier: str, depth: int) -> SpendTheRunBudget:
    """The budget middleware a tier is built with, from the product's `AgentDefinition.budget`."""
    return SpendTheRunBudget(spent, definition.budget, tier=tier, depth=depth)


def budgeted(spec: dict[str, Any], spent: WhatTheRunSpent, budget: RunBudget) -> dict[str, Any]:
    """A worker's spec with the run's budget innermost, under the worker's own name."""
    return {**spec, "middleware": [*spec.get("middleware", []),
                                   SpendTheRunBudget(spent, budget, tier=spec["name"], depth=WORKER)]}
