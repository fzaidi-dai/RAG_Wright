"""How many times one tier may hand the same work down.

Ported from RuleWright under ADR-0139's handover (PS-48g); the substrate is `rag_wright.harness.dispatch`, driven
through real agents and the fabric product's chat-manager. RuleWright's tests of its own prompt wording and of its
routing eval's variants stay in RuleWright.

**Measured live, twice, and neither setting was right**: the chat-manager delegated, read back "Complete -- nothing
further is needed", and delegated again -- 3 times with reasoning on, 24 with it off. **So this is a count, not a
request**, and the refusal hands back what the earlier dispatch said rather than only saying no.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from itertools import cycle
from types import SimpleNamespace
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from rag_wright.harness import agent as agent_module
from rag_wright.harness.dispatch import DISPATCHES_PER_RUN, OnlySoManyDispatches, _Budget
from rag_wright.harness.seen import run_key
from tests.harness.fabric import _a_context, _fabric, _resources, _settings, scripted


def _dispatch(ident: str, brief: str = "match the poplin") -> dict:
    return {"name": "task", "args": {"description": brief, "subagent_type": "orchestrator"},
            "id": ident, "type": "tool_call"}


def _run(times: int, *, limit: int = DISPATCHES_PER_RUN, thread: str = "run-1") -> tuple[list[str], Any]:
    """Drive a real agent that keeps dispatching, and report what actually reached the tool."""
    ran: list[str] = []

    @tool
    def task(description: str, subagent_type: str) -> str:
        """Hand work to a subagent."""
        ran.append(description)
        return "Complete. Two mills stock cotton poplin. Nothing further is needed."

    replies = [AIMessage(content="", tool_calls=[_dispatch(str(n))]) for n in range(times)]
    agent = create_agent(scripted(*replies, AIMessage(content="mill-a and mill-b")), tools=[task],
                         system_prompt="s", middleware=[OnlySoManyDispatches(limit=limit)])
    state = agent.invoke({"messages": [{"role": "user", "content": "go"}]},
                         config={"configurable": {"thread_id": thread}})
    return ran, state


def _refusals(state: Any) -> list:
    return [m for m in state["messages"] if getattr(m, "name", "") == "task" and "already" in str(m.content)]


def test_THE_FIRST_DISPATCH_RUNS() -> None:
    assert _run(1)[0] == ["match the poplin"]


def test_A_SECOND_DISPATCH_WITHIN_A_LIMIT_OF_TWO_ALSO_RUNS() -> None:
    """**The limit is a parameter, not 1 by reflex**: a tier given two runs two."""
    assert len(_run(2, limit=2)[0]) == 2


def test_THE_DISPATCH_PAST_THE_LIMIT_NEVER_REACHES_THE_TOOL() -> None:
    """**The saving is the point.** 23 of 24 dispatches were a model call, an orchestrator run and a reply the
    tier had already read."""
    assert len(_run(6, limit=2)[0]) == 2


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_THE_BOUND_IS_EXACTLY_THE_LIMIT(limit: int) -> None:
    """Off by one either way is silent: one too few forbids real work, one too many keeps a loop alive."""
    assert len(_run(8, limit=limit)[0]) == limit


def test_THE_REFUSAL_HANDS_BACK_WHAT_THE_EARLIER_DISPATCH_SAID() -> None:
    """**A bare "no" makes a model flail.** The tier must be able to answer from the refusal itself."""
    refusals = _refusals(_run(3, limit=1)[1])

    assert refusals, "the extra dispatch produced no explanation at all"
    assert "Two mills stock cotton poplin" in str(refusals[-1].content)


def test_THE_REFUSAL_SAYS_WHAT_TO_DO_NEXT() -> None:
    said = str([m for m in _run(3, limit=1)[1]["messages"] if getattr(m, "name", "") == "task"][-1].content)

    assert "answer" in said.lower(), f"the refusal does not say what to do instead: {said[:200]!r}"


def test_ONE_RUNS_BUDGET_NEVER_REACHES_ANOTHER() -> None:
    """Keyed by `run_key`, the definition of *a run* the call record and the ledger already use."""
    assert len(_run(2, limit=1, thread="run-1")[0]) == 1
    assert len(_run(2, limit=1, thread="run-2")[0]) == 1


def test_A_TOOL_THAT_IS_NOT_A_DISPATCH_IS_UNTOUCHED() -> None:
    """**The negative half.** A bound that throttled ordinary tools would break the tier it protects."""
    ran: list[str] = []

    @tool
    def list_mills() -> dict:
        """The mills this account orders from."""
        ran.append("listed")
        return {"mills": []}

    calls = [AIMessage(content="", tool_calls=[{"name": "list_mills", "args": {}, "id": str(n), "type": "tool_call"}])
             for n in range(4)]
    agent = create_agent(scripted(*calls, AIMessage(content="done")), tools=[list_mills], system_prompt="s",
                         middleware=[OnlySoManyDispatches(limit=1)])
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config={"configurable": {"thread_id": "run-1"}})

    assert len(ran) == 4


def test_TWO_DISPATCHES_IN_ONE_TURN_DO_NOT_BOTH_RUN() -> None:
    """**The concurrency property, asserted rather than assumed.** Several tool calls in one turn run as parallel
    `Send`s, so a tier emitting two `task` calls would start two orchestrators side by side. The check-and-increment
    has no `await` between read and write, so the two cannot both pass it."""
    ran: list[str] = []

    @tool
    def task(description: str, subagent_type: str) -> str:
        """Hand work to a subagent."""
        ran.append(description)
        return "Complete."

    both = AIMessage(content="", tool_calls=[_dispatch("one", "the poplin"), _dispatch("two", "the twill")])
    agent = create_agent(scripted(both, AIMessage(content="done")), tools=[task], system_prompt="s",
                         middleware=[OnlySoManyDispatches(limit=DISPATCHES_PER_RUN)])
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config={"configurable": {"thread_id": "run-1"}})

    assert len(ran) == 1, f"{len(ran)} orchestrators were started from one turn"


def test_ONE_ORCHESTRATOR_PER_RUN_IS_THE_LIMIT() -> None:
    """**Decomposition is the orchestrator's job, not the conversation tier's**: a message with two requests is
    still one delegation."""
    assert DISPATCHES_PER_RUN == 1


def test_THE_CHAT_MANAGER_CARRIES_IT() -> None:
    """**Where a bound like this usually dies.** Deleting the line from the builder would leave every test above
    green while the 24-dispatch loop came straight back."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(agent_module.build_chat_agent)))

    assert "dispatch_bound" in {n.func.id for n in ast.walk(tree)
                                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


# --- the other half: the model is not tempted ------------------------------------------------------------


def _offered(spent: int, *, limit: int = 2) -> list[str]:
    """What the model is offered on a turn after `spent` dispatches have run in this run."""
    middleware = OnlySoManyDispatches(limit=limit)

    @tool
    def task(description: str, subagent_type: str) -> str:
        """Hand work to a subagent."""
        return "Complete."

    @tool
    def list_mills() -> dict:
        """The mills this account orders from."""
        return {"mills": []}

    request = SimpleNamespace(tools=[task, list_mills], runtime=None)
    request.override = lambda **changes: SimpleNamespace(**{**vars(request), **changes})
    middleware._runs[run_key(request)] = _Budget(spent=spent)

    seen: list[str] = []
    middleware.wrap_model_call(request, lambda r: seen.extend(t.name for t in r.tools))
    return seen


def test_BEFORE_THE_BUDGET_IS_SPENT_THE_TOOL_IS_OFFERED() -> None:
    """A bound that hid `task` from the start would stop the tier delegating at all."""
    assert "task" in _offered(0) and "task" in _offered(1)


def test_ONCE_THE_BUDGET_IS_SPENT_THE_TOOL_IS_NOT_OFFERED() -> None:
    """*A tool in the schema is a tool the model will try.* Measured: the refusal held -- 2 dispatches ran -- while
    the tier attempted 24, at 156.6 s for one item."""
    assert "task" not in _offered(2)


def test_THE_TIERS_OWN_TOOLS_ARE_NEVER_TAKEN_AWAY() -> None:
    """It must still be able to answer. Removing its reads would turn a bounded loop into a dead tier."""
    assert "list_mills" in _offered(2)


def test_THE_REFUSAL_REMAINS_AS_THE_BACKSTOP() -> None:
    """**Both halves**: filtering this turn's schema does not cover a call arriving from a replayed history."""
    ran, state = _run(4, limit=1)

    assert len(ran) == 1
    assert _refusals(state), "the schema half replaced the refusal instead of joining it"


# --- on the production path ---------------------------------------------------------------------------


def _answering_orchestrator(context: Any, resources: Any, settings: Any, **_: Any) -> dict:
    """The orchestrator as the chat-manager sees it, with nothing underneath: a compiled subagent that replies."""
    return {"name": "orchestrator", "description": _fabric().prompts.orchestrator_description,
            "runnable": create_agent(scripted(*[AIMessage(content="Complete. Nothing further is needed.")] * 10),
                                     tools=[])}


def test_THE_REAL_CHAT_AGENT_STOPS_OFFERING_TASK_ONCE_THE_BUDGET_IS_SPENT(monkeypatch) -> None:
    """**The test a hand-built request could not be.** The unit tests above passed against one, and live the tier
    was offered `task` and emitted it 25 times. Measured where the model sees it: `bind_tools` is called with the
    turn's final tool list. The orchestrator is replaced where the builder looks it up, so the chat-manager is the
    only tier that runs."""
    offered_per_turn: list[list[str]] = []
    dispatches = [AIMessage(content="", tool_calls=[_dispatch(f"call-{n}")]) for n in range(6)]
    model = scripted()
    object.__setattr__(model, "messages", cycle([*dispatches, AIMessage(content="mill-a and mill-b")]))

    def bind_tools(tools: Any, **kwargs: Any) -> Any:
        offered_per_turn.append(sorted(getattr(t, "name", str(t)) for t in (tools or [])))
        return model

    object.__setattr__(model, "bind_tools", bind_tools)
    monkeypatch.setattr(agent_module, "orchestrator_subagent", _answering_orchestrator)

    context = _a_context()
    agent = agent_module.build_chat_agent(context, _resources(), _settings(), model=model, definition=_fabric())
    agent.invoke({"messages": [{"role": "user", "content": "which mills stock cotton poplin?"}]}, context=context,
                 config={"configurable": {"thread_id": "w1:bound"}})

    with_task = [n for n, tools in enumerate(offered_per_turn, 1) if "task" in tools]
    # **Anti-vacuity first**: an upper bound passes trivially if the run stopped after two turns.
    assert len(offered_per_turn) >= DISPATCHES_PER_RUN + 3, f"only {len(offered_per_turn)} turn(s) ran"
    assert with_task == list(range(1, DISPATCHES_PER_RUN + 1)), (
        f"`task` was offered on turns {with_task} of {len(offered_per_turn)}"
    )
    assert set(offered_per_turn[0]) - set(offered_per_turn[-1]) == {"task"}, "more than `task` was taken away"
