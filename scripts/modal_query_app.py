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


@app.function(image=image, timeout=900, scaledown_window=300)
@modal.asgi_app()
def query():
    from fastapi import FastAPI, Request

    from rag_wright.capabilities.dg_extraction import default_extraction_model
    from rag_wright.capabilities.remote_encoders import query_classifier, query_embedder
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.subgraphs.typed_property_retrieval import production_typed_property_retrieval

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

    @web.post("/query")
    async def q(req: Request):
        body = await req.json()
        question = body["question"]
        store = _store()
        leg_b = production_typed_property_retrieval(
            store=store, embedder=query_embedder(), classifier=query_classifier(),
            extract_model=default_extraction_model("query-constraints", "ibm-granite/granite-4.1-8b"),
            function_model_id=model_for(ModelRole.GENERAL), k=int(body.get("k", 5)))
        state = leg_b.invoke({"query": question})
        r = state["retrieval"]
        return {
            "question": question,
            "constraints": sorted(state.get("constraints", set())),
            "functions": state.get("functions", []),
            "results": [
                {"rank": s.rank, "span_id": s.span_id, "function": s.function,
                 "matched": s.matched, "text": s.text[:400]}
                for s in r.results
            ],
        }

    return web
