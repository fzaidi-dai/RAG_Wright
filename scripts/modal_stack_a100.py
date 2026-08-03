"""MS1-5a/5b (MODAL-STACK-1, ADR-0039): the CO-LOCATED A100 GPU-services app.

ONE Modal A100 hosts all three product-substrate GPU models together -- vLLM-Granite (subprocess, OpenAI
server) + BGE-M3 (in-process) + LegalBERT (in-process) -- to validate that LLM + non-LLM GPU usage coexist
cleanly on one 40GB card. vLLM's `--gpu-memory-utilization` is tuned DOWN so the two encoders fit alongside
granite's weights + KV cache.

Endpoints (one ASGI app):
  GET  /health          -> {vllm_up, gpu memory free/total}  (the FIT signal, MS1-5a)
  POST /embed  {texts}  -> {dense, sparse}                   (BGE-M3, same call the KG spans were built with)
  POST /classify {texts}-> {labels}                          (LegalBERT function classifier)
  ANY  /v1/{path}       -> proxied to the local vLLM OpenAI server (chat/completions + guided decoding)

  uv run --no-sync modal deploy scripts/modal_stack_a100.py
  # seam:   RAG_SERVING=vllm  VLLM_BASE_URL=https://<app>.modal.run/v1
  # encoders: POST https://<app>.modal.run/embed | /classify
  uv run --no-sync modal app stop rw-stack-a100
"""

import os
import subprocess

import modal

MODEL_HF = os.environ.get("GRANITE_HF", "ibm-granite/granite-4.1-8b")
API_KEY = os.environ.get("VLLM_API_KEY", "rw-vllm-dev-key")
GPU_UTIL = os.environ.get("VLLM_GPU_UTIL", "0.75")  # leave ~10GB of the 40GB for BGE (~2.3GB) + LegalBERT (~0.5GB)
HF_CACHE = "/root/.cache/huggingface"
LEGALBERT_PATH = "/models/legalbert_function"

app = modal.App("rw-stack-a100")
hf_vol = modal.Volume.from_name("rw-hf-cache", create_if_missing=True)
models_vol = modal.Volume.from_name("rw-models", create_if_missing=True)
image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.12")
    .pip_install("vllm", "FlagEmbedding", "transformers", "fastapi", "httpx", "huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "HF_HOME": HF_CACHE})
)


@app.cls(image=image, gpu="A100-40GB", volumes={HF_CACHE: hf_vol, "/models": models_vol},
         timeout=3600, scaledown_window=300, max_containers=1)
class Stack:
    @modal.enter()
    def start(self):
        import torch
        from FlagEmbedding import BGEM3FlagModel
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        hf_vol.reload()
        # 1. vLLM-Granite as a subprocess (OpenAI server on 127.0.0.1:8000); reduced util leaves GPU room
        self._vllm = subprocess.Popen([
            "vllm", "serve", MODEL_HF, "--host", "127.0.0.1", "--port", "8000",
            "--served-model-name", MODEL_HF, "--max-model-len", "16384",
            "--gpu-memory-utilization", GPU_UTIL, "--api-key", API_KEY,
        ])
        # 2. the two encoders on the SAME GPU, in-process
        self._bge = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True, devices="cuda")
        self._lb_tok = AutoTokenizer.from_pretrained(LEGALBERT_PATH)
        self._lb = AutoModelForSequenceClassification.from_pretrained(LEGALBERT_PATH).to("cuda").eval()
        self._id2label = self._lb.config.id2label
        self._torch = torch

    def _mem(self):
        free, total = self._torch.cuda.mem_get_info()  # free/total across ALL processes on the device
        return {"gpu_free_GB": round(free / 1e9, 2), "gpu_total_GB": round(total / 1e9, 2),
                "gpu_used_GB": round((total - free) / 1e9, 2),
                "this_proc_alloc_GB": round(self._torch.cuda.memory_allocated() / 1e9, 2)}

    @modal.asgi_app()
    def web(self):
        import httpx
        from fastapi import FastAPI, Request
        from fastapi.responses import Response

        api = FastAPI()
        vllm_base = "http://127.0.0.1:8000"

        @api.get("/health")
        def health():
            try:
                vllm_up = httpx.get(vllm_base + "/health", timeout=2).status_code == 200
            except Exception:  # noqa: BLE001
                vllm_up = False
            return {"vllm_up": vllm_up, "bge": True, "legalbert": True, "memory": self._mem()}

        @api.post("/embed")
        async def embed(req: Request):
            body = await req.json()
            texts = body["texts"] if isinstance(body.get("texts"), list) else [body["text"]]
            out = self._bge.encode(texts, return_dense=True, return_sparse=True)
            return {
                "dense": [v.tolist() for v in out["dense_vecs"]],
                "sparse": [{int(k): float(v) for k, v in lw.items()} for lw in out["lexical_weights"]],
            }

        @api.post("/classify")
        async def classify(req: Request):
            body = await req.json()
            texts = body["texts"] if isinstance(body.get("texts"), list) else [body["text"]]
            enc = self._lb_tok(texts, padding=True, truncation=True, max_length=256, return_tensors="pt").to("cuda")
            with self._torch.no_grad():
                logits = self._lb(**enc).logits
            resp = {"labels": [self._id2label[int(i)] for i in logits.argmax(-1).tolist()]}
            k = int(body.get("k", 0))  # top-k routing (LegalBERT KG-5e); omitted -> top-1 labels only
            if k > 1:
                topk = logits.topk(min(k, logits.shape[-1]), dim=-1).indices.tolist()
                resp["topk"] = [[self._id2label[int(i)] for i in row] for row in topk]
            return resp

        @api.api_route("/v1/{path:path}", methods=["GET", "POST"])
        async def proxy(path: str, request: Request):
            body = await request.body()
            async with httpx.AsyncClient(timeout=180) as client:
                r = await client.request(
                    request.method, f"{vllm_base}/v1/{path}", content=body,
                    headers={"Authorization": request.headers.get("authorization", ""),
                             "Content-Type": "application/json"})
            return Response(content=r.content, status_code=r.status_code, media_type="application/json")

        return api
