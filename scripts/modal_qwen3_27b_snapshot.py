"""Qwen3.8-27B (bf16, TP=2) fast-cold-start via Modal memory snapshot -- measure RESTORE-to-first-token.

Adapts the proven gemma snapshot pattern (scripts/modal_gemma4_vllm_snapshot.py) to Qwen 27B on 2x A100-80GB.
The bf16 serve cold-start is ~472s, almost all ONE-TIME warmup (KV profiling + CUDA-graph capture + wake), not
weight bytes -- exactly Modal's cold-start-snapshot case. Snapshot the fully-warmed container ONCE; later cold
starts RESTORE in seconds instead of re-warming.

Pattern (Modal 1.5.3): enable_memory_snapshot + @modal.enter(snap=True) starts+warms+/sleep (vLLM offloads
weights to CPU so the CPU memory snapshot captures a restorable state), @modal.enter(snap=False) /wake_up moves
weights back to GPU. GPU_SNAPSHOT (alpha) is OFF by default -- it FAILED for the gemma attempt; the CPU snapshot
+ sleep/wake is the realistic path. Compiled by default (production keeps torch.compile ON).

  uv run --no-sync modal deploy scripts/modal_qwen3_27b_snapshot.py   # creates the snapshot on first container
  # then measure restore: force a fresh container and time a streamed request (see the companion measure step)
"""

import os
import subprocess
import time

import modal

MODEL = os.environ.get("MODEL", "Qwen/Qwen3.8-27B")
GPU = os.environ.get("GPU", "A100-80GB:2")
TP = os.environ.get("TP", "2")
API_KEY = os.environ.get("VLLM_API_KEY", "rw-vllm-dev-key")
GPU_UTIL = os.environ.get("VLLM_GPU_UTIL", "0.90")
MAX_LEN = os.environ.get("VLLM_MAX_LEN", "16384")
MAX_NUM_SEQS = os.environ.get("MAX_NUM_SEQS", "256")
ENFORCE_EAGER = os.environ.get("ENFORCE_EAGER", "0") == "1"  # default compiled (production latency)
SNAPSHOT = os.environ.get("ENABLE_SNAPSHOT", "1") == "1"
GPU_SNAPSHOT = os.environ.get("GPU_SNAPSHOT", "0") == "1"    # alpha; OFF (it failed for gemma). CPU snapshot + sleep.
MM_LIMIT = os.environ.get("MM_LIMIT", "")                    # Qwen3VL is multimodal; "image=0" => text-only (faster)
MAX_INPUTS = int(os.environ.get("MAX_INPUTS", "32"))
HF_CACHE = "/root/.cache/huggingface"
VLLM_CACHE = "/root/.cache/vllm"
_VLLM = "http://127.0.0.1:8000"

app = modal.App("rw-qwen3-snap")
hf_vol = modal.Volume.from_name("rw-hf-cache", create_if_missing=True)
vllm_cache_vol = modal.Volume.from_name("rw-vllm-serve-compile-cache", create_if_missing=True)
image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.12")
    .apt_install("curl")
    .pip_install("vllm", "transformers", "fastapi", "httpx", "huggingface_hub[hf_transfer]")
    .env({
        "HF_HUB_ENABLE_HF_TRANSFER": "1", "HF_HOME": HF_CACHE,
        "VLLM_SERVER_DEV_MODE": "1",           # exposes /sleep + /wake_up (required for the snapshot pattern)
        "TORCHINDUCTOR_COMPILE_THREADS": "1",  # snapshot-friendly compile (per Modal's vLLM snapshot example)
        # bake config into the image so the CONTAINER reads the same values the deploy did
        "MODEL": MODEL, "GPU": GPU, "TP": TP, "VLLM_API_KEY": API_KEY, "VLLM_GPU_UTIL": GPU_UTIL,
        "VLLM_MAX_LEN": MAX_LEN, "MAX_NUM_SEQS": MAX_NUM_SEQS, "MM_LIMIT": MM_LIMIT,
        "ENFORCE_EAGER": os.environ.get("ENFORCE_EAGER", "0"),
        "ENABLE_SNAPSHOT": os.environ.get("ENABLE_SNAPSHOT", "1"),
        "GPU_SNAPSHOT": os.environ.get("GPU_SNAPSHOT", "0"), "MAX_INPUTS": str(MAX_INPUTS),
    })
)


def _vllm_args() -> list:
    args = [
        "vllm", "serve", MODEL, "--host", "127.0.0.1", "--port", "8000", "--served-model-name", MODEL,
        "--tensor-parallel-size", TP, "--dtype", "bfloat16", "--max-model-len", MAX_LEN,
        "--gpu-memory-utilization", GPU_UTIL, "--max-num-seqs", MAX_NUM_SEQS, "--api-key", API_KEY,
        "--trust-remote-code", "--enable-sleep-mode",  # sleep-mode: offload weights to CPU before the snapshot
    ]
    if ENFORCE_EAGER:
        args.append("--enforce-eager")
    if MM_LIMIT:
        args += ["--limit-mm-per-prompt", MM_LIMIT]
    return args


def _wait_ready(timeout_s: int = 1500) -> None:
    import httpx

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if httpx.get(f"{_VLLM}/health", timeout=4).status_code == 200:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3)
    raise RuntimeError("vLLM did not become ready")


_CLS_KWARGS: dict = {}
if SNAPSHOT:
    _CLS_KWARGS["enable_memory_snapshot"] = True
    if GPU_SNAPSHOT:
        _CLS_KWARGS["experimental_options"] = {"enable_gpu_snapshot": True}


@app.cls(image=image, gpu=GPU, volumes={HF_CACHE: hf_vol, VLLM_CACHE: vllm_cache_vol},
         timeout=3600, scaledown_window=120, max_containers=1, **_CLS_KWARGS)
@modal.concurrent(max_inputs=MAX_INPUTS)
class Qwen:
    @modal.enter(snap=SNAPSHOT)
    def start(self):
        """Start + warm the server. When SNAPSHOT: /sleep (weights -> CPU) so THIS warmed state is snapshotted;
        later cold starts restore + /wake_up instead of re-warming."""
        import httpx

        hf_vol.reload()
        vllm_cache_vol.reload()
        self._vllm = subprocess.Popen(_vllm_args())
        _wait_ready()
        try:
            httpx.post(f"{_VLLM}/v1/completions", timeout=180,
                       headers={"Authorization": f"Bearer {API_KEY}"},
                       json={"model": MODEL, "prompt": "ok", "max_tokens": 1})
        except Exception:  # noqa: BLE001
            pass
        if SNAPSHOT:
            print("[snap] /sleep (offload weights to CPU) before snapshot", flush=True)
            httpx.post(f"{_VLLM}/sleep", params={"level": 1}, timeout=600,
                       headers={"Authorization": f"Bearer {API_KEY}"})

    @modal.enter(snap=False)
    def wake(self):
        """On every restored start: /wake_up (weights CPU -> GPU). This is the RESTORE cost we are measuring."""
        import httpx

        if SNAPSHOT:
            t0 = time.time()
            httpx.post(f"{_VLLM}/wake_up", timeout=600, headers={"Authorization": f"Bearer {API_KEY}"})
            _wait_ready()
            print(f"[snap] RESTORE wake_up + ready took {time.time() - t0:.1f}s", flush=True)

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
            return {"vllm_up": up, "model": MODEL, "snapshot": SNAPSHOT, "enforce_eager": ENFORCE_EAGER,
                    "vision": "off" if MM_LIMIT else "on"}

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
