"""MS1-5b (MODAL-STACK-1, ADR-0039): service CORRECTNESS + coexistence on the co-located A100 stack.

Distinct from MS1-5a's fit test. Verifies each service produces CORRECT output against ground truth from the
adopted KG (ragwright_cuad_full), then that all three coexist under CONCURRENT load without OOM:
  (1) BGE-M3 /embed  -> cosine(A100-dense, the span's STORED dense) ~1.0  (the silent-retrieval-drift check;
      stored vectors were built with BGE-M3 on the GCP L4 -> A100 BGE must land in the same space),
  (2) LegalBERT /classify -> matches the span's STORED `function` (the classifier label at ingest),
  (3) concurrent /embed + /classify + /v1 -> all succeed, memory still fits (no OOM under load).

  STACK_URL=https://farhan-zaidi--rw-stack-a100-stack-web.modal.run uv run python -m scripts.stack_correctness_validate
"""

from __future__ import annotations

import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv


def _post(url: str, payload: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer rw-vllm-dev-key"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _get(url: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def _cosine(a: list[float], b: list[float]) -> float:
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def main() -> None:
    load_dotenv()
    from arcadedb_python import DatabaseDao, SyncClient

    url = os.environ["STACK_URL"].rstrip("/")
    c = SyncClient(os.environ["ARCADEDB_HOST"], os.environ["ARCADEDB_PORT"],
                   username=os.environ["ARCADEDB_USER"], password=os.environ["ARCADEDB_PASSWORD"])
    dao = DatabaseDao(c, "ragwright_cuad_full")

    # a diverse sample: distinct functions, non-trivial text
    seen, spans = set(), []
    for r in dao.query("sql", "SELECT text, dense, function FROM Span WHERE function <> 'NONE' LIMIT 400"):
        f = r.get("function")
        if f and f not in seen and 40 < len(r["text"]) < 900:
            seen.add(f)
            spans.append(r)
        if len(spans) >= 10:
            break
    print(f"[5b] {len(spans)} distinct-function spans from ragwright_cuad_full", flush=True)

    # (1) BGE consistency + (2) LegalBERT correctness
    cos_min = 1.0
    lb_match = 0
    for i, sp in enumerate(spans, 1):
        emb = _post(f"{url}/embed", {"text": sp["text"]})
        cos = _cosine(emb["dense"][0], sp["dense"])
        cos_min = min(cos_min, cos)
        lab = _post(f"{url}/classify", {"text": sp["text"]})["labels"][0]
        ok = lab == sp["function"]
        lb_match += ok
        print(f"  [{i}/{len(spans)}] {sp['function'][:26]:26s} | BGE cos={cos:.4f} | "
              f"LegalBERT={lab[:26]:26s} {'✓' if ok else '✗ (stored '+sp['function']+')'}", flush=True)

    # (3) concurrent coexistence: mixed load hitting all three at once
    texts = [sp["text"] for sp in spans]

    errors: list[str] = []

    def _job(k):
        try:
            if k % 3 == 0:
                return "embed", bool(_post(f"{url}/embed", {"text": texts[k % len(texts)]})["dense"])
            if k % 3 == 1:
                return "classify", bool(_post(f"{url}/classify", {"text": texts[k % len(texts)]})["labels"])
            out = _post(f"{url}/v1/chat/completions", {
                "model": "ibm-granite/granite-4.1-8b",
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "max_tokens": 5, "temperature": 0})
            return "generate", bool(out["choices"][0]["message"]["content"])
        except Exception as exc:  # noqa: BLE001 - record, don't crash the coexistence probe
            kind = ("embed", "classify", "generate")[k % 3]
            errors.append(f"{kind}: {exc}")
            return kind, False

    with ThreadPoolExecutor(max_workers=12) as ex:
        results = list(ex.map(_job, range(24)))
    by_kind = {}
    for kind, ok in results:
        by_kind.setdefault(kind, [0, 0])
        by_kind[kind][0] += ok
        by_kind[kind][1] += 1
    mem = _get(f"{url}/health")["memory"]

    print("\n[5b] SUMMARY", flush=True)
    if errors:
        print(f"  concurrent errors ({len(errors)}): {errors[0]}", flush=True)
    print(f"  (1) BGE consistency: min cosine vs stored = {cos_min:.4f}  "
          f"({'PASS' if cos_min > 0.99 else 'CHECK'} -- same vector space as the KG)", flush=True)
    print(f"  (2) LegalBERT correctness: {lb_match}/{len(spans)} match the stored function label", flush=True)
    print(f"  (3) concurrent coexistence (24 mixed reqs): "
          f"{', '.join(f'{k} {v[0]}/{v[1]}' for k, v in sorted(by_kind.items()))}", flush=True)
    print(f"      memory after load: used {mem['gpu_used_GB']}GB / {mem['gpu_total_GB']}GB "
          f"(free {mem['gpu_free_GB']}GB) -- {'no OOM' if mem['gpu_free_GB'] > 0.5 else 'TIGHT'}", flush=True)


if __name__ == "__main__":
    main()
