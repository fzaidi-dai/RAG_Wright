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
    """The decision model at the capability layer: D1 is `choice` at `p`; B1 (is the message self-contained) is a yes
    at probability `b1`."""

    def __init__(self, choice: str, p: float, b1: float) -> None:
        self.choice, self.p, self.b1, self.asked = choice, p, b1, []

    async def __call__(self, name: str, inputs: dict, *, resources: object = None) -> dict:
        self.asked.append(inputs)
        if "self_contained" in inputs["questions"]:
            return {"answers": {"self_contained": {"type": "noul", "noul": self.b1}}}
        return {"answers": {"choice": {"type": "choice", "choice": self.choice,
                                       "probabilities": {self.choice: self.p}}}}


@pytest.fixture
def routes(monkeypatch: pytest.MonkeyPatch):
    import rag_wright.api

    def install(choice: str, p: float, *, b1: float = 0.9) -> Routes:
        fake = Routes(choice, p, b1)
        monkeypatch.setattr(rag_wright.api, "ainvoke_model", fake)
        monkeypatch.setattr(rag_wright.api, "capability_index", lambda: {"jev_decision": {}})
        return fake
    return install


def _turn(model: Any, message: str, *, ledger: WhatTheRunDecided | None = None, definition: Any = None,
          earlier: tuple[tuple[str, str], ...] = ()) -> list:
    agent = build_chat_agent(_a_context(), _resources(), _settings(), model=model,
                             definition=definition or loom(variant="small"), ledger=ledger)
    turns = [{"role": role, "content": text} for role, text in earlier] + [{"role": "user", "content": message}]
    out = asyncio.run(agent.ainvoke({"messages": turns}, {"configurable": {"thread_id": uuid.uuid4().hex}},
                                    context=_a_context()))
    return out["messages"]


def _brief(messages: list) -> str:
    (handover,) = [c for m in messages if isinstance(m, AIMessage) for c in m.tool_calls if c["name"] == "task"]
    return handover["args"]["description"]


EARLIER = (("user", "Which mills can we order from?"), ("assistant", "mill-a, mill-b, mill-c and mill-d."))
REPLY = "mill-c is fastest."


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


# --- B1 and the brief (HD-6) ---------------------------------------------------------------------------------


def test_A_FIRST_MESSAGE_DELEGATED_IS_HANDED_OVER_VERBATIM_WITH_NO_MODEL_CALL_AND_NO_B1(routes) -> None:
    """With no earlier turns the message cannot refer back to any, so nothing is asked about it."""
    decision = routes("delegate", 0.8)
    model, offered = recording(AIMessage(content=REPLY))
    ledger = WhatTheRunDecided(points=())

    with only_the_chat_manager(loom(variant="small"), reply=REPLY):
        messages = _turn(model, "Which mill is best for 8,000 m of linen twill?", ledger=ledger)

    assert _brief(messages) == "Which mill is best for 8,000 m of linen twill?"
    assert len(offered) == 1, "the only model call is the reply after the orchestrator answered"
    assert len(decision.asked) == 1, "D1 only"
    made = _d1(ledger)
    assert (made.chose, made.confidence, made.inputs["route"]) == ("delegate", 0.8, "delegate")
    assert (made.inputs["brief"], made.inputs["brief_by"]) == ("Which mill is best for 8,000 m of linen twill?",
                                                               "verbatim")


def test_A_SELF_CONTAINED_MESSAGE_AFTER_EARLIER_TURNS_IS_HANDED_OVER_VERBATIM(routes) -> None:
    decision = routes("delegate", 0.8, b1=0.9)
    model, offered = recording(AIMessage(content=REPLY))
    ledger = WhatTheRunDecided(points=())

    with only_the_chat_manager(loom(variant="small"), reply=REPLY):
        messages = _turn(model, "Compare mill-a and mill-c for 5,000 m of poplin.", ledger=ledger, earlier=EARLIER)

    assert _brief(messages) == "Compare mill-a and mill-c for 5,000 m of poplin."
    assert len(offered) == 1
    assert decision.asked[1]["state"].startswith("Earlier turns:\nuser: Which mills can we order from?")
    assert _d1(ledger).inputs["self_contained"] == 0.9


def test_A_MESSAGE_THAT_REFERS_BACK_HAS_ITS_BRIEF_WRITTEN_BY_THE_MODEL(routes) -> None:
    routes("delegate", 0.8, b1=0.2)
    model, offered = recording(_call("task", {"description": "Which of mill-a, mill-b, mill-c, mill-d is fastest?",
                                              "subagent_type": "orchestrator"}), AIMessage(content=REPLY))
    ledger = WhatTheRunDecided(points=())

    with only_the_chat_manager(loom(variant="small"), reply=REPLY):
        _turn(model, "Which of them is fastest?", ledger=ledger, earlier=EARLIER)

    assert offered[0] == ["task"], "the model writes the brief, offered the hand-off alone"
    assert (_d1(ledger).inputs["brief_by"], _d1(ledger).inputs["self_contained"]) == ("model", 0.2)


def test_A_PRODUCTS_OWN_B1_THRESHOLD_DECIDES_WHEN_A_BRIEF_IS_VERBATIM(routes) -> None:
    """0.9 is verbatim at the default 0.6; a product that asks for 0.95 has the model write it."""
    routes("delegate", 0.8, b1=0.9)
    model, offered = recording(_call("task", {"description": "Compare mill-a and mill-c.",
                                              "subagent_type": "orchestrator"}), AIMessage(content=REPLY))
    stricter = dataclasses.replace(loom(variant="small"), routing=RoutingThresholds(b1=0.95))

    with only_the_chat_manager(stricter, reply=REPLY):
        _turn(model, "Compare mill-a and mill-c for 5,000 m of poplin.", definition=stricter, earlier=EARLIER)

    assert offered[0] == ["task"]


def test_WHEN_B1_CANNOT_BE_ASKED_THE_MODEL_WRITES_THE_BRIEF(routes, monkeypatch: pytest.MonkeyPatch) -> None:
    """The decision model fails on the B1 question: the brief is the model's, never a guess that it stands alone."""
    decision = routes("delegate", 0.8)

    async def b1_fails(name: str, inputs: dict, *, resources: object = None) -> dict:
        if "self_contained" in inputs["questions"]:
            raise RuntimeError("the decision model is down")
        return await decision(name, inputs, resources=resources)

    import rag_wright.api

    monkeypatch.setattr(rag_wright.api, "ainvoke_model", b1_fails)
    model, offered = recording(_call("task", {"description": "Which mill is fastest?", "subagent_type": "orchestrator"}),
                               AIMessage(content=REPLY))
    ledger = WhatTheRunDecided(points=())

    with only_the_chat_manager(loom(variant="small"), reply=REPLY):
        _turn(model, "Which of them is fastest?", ledger=ledger, earlier=EARLIER)

    assert offered[0] == ["task"]
    assert (_d1(ledger).inputs["brief_by"], _d1(ledger).inputs["self_contained"]) == ("model", None)


# --- forcing the tool the router chose (HD-9) ---------------------------------------------------------------


def _choices(*messages: AIMessage) -> tuple[Any, list]:
    """A scripted chat model that records the `tool_choice` each call was bound with."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    chosen: list = []

    class Choosing(GenericFakeChatModel):
        choice: Any = None

        def bind_tools(self, tools: Any, *, tool_choice: Any = None, **_kwargs: Any) -> Any:
            return self.model_copy(update={"choice": tool_choice})

        async def _agenerate(self, msgs: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any):
            chosen.append(self.choice)
            return await super()._agenerate(msgs, stop=stop, run_manager=run_manager, **kwargs)

    return Choosing(messages=iter(messages)), chosen


def test_A_MODEL_MEASURED_TO_HONOUR_FORCING_IS_FORCED_TO_THE_TOOL_THE_ROUTER_CHOSE(routes, monkeypatch) -> None:
    """Measured (HD-9): offered alone but not forced, the chat model answered a routed `ask_the_user` with a reply 4
    times of 18; forced, 0 of 18."""
    import rag_wright.harness.agent as agent_module

    monkeypatch.setattr(agent_module, "forces_a_tool", lambda definition, settings, model=None: True)
    routes("ask_the_user", 0.9)
    model, chosen = _choices(_call("ask_the_user", {"question": "Which standard?"}))

    _turn(model, "Check it against the standard.")  # the run pauses at the person's interrupt

    assert chosen[0] == "ask_the_user"


def test_A_MODEL_NOT_KNOWN_TO_HONOUR_FORCING_IS_OFFERED_THE_TOOL_UNFORCED(routes) -> None:
    routes("read_swatch", 0.9)
    model, chosen = _choices(_call("read_swatch", {"swatch": "sw-1042"}), AIMessage(content="A poplin."))

    _turn(model, "What is sw-1042 made of?")

    assert chosen[0] is None


def test_ONLY_THE_CONFIGURED_MODEL_WITH_A_MEASURED_PROFILE_IS_FORCED(monkeypatch: pytest.MonkeyPatch) -> None:
    import rag_wright.api
    from rag_wright.api import ModelProfile
    from rag_wright.harness.agent import forces_a_tool

    measured = {"hd9-honours": ModelProfile(model_id="hd9-honours", forced_tool_choice=True)}
    monkeypatch.setattr(rag_wright.api, "profile_for", lambda model_id: measured.get(model_id, ModelProfile(model_id=model_id)))
    honours = dataclasses.replace(loom(variant="small"), model_id_for=lambda settings: "hd9-honours")
    unknown = dataclasses.replace(loom(variant="small"), model_id_for=lambda settings: "hd9-never-measured")

    assert forces_a_tool(honours, _settings()) is True
    assert forces_a_tool(unknown, _settings()) is False, "a model nobody measured is never assumed to honour it"
    assert forces_a_tool(honours, _settings(), model=object()) is False, "a model passed in has no profile"


def test_THE_MEASURED_PROFILE_SAYS_IT_HONOURS_FORCING() -> None:
    from rag_wright.api import profile_for

    assert profile_for("qwen3.8-27b-modal-or").forced_tool_choice is True



# --- routing off (HD-10) --------------------------------------------------------------------------------------


def test_ROUTING_OFF_NEVER_ASKS_THE_DECISION_MODEL_AND_THE_TURN_IS_AS_BEFORE(routes) -> None:
    """A product that must not send its users' messages to the decision model's provider turns routing off."""
    decision = routes("list_mills", 0.9)
    model, offered = recording(AIMessage(content="We have two mills."))

    _turn(model, "Which mills can we order from?", definition=dataclasses.replace(loom(variant="small"), routing=None))

    assert decision.asked == []
    assert {"list_mills", "read_swatch", "task", "ask_the_user", "ls"} <= set(offered[0])
