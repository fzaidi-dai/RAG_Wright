"""GP-1B.4c: a reusable Granite-on-Modal server (Modal's new `@app.server()` primitive; ADR-0030 infra).

Modal Endpoints (the managed product) does NOT support Granite (its hybrid Mamba2/MoE architecture isn't in
the Endpoints catalog -- only Qwen/Gemma/DeepSeek/GLM/Nemotron/gpt-oss/Kimi). So we self-host Granite via a
custom `@app.server` running Ollama on a GPU, exposing Ollama's HTTP API. Our extraction seam already talks
to a remote Ollama via `ollama_model(base_url=<url>)`, so nothing else changes.

Reusable for any Ollama Granite variant (set GRANITE_MODEL). Default `granite4.1:8b-bf16` -- the adopted
production model (KG-2/KG-4 clause extraction): unquantized 8B (~16GB), fits A10, matches the precision we
validated on OpenRouter. (The earlier 32B `granite4:small-h` A/B is done; generation 4.1 > size 4.0.)

Flow:
  1. `uv run --no-sync modal run scripts/modal_granite_server.py::prepull`   # one-time: model -> Volume (CPU)
  2. `uv run --no-sync modal deploy scripts/modal_granite_server.py`         # deploy the GPU server -> URL
  3. point `ollama_model(base_url=<url>)` at it; run the A/B
  4. `uv run --no-sync modal app stop rw-granite-ollama`                     # tear down (stop billing)
"""

import os
import subprocess
import time
import urllib.request

import modal

MODEL = os.environ.get("GRANITE_MODEL", "granite4.1:8b-bf16")
OLLAMA_DIR = "/root/.ollama"

app = modal.App("rw-granite-ollama")
image = (
    modal.Image.debian_slim()
    .apt_install("curl", "zstd")  # Ollama's install script needs zstd for extraction
    .run_commands("curl -fsSL https://ollama.com/install.sh | sh")
)
vol = modal.Volume.from_name("rw-ollama-models", create_if_missing=True)


def _serve_and_wait(host: str, timeout: int = 180) -> None:
    """Start `ollama serve` bound to `host` and block until its HTTP API answers."""
    subprocess.Popen(["ollama", "serve"], env={**os.environ, "OLLAMA_HOST": host})
    for _ in range(timeout):
        try:
            urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2)
            return
        except Exception:  # noqa: BLE001 - server still starting
            time.sleep(1)
    raise RuntimeError("ollama serve did not become ready")


@app.function(image=image, volumes={OLLAMA_DIR: vol}, timeout=3600)  # CPU only: a pull is just a download
def prepull() -> None:
    """One-time: pull the Granite model into the Volume so the GPU server starts with it cached."""
    _serve_and_wait("127.0.0.1:11434")
    for attempt in range(1, 5):  # large pulls can hit a transient digest mismatch; Ollama resumes on retry
        if subprocess.run(["ollama", "pull", MODEL]).returncode == 0:
            vol.commit()
            print(f"[prepull] {MODEL} cached in the Volume", flush=True)
            return
        print(f"[prepull] pull attempt {attempt} failed, retrying...", flush=True)
    raise RuntimeError(f"ollama pull {MODEL} failed after retries")


@app.function(image=image, gpu="A10", volumes={OLLAMA_DIR: vol}, timeout=1800)
def measure_gpu_memory() -> None:
    """GPU-memory occupancy of granite-4.1-8b (bf16) on an A10: baseline -> weights loaded -> a forward pass.

    Complements the earlier throughput note ([[local-vs-hosted-granite-throughput]]) with VRAM. Reports both
    `nvidia-smi` process memory and Ollama's own `size`/`size_vram` for the loaded model.
      uv run --no-sync modal run scripts/modal_granite_server.py::measure_gpu_memory
    """
    import json

    def gpu_used_mib() -> int:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True).stdout.strip().splitlines()[0]
        used, total = (int(x.strip()) for x in out.split(","))
        gpu_used_mib.total = total  # stash for the caller
        return used

    def ollama_ps() -> list:
        try:
            return json.load(urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=5)).get("models", [])
        except Exception:  # noqa: BLE001
            return []

    _serve_and_wait("0.0.0.0:11434")
    # ensure the model is in the Volume (prepull normally does this; pull here if missing)
    tags = json.load(urllib.request.urlopen("http://127.0.0.1:11434/api/tags")).get("models", [])
    if not any(MODEL in m.get("name", "") for m in tags):
        subprocess.run(["ollama", "pull", MODEL], check=True)
        vol.commit()

    base = gpu_used_mib()
    total = gpu_used_mib.total
    print(f"[mem] A10: {total} MiB total | baseline (server up, weights NOT loaded): {base} MiB", flush=True)

    # a single forward pass -- this loads the weights into VRAM and runs one generation
    prompt = ("Extract the governing law as JSON. Clause: 'This Agreement shall be governed by and construed "
              "in accordance with the laws of the State of Delaware, without regard to conflict-of-law rules.'")
    t0 = time.time()
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=json.dumps({"model": MODEL, "prompt": prompt, "stream": False,
                         "options": {"num_predict": 128, "num_ctx": 4096}}).encode(),
        headers={"Content-Type": "application/json"})
    json.load(urllib.request.urlopen(req, timeout=900))
    dt = time.time() - t0

    loaded = gpu_used_mib()
    print(f"[mem] after 1 forward pass ({dt:.1f}s): {loaded} MiB used "
          f"| model+KV footprint (delta from baseline): {loaded - base} MiB", flush=True)
    for m in ollama_ps():
        sz = m.get("size", 0) // (1024 * 1024)
        vram = m.get("size_vram", 0) // (1024 * 1024)
        print(f"[mem] ollama /api/ps: {m.get('name')} | size={sz} MiB | size_vram={vram} MiB "
              f"(this is the weights+KV Ollama reserved on GPU)", flush=True)
    print(f"[mem] SUMMARY: granite-4.1-8b (bf16) on A10 -> ~{loaded} MiB in use of {total} MiB "
          f"({100.0 * loaded / total:.0f}% of the A10) for load + a forward pass", flush=True)


@app.server(image=image, gpu="A10", port=11434, volumes={OLLAMA_DIR: vol},
            unauthenticated=True, startup_timeout=600, scaledown_window=300)
class GraniteServer:
    @modal.enter()
    def start(self) -> None:
        # bind 0.0.0.0 so Modal's proxy can reach it; the model is already in the mounted Volume
        _serve_and_wait("0.0.0.0:11434")
