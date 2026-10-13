"""The error middleware a run is wrapped in (T-5a.8, AR-14, AR-19).

**Four levels, by who fixes it** (`AR-14`): transient failures retry, tool failures reach the model as a
message it can act on, missing information interrupts for a human, and anything unexpected bubbles to a
developer. Runaway loops are capped.

**The composition order is load-bearing and documented by the library**: *"This middleware does not retry.
For retries, compose with `ToolRetryMiddleware` placed **inner** and configured with `on_failure="error"`
so exceptions reach this middleware."* Placed the other way round, a tool failure would be converted to a
message before the retry ever saw it, and the transient row of `AR-14` would quietly never fire.

**The taxonomy lives in `rulewright.errors`**, not here: `UnknownSubjectClass` is in
`rulewright.workflows`, which no harness module may import (`AR-4`). Which failures are recoverable is
product knowledge; wrapping middleware around them is this module's job.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

#: **Caps, not guesses at good behaviour.** A run that has made this many model calls is looping, and the
#: cost of letting it continue is paid by the customer. `exit_behavior="end"` stops the run with what it
#: has rather than raising, so a partial answer still reaches the user.
MODEL_CALLS_PER_RUN = 25
TOOL_CALLS_PER_RUN = 40

#: How many times `ModelRetryMiddleware` tries one turn: the first attempt plus `max_retries`.
#: **Named, because the turn deadline has to be checked against it** -- a ceiling below this budget
#: would leave retries configured that can never run.
MODEL_RETRY_LIMIT = 2
MODEL_ATTEMPTS = MODEL_RETRY_LIMIT + 1

#: Room for the middleware's backoff between attempts (1s then 2s at `backoff_factor=2.0`, plus jitter).
#: 1s + 2s at `backoff_factor=2.0`, plus jitter.
RETRY_BACKOFF_BUDGET_S = 5.0


class ModelTooSlow(TimeoutError):
    """A model call passed a wall-clock ceiling. **Never raised directly** -- one of the two below is.

    There are two ceilings with OPPOSITE retry semantics, and they had one exception type between them
    until T-5a.12q. That cost a live run `compliance_worker` SEVEN identical dispatches: the whole-turn
    breach escaped the worker, arrived at the parent as a failed `task` tool call, and
    `ToolRetryMiddleware(retry_on=TRANSIENT)` re-ran the worker's entire job. Catch this base when you
    mean "some deadline fired"; never put it in a retry set.
    """


class ModelAttemptTooSlow(ModelTooSlow):
    """ONE attempt at a model call ran long. **Transient by construction.**

    It is raised inside the retry middleware and handled there: the attempt is abandoned and another is
    made. Measured, that is usually faster than waiting -- median 4.6s, p90 14.0s, one call at 124s in
    the same run. It never escapes the agent, so no enclosing retry layer ever sees it.
    """


class ModelTurnTooSlow(ModelTooSlow):
    """A whole model turn passed its ceiling, retries included. **Terminal by construction.**

    **Deliberately NOT in `TRANSIENT`, and that is the whole point of the split.** This one escapes the
    agent: to a parent it looks like a failed `task` tool call, and a retry there does not re-try a model
    call -- it re-runs the worker's entire job, repeating every tool call and every token it already
    spent. A ceiling that gets retried is not a ceiling.

    **Not recoverable either**, so it is not in the allow-list `message_for` reads: a model told "you
    were too slow" would try again, inside the very turn that was too slow.
    """


class ADeadlineForOneModelTurn(AgentMiddleware):
    """A wall-clock ceiling on one model turn, **outside** the retry middleware.

    **The ceiling that was missing, and the one that was decorative.** `MODEL_REQUEST_TIMEOUT_S` reaches
    httpx, where a *read* timeout bounds the idle gap between bytes rather than total elapsed time. A
    provider that holds the connection open while it thinks never trips it, and httpx has no
    total-elapsed option -- so the bound has to be imposed here. A live run sat 445 seconds on one
    worker's first turn with that timeout correctly configured and correctly applied.

    **Outside the retry middleware, and that placement is the point.** Listed inside, it would bound
    each attempt, so three retries would take three deadlines and the real ceiling would silently be
    triple the one written down -- the same multiplication that made the socket timeout meaningless.
    `error_middleware` lists it first, and first is outermost (measured, not assumed).

    **What it cannot do, said plainly.** Cancellation lands at an await point, so this bounds a call that
    is genuinely suspended -- which is the observed failure, a task parked in `_agenerate_with_cache`.
    It cannot pre-empt a blocking read inside a synchronous step; that is what the socket timeout is for,
    and the two are not interchangeable. Both are needed and neither is sufficient.

    **The synchronous path is a pass-through, and it still has to exist.** There is no way to interrupt a
    blocking call from the same thread, so a sync `wrap_model_call` cannot enforce this and one that
    pretended to would read like a guarantee. But it cannot simply be omitted either: a middleware that
    defines only the async hook makes `invoke()` raise `NotImplementedError` from inside the model node --
    *"Synchronous implementation of wrap_model_call is not available"* -- which broke every synchronous
    caller the moment this was added. So the sync hook passes through, deliberately and visibly, and
    `MODEL_REQUEST_TIMEOUT_S` remains that path's only bound. Production drives these agents with
    `ainvoke`.
    """

    def __init__(self, seconds: float, *, bounds: str, raises: type[Exception]) -> None:
        super().__init__()
        self._seconds = seconds
        self._bounds = bounds
        # **Which ceiling this is, expressed as the exception it raises.** The two have opposite retry
        # semantics and shared one type until T-5a.12q; a parent cannot tell them apart from a message.
        self._raises = raises

    @property
    def name(self) -> str:
        """**Distinct per instance, because there are two and `create_agent` refuses a name clash.**
        One bounds a single attempt from inside the retry layer; one bounds the whole turn from
        outside it."""
        return f"ADeadlineForOneModelTurn({self._bounds})"

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        """Pass through. See the class: a blocking call cannot be abandoned from its own thread."""
        return handler(request)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        try:
            async with asyncio.timeout(self._seconds) as bound:
                return await handler(request)
        except TimeoutError as expired:
            # **Relabel ONLY our own expiry** (T-5a.13c). `ModelTooSlow` subclasses `TimeoutError`, so
            # without this check the OUTER clock caught the INNER one's `ModelAttemptTooSlow` and reissued
            # it as `ModelTurnTooSlow` -- measured by the fault injector at **3.6s against a 30s turn
            # deadline**, with a message naming a bound that was never reached.
            #
            # **It inverted the very split T-5a.12q created.** The two types exist because they have
            # opposite retry semantics: an attempt breach is transient and retryable, a turn breach is
            # terminal by construction. Relabelling turned the retryable one into the terminal one, which
            # is the type that cost `compliance_worker` SEVEN identical dispatches -- and left a parent
            # tier unable to tell which bound fired.
            #
            # `asyncio.timeout`'s own `.expired()` is the discriminator: `False` when a `TimeoutError` is
            # merely passing through, `True` when this context is the one that ran out.
            if not bound.expired():
                raise
            message = (
                f"{self._bounds} passed its {self._seconds:.0f}s deadline and was abandoned. A socket "
                "timeout cannot bound this: it measures the gap between bytes, not elapsed time."
            )
            raise self._raises(message) from expired


def error_middleware(*, retry_on: tuple[type[BaseException], ...], message_for: Any) -> list[Any]:
    """The stack, inner-first.

    `ToolRetryMiddleware` carries `on_failure="error"` so a failure that exhausts its retries becomes an
    exception the error middleware can see — rather than a message the model receives without anybody
    having retried.

    **Both arguments are the PRODUCT's and are required (T-7.58).** This imported
    one product's retry set and `message_for` directly, so the substrate held that product's judgement
    about what is recoverable -- and a greenfield product retrying a mill feed got the first product's
    client exception types and its domain errors. **No defaults**, deliberately: a default
    retry set is one a second product inherits without noticing, and `retry_on` defaulting to anything is
    how `Exception` creeps back in and a scope violation gets re-attempted with backoff.

    The deadline exceptions are the HARNESS's own (above in this module), because
    `ADeadlineForOneModelTurn` is what raises them. A product chooses whether its own `retry_on` includes
    `ModelAttemptTooSlow`; ours does, and deliberately omits `ModelTurnTooSlow`.
    """
    from langchain.agents.middleware import (
        ModelCallLimitMiddleware,
        ModelRetryMiddleware,
        ToolCallLimitMiddleware,
        ToolErrorMiddleware,
        ToolRetryMiddleware,
    )

    from rag_wright.harness.models import MODEL_ATTEMPT_DEADLINE_S, MODEL_TURN_DEADLINE_S

    return [
        # **Row zero, outermost: the absolute bound.** First is outermost, so this bounds the whole
        # retry sequence rather than any one attempt of it -- the backstop that cannot be retried past.
        ADeadlineForOneModelTurn(MODEL_TURN_DEADLINE_S, bounds="the whole turn",
                                 raises=ModelTurnTooSlow),
        # Row one: the system's problem. **`retry_on` is named, never `Exception`** -- the default would
        # retry a ScopeViolation, which is re-attempting a leak with backoff.
        ToolRetryMiddleware(retry_on=retry_on, on_failure="error", max_retries=MODEL_RETRY_LIMIT),
        # **`on_failure="error"`, not the default `"continue"`.** On exhaustion the default returns an
        # AIMessage carrying the error, so a turn that never produced an answer looks like one that did.
        # It has to raise, and reach the ceiling above.
        ModelRetryMiddleware(retry_on=retry_on, max_retries=MODEL_RETRY_LIMIT, on_failure="error"),
        # **Row one-and-a-half: the per-attempt clock, INSIDE the retry above.**
        #
        # A slow attempt should be abandoned and retried, not waited out. Measured on a live
        # `three_ways` run: 50 completed calls, median 4.6s, p90 14.0s -- and one at 124s. Abandoning
        # that one at 60s and trying again is very probably FASTER than waiting for it, because the tail
        # is provider variance rather than anything about the request. Without this, the outer ceiling
        # fired at 190s, no retry ever ran, and three workers' completed reports were thrown away.
        ADeadlineForOneModelTurn(MODEL_ATTEMPT_DEADLINE_S, bounds="one attempt",
                                 raises=ModelAttemptTooSlow),
        # Row two: the model's problem -- but only for failures it can actually act on. Everything else
        # returns None from `message_for` and propagates, which is `AR-19`.
        ToolErrorMiddleware(on_error=_what_the_model_is_told(message_for or _tell_nothing)),
        # Runaway loops. A cap is not a judgement about the work, it is a bound on the bill.
        ModelCallLimitMiddleware(run_limit=MODEL_CALLS_PER_RUN, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=TOOL_CALLS_PER_RUN, exit_behavior="end"),
    ]


def _tell_nothing(error: BaseException) -> str | None:
    """The conservative default when a product names no recoverable failure: everything surfaces.

    **`None` halts the run, which is `AR-19`.** A substrate that invented a message here would be deciding
    that some failure is recoverable on a product's behalf -- exactly the judgement that had to leave.
    """
    return None


def _what_the_model_is_told(decide: Any) -> Any:
    """Adapt the product's error taxonomy to the middleware's callback shape.

    **The library calls `on_error(exc, request)` — TWO arguments** — and `message_for(error)` takes one.
    Passing it directly raised `TypeError: message_for() takes 1 positional argument but 2 were given`
    **from inside the error handler**, so the taxonomy never ran: `AR-14`'s "tool failure -> the model" row
    was dead, and the `TypeError` replaced whatever had actually gone wrong.

    T-5a.8's tests called `message_for` directly, so they proved the taxonomy and never the wiring. Found
    by the routing eval, on its first real run, because that was the first thing to drive a tool failure
    through the real middleware.

    **The adapter lives here, not in `rulewright/errors.py`.** Which failures are recoverable is product
    knowledge; the shape a middleware wants is the harness's problem. Same split the module docstring
    already makes.
    """

    def on_error(exc: BaseException, request: Any) -> str | None:
        return decide(exc)

    return on_error
