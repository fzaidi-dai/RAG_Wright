"""D1 is routed by the decision model on the chat-manager's opening call (HD-5, ADR-0146).

Driven through the real `build_chat_agent` on Loom's four-tool chat-manager. The decision model is a fake at the
capability layer; the chat model records which tools each of its calls was offered, so each test reads exactly which
calls the turn made and what they could do.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from rag_wright.harness import RoutingThresholds, build_chat_agent, only_the_chat_manager
from rag_wright.harness.decisions import WhatTheRunDecided
from tests.harness.fabric import _a_context, _resources, _settings
from tests.harness.gold.loom import loom

SMALL_OPTIONS = {"list_mills", "read_swatch", "list_swatches", "read_order", "ask_the_user", "delegate", "answer"}


def _name(tool: Any) -> str:
    return getattr(tool, "name", None) or tool.get("name", "")


def recording(*messages: AIMessage) -> tuple[Any, list[list[str]]]:
    """A scripted chat model that records, per call, the names of the tools that call was offered."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    offered: list[list[str]] = []

    class Recording(GenericFakeChatModel):
        bound: list[str] = []

        def bind_tools(self, tools: Any, **_kwargs: Any) -> Any:
            return self.model_copy(update={"bound": [_name(t) for t in tools]})

        async def _agenerate(self, msgs: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any):
            offered.append(list(self.bound))
            return await super()._agenerate(msgs, stop=stop, run_manager=run_manager, **kwargs)

    return Recording(messages=iter(messages)), offered


class Routes:
    """The decision model at the capability layer: answers every routing question with `choice` at `p`."""

    def __init__(self, choice: str, p: float) -> None:
        self.choice, self.p, self.asked = choice, p, []

    async def __call__(self, name: str, inputs: dict, *, resources: object = None) -> dict:
        self.asked.append(inputs)
        return {"answers": {"choice": {"type": "choice", "choice": self.choice,
                                       "probabilities": {self.choice: self.p}}}}


@pytest.fixture
def routes(monkeypatch: pytest.MonkeyPatch):
    import rag_wright.api

    def install(choice: str, p: float) -> Routes:
        fake = Routes(choice, p)
        monkeypatch.setattr(rag_wright.api, "ainvoke_model", fake)
        monkeypatch.setattr(rag_wright.api, "capability_index", lambda: {"jev_decision": {}})
        return fake
    return install


def _turn(model: Any, message: str, *, ledger: WhatTheRunDecided | None = None, definition: Any = None) -> list:
    agent = build_chat_agent(_a_context(), _resources(), _settings(), model=model,
                             definition=definition or loom(variant="small"), ledger=ledger)
    out = asyncio.run(agent.ainvoke({"messages": [{"role": "user", "content": message}]},
                                    {"configurable": {"thread_id": uuid.uuid4().hex}}, context=_a_context()))
    return out["messages"]


def _call(name: str, args: dict) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c-{name}"}])


def _d1(ledger: WhatTheRunDecided) -> Any:
    (run,) = ledger.runs.values()
    (made,) = run.by_point("D1")
    return made


def test_A_CONFIDENT_ROUTE_TO_A_TOOL_WITHOUT_ARGUMENTS_IS_CALLED_WITH_NO_ROUTING_CALL(routes) -> None:
    routes("list_mills", 0.9)
    model, offered = recording(AIMessage(content="We can order from mill-a and mill-b."))

    messages = _turn(model, "Which mills can we order from?")

    assert len(offered) == 1, "the only model call is the reply after the tool ran"
    assert any(isinstance(m, ToolMessage) and m.name == "list_mills" for m in messages)


def test_A_CONFIDENT_ROUTE_TO_A_TOOL_WITH_ARGUMENTS_OFFERS_THE_MODEL_THAT_TOOL_ALONE(routes) -> None:
    decision = routes("read_swatch", 0.9)
    model, offered = recording(_call("read_swatch", {"swatch": "sw-1042"}), AIMessage(content="It is a poplin."))

    _turn(model, "What is sw-1042 made of?")

    assert offered[0] == ["read_swatch"]
    assert "read_swatch" in offered[1] and "task" in offered[1], "the rest of the turn has every tool again"
    assert len(decision.asked) == 1, "only the opening call is routed"


def test_THE_OPTIONS_ARE_THE_DIRECT_TOOLS_ASKING_DELEGATING_AND_ANSWERING(routes) -> None:
    decision = routes("answer", 0.9)
    model, _offered = recording(AIMessage(content="Hello!"))

    _turn(model, "Hi!")

    assert set(decision.asked[0]["questions"]["choice"]["criteria"]) == SMALL_OPTIONS
    assert decision.asked[0]["state"] == "Hi!"


def test_A_CONFIDENT_ANSWER_IS_WRITTEN_WITH_NO_TOOLS_AND_D1_CARRIES_ITS_CONFIDENCE(routes) -> None:
    routes("answer", 0.9)
    model, offered = recording(AIMessage(content="Hello! How can I help?"))
    ledger = WhatTheRunDecided(points=())

    _turn(model, "Hi!", ledger=ledger)

    assert offered == [[]]
    made = _d1(ledger)
    assert (made.chose, made.confidence, made.inputs["route"]) == ("answer_directly", 0.9, "answer")


def test_A_CONFIDENT_DELEGATE_OFFERS_THE_HANDOFF_ALONE_AND_D1_CARRIES_ITS_CONFIDENCE(routes) -> None:
    routes("delegate", 0.8)
    model, offered = recording(_call("task", {"description": "Compare mills for 8,000 m of linen twill.",
                                              "subagent_type": "orchestrator"}),
                               AIMessage(content="mill-c is fastest."))
    ledger = WhatTheRunDecided(points=())

    with only_the_chat_manager(loom(variant="small"), reply="mill-c is fastest."):
        _turn(model, "Which mill is best for 8,000 m of linen twill?", ledger=ledger)

    assert offered[0] == ["task"]
    made = _d1(ledger)
    assert (made.chose, made.confidence, made.inputs["route"]) == ("delegate", 0.8, "delegate")


def test_BELOW_THE_THRESHOLD_THE_TURN_IS_EXACTLY_AS_BEFORE(routes) -> None:
    routes("list_mills", RoutingThresholds().d1 - 0.01)
    model, offered = recording(AIMessage(content="Which mills? We have two."))

    _turn(model, "Which mills can we order from?")

    assert {"list_mills", "read_swatch", "task", "ask_the_user", "ls"} <= set(offered[0])


def test_WITH_NO_DECISION_MODEL_THE_TURN_IS_EXACTLY_AS_BEFORE() -> None:
    """The harness conftest keeps the decision model out of the catalog, as for a product that never registers it."""
    model, offered = recording(AIMessage(content="We have two mills."))
    ledger = WhatTheRunDecided(points=())

    _turn(model, "Which mills can we order from?", ledger=ledger)

    assert {"list_mills", "read_swatch", "task", "ask_the_user", "ls"} <= set(offered[0])
    assert _d1(ledger).confidence is None


def test_A_PRODUCTS_OWN_THRESHOLD_DECIDES_WHEN_A_ROUTE_IS_TAKEN(routes) -> None:
    """A route the default would take (0.9 >= 0.7) is not taken by a product that asks for 0.95."""
    routes("list_mills", 0.9)
    model, offered = recording(AIMessage(content="We have two mills."))
    stricter = dataclasses.replace(loom(variant="small"), routing=RoutingThresholds(d1=0.95))

    _turn(model, "Which mills can we order from?", definition=stricter)

    assert {"list_mills", "read_swatch", "task"} <= set(offered[0]), "routed as before: the model chose"


def test_A_THRESHOLD_THAT_IS_NOT_A_PROBABILITY_IS_REFUSED() -> None:
    with pytest.raises(ValueError, match="d1"):
        RoutingThresholds(d1=1.5)
