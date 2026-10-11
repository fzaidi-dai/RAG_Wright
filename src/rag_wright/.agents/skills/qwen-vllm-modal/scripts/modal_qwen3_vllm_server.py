"""Qwen3.8-27B as an OpenAI-compatible vLLM web server on Modal A100-80GB. Two uses, one script:

(1) QUANTIZATION ACCURACY EVAL (ADR-0110): serve one config under a stable served-name ("qwen3-eval").
  MODEL=Qwen/Qwen3.8-27B     KV_CACHE_DTYPE=auto uv run --no-sync modal deploy scripts/modal_qwen3_vllm_server.py
  MODEL=Qwen/Qwen3.8-27B-FP8 KV_CACHE_DTYPE=fp8  uv run --no-sync modal deploy scripts/modal_qwen3_vllm_server.py

(2) 0048 part-2 JOINT RUN -- the UNIFIED endpoint both the product's agent-llm (tool-calling) and the engine
    drive: Config B (FP8), ALL capabilities on (tool calls + reasoning + guided decoding), served under the
    name the engine profile `qwen3.8-27b-modal` expects ("Qwen/Qwen3.8-27B"), sized for the measured ~16 peak
    concurrency of unbounded reasoning outputs (2 GPUs to keep decode from saturating).
  APP_NAME=rw-qwen3-modal MODEL=Qwen/Qwen3.8-27B-FP8 KV_CACHE_DTYPE=fp8 \
    SERVED_NAME=Qwen/Qwen3.8-27B TOOL_PARSER=hermes REASONING_PARSER=qwen3 \
    GPU=A100-80GB:2 TP=2 MAX_NUM_SEQS=32 \
    uv run --no-sync modal deploy scripts/modal_qwen3_vllm_server.py
  uv run --no-sync modal app stop <APP_NAME>   # tear down (stop billing)

URL: https://<workspace>--<APP_NAME>-serve.modal.run  (+ /v1 for the OpenAI base). Weights + the serve-path
torch.compile cache reuse the persistent Volumes (fast warm cold-start where a matching cache exists).

Cold start (PS-20, ADR-0132): at TP=1 the server is restored from a Modal GPU memory snapshot. A boot without one
starts vLLM, warms it, puts it to sleep (weights parked in host RAM) and Modal snapshots the container; a restore
only wakes vLLM (~13 s from container start). Modal builds the first 2-3 snapshots per GPU type, and now and then
another, on ordinary boots; stopping the app discards them. vLLM listens on 127.0.0.1 only: sleep mode needs its
unauthenticated dev routes, so the public port is `rw_gate.py`, which forwards only /v1/* and /health.
"""

import os
import subprocess
import time
import urllib.request
from pathlib import Path

import modal

MODEL = os.environ.get("MODEL", "Qwen/Qwen3.8-27B")          # ...-FP8 for the quantized-weights config
KV_DTYPE = os.environ.get("KV_CACHE_DTYPE", "auto")          # "auto" (bf16 KV, the reference) | "fp8"
GPU = os.environ.get("GPU", "A100-80GB:1")
TP = int(os.environ.get("TP", "1"))
MAX_LEN = int(os.environ.get("MAX_LEN", "16384"))            # the real workload's context (9K+ prompts)
GPU_UTIL = os.environ.get("GPU_UTIL", "0.95")
MAX_NUM_SEQS = os.environ.get("MAX_NUM_SEQS", "256")         # concurrency ceiling; size to the eval's ~16 peak
SERVED = os.environ.get("SERVED_NAME", "qwen3-eval")         # served-model-name clients ask for (profile match)
TOOL_PARSER = os.environ.get("TOOL_PARSER", "")             # "" off | "hermes" -> tool calling (the product agent)
REASONING_PARSER = os.environ.get("REASONING_PARSER", "")   # "" off | "qwen3" -> reasoning coexists with a tool call
APP_NAME = os.environ.get("APP_NAME", "rw-qwen3-vllm-eval")  # distinct app (=distinct URL) per purpose
API_KEY = os.environ.get("VLLM_API_KEY")  # vLLM's --api-key: the only gate on the server's public URL
if not API_KEY:  # no default key (PS-21): a default would be public, and would gate a billing GPU
    raise SystemExit("set VLLM_API_KEY to the server's API key (a long random string) before deploying")
SNAPSHOT = TP == 1  # Modal: GPU memory snapshots do not work across several GPUs; TP>1 boots without one
VLLM_PORT, PUBLIC_PORT = 8001, 8000  # vLLM on loopback; the gate on the public port
GATE = "/root/rw_gate.py"

HF_CACHE = "/root/.cache/huggingface"
VLLM_CACHE = "/root/.cache/vllm"
hf_vol = modal.Volume.from_name("rw-hf-cache", create_if_missing=True)
vllm_cache_vol = modal.Volume.from_name("rw-vllm-serve-compile-cache", create_if_missing=True)

app = modal.App(APP_NAME)
vllm_image = (
    # Base off the OFFICIAL prebuilt vLLM image: vllm + torch + xformers + flashinfer already installed as matched
    # wheels, so a fresh build on a clean account compiles NOTHING (the xformers source-build / resolver failures
    # only happen when pip/uv try to assemble that stack themselves). Latest/unpinned by design — `:latest` tracks
    # newest vLLM, which is all we need since we only consume it. add_python omitted (the image ships Python).
    # The vLLM image ships `python3` but no `python`; Modal's OWN pip bootstrap runs right after FROM (before any
    # later layer) as `python -m pip …` -> exit 127. setup_dockerfile_commands runs BEFORE that bootstrap, so the
    # symlink is in place when Modal bootstraps.
    modal.Image.from_registry(
        "vllm/vllm-openai:latest",
        setup_dockerfile_commands=["RUN ln -sf $(command -v python3) /usr/local/bin/python"],
    )
    .entrypoint([])  # the image's ENTRYPOINT (the api_server) would hijack Modal's runtime; clear it
    .pip_install("hf_transfer")  # enable fast HF weight downloads (HF_HUB_ENABLE_HF_TRANSFER below)
    # Bake the runtime knobs into the image env: a deployed function reads the CONTAINER's env, not the deploying
    # shell's, so an override must live here to reach the container (same lesson as the bench script). Xet disabled
    # so a fresh weight download's open log handle can't block a Volume commit.
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "HF_HUB_DISABLE_XET": "1", "HF_HOME": HF_CACHE,
          "MODEL": MODEL, "KV_CACHE_DTYPE": KV_DTYPE, "TP": str(TP), "MAX_LEN": str(MAX_LEN),
          "GPU_UTIL": GPU_UTIL, "MAX_NUM_SEQS": str(MAX_NUM_SEQS), "SERVED_NAME": SERVED,
          "TOOL_PARSER": TOOL_PARSER, "REASONING_PARSER": REASONING_PARSER, "VLLM_API_KEY": API_KEY,
          # Sleep mode's /sleep and /wake_up exist only in dev mode; they stay on loopback (see the gate).
          "VLLM_SERVER_DEV_MODE": "1", "RW_VLLM_PORT": str(VLLM_PORT), "RW_PUBLIC_PORT": str(PUBLIC_PORT),
          "TORCHINDUCTOR_COMPILE_THREADS": "1"})  # Modal: torch.compile can otherwise break snapshot creation
    .add_local_file(Path(__file__).resolve().parent / "rw_gate.py", GATE)  # resolve(): scripts/ holds a symlink
)


def _vllm(path: str, method: str = "GET", body: bytes | None = None, timeout: float = 600) -> int:
    request = urllib.request.Request(f"http://127.0.0.1:{VLLM_PORT}{path}", data=body, method=method, headers={
        "Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status


def _wait_healthy(proc: subprocess.Popen) -> None:
    while True:
        if proc.poll() is not None:
            raise RuntimeError(f"vLLM exited with {proc.returncode}")
        try:
            if _vllm("/health", timeout=5) == 200:
                return
        except OSError:
            pass
        time.sleep(1)


@app.cls(image=vllm_image, gpu=GPU, volumes={HF_CACHE: hf_vol, VLLM_CACHE: vllm_cache_vol},
         timeout=3600, max_containers=1, scaledown_window=600,
         memory=65536,  # host RAM for the sleeping weights (~29 GiB for Config B), which the snapshot captures
         enable_memory_snapshot=SNAPSHOT, experimental_options={"enable_gpu_snapshot": SNAPSHOT})
# Without this Modal feeds the container ONE HTTP request at a time (Running:1 + "cancellation signal" floods,
# /health blocked) — vLLM's continuous batching never gets fed. Let many requests hit the one container; vLLM
# batches them internally up to --max-num-seqs.
@modal.concurrent(max_inputs=int(MAX_NUM_SEQS))
class QwenServer:
    """vLLM's OpenAI-compatible server for the configured Qwen config. Reuses the persisted weights + serve-path
    torch.compile cache; compiled (production-faithful), optional FP8 KV, optional tool-calling + reasoning parsers so
    ONE endpoint serves free-text, guided/JSON, tool calls, and reasoning together."""

    @modal.enter(snap=True)
    def start(self) -> None:
        """Start vLLM, warm it, and put it to sleep: the snapshot is taken after this (when SNAPSHOT is on)."""
        hf_vol.reload()
        vllm_cache_vol.reload()
        args = [
            "vllm", "serve", MODEL, "--host", "127.0.0.1", "--port", str(VLLM_PORT), "--served-model-name", SERVED,
            "--tensor-parallel-size", str(TP), "--dtype", "bfloat16", "--max-model-len", str(MAX_LEN),
            "--gpu-memory-utilization", GPU_UTIL, "--max-num-seqs", str(MAX_NUM_SEQS),
            "--api-key", API_KEY, "--trust-remote-code", "--enable-sleep-mode",
        ]
        if KV_DTYPE != "auto":
            args += ["--kv-cache-dtype", KV_DTYPE]
        if TOOL_PARSER:  # the product's deep-agent orchestrator is a tool-caller (RuleWright Q1/Q3)
            args += ["--enable-auto-tool-choice", "--tool-call-parser", TOOL_PARSER]
        if REASONING_PARSER:  # so reasoning coexists with a forced tool/schema instead of colliding
            args += ["--reasoning-parser", REASONING_PARSER]
        print(f"[qwen] serving {MODEL} as '{SERVED}' | TP={TP} max_len={MAX_LEN} util={GPU_UTIL} kv={KV_DTYPE} "
              f"max_seqs={MAX_NUM_SEQS} tool={TOOL_PARSER or '-'} reasoning={REASONING_PARSER or '-'} "
              f"snapshot={SNAPSHOT}", flush=True)
        self.vllm = subprocess.Popen(args)
        _wait_healthy(self.vllm)
        warm = (b'{"model": "%s", "messages": [{"role": "user", "content": "Say OK."}], "max_tokens": 8, '
                b'"chat_template_kwargs": {"enable_thinking": false}}' % SERVED.encode())
        for _ in range(2):
            _vllm("/v1/chat/completions", "POST", warm)
        _vllm("/sleep?level=1", "POST", b"")  # level 1: weights to host RAM (in the snapshot), KV cache discarded
        print("[qwen] warmed and asleep; snapshot next", flush=True)

    @modal.enter(snap=False)
    def wake(self) -> None:
        """After a restore (or straight after `start` without one): wake vLLM, then open the public port."""
        _vllm("/wake_up", "POST", b"")
        _wait_healthy(self.vllm)
        self.gate = subprocess.Popen(["python", GATE])
        print("[qwen] awake; gate open", flush=True)

    @modal.web_server(port=PUBLIC_PORT, startup_timeout=1200, label=f"{APP_NAME}-serve")
    def serve(self) -> None:
        """The public URL: the gate, which forwards /v1/* and /health to vLLM."""

    @modal.exit()
    def stop(self) -> None:
        for proc in (getattr(self, "gate", None), getattr(self, "vllm", None)):
            if proc:
                proc.terminate()
