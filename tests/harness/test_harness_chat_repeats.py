"""The chat-manager answers an identical repeat from the run's record, as the orchestrator and every worker do (PS-73).

**Until this, repeat suppression was on the orchestrator and its workers and never on the chat-manager.** A product
whose agent is a chat-manager with direct tools (TexWright's) cycled five byte-identical calls until the run's model
call limit stopped it: every repeat ran the tool again and put its whole payload back into the context.

Driven through `build_chat_agent` with a scripted model that repeats itself, counting what the tools actually ran.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from rag_wright.harness import build_chat_agent, start_run
from tests.harness.fabric import _a_context, _fabric, _resources, _settings, scripted


class CountingSurface:
    """Two direct operations that count every time they really run."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    def build(self, context: Any, resources: Any, *, only: Any = None) -> list[Any]:
        ran = self.ran

        @tool
        def case_dossier(case: str) -> dict:
            """Read one case's dossier."""
            ran.append(f"case_dossier:{case}")
            return {"case": case, "runs": ["r1", "r2"]}

        @tool
        def job_status(job: str) -> dict:
            """The status of a running job, which changes while the run watches it."""
            ran.append(f"job_status:{job}")
            return {"job": job, "status": "running"}

        tools = {"product.case_dossier": case_dossier, "product.job_status": job_status}
        return [t for name, t in tools.items() if only is None or name in set(only)]

    @property
    def exposed_names(self) -> set[str]:
        return {"product.case_dossier", "product.job_status"}


def _chat_only(surface: CountingSurface) -> Any:
    """A chat-manager and nothing else: nothing needs planning, there are no workers, no routing."""
    return dataclasses.replace(_fabric(), surface=surface, planning={}, workers=(), routing=None,
                               repeatable=frozenset({"case_dossier"}), question_argument={})


def _call(name: str, args: dict, n: int) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{n}", "type": "tool_call"}])


def _run(surface: CountingSurface, *replies: AIMessage) -> Any:
    agent = build_chat_agent(_a_context(), _resources(), _settings(), definition=_chat_only(surface),
                             model=scripted(*replies))
    return asyncio.run(start_run(agent, _a_context(), message="What knitting settings does the mill record?"))


def test_A_CHAT_MANAGER_CYCLING_THE_SAME_CALLS_RUNS_EACH_ONCE() -> None:
    """**TexWright's trace**, in small: the same cases read in the same shape, round after round. Each distinct call
    runs once; every repeat is answered from the record and says so."""
    surface = CountingSurface()
    cases = ["1256", "1678", "1837"]
    cycle = [_call("case_dossier", {"case": case}, n) for n, case in enumerate(cases * 3)]

    run = _run(surface, *cycle, AIMessage(content="The mill does not record that."))

    assert surface.ran == [f"case_dossier:{case}" for case in cases], surface.ran
    served = [m for m in run.state["messages"] if isinstance(m, ToolMessage)][len(cases):]
    assert len(served) == 6
    assert all("You already made this exact call" in str(m.content) for m in served), served


def test_A_TOOL_NOT_DECLARED_REPEATABLE_RUNS_EVERY_TIME() -> None:
    """**Opt-in, never opt-out**: a job's status changes while the run watches it, so asking again must ask again."""
    surface = CountingSurface()

    _run(surface, *[_call("job_status", {"job": "j1"}, n) for n in range(3)], AIMessage(content="Still running."))

    assert surface.ran == ["job_status:j1"] * 3


def test_ONE_RESPONSE_REPEATING_ONE_CALL_RUNS_IT_ONCE() -> None:
    """`OneAnswerPerTurn` on the chat-manager: copies of one call in a single response are collapsed before they run,
    even for a tool that is not repeatable across turns."""
    surface = CountingSurface()
    twice = AIMessage(content="", tool_calls=[
        {"name": "job_status", "args": {"job": "j1"}, "id": f"c{n}", "type": "tool_call"} for n in range(2)])

    _run(surface, twice, AIMessage(content="Still running."))

    assert surface.ran == ["job_status:j1"]
