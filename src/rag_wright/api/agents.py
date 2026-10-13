"""PS-36 (ADR-0139): an agent's chat model, from a model profile.

A product builds its agents on Deep Agents or LangGraph and asks the engine for the model they reason with, by
engine model id, the same strings every other engine model setting uses. The profile decides the backend, endpoint,
key, served id and the agent turn's reasoning flag, so the product names no model, vendor or vendor flag in code,
and a profile change moves every agent with it. Each call is metered into `measure_usage()` (with the provider's
real per-call cost) and traced as a Langfuse generation when tracing is on.

    model = agent_chat_model()                       # the GENERAL role's model (workspace / env overrides apply)
    agent = create_deep_agent(model=model, tools=[...])
    with measure_usage() as usage:
        await agent.ainvoke({"messages": [...]})
"""
from __future__ import annotations

from typing import Any, Optional

from langchain_core.language_models import BaseChatModel

from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_agent_model

__all__ = ["agent_chat_model"]


def agent_chat_model(model: Optional[str] = None, *, temperature: float = 0.0, trace: bool = True,
                     **overrides: Any) -> BaseChatModel:
    """The chat model for an agent: `model` is an engine model id (a registered profile's key, or any served id);
    `None` takes the GENERAL role's model. `overrides` are the chat model's own settings (`timeout`, `max_retries`,
    `max_tokens`, `callbacks`, ...). Retries are the caller's to place: an agent harness usually keeps them in its
    middleware and passes `max_retries=0`. `trace=False` keeps the metering and emits no Langfuse generation, for a
    caller that emits its own (so a call is never traced twice)."""
    return build_agent_model(model or model_for(ModelRole.GENERAL), temperature=temperature, trace=trace,
                             **overrides)
