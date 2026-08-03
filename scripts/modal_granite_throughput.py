"""RESEARCH (open thread): granite-4.1-8b BULK clause-extraction throughput --
self-hosted **vLLM** (continuous batching, 1 GPU) vs the **single OpenRouter provider**.

Tests your "run granite on the GPU" instinct properly: Ollama was single-stream (slow); vLLM does continuous
batching, so one GPU can serve many concurrent extraction requests. Question: does a single-GPU vLLM granite
match/beat the one OpenRouter provider for the CUAD-FULL-COVERAGE fresh-tail bottleneck (~63 granite calls/doc)?

  # vLLM batched throughput on an A10 (also GPU=L4 / GPU=A100)
  GPU=A10 uv run --no-sync modal run scripts/modal_granite_throughput.py::vllm_sweep
  # OpenRouter throughput at a concurrency sweep (single provider; run from Modal = fair us-central network)
  uv run --no-sync modal run scripts/modal_granite_throughput.py::openrouter_sweep

Metric: extraction requests/sec + output tokens/sec (and error rate) on a representative clause->JSON workload.
"""

import os
import time

import modal

MODEL_HF = os.environ.get("GRANITE_HF", "ibm-granite/granite-4.1-8b")  # HF id for vLLM self-host
MODEL_OR = os.environ.get("GRANITE_OR", "ibm-granite/granite-4.1-8b")  # OpenRouter id (single provider)
GPU = os.environ.get("GPU", "A10")
N = int(os.environ.get("N", "200"))  # requests per sweep
MAX_TOK = int(os.environ.get("MAX_TOK", "256"))  # ~ a clause-property JSON

app = modal.App("rw-granite-throughput")

# Representative clause-extraction workload: real-ish legal clauses (varied length) + a structured-JSON ask,
# ~MAX_TOK output -- the shape of KG-2 clause extraction, isolated from docling-graph so we measure the BACKEND.
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
hf_vol = modal.Volume.from_name("rw-hf-cache", create_if_missing=True)  # persist the ~16GB granite download
# CUDA `devel` base (has nvcc at /usr/local/cuda) so flashinfer's runtime JIT works; Modal-managed python
# via add_python; vllm via pip. (The slim image lacked nvcc; the vllm-openai image's python wasn't at `python`.)
vllm_image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.12")
    .pip_install("vllm", "huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "HF_HOME": HF_CACHE})
)


@app.function(image=vllm_image, gpu=GPU, timeout=2400, volumes={HF_CACHE: hf_vol})
def vllm_sweep() -> None:
    """Load granite on ONE GPU under vLLM and measure batched (continuous-batching) throughput over N prompts.
    The HF cache is a persistent Modal Volume, so granite is downloaded ONCE and reused across runs/GPUs."""
    import torch
    from huggingface_hub import snapshot_download
    from vllm import LLM, SamplingParams

    hf_vol.reload()  # pick up a previously-cached download
    # download weights FIRST + commit, so the ~16GB persists on the Volume even if engine init fails
    print(f"[vllm] ensuring {MODEL_HF} weights on the Volume ...", flush=True)
    snapshot_download(MODEL_HF)
    hf_vol.commit()
    print(f"[vllm] weights cached; loading on {GPU} (vLLM continuous batching) ...", flush=True)
    t0 = time.time()
    llm = LLM(model=MODEL_HF, max_model_len=4096, gpu_memory_utilization=0.90, trust_remote_code=True)
    load = time.time() - t0
    dev = torch.cuda.get_device_name(0)  # the ACTUAL GPU (the GPU env label defaults in-container)
    hf_vol.commit()  # persist the downloaded weights so the next run/GPU reuses them (no re-download)
    print(f"[vllm] loaded in {load:.0f}s on {dev}", flush=True)

    sp = SamplingParams(max_tokens=MAX_TOK, temperature=0.0)
    prompts = _prompts(N)
    t0 = time.time()
    outs = llm.generate(prompts, sp)  # vLLM auto-batches all N (continuous batching) = the 1-GPU peak
    dt = time.time() - t0
    out_tok = sum(len(o.outputs[0].token_ids) for o in outs)
    print(f"[vllm] RESULT {dev}: {N} reqs in {dt:.1f}s = {N / dt:.2f} req/s | {out_tok / dt:.0f} out-tok/s "
          f"(one GPU, batched)", flush=True)


_OR_KEY = os.environ.get("OPENROUTER_API_KEY", "")  # real value comes from the Modal secret in-container


@app.function(image=modal.Image.debian_slim(python_version="3.12").pip_install("openai"),
              timeout=2400, secrets=[modal.Secret.from_name("rw-openrouter")])
def openrouter_sweep() -> None:
    """Fire the same clause->JSON workload at OpenRouter granite-4.1-8b at rising concurrency; report req/s +
    tok/s + error rate. Answers thread #1: does bumping concurrency raise throughput or just 429?"""
    import concurrent.futures

    from openai import OpenAI

    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"])

    def one(p: str):
        t0 = time.time()
        try:
            r = client.chat.completions.create(
                model=MODEL_OR, messages=[{"role": "user", "content": p}],
                max_tokens=MAX_TOK, temperature=0.0)
            return time.time() - t0, (r.usage.completion_tokens if r.usage else 0), None
        except Exception as e:  # noqa: BLE001
            return time.time() - t0, 0, type(e).__name__ + ": " + str(e)[:100]

    for C in (8, 16, 32, 64):
        prompts = _prompts(N)
        t0 = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=C) as ex:
            res = list(ex.map(one, prompts))
        dt = time.time() - t0
        errs = [r[2] for r in res if r[2]]
        toks = sum(r[1] for r in res)
        lat = sorted(r[0] for r in res if not r[2])
        p50 = lat[len(lat) // 2] if lat else 0
        ok = N - len(errs)
        print(f"[openrouter] C={C:>2}: {ok}/{N} ok in {dt:.1f}s = {ok / dt:.2f} req/s | {toks / dt:.0f} tok/s "
              f"| p50 lat {p50:.1f}s | {len(errs)} errors", flush=True)
        if errs:
            print(f"  first error: {errs[0]}", flush=True)
