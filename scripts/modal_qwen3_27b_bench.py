"""Qwen3.8-27B (full bf16) on Modal 2x A100-80GB -- PRODUCTION-FAITHFUL benchmark (served path, compiled).

Measures the model the way it is actually deployed (ADR-0039, like `modal_granite_vllm_server.py`): a persistent
`vllm serve` OpenAI endpoint, tensor_parallel_size=2, torch.compile ON (compiled kernels + CUDA graphs), serving
concurrent requests via continuous batching. NOT the offline `LLM()` engine, and NOT `enforce_eager` -- both give
numbers that don't reflect production (a served, compiled, kept-warm deployment).

Load time is a COLD-START cost (deploy / scale-up / post-idle), paid rarely; inference speed (compiled) is paid on
every request. So we keep compilation ON and cut cold-start by persisting the SERVE-path torch.compile cache on a
Volume -- exactly what a real deploy does (first container compiles, later containers reuse it).

  # step 1 (once): cold-compile the SERVE path and persist its compile cache to the Volume
  uv run --no-sync modal run scripts/modal_qwen3_27b_bench.py::warm_serve_cache
  # step 2: production-grade numbers -- WARM cold-start load time + latency/throughput under concurrency
  uv run --no-sync modal run scripts/modal_qwen3_27b_bench.py::serve_bench
  # optional: pre-download weights to the Volume (no GPU)
  uv run --no-sync modal run scripts/modal_qwen3_27b_bench.py::warm_weights

Config (env): MODEL, GPU, TP, MAX_LEN, GPU_UTIL, MAX_NUM_SEQS, N (per concurrency level), MAX_TOK, IN_TOK, CSWEEP.
Metrics: server-ready seconds (cold-start load); and per concurrency C: req/s, out-tok/s, TTFT p50/p95, total p50/p95.
"""

import os
import time

import modal

MODEL = os.environ.get("MODEL", "Qwen/Qwen3.8-27B")      # full bf16; the HF repo id
GPU = os.environ.get("GPU", "A100-80GB:2")               # TWO 80GB A100s
TP = int(os.environ.get("TP", "2"))                      # tensor_parallel_size -- must equal the GPU count
MAX_LEN = int(os.environ.get("MAX_LEN", "16384"))        # context per request; KV cache is sized against this
GPU_UTIL = os.environ.get("GPU_UTIL", "0.90")            # fraction of 2x80GB (weights + KV cache)
MAX_NUM_SEQS = os.environ.get("MAX_NUM_SEQS", "256")     # vLLM running-sequence cap (concurrency ceiling)
N = int(os.environ.get("N", "48"))                       # requests per concurrency level (small -> cheap, meaningful)
MAX_TOK = int(os.environ.get("MAX_TOK", "256"))          # output tokens/request (a clause-property JSON size)
CSWEEP = [int(x) for x in os.environ.get("CSWEEP", "1,4,8,16,32,64").split(",")]
API_KEY = os.environ.get("VLLM_API_KEY", "rw-vllm-dev-key")

app = modal.App("rw-qwen3-27b-bench")

# Representative clause->JSON workload (same shape as the granite benchmark), so numbers compare like-for-like.
CLAUSES = [
    "This Agreement shall be governed by and construed in accordance with the laws of the State of Delaware, "
    "without regard to its conflict-of-law principles, and the parties consent to the exclusive jurisdiction "
    "of the state and federal courts located in Wilmington, Delaware.",
    "In no event shall either party's aggregate liability arising out of or related to this Agreement exceed "
    "the total fees paid by Customer in the twelve (12) months preceding the event giving rise to the claim, "
    "except for breaches of confidentiality or indemnification obligations, which shall be uncapped.",
    "Either party may terminate this Agreement for convenience upon ninety (90) days' prior written notice; "
    "the non-breaching party may terminate immediately upon a material breach that remains uncured thirty (30) "
    "days after written notice thereof.",
    "All intellectual property, including inventions, works of authorship, and improvements conceived or "
    "reduced to practice in the performance of the Services, shall be the sole and exclusive property of the "
    "Company, and Contractor hereby irrevocably assigns all right, title, and interest therein.",
    "Each party agrees to hold in strict confidence and not to disclose to any third party any Confidential "
    "Information of the other party for a period of five (5) years following the date of disclosure, except as "
    "required by law or with the prior written consent of the disclosing party.",
    "The Supplier shall indemnify, defend, and hold harmless the Buyer and its affiliates from and against any "
    "and all losses, damages, liabilities, and expenses (including reasonable attorneys' fees) arising from any "
    "third-party claim of infringement of intellectual property rights.",
    "During the Term and for a period of two (2) years thereafter, the Distributor shall not, directly or "
    "indirectly, sell, market, or distribute any products that compete with the Products within the Territory.",
    "Neither party may assign this Agreement or any of its rights or obligations hereunder without the prior "
    "written consent of the other party, except that either party may assign to a successor in connection with "
    "a merger, acquisition, or sale of all or substantially all of its assets.",
]
_FIELDS = ("clause_type, governing_law, jurisdiction, liability_cap_amount, liability_cap_basis, "
           "term_notice_days, cure_period_days, ip_ownership, confidentiality_duration, exclusivity_scope, "
           "assignment_consent_required, indemnification_scope")
_PROMPT = ("You extract structured fields from a contract clause. Return ONLY a compact JSON object with these "
           "keys (use null if not present): " + _FIELDS + ".\n\nClause:\n{clause}\n\nJSON:")


def _prompts(n: int) -> list:
    return [_PROMPT.format(clause=CLAUSES[i % len(CLAUSES)]) for i in range(n)]


HF_CACHE = "/root/.cache/huggingface"
VLLM_CACHE = "/root/.cache/vllm"  # vLLM's torch.compile cache -- persisted so the SERVE-path compile is paid once
hf_vol = modal.Volume.from_name("rw-hf-cache", create_if_missing=True)
vllm_cache_vol = modal.Volume.from_name("rw-vllm-serve-compile-cache", create_if_missing=True)
_VOLS = {HF_CACHE: hf_vol, VLLM_CACHE: vllm_cache_vol}
vllm_image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.12")
    .pip_install("vllm", "huggingface_hub[hf_transfer]", "openai")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "HF_HOME": HF_CACHE})
)


def _serve_args() -> list:
    """The vLLM serve flags -- IDENTICAL across warm_serve_cache and serve_bench so the torch.compile cache key
    matches and the persisted cache is reused. Compiled (NO --enforce-eager): production keeps compilation ON."""
    return [
        "vllm", "serve", MODEL, "--host", "127.0.0.1", "--port", "8000", "--served-model-name", MODEL,
        "--tensor-parallel-size", str(TP), "--dtype", "bfloat16", "--max-model-len", str(MAX_LEN),
        "--gpu-memory-utilization", str(GPU_UTIL), "--max-num-seqs", str(MAX_NUM_SEQS), "--api-key", API_KEY,
        "--trust-remote-code",
    ]


def _start_server_and_wait():
    """Start `vllm serve`, wait for /health, return (proc, ready_seconds). Reuses the persisted compile cache."""
    import subprocess

    import httpx

    hf_vol.reload()
    vllm_cache_vol.reload()  # reuse the persisted SERVE-path torch.compile cache (fast warm cold-start)
    args = _serve_args()
    print(f"[qwen] starting: {' '.join(args)}", flush=True)
    t0 = time.time()
    proc = subprocess.Popen(args)
    deadline = time.time() + 1500
    while time.time() < deadline:
        try:
            if httpx.get("http://127.0.0.1:8000/health", timeout=2).status_code == 200:
                break
        except Exception:  # noqa: BLE001
            pass
        if proc.poll() is not None:
            raise RuntimeError(f"vLLM server exited early (rc={proc.returncode})")
        time.sleep(3)
    else:
        proc.terminate()
        raise RuntimeError("vLLM server did not become healthy in time")
    ready = time.time() - t0
    print(f"[qwen] SERVER READY in {ready:.0f}s (cold-start load: weights + compile-cache reuse + capture)", flush=True)
    return proc, ready


@app.function(image=vllm_image, timeout=3600, volumes={HF_CACHE: hf_vol})
def warm_weights() -> None:
    """Pre-download the bf16 weights to the persistent Volume (no GPU)."""
    from huggingface_hub import snapshot_download

    hf_vol.reload()
    t0 = time.time()
    print(f"[warm] downloading {MODEL} ...", flush=True)
    snapshot_download(MODEL)
    hf_vol.commit()
    print(f"[warm] cached {MODEL} in {time.time() - t0:.0f}s", flush=True)


@app.function(image=vllm_image, gpu=GPU, timeout=3600, volumes=_VOLS)
def warm_serve_cache() -> None:
    """(step 1, once) Cold-compile the SERVE path and PERSIST its torch.compile cache to the Volume -- exactly
    what a real first deploy does. Later `serve_bench` / a deployed server reuse it and cold-start fast."""
    from huggingface_hub import snapshot_download

    snapshot_download(MODEL)
    hf_vol.commit()
    proc, ready = _start_server_and_wait()
    vllm_cache_vol.commit()  # PERSIST the SERVE-path compile cache (matched key, unlike an offline LLM() warm)
    print(f"[warm] serve-path compile cache persisted after a {ready:.0f}s cold compile; later loads reuse it",
          flush=True)
    proc.terminate()


@app.function(image=vllm_image, gpu=GPU, timeout=3600, volumes=_VOLS)
def serve_bench() -> None:
    """(step 2) PRODUCTION-GRADE numbers on the compiled, served path: warm cold-start load time, then per-request
    TTFT + total latency and req/s + out-tok/s at a concurrency sweep. Thinking OFF for a clean latency baseline."""
    import concurrent.futures

    from huggingface_hub import snapshot_download
    from openai import OpenAI

    snapshot_download(MODEL)
    hf_vol.commit()
    proc, ready = _start_server_and_wait()
    vllm_cache_vol.commit()  # keep the cache fresh (no-op if unchanged)
    client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key=API_KEY)

    def one(p: str):
        """One streamed request -> (ttft, total, out_tokens, err). TTFT = time to first token (what users feel)."""
        s = time.time()
        ttft = None
        toks = 0
        try:
            stream = client.chat.completions.create(
                model=MODEL, messages=[{"role": "user", "content": p}], max_tokens=MAX_TOK, temperature=0.0,
                stream=True, stream_options={"include_usage": True},
                extra_body={"chat_template_kwargs": {"enable_thinking": False}})  # thinking off (clean baseline)
            for chunk in stream:
                if ttft is None and chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    ttft = time.time() - s
                if chunk.usage:
                    toks = chunk.usage.completion_tokens
            return ttft or (time.time() - s), time.time() - s, toks, None
        except Exception as e:  # noqa: BLE001
            return time.time() - s, time.time() - s, 0, type(e).__name__ + ": " + str(e)[:100]

    print("[qwen] warmup (8 reqs, not measured) ...", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(one, _prompts(8)))

    def _pct(xs, q):
        return sorted(xs)[min(len(xs) - 1, int(len(xs) * q))] if xs else 0.0

    print(f"[qwen] SWEEP: N={N}/level, max_tokens={MAX_TOK}, compiled bf16 TP={TP}, thinking off", flush=True)
    for C in CSWEEP:
        t0 = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=C) as ex:
            res = list(ex.map(one, _prompts(N)))
        dt = time.time() - t0
        errs = [r[3] for r in res if r[3]]
        ok = [r for r in res if not r[3]]
        toks = sum(r[2] for r in ok)
        ttfts = [r[0] for r in ok]
        totals = [r[1] for r in ok]
        print(f"[qwen] C={C:>3}: {len(ok)}/{N} ok in {dt:.1f}s = {len(ok) / dt:.2f} req/s | {toks / dt:.0f} out-tok/s "
              f"| TTFT p50 {_pct(ttfts, .5):.2f}s p95 {_pct(ttfts, .95):.2f}s "
              f"| total p50 {_pct(totals, .5):.2f}s p95 {_pct(totals, .95):.2f}s "
              f"| {len(errs)} err" + (f" ({errs[0]})" if errs else ""), flush=True)

    print(f"[qwen] DONE. cold-start(warm-cache)={ready:.0f}s", flush=True)
    proc.terminate()
