"""A changed view is confirmed, never silently adopted; and the view reaches the run through the door.

Ported from RuleWright under ADR-0139's handover (PS-48c); the substrate is `rag_wright.harness.confirm.ConfirmTheView`
and the continuation half of `runs.start_run`, driven with the fabric product's own view (`FabricView`). RuleWright's
tests of what counts as a new subject on its surfaces, and of its own renderer and prompt, stay in RuleWright.

**Silently switching subject produces a confident answer about the wrong object.** A person looks at one swatch,
moves to another, asks "what about this one?" -- and every word of the answer is about the first. **Deterministic,
not prompt-directed**: the comparison happens in `before_agent`, before the model runs, against the view the thread
last confirmed, which is in the checkpoint. **And `before_agent` replays on resume**, so nothing with a side effect
may run before its `interrupt()`.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from rag_wright.harness.confirm import PURE_BEFORE_THE_INTERRUPT, ConfirmTheView, confirmation_question
from tests.harness.fabric import FabricContext, FabricView, UnresolvedView, scripted


def an_agent():
    from deepagents import create_deep_agent

    return create_deep_agent(model=scripted(*[AIMessage(content="ok")] * 8), tools=[], middleware=[ConfirmTheView()],
                             checkpointer=InMemorySaver())


async def a_turn(agent, thread, message, view=None):
    payload = {"messages": [{"role": "user", "content": message}]}
    if view is not None:
        payload["viewing"] = {"surface": view.surface, "subject": view.subject()}
    return await agent.ainvoke(payload, config={"configurable": {"thread_id": thread}})


# --- the confirm turn ------------------------------------------------------------------------------------


async def test_THE_FIRST_VIEW_OF_A_THREAD_IS_NOT_CONFIRMED() -> None:
    """There is nothing to have moved from. Asking here would make opening chat cost a turn, every time."""
    state = await a_turn(an_agent(), "t1", "what matched?", FabricView())

    assert not state.get("__interrupt__")


async def test_THE_SAME_VIEW_TWICE_IS_NOT_CONFIRMED() -> None:
    """Paging is not a new subject; a question the person learns to click through is worse than none."""
    agent = an_agent()
    await a_turn(agent, "t2", "what matched?", FabricView())
    state = await a_turn(agent, "t2", "and the rest?", FabricView(page=3))

    assert not state.get("__interrupt__"), "paging produced a confirmation turn"


async def test_A_VIEW_THAT_MOVED_MID_THREAD_IS_CONFIRMED() -> None:
    """**The requirement itself.** The run stops before the model has seen anything."""
    agent = an_agent()
    await a_turn(agent, "t3", "what matched?", FabricView(swatch="cotton-poplin-120gsm"))
    state = await a_turn(agent, "t3", "what about this one?", FabricView(swatch="linen-slub-180gsm"))

    interrupts = state.get("__interrupt__")
    assert interrupts, "the thread switched subject silently -- the next answer is about the previous object"
    assert "different" in interrupts[0].value["question"].lower()
    assert interrupts[0].value["kind"] == "confirm_view"


async def test_A_MISSING_VIEW_MEANS_UNCHANGED_not_moved() -> None:
    """The client sends the view **when it changes**, so absence says nothing moved; treating it as a move would
    confirm on every ordinary turn."""
    agent = an_agent()
    await a_turn(agent, "t4", "what matched?", FabricView())
    state = await a_turn(agent, "t4", "and the rest?")

    assert not state.get("__interrupt__")


async def test_THE_ANSWER_IS_PUT_IN_FRONT_OF_THE_MODEL_IN_FRAMED_FORM() -> None:
    """The person's decision reaches the model as a recorded fact, not as an interrupt payload it never sees."""
    agent = an_agent()
    await a_turn(agent, "t5", "what matched?", FabricView(swatch="cotton-poplin-120gsm"))
    await a_turn(agent, "t5", "what about this one?", FabricView(swatch="linen-slub-180gsm"))

    state = await agent.ainvoke(Command(resume="no, I still mean the first one"),
                                config={"configurable": {"thread_id": "t5"}})

    said = " ".join(str(getattr(m, "content", "")) for m in state["messages"])
    assert "no, I still mean the first one" in said, "the person's decision never reached the conversation"
    assert "<view_confirmation>" in said, "it reached the model as unframed text, indistinguishable from a question"


async def test_THE_THREAD_ADOPTS_THE_NEW_VIEW_ONLY_AFTER_THE_ANSWER() -> None:
    """**Measured rather than read.** If the adoption happened before the interrupt, the replay would confirm the
    move against itself and the second turn would never ask."""
    agent = an_agent()
    first, second = FabricView(swatch="cotton-poplin-120gsm"), FabricView(swatch="linen-slub-180gsm")
    await a_turn(agent, "t6", "what matched?", first)
    interrupted = await a_turn(agent, "t6", "what about this one?", second)

    assert interrupted["confirmed_view"]["subject"] == first.subject(), (
        "the thread adopted the new view while still asking about it"
    )

    resumed = await agent.ainvoke(Command(resume="yes"), config={"configurable": {"thread_id": "t6"}})
    assert resumed["confirmed_view"]["subject"] == second.subject(), (
        "the answer did not move the thread on, so the next turn asks the same question again"
    )


# --- nothing that costs anything before the interrupt ------------------------------------------------------


def _before_the_interrupt(function) -> list[ast.stmt]:
    body = ast.parse(textwrap.dedent(inspect.getsource(function))).body[0].body
    before = []
    for statement in body:
        if any(isinstance(n, ast.Call) and getattr(n.func, "id", None) == "interrupt" for n in ast.walk(statement)):
            return before
        before.append(statement)
    raise AssertionError("`before_agent` never interrupts; this guard is reading the wrong function")


def test_NOTHING_IS_AWAITED_BEFORE_THE_INTERRUPT() -> None:
    """**The rule that fits this node.** Deciding whether to ask requires reading both views, so the pre-interrupt
    half cannot be empty; the guard forbids what actually costs something twice -- every side effect is awaited."""
    awaited = [node for statement in _before_the_interrupt(ConfirmTheView.before_agent)
               for node in ast.walk(statement) if isinstance(node, ast.Await)]

    assert not awaited, "something is awaited before the interrupt; resume replays this node, so it happens twice"


def test_THE_SYNC_CALLS_BEFORE_THE_INTERRUPT_ARE_THE_ONES_WE_MEANT() -> None:
    """A maintained allow-list: a synchronous side effect (a logger, a counter, a cache write) is exactly what
    would slip past the `await` guard above."""
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "?")
        for statement in _before_the_interrupt(ConfirmTheView.before_agent)
        for node in ast.walk(statement) if isinstance(node, ast.Call)
    }
    assert called <= PURE_BEFORE_THE_INTERRUPT, (
        f"{sorted(called - PURE_BEFORE_THE_INTERRUPT)} is called before the interrupt and is not on the allow-list"
    )


def test_THE_QUESTION_NAMES_WHAT_MOVED() -> None:
    """The surface, so the person knows what is being asked without re-reading the screen."""
    assert "swatch" in confirmation_question("swatch").lower()
    assert "collection" in confirmation_question("collection").lower()


def test_THE_MIDDLEWARE_IS_ACTUALLY_INSTALLED() -> None:
    """**A middleware nobody installs is a requirement nobody meets.** Built the way a product builds its chat
    agent, and read off the compiled graph's own input schema, which is what actually runs."""
    from rag_wright.harness.agent import build_chat_agent
    from tests.harness.fabric import _a_context, _fabric, _FakeModel, _resources, _settings

    agent = build_chat_agent(_a_context(), _resources(), _settings(), model=_FakeModel(), definition=_fabric())

    assert "viewing" in agent.get_input_jsonschema()["properties"], (
        "the confirm-the-view middleware is not on the agent a product builds, so a thread that changes subject "
        "switches silently"
    )


def test_A_MODEL_PASSED_TO_THE_CHAT_AGENT_REACHES_EVERY_TIER(monkeypatch) -> None:
    """**Found by this port** (PS-48c). `build_chat_agent(model=)` reached the chat-manager only; the orchestrator
    beneath it built its own client from the product's configuration. So an agent built with a scripted model
    could still make a real, paid call the moment the chat-manager delegated -- and a product passing its own
    model got two different ones. Every tier the call builds uses the model it was given."""
    from rag_wright.harness import agent as agent_module
    from tests.harness.fabric import _a_context, _fabric, _FakeModel, _resources, _settings

    def builds_its_own(*_a, **_k):
        raise AssertionError("a tier built its own model although one was passed in")

    # Both tiers build through this one name: `tiers` imports it from `agent` at call time.
    monkeypatch.setattr(agent_module, "agent_chat_model", builds_its_own)

    agent_module.build_chat_agent(_a_context(), _resources(), _settings(), model=_FakeModel(), definition=_fabric())


# --- the view reaches the run through the door -------------------------------------------------------------


class _Recording:
    """An agent that records what `start_run` handed it."""

    context_schema = FabricContext

    def __init__(self) -> None:
        self.payload: dict = {}

    async def ainvoke(self, payload, config=None, context=None):
        self.payload = payload
        return {"messages": []}


def _context() -> FabricContext:
    return FabricContext(account="acme-mills", season="ss26", collection="resort")


async def test_THE_DOOR_CARRIES_THE_VIEW_INTO_STATE() -> None:
    """The middleware compares the subject in STATE against the checkpoint, and the model reads the rendered
    block in the TURN. Both, or the comparison has nothing to read."""
    from rag_wright.harness.runs import start_run

    agent, view = _Recording(), FabricView(swatch="linen-slub-180gsm")

    await start_run(agent, _context(), message="what about this one?", continuation=view)

    assert agent.payload["viewing"] == {"surface": "swatch", "subject": view.subject()}
    assert agent.payload["messages"][0]["content"].startswith("<viewing>")


async def test_THE_BLOCK_REACHES_THE_RUN_THROUGH_THE_DOOR() -> None:
    """**Unrun code rots silently.** A renderer nothing calls is a guard nobody runs."""
    from rag_wright.harness.runs import start_run

    agent = _Recording()
    await start_run(agent, _context(), message="what about this one?", continuation=FabricView(swatch="linen-slub-180gsm"))
    content = agent.payload["messages"][0]["content"]

    assert content.startswith("<viewing>")
    assert content.endswith("what about this one?"), "the person's own turn was lost"
    assert "linen-slub-180gsm" in content


async def test_A_VIEW_THE_PRODUCT_REFUSES_TO_RENDER_NEVER_REACHES_THE_RUN() -> None:
    """The refusal at the place a route would actually make the mistake: the product's `render()` raises, and the
    run does not start with an unchecked view in its prompt."""
    from rag_wright.harness.runs import start_run

    class Refuses(_Recording):
        async def ainvoke(self, payload, config=None, context=None):
            raise AssertionError("the run started with an unresolved view in its prompt")

    with pytest.raises(UnresolvedView):
        await start_run(Refuses(), _context(), message="hi", continuation=FabricView(resolved=False))


async def test_NO_CONTINUATION_LEAVES_THE_TURN_UNTOUCHED() -> None:
    """Anti-vacuity: most turns carry none, and a block that appeared anyway would be prompt weight on every one."""
    from rag_wright.harness.runs import start_run

    agent = _Recording()
    await start_run(agent, _context(), message="plain question")

    assert agent.payload["messages"][0]["content"] == "plain question"
    assert "viewing" not in agent.payload
