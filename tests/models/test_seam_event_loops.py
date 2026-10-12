"""PS-24: async model calls work on every event loop in a process.

langchain_openai shares one cached async HTTP client per base URL and timeout across the process, and its pooled
connections belong to the event loop that opened them. A second `asyncio.run` (Langfuse runs each experiment on a
fresh loop; a web backend has its own loop beside a worker's) then reuses a dead connection and fails at once with
`APIConnectionError` (EV-0 finding 5). The seam gives each event loop its own client.

Hermetic: a keep-alive OpenAI-compatible server on 127.0.0.1.
"""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from rag_wright.models import seam
from rag_wright.models.seam import build_model

_REPLY = {"id": "c1", "object": "chat.completion", "created": 0, "model": "m",
          "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
          "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


class _KeepAlive(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep the connection open, so the client pools it

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.dumps(_REPLY).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


@pytest.fixture
def local_server(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _KeepAlive)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("VLLM_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}/v1")
    monkeypatch.setenv("VLLM_API_KEY", "test-key")
    yield
    server.shutdown()


def test_one_model_answers_on_two_event_loops(local_server):
    model = build_model("qwen3.8-27b-modal", max_retries=0)  # no SDK retry to hide a dead connection
    assert asyncio.run(model.ainvoke("hi")).content == "ok"
    assert asyncio.run(model.ainvoke("hi")).content == "ok"


def test_models_built_separately_answer_on_a_new_event_loop(local_server):
    assert asyncio.run(build_model("qwen3.8-27b-modal", max_retries=0).ainvoke("hi")).content == "ok"
    assert asyncio.run(build_model("qwen3.8-27b-modal", max_retries=0).ainvoke("hi")).content == "ok"


def test_calls_on_one_event_loop_share_one_client(local_server):
    model = build_model("qwen3.8-27b-modal", max_retries=0)

    async def twice():
        await model.ainvoke("hi")
        await model.ainvoke("hi")
        return seam._loop_clients_in_use()

    assert asyncio.run(twice()) == 1


def test_an_unhashable_timeout_still_answers_on_two_event_loops(local_server):
    model = build_model("qwen3.8-27b-modal", max_retries=0, timeout=httpx.Timeout(30.0))
    assert asyncio.run(model.ainvoke("hi")).content == "ok"
    assert asyncio.run(model.ainvoke("hi")).content == "ok"


def test_a_callers_own_async_client_is_kept(local_server):
    own = httpx.AsyncClient()
    assert build_model("qwen3.8-27b-modal", http_async_client=own).root_async_client._client is own
