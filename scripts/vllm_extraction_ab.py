"""MODAL-STACK-2 (ADR-0039): does vLLM-Granite **structured** clause extraction match OpenRouter-Granite QUALITY?

Extract a real-clause sample via OpenRouter granite-4.1-8b (reference) vs the Modal vLLM granite endpoint (test)
through the SAME `DGClausePropertyExtractor` path, then diff the typed property assertions. The throughput
benchmark only measured raw generation; this measures extraction fidelity.

  VLLM_URL=https://farhan-zaidi--rw-granite-vllm-serve.modal.run N=30 uv run python -m scripts.vllm_extraction_ab
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

from dotenv import load_dotenv


def _warm(url: str, key: str, timeout: int = 600) -> None:
    """Poll the vLLM OpenAI endpoint until it serves the model (first hit cold-starts the A100 container)."""
    req = urllib.request.Request(url + "/models", headers={"Authorization": f"Bearer {key}"})
    for i in range(timeout // 5):
        try:
            urllib.request.urlopen(req, timeout=5)
            print(f"[ab] vLLM endpoint ready after ~{i * 5}s", flush=True)
            return
        except Exception:  # noqa: BLE001 - still cold-starting
            time.sleep(5)
    raise RuntimeError("vLLM endpoint did not become ready")


def main() -> None:
    load_dotenv()
    from rag_wright.capabilities.dg_extraction import ExtractionModel, extract_clause, openrouter_model
    from rag_wright.contracts.identifiers import ChunkId
    from rag_wright.spans.clause_kg_extractor import DGClausePropertyExtractor
    from rag_wright.util.concurrent import map_concurrent

    vllm_base = os.environ["VLLM_URL"].rstrip("/") + "/v1"
    key = os.environ.get("VLLM_API_KEY", "rw-vllm-dev-key")
    n = int(os.environ.get("N", "30"))
    temp = float(os.environ.get("TEMP", "0"))  # pin to 0 (greedy) to isolate model diff from sampling noise
    mode = os.environ.get("MODE", "vllm")  # "vllm" = vLLM-vs-OpenRouter; "control" = OpenRouter-vs-OpenRouter

    clauses = []
    for line in open("data/models/cuad_clause_cache.jsonl", encoding="utf-8"):
        r = json.loads(line)
        if r.get("function", "NONE") not in ("NONE", "", None):
            clauses.append(r)
        if len(clauses) >= n:
            break
    print(f"[ab] {len(clauses)} clauses | TEMP={temp} | MODE={mode}", flush=True)
    or_model = openrouter_model("granite-4.1-8b", "ibm-granite/granite-4.1-8b")
    # litellm's dedicated self-hosted-vLLM provider: docling-graph prefixes -> hosted_vllm/<model> + api_base
    vllm_model = ExtractionModel(label="vllm-granite", provider="hosted_vllm", model="ibm-granite/granite-4.1-8b",
                                 base_url=vllm_base, api_key=key, inference="remote")
    test_model = or_model if mode == "control" else vllm_model  # control = OpenRouter vs itself (noise floor)
    if mode != "control":
        print("[ab] warming vLLM ...", flush=True)
        _warm(vllm_base, key)

    structured = os.environ.get("STRUCTURED", "0") == "1"  # vLLM guided decoding (xgrammar) on the TEST side
    print(f"[ab] test-side structured_output (guided decoding): {structured}", flush=True)
    ref_ext = DGClausePropertyExtractor(lambda t: extract_clause(t, or_model, temperature=temp))
    test_ext = DGClausePropertyExtractor(
        lambda t: extract_clause(t, test_model, temperature=temp, structured_output=structured))

    def _extract(ext, c):
        cid = ChunkId.of(c["clause_id"].rsplit(":", 2)[0], 0, c["text"])
        return ext(chunk_id=cid, function=c["function"], text=c["text"])

    print("[ab] extracting via OpenRouter (reference) ...", flush=True)
    or_recs = map_concurrent(clauses, lambda c: _extract(ref_ext, c), max_concurrency=6)
    print(f"[ab] extracting via {'OpenRouter-again' if mode == 'control' else 'vLLM-Granite'} (test) ...",
          flush=True)
    vllm_recs = map_concurrent(clauses, lambda c: _extract(test_ext, c), max_concurrency=6)

    def aset(rec):
        return {(str(a.dimension), str(a.value)) for a in rec.assertions}

    def amb(rec):
        return sum(1 for a in rec.assertions if "AMBIGUOUS" in str(a.confidence))

    exact = inter = union = or_tot = vllm_tot = or_amb = vllm_amb = or_empty = vllm_empty = 0
    for o, v in zip(or_recs, vllm_recs):
        so, sv = aset(o), aset(v)
        exact += (so == sv)
        inter += len(so & sv)
        union += len(so | sv)
        or_tot += len(so)
        vllm_tot += len(sv)
        or_amb += amb(o)
        vllm_amb += amb(v)
        or_empty += (len(o.assertions) == 0)
        vllm_empty += (len(v.assertions) == 0)

    k = len(clauses)
    test_name = "OpenRouter#2" if mode == "control" else "vLLM"
    print(f"\n=== MODAL-STACK-2 RESULT ({mode}, TEMP={temp}): OpenRouter vs {test_name} clause extraction ===",
          flush=True)
    print(f"  clauses:            {k}", flush=True)
    print(f"  assertions total:   OpenRouter {or_tot}  |  {test_name} {vllm_tot}", flush=True)
    print(f"  exact-match records: {exact}/{k} ({100 * exact / k:.0f}%)  [identical assertion set]", flush=True)
    print(f"  assertion Jaccard:  {inter / union:.3f}  (shared {inter} / union {union})", flush=True)
    print(f"  empty records:      OpenRouter {or_empty}  |  {test_name} {vllm_empty}", flush=True)
    print(f"  AMBIGUOUS (judge):  OpenRouter {or_amb}  |  {test_name} {vllm_amb}", flush=True)


if __name__ == "__main__":
    main()
