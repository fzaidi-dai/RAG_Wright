"""D1 by the decision model: the chat-manager's turn is routed before its model is asked to route it (ADR-0146, HD-5).

**The opening call of a turn is the routing decision**, and the harness's own measurement calls D1 a classification
(reasoning off chose what reasoning on chose, in a seventh of the time). So on that call the decider picks among the
product's direct tools, `ask_the_user`, `delegate` and `answer`, each described by its own description; at the
product's D1 threshold (`RoutingThresholds.d1`, 0.7 by default) or more the route is taken:

- a direct tool that takes no arguments is called in code, with no model call;
- a tool that takes arguments, `ask_the_user` or `delegate` goes to the model with that one tool, to fill it in;
- `answer` goes to the model with no tools.

**A delegated message that stands on its own is handed over in the person's own words** (B1, HD-6): the brief is
the opening turn as the harness rendered it, `<viewing>` block included, so nothing is lost or reworded on the way to
the orchestrator. It stands on its own when there are no earlier turns, or when the decider says at the product's B1
threshold (`RoutingThresholds.b1`, 0.6 by default) that it can be understood without them. Otherwise the model writes
the brief, offered the hand-off alone.

Below the threshold, or when the decider is unavailable, the call is exactly what it was. The route and its
probability ride on the reply (`ROUTED`), so the decision ledger records the confidence when that reply settles D1.

**Innermost in the chat-manager's middleware**, so everything that shapes or observes a model call still does: the
dispatch bound has already removed `task` when it is spent, and the decision observer sees a reply built here exactly
as it sees the model's.
"""

from __future__ import annotations

import uuid
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage

from rag_wright.harness.clarify import clarification_tool
from rag_wright.harness.decider import ROUTED, Decider
from rag_wright.harness.definition import RoutingThresholds
from rag_wright.harness.seen import run_key

D1_QUESTION = "What should the assistant do with the person's latest message? Pick the one action that serves it."
ANSWER = "Reply directly: a greeting, thanks, or a general question that needs no data."
B1_QUESTION = ("Can the latest message be understood on its own, without the earlier turns? It cannot when it points "
               "back to something only the earlier turns name: 'the second one', 'it', 'those two', 'same for'.")
#: How many earlier text turns B1 is shown; a reference reaches back a turn or two, not a whole conversation.
EARLIER_TURNS = 6
_DELEGATE = "task"


def _name_of(tool: Any) -> str:
    return getattr(tool, "name", None) or (tool.get("name", "") if isinstance(tool, dict) else str(tool))


def _text(message: Any) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return " ".join(block.get("text", "") for block in content if isinstance(block, dict))


class RouteTheTurn(AgentMiddleware):
    """Routes the chat-manager's opening call of a turn with the decider. `direct` is the product's direct
    operations; `delegating` is what handing the request to the orchestrator does (its description) and
    `orchestrator` its subagent name; `thresholds` is the product's `RoutingThresholds`."""

    def __init__(self, decider: Decider, *, direct: frozenset[str], delegating: str, orchestrator: str,
                 thresholds: RoutingThresholds) -> None:
        super().__init__()
        self._decider = decider
        self._thresholds = thresholds
        self._orchestrator = orchestrator
        self._offered = direct | {clarification_tool().name}
        self._delegating = delegating

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(request)  # the decider is asynchronous; a synchronous run routes as before

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        messages = request.messages or []
        if not messages or not isinstance(messages[-1], HumanMessage):
            return await handler(request)  # not the opening call: the turn is already under way
        tools = {_name_of(tool): tool for tool in request.tools or []}
        options = self._options(tools)
        routed = await self._decider.choose(run_key(request), _text(messages[-1]), D1_QUESTION, options)
        if routed is None or routed.confidence < self._thresholds.d1:
            return await handler(request)
        stamp = {"choice": routed.choice, "confidence": routed.confidence}
        if routed.choice == "delegate":
            brief, stamp["brief"] = await self._brief(request, messages)
            if brief is not None:
                return _stamped(_called(_DELEGATE, {"description": brief, "subagent_type": self._orchestrator}), stamp)
        chosen = {"answer": None, "delegate": tools.get(_DELEGATE)}.get(routed.choice, tools.get(routed.choice))
        if chosen is not None and not _takes_arguments(chosen):
            reply = _called(routed.choice, {})
        else:
            reply = await handler(request.override(tools=[chosen] if chosen is not None else []))
        return _stamped(reply, stamp)

    async def _brief(self, request: Any, messages: list) -> tuple[str | None, dict]:
        """The person's opening turn as the brief when it stands on its own (B1), else None for the model to write
        one; with how it was decided, for the ledger."""
        opening = _text(messages[-1])
        earlier = [m for m in messages[:-1] if isinstance(m, (HumanMessage, AIMessage)) and _text(m).strip()]
        if not earlier:
            return opening, {"by": "verbatim", "self_contained": None}
        yes = await self._decider.yes_no(run_key(request), _transcript(earlier[-EARLIER_TURNS:], opening),
                                         {"self_contained": (B1_QUESTION, "yes")})
        p = None if yes is None else yes["self_contained"]
        if p is not None and p >= self._thresholds.b1:
            return opening, {"by": "verbatim", "self_contained": p}
        return None, {"by": "model", "self_contained": p}

    def _options(self, tools: dict[str, Any]) -> dict[str, str]:
        options = {name: getattr(tool, "description", "") for name, tool in tools.items() if name in self._offered}
        if _DELEGATE in tools:
            options["delegate"] = self._delegating
        options["answer"] = ANSWER
        return options


def _called(name: str, args: dict) -> AIMessage:
    """A tool call the router makes itself, with no model call."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"routed-{uuid.uuid4().hex}"}])


def _transcript(earlier: list, opening: str) -> str:
    """The earlier turns and the latest message, as B1 was measured on them (HD-2)."""
    turns = "\n".join(f"{'user' if isinstance(m, HumanMessage) else 'assistant'}: {_text(m)}" for m in earlier)
    return f"Earlier turns:\n{turns}\n\nLatest message:\n{opening}"


def _takes_arguments(tool: Any) -> bool:
    return bool(getattr(tool, "args", None) or (tool.get("parameters", {}).get("properties")
                                                 if isinstance(tool, dict) else None))


def _stamped(reply: Any, routed: dict) -> Any:
    """The reply with the route on its AI message, whether the handler returned a message or a model response."""
    messages = getattr(reply, "result", None) or [reply]
    for message in messages:
        if isinstance(message, AIMessage):
            message.response_metadata[ROUTED] = routed
    return reply
