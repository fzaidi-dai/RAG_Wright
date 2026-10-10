"""CHUNKER-OPEN (granite-only, no comparison): assess Granite-4.1-8b as the SingleCallBoundaryDiscoverer.

Chunking consumes docling's parse (document.texts items); the discoverer sends `[index] text[:140]` lines and
ONE structured `_BoundaryList` (list of spans) call -> `repair_partition` -> a valid partition. `_BoundaryList`
is a list-of-objects (the shape granite failed under function_calling), so json_schema (guided decoding) is used
-- the granite structured-output fix. Runs on self-hosted vLLM-Granite (A100); NO OpenRouter, NO Gemma.

Per doc, at temperature=0, over R runs: (1) structured output non-degenerate (real span list), (2) RAW span
validity (was repair_partition needed?), (3) CONSISTENCY across runs (identical repaired boundaries?), (4)
boundary sensibility (chunk count + char sizes).

  VLLM_URL=https://farhan-zaidi--rw-granite-vllm-serve.modal.run DOCS=4 RUNS=3 uv run python -m scripts.granite_chunker_assess
"""

from __future__ import annotations

import os
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv


def _warm(base: str, key: str) -> None:
    for _ in range(120):
        try:
            urllib.request.urlopen(urllib.request.Request(
                base + "/models", headers={"Authorization": f"Bearer {key}"}), timeout=5)
            return
        except Exception:  # noqa: BLE001
            time.sleep(5)


def _raw_partition_issues(raw_spans, n: int) -> list[str]:
    """Whether the model's RAW spans already form a valid partition of [0, n) -- what repair_partition had to fix."""
    issues = []
    pairs = [(s.start_index, s.end_index) for s in raw_spans]
    if not pairs:
        return ["empty"]
    if any(not (0 <= a <= b < n) for a, b in pairs):
        issues.append("out-of-range/start>end")
    ordered = sorted(pairs)
    if pairs != ordered:
        issues.append("unordered")
    if ordered[0][0] != 0:
        issues.append("gap-at-start")
    if ordered[-1][1] != n - 1:
        issues.append("gap-at-end")
    for (a, b), (c, d) in zip(ordered, ordered[1:]):
        if c != b + 1:
            issues.append("gap-or-overlap-mid")
            break
    return issues


def main() -> None:
    load_dotenv()
    from docling_core.types.doc.document import DoclingDocument
    from langchain_openai import ChatOpenAI

    from rag_wright.capabilities.rlm_chunking import (
        _BoundaryList,
        _document_items,
        _SINGLE_CALL_PROMPT,
        repair_partition,
    )

    vbase = os.environ["VLLM_URL"].rstrip("/") + "/v1"
    vkey = os.environ["VLLM_API_KEY"]
    n_docs = int(os.environ.get("DOCS", "4"))
    runs = int(os.environ.get("RUNS", "3"))
    model_id = "ibm-granite/granite-4.1-8b"

    offset = int(os.environ.get("OFFSET", "0"))
    cache = Path("data/gate2_cache/parsed")
    paths = sorted(cache.glob("*.json"))[offset:offset + n_docs]
    if not paths:
        raise SystemExit(f"no parsed docs under {cache}")

    print(f"[chunk] warming vLLM-Granite ({model_id}); {len(paths)} docs x {runs} runs @ temp=0", flush=True)
    _warm(vbase, vkey)
    factory = ChatOpenAI(model=model_id, temperature=0.0, base_url=vbase, api_key=vkey,
                         timeout=180, max_retries=4).with_structured_output(_BoundaryList, method="json_schema")

    for path in paths:
        doc = DoclingDocument.load_from_json(path)
        items = _document_items(doc)
        n = len(items)
        name = path.name.split(".")[0][:48]
        if n == 0:
            print(f"\n[{name}] 0 items -> skip", flush=True)
            continue
        body = "\n".join(f"[{it['index']}] {it['text'][:140]}" for it in items)
        prompt = _SINGLE_CALL_PROMPT.format(n=n, last=n - 1, body=body)

        boundary_sigs = []
        for r in range(runs):
            out = factory.invoke(prompt)  # ONE structured call, temp=0
            raw = out.spans
            partition = repair_partition([(s.start_index, s.end_index) for s in raw], n)
            sig = tuple((s.start_index, s.end_index) for s in partition)
            boundary_sigs.append(sig)
            if r == 0:
                issues = _raw_partition_issues(raw, n)
                sizes = [sum(len(items[i]["text"]) for i in range(s.start_index, s.end_index + 1))
                         for s in partition]
                degenerate = "DEGENERATE (empty)" if not raw else "ok"
                print(f"\n[{name}]  items={n}", flush=True)
                print(f"   run1: structured={degenerate}  raw_spans={len(raw)}  "
                      f"repaired_chunks={len(partition)}  raw_valid={'YES' if not issues else 'no -> ' + ','.join(issues)}",
                      flush=True)
                print(f"   chunk chars: min={min(sizes)} mean={sum(sizes) // len(sizes)} max={max(sizes)}",
                      flush=True)
        identical = len(set(boundary_sigs)) == 1
        print(f"   consistency @temp=0 over {runs} runs: "
              f"{'IDENTICAL' if identical else f'VARIED ({len(set(boundary_sigs))} distinct partitions)'}",
              flush=True)

    print("\n[chunk] done", flush=True)


if __name__ == "__main__":
    main()
