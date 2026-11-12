"""Taking a tool away from one tier.

Ported from RuleWright under ADR-0139's handover (PS-48g); the substrate is `rag_wright.harness.withhold`, on tiers
built from the fabric product.

**The obvious mechanism does not work.** `HarnessProfileConfig.excluded_tools` keys a profile by the MODEL, and every
tier runs the same one, so it would strip the interpreter from the orchestrator too. A middleware is attached to a
tier, so the tool is taken out of the turn -- and a call that arrives anyway, from a replayed history or a resumed
run, is refused as well.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from rag_wright.harness import agent as agent_module
from rag_wright.harness import tiers as tiers_module
from rag_wright.harness.agent import build_chat_agent
from rag_wright.harness.tiers import build_orchestrator, chat_manager_tools
from rag_wright.harness.withhold import WITHHELD_FROM_CHAT, WITHHELD_FROM_ORCHESTRATOR, WITHHELD_FROM_WORKERS, withhold
from tests.harness.fabric import _a_context, _fabric, _resources, _settings, scripted


def _a_turn(*names: str) -> tuple[SimpleNamespace, dict]:
    """A model request whose offered tools include the ones named, and a record of what it was overridden with."""
    tools = []
    for name in names:
        @tool(name)
        def one(command: str = "") -> str:
            """A tool."""
            return "x"

        tools.append(one)
    captured: dict = {}

    def override(**changes: object) -> SimpleNamespace:
        captured.update(changes)
        return SimpleNamespace(tools=changes.get("tools", tools), override=override)

    return SimpleNamespace(tools=tools, override=override), captured


def _a_call(name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(tool_call={"name": name, "args": args, "id": "1", "type": "tool_call"})


def _builders_with_a_model() -> list:
    context, model = _a_context(), scripted(AIMessage(content="x"))
    return [build_chat_agent(context, _resources(), _settings(), definition=_fabric(), model=model),
            build_orchestrator(context, _resources(), _settings(), definition=_fabric(), model=model)]


# --- what a turn offers -------------------------------------------------------------------------------


def test_THE_WITHHELD_TOOL_IS_TAKEN_OUT_OF_THE_TURNS_TOOLS() -> None:
    """**The mechanism, on a turn that actually offers it.** A scripted model's binding cannot show this: a
    pre-built model resolves a different harness profile, and `execute` was absent from it whether or not the
    middleware was installed."""
    request, captured = _a_turn("execute", "list_mills", "glob")

    withhold("execute").wrap_model_call(request, lambda r: r)

    assert {t.name for t in captured["tools"]} == {"list_mills", "glob"}


def test_A_TURN_WITHOUT_THE_TOOL_IS_NOT_REBUILT() -> None:
    """**Anti-vacuity, and the cheap path**: `override` makes a new request, which a turn with nothing to remove
    does not need."""
    request, captured = _a_turn("list_mills", "glob")

    assert withhold("execute").wrap_model_call(request, lambda r: r) is request
    assert not captured


def test_BOTH_TIERS_INSTALL_IT() -> None:
    """**The wiring**, read as the argument rather than as a source string."""
    def installs_withhold(builder: object) -> bool:
        tree = ast.parse(textwrap.dedent(inspect.getsource(builder)))
        return any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "withhold" for n in ast.walk(tree))

    assert installs_withhold(agent_module.build_chat_agent)
    assert installs_withhold(tiers_module.build_orchestrator)


def test_BOTH_TIERS_STILL_HOLD_THE_TOOL_IN_THEIR_TOOL_NODE() -> None:
    """**Why the refusal half exists.** Filtering the schema keeps the model from reaching; the tool is still
    executable, so a call from a replayed history would still run it."""
    for built in _builders_with_a_model():
        assert "execute" in built.nodes["tools"].bound.tools_by_name


def test_THE_CHAT_MANAGER_KEEPS_EVERYTHING_ELSE() -> None:
    """A blunt exclusion that took the memory tools would break recall silently."""
    context = _a_context()
    node = _builders_with_a_model()[0].nodes["tools"].bound.tools_by_name

    for kept in chat_manager_tools(context, _resources(), definition=_fabric()):
        assert kept.name in node
    for memory_tool in ("ls", "glob", "grep", "read_file", "write_file"):
        assert memory_tool in node, f"{memory_tool} is gone, which breaks memory recall"
    assert "task" in node, "the chat-manager cannot delegate"
    assert set(WITHHELD_FROM_CHAT) == {"execute", "eval"}, "more is withheld than this test knows about"


def test_A_WORKER_HAS_NOTHING_WITHHELD() -> None:
    """A worker is never offered `execute`, `eval` or `task` (measured in the workers' tests), so a list naming
    them would be a removal with nothing behind it."""
    assert WITHHELD_FROM_WORKERS == {}


# --- the refusal, for a call that did not come from this turn ------------------------------------------


def test_A_WITHHELD_TOOL_IS_REFUSED_IF_IT_IS_CALLED_ANYWAY() -> None:
    def handler(_request: object) -> None:
        raise AssertionError("the tool ran on a tier that is not supposed to have it")

    result = withhold("execute").wrap_tool_call(_a_call("execute", {"command": "ls /"}), handler)

    assert isinstance(result, ToolMessage) and "not available" in str(result.content).lower()


def test_AN_ALLOWED_TOOL_REACHES_THE_REAL_HANDLER() -> None:
    result = withhold("execute").wrap_tool_call(
        _a_call("list_mills", {}), lambda _r: ToolMessage(content="listed", tool_call_id="1", name="list_mills"))

    assert result.content == "listed"


def test_EVERY_WITHHELD_TOOL_HAS_A_STATED_REASON() -> None:
    """A removal nobody wrote down is one nobody can revisit when it turns out to be wrong."""
    for listing in (WITHHELD_FROM_CHAT, WITHHELD_FROM_ORCHESTRATOR):
        assert listing
        for name, reason in listing.items():
            assert len(reason) > 40, f"{name}'s reason is too short to mean anything: {reason!r}"
