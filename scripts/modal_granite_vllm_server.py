"""MODAL-STACK-2 / CHUNKER-OPEN infra (ADR-0039): granite-4.1-8b as an OpenAI-compatible **vLLM** server on a
Modal A100 -- the product substrate's LLM. Both the clause-extraction seam (`ExtractionModel.base_url`) and the
chunker seam (`OPENROUTER_BASE_URL`) are OpenAI-compatible base_urls, so pointing them here is a base_url swap.

  uv run --no-sync modal deploy scripts/modal_granite_vllm_server.py     # -> a stable https URL
  # then locally: OPENROUTER_BASE_URL=<url>/v1  (+ a dummy key)  routes extraction + chunking to vLLM-Granite
  uv run --no-sync modal app stop rw-granite-vllm                        # tear down (stop billing)

Weights reuse the persistent `rw-hf-cache` Volume (downloaded once by the throughput benchmark).
"""

import os
import subprocess

import modal

MODEL_HF = os.environ.get("GRANITE_HF", "ibm-granite/granite-4.1-8b")
GPU = os.environ.get("GPU", "A100-40GB")
API_KEY = os.environ.get("VLLM_API_KEY", "rw-vllm-dev-key")  # vLLM's --api-key; clients send it as the bearer
HF_CACHE = "/root/.cache/huggingface"

app = modal.App("rw-granite-vllm")
hf_vol = modal.Volume.from_name("rw-hf-cache", create_if_missing=True)
vllm_image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.12")
    .pip_install("vllm", "huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "HF_HOME": HF_CACHE})
)


@app.function(image=vllm_image, gpu=GPU, volumes={HF_CACHE: hf_vol}, timeout=3600,
              max_containers=1, scaledown_window=300)
@modal.web_server(port=8000, startup_timeout=900)
def serve() -> None:
    """Start vLLM's OpenAI-compatible server for granite-4.1-8b (guided-decoding capable via xgrammar)."""
    hf_vol.reload()
    subprocess.Popen([
        "vllm", "serve", MODEL_HF,
        "--host", "0.0.0.0", "--port", "8000",
        "--served-model-name", MODEL_HF,
        "--max-model-len", "16384",
        "--gpu-memory-utilization", "0.90",
        "--api-key", API_KEY,
    ])
