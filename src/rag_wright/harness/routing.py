"""D1 by the decision model: the chat-manager's turn is routed before its model is asked to route it (ADR-0146, HD-5).

**The opening call of a turn is the routing decision**, and the harness's own measurement calls D1 a classification
(reasoning off chose what reasoning on chose, in a seventh of the time). So on that call the decider picks among the
product's direct tools, `ask_the_user`, `delegate` and `answer`, each described by its own description; at the
product's D1 threshold (`RoutingThresholds.d1`, 0.7 by default) or more the route is taken:

- a direct tool that takes no arguments is called in code, with no model call;
- a tool that takes arguments, `ask_the_user` or `delegate` goes to the model with that one tool, to fill it in;
- `answer` goes to the model with no tools.

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
from rag_wright.harness.seen import run_key

D1_QUESTION = "What should the assistant do with the person's latest message? Pick the one action that serves it."
ANSWER = "Reply directly: a greeting, thanks, or a general question that needs no data."
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
    operations; `delegating` is what handing the request to the orchestrator does (its description); `threshold`
    is the probability at or above which a route is taken."""

    def __init__(self, decider: Decider, *, direct: frozenset[str], delegating: str, threshold: float) -> None:
        super().__init__()
        self._decider = decider
        self._threshold = threshold
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
        if routed is None or routed.confidence < self._threshold:
            return await handler(request)
        chosen = {"answer": None, "delegate": tools.get(_DELEGATE)}.get(routed.choice, tools.get(routed.choice))
        if chosen is not None and not _takes_arguments(chosen):
            reply = AIMessage(content="", tool_calls=[{"name": routed.choice, "args": {},
                                                       "id": f"routed-{uuid.uuid4().hex}"}])
        else:
            reply = await handler(request.override(tools=[chosen] if chosen is not None else []))
        return _stamped(reply, {"choice": routed.choice, "confidence": routed.confidence})

    def _options(self, tools: dict[str, Any]) -> dict[str, str]:
        options = {name: getattr(tool, "description", "") for name, tool in tools.items() if name in self._offered}
        if _DELEGATE in tools:
            options["delegate"] = self._delegating
        options["answer"] = ANSWER
        return options


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
