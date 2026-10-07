"""EC-2 (ENTERPRISE-CONTAINER, ADR-0039): the query-service app on Modal (scale-to-zero, CPU).

The deployable front door. Runs the registered query subgraphs (Leg B = `typed_property_retrieval`), with the
seams wired to the Modal-hosted infra: the KG on `rw-arcadedb` (https + basic-auth, via the store env) and all
model work on the `rw-stack-a100` GPU app (`RAG_SERVING=vllm` for the LLM seam + `STACK_URL` for the BGE/LegalBERT
adapters). No GPU here (CPU orchestration; the GPU is the separate scale-to-zero A100). The heavy GPU libs
(FlagEmbedding/torch/spacy) are NOT installed -- the query path uses the A100 adapters, so those imports never fire.

  uv run --no-sync modal deploy scripts/modal_query_app.py     # -> a stable https URL (POST /query, GET /health)
"""

import modal

ARCADEDB_URL = "farhan-zaidi--rw-arcadedb-serve.modal.run"
A100_URL = "https://farhan-zaidi--rw-stack-a100-stack-web.modal.run"

app = modal.App("rw-query")
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "langgraph", "langchain-openai>=1.3.3", "arcadedb-python>=0.4.0",
        "docling-graph[templategen]==1.9.1", "pyshacl>=0.40.1", "pydantic>=2.0",
        "httpx", "python-dotenv", "fastapi",
    )
    .env({
        # the KG on Modal (https + basic-auth), read by ArcadeDBStore.from_env
        "ARCADEDB_HOST": ARCADEDB_URL, "ARCADEDB_PORT": "443", "ARCADEDB_PROTOCOL": "https",
        "ARCADEDB_USER": "root", "ARCADEDB_PASSWORD": "rag_wright_dev_2026",
        "ARCADEDB_DATABASE": "ragwright_cuad_full",
        # all model work on the A100 GPU app
        "RAG_SERVING": "vllm", "VLLM_BASE_URL": f"{A100_URL}/v1", "STACK_URL": A100_URL,
    })
    .add_local_python_source("rag_wright")  # MUST be last: no build steps after add_local_*
)


def _a100_ready() -> bool:
    """One quick check that hits the A100 /health -- this ALSO triggers the scale-to-zero container to spin up
    (a request wakes it), so a cold check kicks off the ~4-min warm-up autonomously (vLLM keeps loading, and
    scaledown_window holds the container until it is ready). Returns True only once vLLM is actually serving."""
    import httpx
    try:
        return bool(httpx.get(f"{A100_URL}/health", timeout=8).json().get("vllm_up"))
    except Exception:  # noqa: BLE001 - cold / not up yet
        return False


@app.function(image=image, timeout=900, scaledown_window=300)
@modal.asgi_app()
def query():
    from fastapi import FastAPI, Request

    from rag_wright.packs.contracts.capabilities.dg_extraction import default_extraction_model
    from rag_wright.capabilities.remote_encoders import query_embedder
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.packs.contracts.subgraphs.typed_property_retrieval import production_typed_property_retrieval

    web = FastAPI()

    def _store():
        return ArcadeDBStore.from_env()  # -> the Modal KG

    @web.get("/health")
    def health():
        # KG reachable (no A100 needed) -- proves the query app <-> Modal KG store seam
        try:
            n = _store()._query("SELECT count(*) AS n FROM Contract")[0]["n"]
            return {"kg": "ok", "contracts": n, "a100": A100_URL}
        except Exception as exc:  # noqa: BLE001
            return {"kg": "error", "detail": str(exc)[:200]}

    async def _run(question: str, k: int) -> dict:
        # EC-3 warm-on-request (serverless cold-start UX): a cold check triggers the A100 + returns fast, so the
        # request never exceeds Modal's web-request timeout. The client retries; once warm, the answer is fast.
        # ADR-0039: "not real-time; first-query warm-up tolerated". ASYNC-D1 (ADR-0057): the leg is now async --
        # ainvoke runs the model nodes on the loop with the true wall-clock deadline (no thread-blocking).
        if not _a100_ready():
            return {"status": "warming",
                    "detail": "the A100 was cold; this request triggered it (~4 min). Retry shortly.",
                    "retry_after_s": 60}
        store = _store()
        leg_b = production_typed_property_retrieval(  # ADR-0047: no classifier -- whole-index pool
            store=store, embedder=query_embedder(),
            extract_model=default_extraction_model("query-constraints", "ibm-granite/granite-4.1-8b"), k=k)
        state = await leg_b.ainvoke({"query": question})
        r = state["retrieval"]
        return {
            "question": question,
            "constraints": sorted(state.get("constraints", set())),
            "results": [
                {"rank": s.rank, "span_id": s.span_id, "function": s.function,
                 "matched": s.matched, "text": s.text[:400]}
                for s in r.results
            ],
        }

    @web.post("/query")
    async def q(req: Request):
        body = await req.json()
        # the async leg runs the model nodes on the loop with the true wall-clock deadline (ASYNC-D1)
        return await _run(body["question"], int(body.get("k", 5)))

    return web
