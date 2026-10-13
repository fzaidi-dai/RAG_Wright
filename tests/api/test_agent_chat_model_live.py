"""PS-36 live: one deep agent, on the engine's default model through its profile, calls a tool and answers, and the
usage scope sees each of its turns with the provider's real cost."""
from __future__ import annotations

import pytest
from langchain_core.tools import tool

from rag_wright.api import agent_chat_model, measure_usage


@pytest.mark.model
async def test_a_deep_agent_on_a_profile_calls_a_tool_and_is_metered():
    from deepagents import create_deep_agent

    seen: list[str] = []

    @tool
    def capital_of(country: str) -> str:
        """Return the capital city of a country."""
        seen.append(country)
        return {"france": "Paris"}.get(country.strip().lower(), "unknown")

    agent = create_deep_agent(model=agent_chat_model("qwen3.8-27b-modal-or", max_retries=1), tools=[capital_of],
                              system_prompt="Answer using the capital_of tool. Reply with the city name only.")
    with measure_usage() as usage:
        out = await agent.ainvoke({"messages": [{"role": "user", "content": "What is the capital of France?"}]})
    print(f"\ncalls={usage.calls} in={usage.input_tokens} out={usage.output_tokens} cost=${usage.cost_usd:.5f} "
          f"unpriced={usage.calls_without_cost} tool_args={seen}")
    assert seen, "the agent never called the tool"
    assert "paris" in str(out["messages"][-1].content).lower()
    assert usage.calls >= 2 and usage.input_tokens > 0 and usage.output_tokens > 0
    assert usage.calls_without_cost == 0 and usage.cost_usd > 0
