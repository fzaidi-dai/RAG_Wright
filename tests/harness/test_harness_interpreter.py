"""No tier runs code: the dead shell tool, and no interpreter (PS-84).

Ported from RuleWright under ADR-0139's handover (PS-48g).

**Two things are called "the interpreter".** `execute` runs a shell command in a sandbox backend, and `StateBackend`
is not one, so every call fails: it is withheld. `eval` ran JavaScript with a host bridge to the product's whole
surface, so a program on the orchestrator could call every worker's operation and bypass the workers; the
orchestrator plans and dispatches and calls no operation, so it has no interpreter (PS-84).
"""

from __future__ import annotations

import pytest
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage

from rag_wright.harness.agent import build_chat_agent
from rag_wright.harness.tiers import build_orchestrator
from rag_wright.harness.withhold import WITHHELD_FROM_CHAT, WITHHELD_FROM_ORCHESTRATOR
from tests.harness.fabric import _a_context, _fabric, _resources, _settings, scripted


def test_NO_TIER_GETS_AN_INTERPRETER() -> None:
    context, model = _a_context(), scripted(AIMessage(content="x"))
    manager = build_chat_agent(context, _resources(), _settings(), definition=_fabric(), model=model)
    orchestrator = build_orchestrator(context, _resources(), _settings(), definition=_fabric(), model=model)

    assert "eval" not in orchestrator.nodes["tools"].bound.tools_by_name
    assert "eval" not in manager.nodes["tools"].bound.tools_by_name


def test_THE_DEAD_SHELL_TOOL_IS_WITHHELD_FROM_BOTH_TIERS() -> None:
    """A tool that can only fail spends a call and returns an error that reads like a temporary problem."""
    assert "execute" in WITHHELD_FROM_CHAT and "execute" in WITHHELD_FROM_ORCHESTRATOR


@pytest.mark.asyncio
async def test_THE_SHELL_TOOL_REALLY_IS_DEAD_ON_THIS_BACKEND() -> None:
    """**The evidence for the line above, kept rather than described.** If a sandbox backend is ever configured this
    fails, and the withholding should be revisited rather than inherited."""
    agent = create_deep_agent(model=scripted(AIMessage(content="", tool_calls=[{
        "name": "execute", "args": {"command": "echo hi"}, "id": "1", "type": "tool_call"}]),
        AIMessage(content="done")), tools=[])
    state = await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]})

    assert "not available" in str(next(m for m in state["messages"] if m.type == "tool").content).lower()
