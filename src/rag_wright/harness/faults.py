"""Faults a product's evals can cause on demand.

**A failure observed once, live, cannot be regression-tested**, so "is it fixed?" gets answered by re-running an
eval and hoping the provider misbehaves in the right place. Measured on the first product: the same routing case
took 8.0 s and 92.9 s on identical code, and five paid runs went into isolating one prompt sentence. An instrument
that produces five outcomes in five runs cannot confirm or refute anything.

**What this does, and what it deliberately does not.** It causes the CONDITION deterministically -- a slow
return, a failure, an empty response, a truncated one, a slow or failing tool. **It cannot make a model take a
wrong DECISION.** That division is the design: the injector creates the condition, and the decision ledger
(`decisions.WhatTheRunDecided`) records what the tier then chose. "A slow-but-correct return is indistinguishable
from a poor one, so the orchestrator re-asks" is exactly that pair: this module causes the first half, the ledger
observes the second.

**Addressed by ORDINAL, not by clock or sampling.** The n-th model call of a run, or a tool by name. A fault that
landed somewhere different each run would reproduce nothing. `probability` exists for the cases where a rate is
the thing under test, and it draws from a **seeded** generator, so "it failed on the third call" is a fact rather
than an anecdote.

**Never installed by the engine.** It ships with the harness so every product's evals use one injector rather
than each writing its own; no tier builder adds it, and a guard asserts that nothing under `rag_wright`
constructs one. A product adds it to the middleware of an agent its eval builds (`build_orchestrator`'s
`extra_middleware=`), and never to the agents it serves.

Ported from the first product's eval tooling under ADR-0139's handover (PS-48h).
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelResponse
from langchain_core.messages import AIMessage

from rag_wright.harness.seen import run_key


@dataclass(frozen=True)
class Fault:
    """One fault, aimed at an occurrence rather than at a moment.

    At most one of `nth_model_call` or `tool` addresses it; with neither it fires on every model call. The
    effects compose, so a call can be both delayed and emptied -- which is how a re-ask of a model that had just
    timed out is built.
    """

    #: The n-th model call of the run, 1-based, counted per run.
    nth_model_call: int | None = None
    #: A tool by name. A slow return and incomplete work both happen at tools as well as at the model.
    tool: str | None = None

    #: Seconds to wait before letting the call through. This is how a bound is made to fire.
    delay_s: float = 0.0
    #: Raise this instead of returning: a real failure, to tell apart from a bad answer.
    raises: type[BaseException] | None = None
    #: Return a response with empty content -- measured live when reasoning was on.
    empty: bool = False
    #: Cut the response's content to this many characters: a capped or cut-off generation.
    truncate_to: int | None = None

    #: Fire only this often, drawn from the injector's SEEDED generator. 1.0 means always, which is the default
    #: because a deterministic fault is the useful one; a rate is for when the rate is the subject.
    probability: float = 1.0

    def aimed_at_model(self, nth: int) -> bool:
        return self.nth_model_call is not None and self.nth_model_call == nth

    def aimed_at_tool(self, name: str) -> bool:
        return self.tool is not None and self.tool == name

    @property
    def unaimed(self) -> bool:
        """A fault with no address fires on every model call, which is what a rate-only fault wants."""
        return self.nth_model_call is None and self.tool is None


class InjectFaults(AgentMiddleware):
    """Cause the named faults, and record where they fired.

    **`fired` is the evidence half.** A test that only observes the outcome cannot tell "the fault landed on the
    second call" from "the fault landed everywhere and the second call was the one that mattered".
    """

    def __init__(self, *faults: Fault, seed: int = 0) -> None:
        super().__init__()
        self._faults = faults
        self._seed = seed
        self._dice = random.Random(seed)
        self._calls: dict[str, int] = {}
        #: Ordinals of the model calls a fault fired on, in order.
        self.fired: list[int] = []

    @property
    def name(self) -> str:
        return f"{type(self).__name__}(seed={self._seed})"

    # --- the model seam ---------------------------------------------------------------------------

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        nth = self._count(request)
        fault = self._for_model(nth)
        if fault is None:
            return handler(request)
        self.fired.append(nth)
        if fault.delay_s:
            time.sleep(fault.delay_s)
        if fault.raises is not None:
            raise fault.raises(f"injected at model call {nth}")
        return self._spoiled(handler(request), fault)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        nth = self._count(request)
        fault = self._for_model(nth)
        if fault is None:
            return await handler(request)
        self.fired.append(nth)
        if fault.delay_s:
            # **`asyncio.sleep`, not `time.sleep`.** A blocking sleep would stall the event loop and the deadline
            # middleware could not pre-empt it -- so a test aimed at a bound would measure the loop being blocked
            # instead of the bound firing.
            await asyncio.sleep(fault.delay_s)
        if fault.raises is not None:
            raise fault.raises(f"injected at model call {nth}")
        return self._spoiled(await handler(request), fault)

    # --- the tool seam ----------------------------------------------------------------------------

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        fault = self._for_tool(request)
        if fault is None:
            return handler(request)
        if fault.delay_s:
            time.sleep(fault.delay_s)
        if fault.raises is not None:
            raise fault.raises(f"injected at tool {self._tool_name(request)}")
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        fault = self._for_tool(request)
        if fault is None:
            return await handler(request)
        if fault.delay_s:
            await asyncio.sleep(fault.delay_s)
        if fault.raises is not None:
            raise fault.raises(f"injected at tool {self._tool_name(request)}")
        return await handler(request)

    # --- choosing -----------------------------------------------------------------------------------

    def _count(self, request: Any) -> int:
        """This call's ordinal within the run. Keyed by `run_key`, the definition of *a run* the call record and
        the decision ledger already use, so one run's ordinals never continue into another."""
        key = run_key(request)
        self._calls[key] = self._calls.get(key, 0) + 1
        return self._calls[key]

    def _for_model(self, nth: int) -> Fault | None:
        for fault in self._faults:
            if fault.tool is not None:
                continue
            if (fault.aimed_at_model(nth) or fault.unaimed) and self._drawn(fault):
                return fault
        return None

    def _for_tool(self, request: Any) -> Fault | None:
        name = self._tool_name(request)
        for fault in self._faults:
            if fault.aimed_at_tool(name) and self._drawn(fault):
                return fault
        return None

    def _drawn(self, fault: Fault) -> bool:
        """**The die is rolled only for a probabilistic fault.** Rolling for a certain one would consume the
        seeded sequence and make an unrelated fault's pattern depend on it."""
        if fault.probability >= 1.0:
            return True
        return self._dice.random() < fault.probability

    @staticmethod
    def _tool_name(request: Any) -> str:
        call = getattr(request, "tool_call", None) or {}
        return str(call.get("name", ""))

    @staticmethod
    def _spoiled(response: Any, fault: Fault) -> Any:
        """The response, emptied or truncated. Returned unchanged when neither was asked for."""
        if not (fault.empty or fault.truncate_to is not None):
            return response
        messages = list(getattr(response, "result", None) or [])
        if not messages:
            return response
        last = messages[-1]
        content = "" if fault.empty else str(getattr(last, "content", ""))[: fault.truncate_to]
        messages[-1] = AIMessage(content=content, tool_calls=list(getattr(last, "tool_calls", None) or []))
        return ModelResponse(result=messages, structured_response=getattr(response, "structured_response", None))
