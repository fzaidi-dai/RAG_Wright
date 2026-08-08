"""Fast-cold-start Gemma-4 vLLM server on Modal via GPU Memory Snapshots (+ Lever-2 fallbacks).

A SEPARATE rewrite of scripts/modal_gemma4_vllm.py (kept intact) that targets the ~9-11 min cold start. Our
cold start is almost all ONE-TIME warmup (torch.compile ~140s, multimodal vision warmup ~75-120s, CUDA-graph
capture + KV alloc ~60-90s), not weight loading -- exactly Modal's "bursty / cold-start-sensitive" workload,
and latency is irrelevant for us, which unlocks aggressive levers. Grounded: Modal 1.5.3 (enable_memory_snapshot
+ experimental_options + @modal.enter(snap=...)); the vLLM sleep/wake snapshot pattern; the cold-start guide.

LEVER 1 -- GPU MEMORY SNAPSHOTS (alpha, the ~10x win): snapshot the fully-warmed container ONCE; later cold
  starts restore in seconds. vLLM must offload weights to CPU before the snapshot (--enable-sleep-mode +
  VLLM_SERVER_DEV_MODE=1 -> /sleep on snap, /wake_up on restore).
LEVER 2 -- stop paying for things we don't use (latency moot; also the zero-alpha-risk FALLBACK if snapshots
  don't engage): --enforce-eager (skip torch.compile + CUDA graphs, ENFORCE_EAGER=1 default) and, OPTIONALLY,
  text-only to skip the vision warmup.

>>> VISION IS A TOGGLE, NEVER PERMANENT (see memory [[vision-needed-for-ingestion]]). Gemma-4 is multimodal and
    the INGESTION pipeline (Granite + docling + docling-graph) NEEDS vision for images/figures/tables in corpus
    docs. The query-gen app can run text-only once data is ingested, but the SAME serving must flip vision back
    ON -- especially on the Lever-2 fallback. Controlled by MM_LIMIT: UNSET => vision ON (default, safe);
    MM_LIMIT="image=0,audio=0" => text-only (faster cold start, query-gen only). NEVER hardcode text-only. <<<

  # query-gen, text-only, snapshots on:
  MODEL=google/gemma-4-31B-it-qat-w4a16-ct MM_LIMIT=image=0,audio=0 \
    uv run --no-sync modal deploy scripts/modal_gemma4_vllm_snapshot.py
  # ingestion / vision ON (default): just omit MM_LIMIT.
"""

import os
import subprocess
import time

import modal

MODEL = os.environ.get("MODEL", "google/gemma-4-31B-it-qat-w4a16-ct")
QUANT = os.environ.get("QUANT", "")  # e.g. int8_per_channel_weight_only for the 26B-A4B MoE
API_KEY = os.environ.get("VLLM_API_KEY", "rw-vllm-dev-key")
GPU = os.environ.get("GPU", "A100-40GB")
GPU_UTIL = os.environ.get("VLLM_GPU_UTIL", "0.80")
MAX_LEN = os.environ.get("VLLM_MAX_LEN", "16384")
CHAT_TEMPLATE = os.environ.get("CHAT_TEMPLATE", "/root/gemma4_chat_template.jinja")
ENFORCE_EAGER = os.environ.get("ENFORCE_EAGER", "1") == "1"  # Lever 2: skip compile/graphs (latency moot)
# VISION TOGGLE. Unset => vision ON (default; ingestion needs it). "image=0,audio=0" => text-only (query-gen).
MM_LIMIT = os.environ.get("MM_LIMIT", "")
MAX_INPUTS = int(os.environ.get("MAX_INPUTS", "8"))  # container concurrency (vLLM batches); keep modest for 31B
_TEMPLATE_URL = "https://raw.githubusercontent.com/vllm-project/vllm/main/examples/tool_chat_template_gemma4.jinja"
HF_CACHE = "/root/.cache/huggingface"
_VLLM = "http://127.0.0.1:8000"

app = modal.App("rw-gemma4-snap")
hf_vol = modal.Volume.from_name("rw-hf-cache", create_if_missing=True)
image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.12")
    .apt_install("curl")
    .pip_install("vllm", "transformers", "fastapi", "httpx", "huggingface_hub[hf_transfer]")
    .run_commands(f"curl -sL {_TEMPLATE_URL} -o /root/gemma4_chat_template.jinja")
    .env({
        "HF_HUB_ENABLE_HF_TRANSFER": "1", "HF_HOME": HF_CACHE,
        "VLLM_SERVER_DEV_MODE": "1",        # exposes /sleep + /wake_up (required for the snapshot pattern)
        "TORCHINDUCTOR_COMPILE_THREADS": "1",  # snapshot-friendly compile (per Modal's vLLM snapshot example)
    })
)


def _vllm_args() -> list[str]:
    args = [
        "vllm", "serve", MODEL, "--host", "127.0.0.1", "--port", "8000",
        "--served-model-name", MODEL, "--max-model-len", MAX_LEN,
        "--gpu-memory-utilization", GPU_UTIL, "--api-key", API_KEY,
        "--enable-sleep-mode",  # Lever 1: lets vLLM offload weights to CPU before the snapshot
    ]
    if ENFORCE_EAGER:
        args.append("--enforce-eager")           # Lever 2: skip torch.compile + CUDA-graph capture
    if QUANT:
        args += ["--quantization", QUANT]
    if CHAT_TEMPLATE and os.path.exists(CHAT_TEMPLATE):
        args += ["--chat-template", CHAT_TEMPLATE]
    if MM_LIMIT:  # text-only => skip vision warmup (query-gen only; NEVER for ingestion)
        args += ["--limit-mm-per-prompt", MM_LIMIT]
    return args


def _wait_ready(timeout_s: int = 900) -> None:
    import httpx

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if httpx.get(f"{_VLLM}/health", timeout=4).status_code == 200:
                return
        except Exception:  # noqa: BLE001 - server still starting
            pass
        time.sleep(3)
    raise RuntimeError("vLLM did not become ready")


@app.cls(
    image=image, gpu=GPU, volumes={HF_CACHE: hf_vol},
    timeout=3600, scaledown_window=300, max_containers=1,
    enable_memory_snapshot=True,                          # Lever 1
    experimental_options={"enable_gpu_snapshot": True},   # Lever 1 (GPU vRAM snapshot, alpha)
)
@modal.concurrent(max_inputs=MAX_INPUTS)  # let the container batch concurrent proxy requests (vLLM batches)
class Gemma:
    @modal.enter(snap=True)
    def start(self):
        """Warm the server, then /sleep (offload weights -> CPU, empty KV). THIS state is snapshotted, so later
        cold starts skip all of load+compile+warmup and just restore + /wake_up."""
        import httpx

        hf_vol.reload()
        self._vllm = subprocess.Popen(_vllm_args())
        _wait_ready()
        # tiny warmup so any first-call init is captured in the snapshot
        try:
            httpx.post(f"{_VLLM}/v1/completions", timeout=120,
                       headers={"Authorization": f"Bearer {API_KEY}"},
                       json={"model": MODEL, "prompt": "ok", "max_tokens": 1})
        except Exception:  # noqa: BLE001
            pass
        # offload weights to CPU + empty KV so the snapshot is restorable (level 1 keeps weights in CPU RAM)
        httpx.post(f"{_VLLM}/sleep", params={"level": 1}, timeout=300,
                   headers={"Authorization": f"Bearer {API_KEY}"})

    @modal.enter(snap=False)
    def wake(self):
        """On every (restored) start: /wake_up moves weights back to GPU + re-allocates KV, then wait ready."""
        import httpx

        httpx.post(f"{_VLLM}/wake_up", timeout=300, headers={"Authorization": f"Bearer {API_KEY}"})
        _wait_ready()

    def _mem(self):
        import torch

        free, total = torch.cuda.mem_get_info()
        return {"gpu_free_GB": round(free / 1e9, 2), "gpu_used_GB": round((total - free) / 1e9, 2)}

    @modal.asgi_app()
    def web(self):
        import httpx
        from fastapi import FastAPI, Request
        from fastapi.responses import Response

        api = FastAPI()

        @api.get("/health")
        def health():
            try:
                up = httpx.get(f"{_VLLM}/health", timeout=2).status_code == 200
            except Exception:  # noqa: BLE001
                up = False
            return {"vllm_up": up, "model": MODEL, "vision": "off" if MM_LIMIT else "on",
                    "enforce_eager": ENFORCE_EAGER, "memory": self._mem()}

        @api.api_route("/v1/{path:path}", methods=["GET", "POST"])
        async def proxy(path: str, request: Request):
            body = await request.body()
            async with httpx.AsyncClient(timeout=300) as client:
                r = await client.request(
                    request.method, f"{_VLLM}/v1/{path}", content=body,
                    headers={"Authorization": request.headers.get("authorization", ""),
                             "Content-Type": "application/json"})
            return Response(content=r.content, status_code=r.status_code, media_type="application/json")

        return api
