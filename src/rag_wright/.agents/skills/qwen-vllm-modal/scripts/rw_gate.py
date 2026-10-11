"""The model server's public front (PS-20): forwards only the OpenAI API (`/v1/...`) and `/health` to vLLM.

vLLM's `--api-key` guards only `/v1`, `/v2`, `/inference` and `/cohere`. Sleep mode, which the GPU snapshot needs,
requires `VLLM_SERVER_DEV_MODE=1`, and that adds unauthenticated `/sleep`, `/wake_up` and `/collective_rpc` routes. So
vLLM listens on 127.0.0.1 only and this gate is what the public URL reaches. It runs inside the server container
(`modal_qwen3_vllm_server.py` copies it into the image); responses stream through as they arrive.
"""
import os

UPSTREAM = f"http://127.0.0.1:{os.environ.get('RW_VLLM_PORT', '8001')}"
_HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "upgrade", "proxy-connection", "te", "trailer",
               "host", "content-length"}


def allowed(path: str) -> bool:
    """The routes the public URL may reach: the OpenAI API and the health check."""
    return path == "/health" or path.startswith("/v1/")


async def _forward(request):
    from aiohttp import web

    if not allowed(request.path):
        return web.json_response({"error": "Not Found"}, status=404)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    async with request.app["session"].request(request.method, UPSTREAM + request.path_qs, headers=headers,
                                              data=await request.read()) as upstream:
        response = web.StreamResponse(status=upstream.status, headers={
            k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP})
        await response.prepare(request)
        async for chunk in upstream.content.iter_any():  # streamed completions pass through as they arrive
            await response.write(chunk)
        await response.write_eof()
        return response


async def _app():
    from aiohttp import ClientSession, ClientTimeout, TCPConnector, web

    app = web.Application(client_max_size=64 * 1024 * 1024)

    async def open_session(app):
        app["session"] = ClientSession(connector=TCPConnector(limit=0), timeout=ClientTimeout(total=None))

    async def close_session(app):
        await app["session"].close()

    app.on_startup.append(open_session)
    app.on_cleanup.append(close_session)
    app.router.add_route("*", "/{tail:.*}", _forward)
    return app


if __name__ == "__main__":
    from aiohttp import web

    web.run_app(_app(), host=os.environ.get("RW_GATE_HOST", "0.0.0.0"),
                port=int(os.environ.get("RW_PUBLIC_PORT", "8000")), print=None)
