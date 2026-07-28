"""GP-1B.4c: a reusable Granite-on-Modal server (Modal's new `@app.server()` primitive; ADR-0030 infra).

Modal Endpoints (the managed product) does NOT support Granite (its hybrid Mamba2/MoE architecture isn't in
the Endpoints catalog -- only Qwen/Gemma/DeepSeek/GLM/Nemotron/gpt-oss/Kimi). So we self-host Granite via a
custom `@app.server` running Ollama on a GPU, exposing Ollama's HTTP API. Our extraction seam already talks
to a remote Ollama via `ollama_model(base_url=<url>)`, so nothing else changes.

Reusable for any Ollama Granite variant (set MODEL): here `granite4:small-h` (32B MoE / 9B active, ~19GB Q4)
to see whether a bigger Granite beats DeepSeek / granite-4.1-8b, and to become Modal-ready.

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

MODEL = os.environ.get("GRANITE_MODEL", "granite4:small-h")
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


@app.server(image=image, gpu="A10", port=11434, volumes={OLLAMA_DIR: vol},
            unauthenticated=True, startup_timeout=600, scaledown_window=300)
class GraniteServer:
    @modal.enter()
    def start(self) -> None:
        # bind 0.0.0.0 so Modal's proxy can reach it; the model is already in the mounted Volume
        _serve_and_wait("0.0.0.0:11434")
