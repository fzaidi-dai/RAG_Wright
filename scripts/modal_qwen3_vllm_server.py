"""Qwen3.8-27B as an OpenAI-compatible vLLM web server on a Modal A100-80GB -- for the QUANTIZATION ACCURACY EVAL
(ADR-0110 follow-up). Serves ONE config at a time under a STABLE served-model-name ("qwen3-eval") so the same
`qwen3-eval` model profile drives whichever config is deployed. Deploy the reference, run the eval, redeploy the
test config, run it again -- the https URL is stable across redeploys.

  # reference: full precision (bf16 weights + bf16 KV) -- the accuracy ground truth
  MODEL=Qwen/Qwen3.8-27B KV_CACHE_DTYPE=auto uv run --no-sync modal deploy scripts/modal_qwen3_vllm_server.py
  # Config B: FP8 weights + FP8 KV
  MODEL=Qwen/Qwen3.8-27B-FP8 KV_CACHE_DTYPE=fp8 uv run --no-sync modal deploy scripts/modal_qwen3_vllm_server.py
  uv run --no-sync modal app stop rw-qwen3-vllm-eval   # tear down (stop billing)

URL: https://<workspace>--rw-qwen3-vllm-eval-serve.modal.run  (append /v1 for the OpenAI base). Weights + the
serve-path torch.compile cache reuse the persistent Volumes (fast warm cold-start where a matching cache exists).
"""

import os
import subprocess

import modal

MODEL = os.environ.get("MODEL", "Qwen/Qwen3.8-27B")          # ...-FP8 for the quantized-weights config
KV_DTYPE = os.environ.get("KV_CACHE_DTYPE", "auto")          # "auto" (bf16 KV, the reference) | "fp8"
GPU = os.environ.get("GPU", "A100-80GB:1")
TP = int(os.environ.get("TP", "1"))
MAX_LEN = int(os.environ.get("MAX_LEN", "16384"))            # the real workload's context (9K+ prompts)
GPU_UTIL = os.environ.get("GPU_UTIL", "0.95")
SERVED = os.environ.get("SERVED_NAME", "qwen3-eval")         # STABLE name so one profile serves every config
API_KEY = os.environ.get("VLLM_API_KEY", "rw-vllm-dev-key")

HF_CACHE = "/root/.cache/huggingface"
VLLM_CACHE = "/root/.cache/vllm"
hf_vol = modal.Volume.from_name("rw-hf-cache", create_if_missing=True)
vllm_cache_vol = modal.Volume.from_name("rw-vllm-serve-compile-cache", create_if_missing=True)

app = modal.App("rw-qwen3-vllm-eval")
vllm_image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.12")
    .pip_install("vllm", "huggingface_hub[hf_transfer]")
    # Bake the runtime knobs into the image env: a deployed function reads the CONTAINER's env, not the deploying
    # shell's, so an override must live here to reach the container (same lesson as the bench script). Xet disabled
    # so a fresh weight download's open log handle can't block a Volume commit.
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "HF_HUB_DISABLE_XET": "1", "HF_HOME": HF_CACHE,
          "MODEL": MODEL, "KV_CACHE_DTYPE": KV_DTYPE, "TP": str(TP), "MAX_LEN": str(MAX_LEN),
          "GPU_UTIL": GPU_UTIL, "SERVED_NAME": SERVED, "VLLM_API_KEY": API_KEY})
)


@app.function(image=vllm_image, gpu=GPU, volumes={HF_CACHE: hf_vol, VLLM_CACHE: vllm_cache_vol},
              timeout=3600, max_containers=1, scaledown_window=600)
@modal.web_server(port=8000, startup_timeout=900)
def serve() -> None:
    """Start vLLM's OpenAI-compatible server for the configured Qwen config. Reuses the persisted weights +
    serve-path torch.compile cache; compiled (production-faithful), TP=1, optional FP8 KV cache."""
    hf_vol.reload()
    vllm_cache_vol.reload()
    args = [
        "vllm", "serve", MODEL, "--host", "0.0.0.0", "--port", "8000", "--served-model-name", SERVED,
        "--tensor-parallel-size", str(TP), "--dtype", "bfloat16", "--max-model-len", str(MAX_LEN),
        "--gpu-memory-utilization", GPU_UTIL, "--api-key", API_KEY, "--trust-remote-code",
    ]
    if KV_DTYPE != "auto":
        args += ["--kv-cache-dtype", KV_DTYPE]
    print(f"[qwen-eval] serving {MODEL} as '{SERVED}' | TP={TP} max_len={MAX_LEN} util={GPU_UTIL} kv={KV_DTYPE}",
          flush=True)
    subprocess.Popen(args)
