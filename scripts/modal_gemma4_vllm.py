"""A minimal, LLM-ONLY vLLM app to validate a self-hosted Gemma 4 QAT generator on the Leg-A silver set.

Isolated from the production `rw-stack-a100` (which co-hosts BGE + LegalBERT): the silver measurement uses FROZEN
evidence, so no encoders are needed, and giving the 31B the whole card avoids VRAM contention. Gemma 4 is Apache
2.0 -> not gated -> no HF token needed. vLLM auto-detects the compressed-tensors W4A16 quantization from the
model config (no --quantization flag for the 4-bit dense model).

  MODEL=google/gemma-4-31B-it-qat-w4a16-ct  uv run --no-sync modal deploy scripts/modal_gemma4_vllm.py
  # then, self-hosted silver run:
  #   RAG_SERVING=vllm VLLM_BASE_URL=https://<app>.modal.run/v1 VLLM_API_KEY=rw-vllm-dev-key \
  #     SILVER_MODELS="gemma4-31b-qat:google/gemma-4-31B-it-qat-w4a16-ct" \
  #     uv run --no-sync python -m scripts.measure_silver 3
  uv run --no-sync modal app stop rw-gemma4

Fallback if the 31B W4A16 is a problem (VRAM/quant/arch): the 26B-A4B MoE has NO 4-bit checkpoint; serve it INT8:
  MODEL=google/gemma-4-26B-A4B-it  QUANT=int8_per_channel_weight_only  ... modal deploy ...
"""

import os
import subprocess

import modal

MODEL = os.environ.get("MODEL", "google/gemma-4-31B-it-qat-w4a16-ct")
QUANT = os.environ.get("QUANT", "")  # empty for the W4A16 compressed-tensors dense model (auto-detected)
API_KEY = os.environ.get("VLLM_API_KEY", "rw-vllm-dev-key")
GPU = os.environ.get("GPU", "A100-40GB")
GPU_UTIL = os.environ.get("VLLM_GPU_UTIL", "0.80")  # LEAVE HEADROOM: json_schema guided decoding (xgrammar)
# needs GPU workspace beyond weights+KV; at 0.90 (KV cache ~15GB + weights ~20GB) a guided request OOM-killed the
# engine. 0.80 keeps ~8GB free for xgrammar. (function_calling would instead need --tool-call-parser gemma4,
# which has a documented <pad>-under-concurrency bug, vllm#39392 -- so we use json_schema, per the model profile.)
MAX_LEN = os.environ.get("VLLM_MAX_LEN", "16384")   # silver prompts are ~<=8k tokens; keep KV cache small
HF_CACHE = "/root/.cache/huggingface"

app = modal.App("rw-gemma4")
hf_vol = modal.Volume.from_name("rw-hf-cache", create_if_missing=True)
image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.12")
    .pip_install("vllm", "transformers", "fastapi", "httpx", "huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "HF_HOME": HF_CACHE,
          "MODEL": MODEL, "QUANT": QUANT, "VLLM_API_KEY": API_KEY,
          "VLLM_GPU_UTIL": GPU_UTIL, "VLLM_MAX_LEN": MAX_LEN})
)


@app.cls(image=image, gpu=GPU, volumes={HF_CACHE: hf_vol},
         timeout=3600, scaledown_window=300, max_containers=1)
class Gemma:
    @modal.enter()
    def start(self):
        import torch

        hf_vol.reload()
        args = [
            "vllm", "serve", MODEL, "--host", "127.0.0.1", "--port", "8000",
            "--served-model-name", MODEL, "--max-model-len", MAX_LEN,
            "--gpu-memory-utilization", GPU_UTIL, "--api-key", API_KEY,
        ]
        if QUANT:
            args += ["--quantization", QUANT]
        self._vllm = subprocess.Popen(args)
        self._torch = torch

    def _mem(self):
        free, total = self._torch.cuda.mem_get_info()
        return {"gpu_free_GB": round(free / 1e9, 2), "gpu_total_GB": round(total / 1e9, 2),
                "gpu_used_GB": round((total - free) / 1e9, 2)}

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
                up = httpx.get(vllm_base + "/health", timeout=2).status_code == 200
            except Exception:  # noqa: BLE001
                up = False
            return {"vllm_up": up, "model": MODEL, "quant": QUANT or "w4a16-ct(auto)", "memory": self._mem()}

        @api.api_route("/v1/{path:path}", methods=["GET", "POST"])
        async def proxy(path: str, request: Request):
            body = await request.body()
            async with httpx.AsyncClient(timeout=300) as client:  # 31B dense is slow; latency is acceptable
                r = await client.request(
                    request.method, f"{vllm_base}/v1/{path}", content=body,
                    headers={"Authorization": request.headers.get("authorization", ""),
                             "Content-Type": "application/json"})
            return Response(content=r.content, status_code=r.status_code, media_type="application/json")

        return api
