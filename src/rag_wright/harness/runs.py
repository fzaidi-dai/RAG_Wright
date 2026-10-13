"""The only way to invoke an agent (T-5a.1, AR-6 / ANFR-2 / ANFR-4).

**Why an entry point rather than a convention.** Runtime context is per-INVOCATION, not per-thread: a
resume that omits `context=` wakes every tool with `runtime.context is None`. That is a framework fact,
pinned fake and live by `test_harness_grounding.py`, not a guess. Two failures meet there -- the tenant
binding comes from the context, and `StoreBackend`'s namespace factory falls back to the single global
`("filesystem",)` when it has nothing -- so a forgetful resume reads and writes ANOTHER TENANT'S memory,
silently, at the moment a human approved an action.

A rule saying "always pass `context=`" would be obeyed until the first refactor. So the call site is made
incapable of omitting it: these two functions take the authenticated tenant, build the context themselves,
and there is no parameter through which a caller could supply a different one.
`tests/test_harness_entry_point_guard.py` is the other half -- nothing else may invoke an agent, and
nothing outside this package may build one.

Grounded (`library-grounding`, product lane, then the installed signature):

    CompiledStateGraph.ainvoke(input, config=None, context=None, ...)   `context` is a real keyword
    Command(resume=...)                                                 langgraph.types
    agent.context_schema                                                the compiled graph exposes it

**Async because ANFR-2 asks for the event loop, not OS threads.** Note that `deepagents.AsyncSubAgent` is
NOT the mechanism for that: it specifies a subagent running on a REMOTE Agent Protocol server
(`deepagents/middleware/async_subagents.py:L34` -- its keys are `graph_id`, `url`, `headers`). In-process
async is `async def` tools, the `a*` middleware hooks, and `ainvoke`/`astream`. See the T-5a.1 design note
in `agent-tasks.md`.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from langgraph.types import Command

from rag_wright.harness.scope import RunScope

logger = logging.getLogger(__name__)


class RunOutcome(StrEnum):
    """What became of a run, named in CODE because none of it is a judgement (T-5a.15).

    **Every transition here is decidable from the state alone**, which is why it is not in a prompt: the
    project's own rule is that a warning in a prompt is not a constraint, and `read_the_question` was
    instructed in four places and obeyed zero times.

    **The distinction between `PARTIAL` and `NOTHING_CAME_BACK` was measured, not designed.** Three
    reproduced failures showed two different shapes: when the whole-turn clock fires, and when a caller's
    own ceiling fires, the finished work is still in the checkpoint -- so there is something to show. When
    the retries are exhausted the model never answered, and the checkpoint holds only the user's question.
    A single "proceed with what returned" rule would have rendered the second as a partial answer, which is
    worse than failing.
    """

    #: Finished: the run produced an answer.
    COMPLETE = "complete"
    #: Cut off after real work. Tool results exist and no answer was composed from them -- so the work is
    #: shown with what is missing said plainly, rather than discarded.
    PARTIAL = "partial"
    #: Cut off before anything came back. There is nothing to show, and saying so is the honest answer.
    NOTHING_CAME_BACK = "nothing_came_back"
    #: Suspended on a question for a person. Not failed and not finished -- `resume_run` continues it.
    WAITING_ON_A_PERSON = "waiting_on_a_person"

    # **There is deliberately no `IN_FLIGHT`, and finding that out cost a wrong branch.** The first version
    # read `snapshot.next` as "still going" -- but after a run is cut off the snapshot STILL shows a pending
    # next step, because the graph would continue if resumed. Measured: a run killed by the whole-turn clock
    # reads back with `next` set, so `next` cannot tell a live run from a dead one.
    #
    # **Liveness is simply not in the stored state**, and faking it from what is would be the
    # proxy-measurement mistake this phase keeps paying for. It needs its own signal -- a job row, a
    # heartbeat -- and this slice does not have one, so no member claims it.
    #
    # The consequence is a better contract than the one intended: an outcome describes WHAT HAS FINISHED SO
    # FAR, not whether a process is alive. A reader polling a live run sees `PARTIAL`, which is the honest
    # answer to "what do you have?" and the same answer a reader of a dead run needs.


def outcome_of(snapshot: Any) -> RunOutcome:
    """What a run's stored state says became of it.

    **Order matters and each branch is a measured case**, not a guess:

    1. An interrupt outranks everything: a run holding a question for a person has neither failed nor
       finished, and calling it partial would tell the user work was lost while a question is outstanding.
    2. A final answer -- an AI message with content and no tool calls left to run -- is `COMPLETE`.
    3. Otherwise: **tool results present means `PARTIAL`** (work finished, nothing composed it), and
       **no tool results means `NOTHING_CAME_BACK`**.

    **It reports what has finished, not whether the run is alive.** `snapshot.next` is still set after a run
    is cut off -- the graph would continue if resumed -- so it cannot distinguish a live run from a dead one,
    and nothing here pretends otherwise.
    """
    values = getattr(snapshot, "values", None) or {}
    if values.get("__interrupt__") or getattr(snapshot, "interrupts", None):
        return RunOutcome.WAITING_ON_A_PERSON

    messages = values.get("messages") or []
    spoke = [m for m in messages if _is_an_answer(m)]
    if spoke:
        return RunOutcome.COMPLETE
    # **A `ToolMessage` is the evidence that work finished**, and it is what the three reproduced failures
    # differed on. Counted rather than inferred from timing or from the exception type -- the exception
    # type was itself wrong until T-5a.13c.
    returned = [m for m in messages if type(m).__name__ == "ToolMessage"]
    return RunOutcome.PARTIAL if returned else RunOutcome.NOTHING_CAME_BACK


def _is_an_answer(message: Any) -> bool:
    """An AI message carrying content and no outstanding tool calls."""
    if type(message).__name__ != "AIMessage":
        return False
    if getattr(message, "tool_calls", None):
        return False
    return bool(str(getattr(message, "content", "") or "").strip())


@dataclass(frozen=True, slots=True)
class RunResult:
    """One user-initiated agent turn, and the scope it ran under.

    `run_id` is the run's identity: `run-contract.md` obligation 1 requires a run to outlive the view that
    started it, so it is returned rather than held in memory, and it is what `resume_run` addresses.
    """

    run_id: str
    context: RunScope
    state: dict[str, Any]
    #: What became of it, for a reader that did not watch it happen. `None` from the entry points, which
    #: return before anything can be said; `read_run` always fills it, because a reader arriving later is
    #: exactly who needs it.
    outcome: RunOutcome | None = None


def _require_context_schema(agent: Any) -> None:
    """Refuse an agent that cannot carry the tenancy.

    An agent compiled with `context_schema=None` **accepts `context=` and silently drops it** -- so every
    tool sees `runtime.context is None` and the memory namespace collapses to the global one. That is the
    same leak a forgetful resume causes, one step earlier, and it is invisible from the call site. Checked
    here because this is the one place every invocation passes through.
    """
    from rag_wright.harness.scope import carries_scope

    schema = getattr(agent, "context_schema", None)
    # **A PROTOCOL check, not an identity one** (engine ADR-0139 addendum). This was
    # `schema is not AgentContext` -- identity against the PRODUCT's own type -- so no other product could
    # satisfy it however correct its own context was. What the substrate actually needs is that the schema
    # carries a scope; `validate_scope` then checks the value when a run begins.
    if schema is None or not carries_scope(schema):
        raise TypeError(
            f"this agent was built with context_schema={schema!r}, so it cannot carry the run's tenancy: "
            f"every tool would wake with `runtime.context is None` and the memory namespace would fall "
            f"back to the one global namespace shared by every customer. Build it with "
            f"`context_schema=<your context type>` -- any type with `scope` and `shared_scope`."
        )


def _opening_turn(message: str, continuation: Any,
                  skills: dict[str, str] | None = None) -> dict[str, Any]:
    """The turn's input: the user's message, and the view they were looking at.

    **The view goes in TWICE, and the two copies do different jobs.** The rendered block goes in the
    MESSAGE, because that is what the model reads and what the checkpoint keeps so a later turn still knows
    what "this one" meant. The subject fingerprint goes in STATE, because `ConfirmTheView` compares it
    against what the thread last confirmed -- and a comparison against prose would be a model call.
    """
    from rag_wright.harness.skills import skill_files

    # **Seeded at the ONE DOOR** (`AR-4`). `StateBackend` serves the virtual filesystem out of graph
    # state, so a skill that is not in the opening turn does not exist for that run -- and a skill the
    # orchestrator is advertised but cannot read is worse than no skill, because it spends a `read_file`
    # to find out. Every tier is seeded rather than only the one with `SkillsMiddleware`: the cost is a
    # few kilobytes of state, and the alternative is this module knowing which tier it is opening.
    turn: dict[str, Any] = {
        "messages": [{"role": "user", "content": _with_continuation(message, continuation)}],
        "files": skill_files(skills or {}),
    }
    if continuation is not None:
        # **Asked of the continuation, not imported from the product** (T-7.58): the substrate puts a
        # `viewing` field on the turn and does not know what a subject reads like.
        turn["viewing"] = {"surface": continuation.surface, "subject": continuation.subject()}
    return turn


def _with_continuation(message: str, continuation: Any) -> str:
    """The user's turn, with the view they were looking at delimited in front of it.

    **The block goes in the turn, and the framing that governs it goes in the static system prompt.** A
    delimited block inside a user turn is a convention the model observes rather than a boundary the
    runtime enforces -- exactly what `MemoryMiddleware` does with `<agent_memory>`, and said plainly here
    rather than implied. Keeping the framing out of reach of the payload is what makes the convention
    worth anything.
    """
    if continuation is None:
        return message

    return f"{continuation.render()}\n\n{message}"


async def start_run(
    agent: Any,
    context: RunScope,
    *,
    message: str,
    continuation: Any = None,
    #: `{published path: text}` -- the product's authored skills, seeded into the run's virtual
    #: filesystem. **Passed in (T-7.58)**: this read `ORCHESTRATING_SKILL` out of the substrate, so every
    #: product got RuleWright's procedure for batching a clarification. `None` seeds none.
    skills: dict[str, str] | None = None,
    run_id: str | None = None,
    settings: Any = None,
    accounting: Any = None,
) -> RunResult:
    """Begin a run under `context`, and supply it to every invocation.

    The caller passes who is asking, what they asked, and the scope it runs under. **That scope is the
    PRODUCT's to derive** (engine ADR-0139): the substrate must not know what authenticated this run, so
    it takes the resolved scope rather than three identity rows it would have to re-read.

    **The obligation `AR-6` placed here therefore moves to the caller, and is not weaker for it**: the
    context must come from the authenticated session of THIS request, never from one remembered across
    requests. On the route that is structural -- `tenant: TenantDep` is a per-request dependency and
    the product's own `for_tenant` rule runs in the handler body -- which is why the door is still impossible to
    forget rather than merely documented.

    A `run_id` is minted when the caller has none, so a run is addressable from the moment it exists
    rather than from whenever somebody remembers to name it (`run-contract.md` obligation 1).

    **Tracing and accounting attach HERE, for the same reason the context does** (T-5a.4b): this is the
    only door, so it is the only place that cannot be forgotten. `settings=None` leaves both off and
    `accounting=None` is a no-op — the precedent `recording_usage` set, because accounting must never be
    the reason a user's work does not happen.

    **`continuation` must already be RESOLVED** (`AR-12`, T-5a.10). It is typed `Any` only because the
    harness must not import its own models into this module's signature dance; `render_continuation`
    refuses anything but a `ResolvedContinuation` by name, so a caller that skipped the workspace check
    gets `UnresolvedContinuation` here rather than a prompt full of the browser's claims.
    """
    _require_context_schema(agent)
    identity = run_id or uuid.uuid4().hex

    config = _config(settings, identity, context)
    async with _accounted(settings, accounting=accounting, run_id=identity):
        try:
            state = await agent.ainvoke(
                _opening_turn(message, continuation, skills), config=config, context=context)
        finally:
            # **The LAST abandoned attempt has nothing following it** (T-5a.14b), so the recorder's
            # close-on-next-start never reaches it -- and it is the interesting one, because on a failed
            # run it is the attempt the run died on. `finally`, for the same reason: a run that raised is
            # exactly when the record matters.
            _close_abandoned_attempts(config)
    return RunResult(run_id=identity, context=context, state=state)


async def resume_run(
    agent: Any,
    context: RunScope,
    *,
    answer: Any,
    run_id: str,
    settings: Any = None,
    accounting: Any = None,
) -> RunResult:
    """Answer a run that paused for a person, and re-supply its context.

    **This is the invocation that goes wrong.** A resume is a second `invoke`, and the context does not
    survive from the first -- so it must be supplied again here, exactly as `start_run` supplies it.

    **The caller owes a FRESHLY RESOLVED context, and this is the call where that matters most.** A
    resume can arrive long after the run paused; re-reading the session rather than reusing the paused
    run's tenancy is what makes a withdrawn membership or a suspended customer end access AT THE RESUME
    rather than at some expiry -- the same property `get_tenant` buys on the request path. Passing a
    context stored alongside the paused run would read as working and quietly lose it.
    """
    _require_context_schema(agent)

    # **The resumed half is accounted too.** A run that paused for a human and then did most of its work
    # would otherwise report only what it spent before the question.
    config = _config(settings, run_id, context)
    async with _accounted(settings, accounting=accounting, run_id=run_id):
        try:
            state = await agent.ainvoke(Command(resume=answer), config=config, context=context)
        finally:
            _close_abandoned_attempts(config)
    return RunResult(run_id=run_id, context=context, state=state)


async def stream_run(
    agent: Any,
    context: RunScope,
    *,
    message: str,
    continuation: Any = None,
    #: `{published path: text}` -- the product's authored skills, seeded into the run's virtual
    #: filesystem. **Passed in (T-7.58)**: this read `ORCHESTRATING_SKILL` out of the substrate, so every
    #: product got RuleWright's procedure for batching a clarification. `None` seeds none.
    skills: dict[str, str] | None = None,
    run_id: str | None = None,
    settings: Any = None,
    accounting: Any = None,
) -> AsyncIterator[tuple[str, Any, str, Any]]:
    """Begin a run and yield `(run_id, namespace, mode, chunk)` as it happens.

    **The streaming sibling of `start_run`, and it carries the same guarantees** — the context, the trace
    and both ledgers — because they attach here rather than at the call site. A surface that streamed
    through `agent.astream` directly would lose all three and look like it worked.

    `subgraphs=True` is what makes a subagent's output distinguishable from the main agent's; without it
    every event arrives flat and `AR-18`'s cards have nothing to index on. Two stream modes are requested,
    so langgraph yields **3-tuples** rather than 2 — verified, not assumed.

    **`continuation` is built the same way `start_run` builds it**, through `_opening_turn`. Two doors
    would mean two chances to forget the view, and the streaming one is the door the product actually uses
    — so a continuation that only worked on `start_run` would be a feature with no users (`AR-12`).
    """
    _require_context_schema(agent)
    identity = run_id or uuid.uuid4().hex

    async with _accounted(settings, accounting=accounting, run_id=identity):
        async for namespace, mode, chunk in agent.astream(
            _opening_turn(message, continuation, skills),
            config=_config(settings, identity, context),
            context=context,
            subgraphs=True,
            stream_mode=["updates", "messages"],
        ):
            yield identity, namespace, mode, chunk


async def read_run(
    agent: Any,
    context: RunScope,
    *,
    run_id: str,
) -> RunResult | None:
    """Where a run got to, by its id alone — or `None` if it is not this matter's (T-5a.4c-iii).

    **`run-contract.md` obligation 1, cashed:** *"the run is addressable, and its region renders from an
    id alone, never from in-memory state that a reload destroys."* A reload, a second tab, or a deploy all
    arrive here.

    **`None` covers three cases deliberately** — no such run, another matter's run, and a run id that was
    never started. Not by comparing afterwards: the thread id is derived from the workspace, so another
    matter's run does not resolve to a thread at all. The checkpointer itself has no tenancy and cannot be
    given any, so the isolation has to be in the key.

    **State, not replay.** This returns where the run got to; it does not re-emit the events that got it
    there. A reloading client renders the state and reconnects for what follows, which is enough for
    obligation 1 and is measurably cheaper than storing and replaying a transcript. If the surface turns
    out to need replay, the checkpointer keeps the history and `alist` is where that would come from —
    decided then, with a real surface to measure against, rather than guessed now.
    """
    _require_context_schema(agent)

    snapshot = await agent.aget_state(
        {"configurable": {"thread_id": thread_for(context, run_id)}}
    )
    if snapshot is None or not snapshot.values:
        return None

    return RunResult(run_id=run_id, context=context, state=dict(snapshot.values),
                     outcome=outcome_of(snapshot))


def thread_for(context: RunScope, run_id: str) -> str:
    """The checkpointer thread a run lives in, **scoped to its matter**.

    **The checkpointer has no tenancy and cannot be given any.** `aget_tuple` keys on `thread_id` and
    `checkpoint_ns` alone — verified in the installed saver — so a bare run id is readable by anybody who
    can name it. That is exactly the hole T-2.4d closed on the jobs route, where a UUID in a URL was the
    whole access control and anybody holding a job id read its result across customers.

    So the workspace is part of the key rather than a comparison somebody must remember afterwards.
    Another matter's run id does not resolve to a thread at all, which makes "not yours" and "no such run"
    the same non-answer **by construction** — the non-oracle property this codebase applies to documents,
    jobs, workspaces, sessions and checks, arrived at structurally for once instead of by a check.

    The run id stays the PUBLIC identity: it is what `usage_run.correlation_id` joins on and what a client
    keeps to reconnect. The thread id is ours and never leaves the process.
    """
    from rag_wright.harness.scope import thread_key

    return thread_key(context, run_id)


def _config(settings: Any, run_id: str, context: RunScope) -> dict[str, Any]:
    """The invocation config: the thread, plus the callbacks that emit and price this run's generations."""
    from rag_wright.harness.models import GenerationRecorder

    config: dict[str, Any] = {"configurable": {"thread_id": thread_for(context, run_id)}}
    if settings is not None:
        # **One recorder, emitting our own generations.** Langfuse's LangChain handler was tried first and
        # cannot carry a real cost: it creates observations with `start_observation` and never
        # `start_as_current_observation`, so nothing can attach one afterwards. Running both would double
        # every token in `usage_since`, so this is the only emitter.
        config["callbacks"] = [GenerationRecorder(settings)]
    return config


def _close_abandoned_attempts(config: dict[str, Any]) -> None:
    """Close any span left open by a cancelled model call.

    **Never breaks the run it is recording** -- the rule every Langfuse touch in this harness follows.
    """
    for handler in config.get("callbacks") or []:
        closer = getattr(handler, "close_abandoned", None)
        if closer is None:
            continue
        try:
            closer()
        except Exception:
            logger.warning("harness_close_abandoned_failed", exc_info=True)


@asynccontextmanager
async def _accounted(settings: Any, *, accounting: Any, run_id: str):
    """Trace, time and record the run -- or do nothing at all when no settings were supplied.

    **No `context` argument any more** (T-7.58 stage 4b). This used to pass
    `context.customer_id, context.workspace_id` through to the accounting, which put our tenancy pair in
    the substrate's signature; the caller now supplies an `accounting` that already knows its own scope.
    That removed one of this module's two tenancy references, and all of `accounting.py`'s.
    """
    if settings is None:
        yield
        return

    from rag_wright.harness.accounting import accounted_run

    async with accounted_run(settings, run_id=run_id, accounting=accounting):
        yield
