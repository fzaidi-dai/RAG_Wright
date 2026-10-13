"""PS-36: an agent's chat model from a model profile, on `rag_wright.api`.

The engine model id (the profile key) decides the backend, endpoint, key, served id and the agent turn's reasoning
control (`agent_extra_body`), so a product names no model, vendor or vendor flag in code. Every call the model makes
-- plain, streamed, tool-bound, or a deep agent's -- is metered into `measure_usage()` with the provider's real cost,
and traced as one Langfuse generation when tracing is on. The HTTP layer is an in-process `httpx.MockTransport`."""
from __future__ import annotations

import json

import httpx
import pytest
from langchain_core.tools import tool

from rag_wright.api import ModelRole, agent_chat_model, measure_usage
from rag_wright.models import profiles, tracing


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setenv("VLLM_BASE_URL", "http://vllm.local/v1")
    monkeypatch.setenv("VLLM_API_KEY", "vllm-test")
    monkeypatch.delenv("OPENROUTER_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_PROXY", raising=False)


def _completion(content="done", *, cost=0.0012, tool_calls=None):
    usage = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    if cost is not None:
        usage["cost"] = cost
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"id": "gen-1", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}], "usage": usage}


def _sse(content="done", *, cost=0.0034):
    def chunk(delta, *, finish=None, usage=None):
        body = {"id": "gen-2", "object": "chat.completion.chunk", "created": 0, "model": "m",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}] if usage is None else []}
        if usage is not None:
            body["usage"] = usage
        return f"data: {json.dumps(body)}\n\n"
    usage = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
    if cost is not None:
        usage["cost"] = cost
    return "".join([chunk({"role": "assistant", "content": content[:2]}), chunk({"content": content[2:]}),
                    chunk({}, finish="stop"), chunk({}, usage=usage), "data: [DONE]\n\n"])


class _Server:
    """Answers chat completions; records each request body."""

    def __init__(self, replies=None, *, stream_cost=0.0034):
        self.requests: list[dict] = []
        self.replies = list(replies or [])
        self.stream_cost = stream_cost

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append({"url": str(request.url), "body": body,
                              "auth": request.headers.get("authorization")})
        if body.get("stream"):
            return httpx.Response(200, text=_sse(cost=self.stream_cost),
                                  headers={"content-type": "text/event-stream"})
        reply = self.replies.pop(0) if self.replies else _completion()
        return httpx.Response(200, json=reply)


def _model(server, model=None, **kw):
    transport = httpx.MockTransport(server)
    return agent_chat_model(model, http_client=httpx.Client(transport=transport),
                            http_async_client=httpx.AsyncClient(transport=transport), **kw)


def test_the_profile_decides_endpoint_served_id_and_agent_reasoning():
    server = _Server()
    _model(server, "qwen3.8-27b-modal-or").invoke("hi")
    req = server.requests[0]
    assert req["url"].startswith("https://openrouter.ai/api/v1/")
    assert req["body"]["model"] == "qwen/qwen3.8-27b"
    assert req["body"]["reasoning"] == {"enabled": True}  # the agent turn's slot, not the free-text one
    assert req["auth"] == "Bearer or-test"


def test_a_self_hosted_profile_gets_its_own_spelling():
    server = _Server()
    _model(server, "qwen3.8-27b-modal").invoke("hi")
    req = server.requests[0]
    assert req["url"].startswith("http://vllm.local/v1/")
    assert req["body"]["model"] == "Qwen/Qwen3.8-27B"
    assert req["body"]["chat_template_kwargs"] == {"enable_thinking": True}
    assert "reasoning" not in req["body"]


def test_no_model_means_the_general_role(monkeypatch):
    monkeypatch.setenv("RAG_MODEL_GENERAL", "qwen3.8-27b-modal")
    assert profiles.model_for(ModelRole.GENERAL) == "qwen3.8-27b-modal"
    server = _Server()
    _model(server).invoke("hi")
    assert server.requests[0]["body"]["model"] == "Qwen/Qwen3.8-27B"


def test_a_model_without_agent_settings_sends_only_its_base_body():
    server = _Server()
    _model(server, "deepseek/deepseek-v4-pro").invoke("hi")
    body = server.requests[0]["body"]
    assert body["provider"] == {"sort": "throughput"} and "reasoning" not in body


def test_framework_settings_pass_through():
    m = _model(_Server(), "qwen3.8-27b-modal-or", timeout=12.0, max_retries=0, temperature=0.3)
    assert (m.request_timeout, m.max_retries, m.temperature) == (12.0, 0, 0.3)


def test_a_call_is_metered_with_the_real_cost():
    m = _model(_Server(), "qwen3.8-27b-modal-or")
    with measure_usage() as usage:
        m.invoke("hi")
    assert (usage.calls, usage.input_tokens, usage.output_tokens) == (1, 11, 7)
    assert usage.cost_usd == pytest.approx(0.0012) and usage.calls_without_cost == 0
    assert set(usage.by_model) == {"qwen3.8-27b-modal-or"}


def test_a_call_without_a_reported_cost_is_unknown_not_free():
    m = _model(_Server([_completion(cost=None)]), "qwen3.8-27b-modal")
    with measure_usage() as usage:
        m.invoke("hi")
    assert usage.calls == 1 and usage.calls_without_cost == 1 and usage.cost_usd == 0.0


async def test_a_streamed_call_keeps_the_cost_streaming_drops():
    m = _model(_Server(stream_cost=0.0034), "qwen3.8-27b-modal-or")
    with measure_usage() as usage:
        text = "".join([str(c.content) async for c in m.astream("hi")])
    assert text == "done"
    assert (usage.calls, usage.input_tokens, usage.output_tokens) == (1, 5, 3)
    assert usage.cost_usd == pytest.approx(0.0034)


async def test_concurrent_streams_keep_their_own_costs():
    import asyncio
    m = _model(_Server(stream_cost=0.5), "qwen3.8-27b-modal-or")
    with measure_usage() as usage:
        await asyncio.gather(*[m.ainvoke("hi") for _ in range(3)])
        await asyncio.gather(*[_drain(m) for _ in range(3)])
    assert usage.calls == 6
    assert usage.cost_usd == pytest.approx(3 * 0.0012 + 3 * 0.5)


async def _drain(m):
    return [c async for c in m.astream("hi")]


async def test_a_tool_bound_call_is_metered():
    @tool
    def lookup(q: str) -> str:
        """Look something up."""
        return q

    m = _model(_Server(), "qwen3.8-27b-modal-or")
    with measure_usage() as usage:
        await m.bind_tools([lookup]).ainvoke("hi")
    assert usage.calls == 1


def test_a_call_is_traced_as_one_generation(monkeypatch):
    opened, finished = [], []
    monkeypatch.setattr(tracing, "tracing_on", lambda: True)
    monkeypatch.setattr(tracing, "start_generation", lambda **kw: opened.append(kw) or object())
    monkeypatch.setattr(tracing, "finish_generation", lambda gen, **kw: finished.append(kw))
    _model(_Server(), "qwen3.8-27b-modal-or").invoke("hi")
    assert len(opened) == 1 and opened[0]["model"] == "qwen3.8-27b-modal-or"
    assert finished[0]["usage"] == {"input": 11, "output": 7}
    assert finished[0]["cost"] == pytest.approx(0.0012) and finished[0]["latency_ms"] >= 0


def test_a_failed_call_closes_its_generation(monkeypatch):
    opened, finished = [], []
    monkeypatch.setattr(tracing, "tracing_on", lambda: True)
    monkeypatch.setattr(tracing, "start_generation", lambda **kw: opened.append(kw) or object())
    monkeypatch.setattr(tracing, "finish_generation", lambda gen, **kw: finished.append(kw))
    m = _model(lambda request: httpx.Response(400, json={"error": {"message": "bad"}}),
               "qwen3.8-27b-modal-or", max_retries=0)
    with pytest.raises(Exception):
        m.invoke("hi")
    assert len(opened) == 1 and finished[0]["metadata"]["error"] == "BadRequestError"


def test_tracing_off_opens_nothing(monkeypatch):
    monkeypatch.setattr(tracing, "tracing_on", lambda: False)
    monkeypatch.setattr(tracing, "start_generation", lambda **kw: pytest.fail("traced while off"))
    _model(_Server(), "qwen3.8-27b-modal-or").invoke("hi")


async def test_a_deep_agent_runs_on_it_and_every_turn_is_metered():
    from deepagents import create_deep_agent

    call = {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": '{"q": "x"}'}}

    @tool
    def lookup(q: str) -> str:
        """Look something up."""
        return "found " + q

    server = _Server([_completion("", tool_calls=[call]), _completion("the answer")])
    agent = create_deep_agent(model=_model(server, "qwen3.8-27b-modal-or"), tools=[lookup])
    with measure_usage() as usage:
        out = await agent.ainvoke({"messages": [{"role": "user", "content": "find x"}]})
    assert out["messages"][-1].content == "the answer"
    assert usage.calls == 2 and usage.cost_usd == pytest.approx(0.0024)


def test_trace_false_meters_the_call_and_opens_no_generation(monkeypatch):
    """PS-32: a caller that emits its own generations (the agent harness) turns the engine's off, so a call is
    traced once, never twice; it is still metered."""
    monkeypatch.setattr(tracing, "tracing_on", lambda: True)
    monkeypatch.setattr(tracing, "start_generation", lambda **kw: pytest.fail("traced although trace=False"))
    with measure_usage() as usage:
        _model(_Server(), "qwen3.8-27b-modal-or", trace=False).invoke("hi")
    assert usage.calls == 1 and usage.cost_usd == pytest.approx(0.0012)
