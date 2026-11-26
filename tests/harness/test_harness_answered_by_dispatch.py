"""A chat-manager handed a report answers from it (PS-76).

**`dispatch_bound` took `task` away after one dispatch and left the direct tools.** TexWright measured one question
four times: the route was right every time and the orchestrator's report was complete every time, and in two of the
runs the chat-manager went on to re-derive that report with its own `case_dossier` and `filter_trials` -- 6 metered
calls became 35, and 31k input tokens became 330k, for the same answer.

So once a dispatch has come back with a report since the person last spoke, the chat-manager's next call keeps only
`ask_the_user`: the report is what it answers from, and a question the orchestrator needs put to the person can
still be put. A failed dispatch did not do the work, so it leaves the tools; the person's next message restores them.

Driven through `build_chat_agent` with a scripted model, shared by both tiers, that records what it was offered.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

import rag_wright.harness.agent as agent_module
from rag_wright.harness import WorkerReport, build_chat_agent, resume_run, start_run, worker_spec
from tests.harness.fabric import _a_context, _fabric, _resources, _settings

ASK = "ask_the_user"


class PrecedentSurface:
    """One direct read and one operation that needs planning."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    def build(self, context: Any, resources: Any, *, only: Any = None) -> list[Any]:
        surface = self

        @tool
        def case_dossier(case: str) -> dict:
            """Read one case's dossier."""
            surface.ran.append("case_dossier")
            return {"case": case, "gsm": 420}

        @tool
        def find_precedents(case: str) -> dict:
            """Find the earlier cases closest to one."""
            surface.ran.append("find_precedents")
            return {"closest": "3204"}

        tools = {"product.case_dossier": case_dossier, "product.find_precedents": find_precedents}
        return [t for name, t in tools.items() if only is None or name in set(only)]

    @property
    def exposed_names(self) -> set[str]:
        return {"product.case_dossier", "product.find_precedents"}


class _Script:
    """The replies in order; an exception in the script is raised by the call that reaches it."""

    def __init__(self, replies: tuple[Any, ...]) -> None:
        self._replies = iter(replies)

    def __iter__(self) -> _Script:
        return self

    def __next__(self) -> AIMessage:
        reply = next(self._replies)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def recording(*replies: Any) -> tuple[Any, list[list[str]]]:
    """A scripted model for every tier, and the tool names it was offered on each call (`[]` for none)."""
    offered: list[list[str]] = []

    class Recording(GenericFakeChatModel):
        def bind_tools(self, tools: Any, **_kwargs: Any) -> Any:
            offered.append(sorted(getattr(t, "name", None) or t.get("name", "") for t in tools))
            return self

        def bind(self, **_kwargs: Any) -> Any:
            offered.append([])
            return self

    return Recording(messages=_Script(replies)), offered


def _call(name: str, args: dict, ident: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": ident, "type": "tool_call"}])


def _delegate(brief: str = "Which earlier development is closest to SDF 3146?") -> AIMessage:
    return _call("task", {"description": brief, "subagent_type": "orchestrator"}, "d1")


def precedent_worker(context: Any, resources: Any, ledger: Any = None) -> dict:
    """The worker that holds the operation needing planning: the orchestrator runs none itself (PS-84)."""
    return worker_spec("precedent_worker", "Finds the earlier cases closest to one.", "You find precedents.",
                       PrecedentSurface().build(context, resources, only=["product.find_precedents"]), WorkerReport)


REPORT = AIMessage(content="The closest earlier development to SDF 3146 is SDF 3204; finished GSM 350 against 420.")


class PlanningFailed(RuntimeError):
    """What the orchestrator raises in the failed-dispatch case; the product tells the model about it."""


def _agent(*replies: Any) -> tuple[Any, PrecedentSurface, list[list[str]]]:
    surface = PrecedentSurface()
    definition = dataclasses.replace(
        _fabric(), surface=surface, planning={"product.find_precedents": "compares across cases"},
        workers=(precedent_worker,), routing=None, repeatable=frozenset(), question_argument={},
        message_for=lambda error: f"the dispatch failed: {error}" if isinstance(error, PlanningFailed) else None)
    model, offered = recording(*replies)
    agent = build_chat_agent(_a_context(), _resources(), _settings(), definition=definition, model=model)
    return agent, surface, offered


def _answers_from_the_report(names: list[str]) -> bool:
    """A call that can ask the person but cannot re-derive: no direct tool, `ask_the_user` kept."""
    return ASK in names and "case_dossier" not in names


def test_THE_CALL_AFTER_A_REPORT_KEEPS_ONLY_THE_QUESTION() -> None:
    """**TexWright's run 4, in small.** Delegated, handed a complete report: the next call cannot re-derive it, and
    can still ask the person."""
    agent, surface, offered = _agent(_delegate(), REPORT, AIMessage(content="SDF 3204 is the closest."))

    run = asyncio.run(start_run(agent, _a_context(), message="Which earlier development is closest to SDF 3146?"))

    assert "case_dossier" in offered[0], offered
    assert _answers_from_the_report(offered[-1]), offered
    assert surface.ran == []
    assert run.state["messages"][-1].content == "SDF 3204 is the closest."


def test_A_TURN_WITHOUT_A_DISPATCH_KEEPS_EVERY_TOOL() -> None:
    """**A direct question is untouched**: the product's single-case questions need `case_dossier`."""
    agent, surface, offered = _agent(_call("case_dossier", {"case": "3146"}, "c1"), AIMessage(content="420 GSM."))

    asyncio.run(start_run(agent, _a_context(), message="What GSM did SDF 3146 finish at?"))

    assert surface.ran == ["case_dossier"]
    assert all("case_dossier" in names for names in offered), offered


def test_A_FAILED_DISPATCH_LEAVES_THE_TOOLS() -> None:
    """**A dispatch that failed did not do the work**, so answering directly is still the tier's to do."""
    agent, _, offered = _agent(_delegate(), PlanningFailed("the planning tier is down"),
                                     _call("case_dossier", {"case": "3146"}, "c1"), AIMessage(content="420 GSM."))

    run = asyncio.run(start_run(agent, _a_context(), message="Which earlier development is closest to SDF 3146?"))

    failed = [m for m in run.state["messages"] if isinstance(m, ToolMessage) and m.status == "error"]
    assert failed, "the dispatch did not fail as scripted"
    assert "case_dossier" in offered[-1], offered


def test_THE_PERSONS_NEXT_MESSAGE_RESTORES_THEM() -> None:
    """**Bounded by the turn.** The report answers the question it was asked for, not the next one."""
    agent, surface, offered = _agent(_delegate(), REPORT, AIMessage(content="SDF 3204 is the closest."),
                                     _call("case_dossier", {"case": "3204"}, "c2"), AIMessage(content="350 GSM."))

    asyncio.run(start_run(agent, _a_context(), message="Which earlier development is closest to SDF 3146?",
                          run_id="r1"))
    asyncio.run(start_run(agent, _a_context(), message="And what GSM did 3204 finish at?", run_id="r1"))

    assert surface.ran == ["case_dossier"]
    assert "case_dossier" in offered[-1], offered


def test_THE_PERSONS_NEXT_MESSAGE_MAY_BE_DELEGATED_AGAIN() -> None:
    """**Measured live (PS-87)**: on a follow-up in the same run, D1 was offered `ask_the_user` alone. The bound counted
    the opening message and each answer, never the person's next message, so a run that had delegated once could not
    delegate the next question -- and a chat-manager with no direct tools could then only ask."""
    agent, _, offered = _agent(_delegate(), REPORT, AIMessage(content="SDF 3204 is the closest."),
                               AIMessage(content="Which one?"))

    asyncio.run(start_run(agent, _a_context(), message="Which earlier development is closest to SDF 3146?",
                          run_id="r1"))
    asyncio.run(start_run(agent, _a_context(), message="And the closest to SDF 3150?", run_id="r1"))

    assert "task" in offered[-1], offered


def test_THE_PERSONS_ANSWER_TO_A_BLOCKED_REPORT_RESTORES_THEM() -> None:
    """**The person answering is the person speaking.** An orchestrator that comes back `blocked` has its question put
    with `ask_the_user`; the answer arrives as that tool's reply, not a new message, and the tier must be able to act
    on it -- its one dispatch is already spent."""
    blocked = AIMessage(content="Blocked: which season's 3146 is meant, SS26 or AW26?")
    agent, _, offered = _agent(_delegate(), blocked, _call(ASK, {"question": "SS26 or AW26?"}, "q1"),
                                     _call("case_dossier", {"case": "3146-ss26"}, "c1"), AIMessage(content="420 GSM."))

    started = asyncio.run(start_run(agent, _a_context(), message="Which earlier development is closest to SDF 3146?"))
    asyncio.run(resume_run(agent, _a_context(), answer="SS26", run_id=started.run_id))

    assert _answers_from_the_report(offered[2]), offered
    assert "case_dossier" in offered[3], offered


def test_A_TWO_PART_MESSAGE_IS_ANSWERED_BY_THE_TIER_IT_WAS_HANDED_TO() -> None:
    """**The risk the card names.** Delegating first leaves no direct turn after the report, so the direct half
    is answered where the whole message went. The orchestrator runs no operation (PS-84), so it is a worker's: here
    one holding both, which is what a product gives a worker when its direct half may arrive with planning work."""
    both = "What GSM did SDF 3146 finish at, and which earlier development is closest to it?"
    surface = PrecedentSurface()

    def case_worker(context: Any, resources: Any, ledger: Any = None) -> dict:
        return worker_spec("case_worker", "Reads cases and finds the ones closest to them.", "You read cases.",
                           surface.build(context, resources), WorkerReport)

    definition = dataclasses.replace(
        _fabric(), surface=surface, planning={"product.find_precedents": "compares across cases"},
        workers=(case_worker,), routing=None, repeatable=frozenset(), question_argument={})
    model, offered = recording(
        _delegate(both), _call("task", {"description": both, "subagent_type": "case_worker"}, "t1"),
        _call("case_dossier", {"case": "3146"}, "w1"), _call("find_precedents", {"case": "3146"}, "w2"),
        _call("WorkerReport", {"status": "done", "need": ""}, "r1"),
        AIMessage(content="3146 finished at 420 GSM; SDF 3204 is the closest."),
        AIMessage(content="420 GSM, and SDF 3204 is the closest."))
    agent = build_chat_agent(_a_context(), _resources(), _settings(), definition=definition, model=model)

    run = asyncio.run(start_run(agent, _a_context(), message=both))

    assert surface.ran == ["case_dossier", "find_precedents"]
    assert _answers_from_the_report(offered[-1]), offered
    assert run.state["messages"][-1].content == "420 GSM, and SDF 3204 is the closest."


def test_THE_CHAT_MANAGER_CARRIES_IT() -> None:
    """**Where a guard like this usually dies**: deleted from the builder, every scripted test above that never
    delegates would stay green."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(agent_module.build_chat_agent)))

    assert "TheReportIsTheAnswer" in {n.func.id for n in ast.walk(tree)
                                      if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
