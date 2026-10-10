"""MODAL-STACK-2 validation (ADR-0039): does vLLM-Granite (json_object) extraction ANSWER property queries
correctly? NO OpenRouter calls -- the model is the same; we only validate vLLM's own output quality + grounding.

Extract a diverse clause sample via vLLM json_object, then for each clause print the text + the extracted typed
assertions (each assertion = the answer to a "what is the <dimension>?" query) + the grounding-judge verdict, so
correctness + hallucination can be judged directly against the clause.

  VLLM_URL=https://...modal.run N=15 uv run python -m scripts.vllm_kg_query_validate
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

from dotenv import load_dotenv


def _warm(base: str, key: str) -> None:
    for _ in range(120):
        try:
            urllib.request.urlopen(urllib.request.Request(
                base + "/models", headers={"Authorization": f"Bearer {key}"}), timeout=5)
            return
        except Exception:  # noqa: BLE001
            time.sleep(5)


def main() -> None:
    load_dotenv()
    from rag_wright.packs.contracts.capabilities.dg_extraction import ExtractionModel, extract_clause
    from rag_wright.contracts.identifiers import ChunkId
    from rag_wright.packs.contracts.spans.clause_kg_extractor import DGClausePropertyExtractor
    from rag_wright.util.concurrent import map_concurrent

    vbase = os.environ["VLLM_URL"].rstrip("/") + "/v1"
    vkey = os.environ["VLLM_API_KEY"]
    n = int(os.environ.get("N", "15"))

    # a DIVERSE sample: distinct functions so the queries span the property space
    seen, clauses = set(), []
    for line in open("data/models/cuad_clause_cache.jsonl", encoding="utf-8"):
        r = json.loads(line)
        f = r.get("function", "NONE")
        if f not in ("NONE", "", None) and f not in seen and len(r["text"]) < 1200:
            seen.add(f)
            clauses.append(r)
        if len(clauses) >= n:
            break

    print(f"[val] warming vLLM ; {len(clauses)} distinct-function clauses", flush=True)
    _warm(vbase, vkey)
    model = ExtractionModel(label="vllm", provider="hosted_vllm", model="ibm-granite/granite-4.1-8b",
                            base_url=vbase, api_key=vkey, inference="remote")
    ext = DGClausePropertyExtractor(lambda t: extract_clause(t, model, temperature=0.0))  # json_object

    def _ex(c):
        cid = ChunkId.of(c["clause_id"].rsplit(":", 2)[0], 0, c["text"])
        return ext(chunk_id=cid, function=c["function"], text=c["text"])

    recs = map_concurrent(clauses, _ex, max_concurrency=6)

    grounded = ambiguous = 0
    for c, rec in zip(clauses, recs):
        print(f"\n{'=' * 90}\n[{c['function']}]  {c['text'][:280].strip()}...", flush=True)
        if not rec.assertions:
            print("   (no properties extracted)", flush=True)
        for a in rec.assertions:
            tag = "AMBIGUOUS" if "AMBIGUOUS" in str(a.confidence) else "grounded"
            grounded += tag == "grounded"
            ambiguous += tag == "AMBIGUOUS"
            print(f"   Q: what is the {a.dimension}?   ->  {a.value}   [{tag}]", flush=True)

    tot = grounded + ambiguous
    print(f"\n{'=' * 90}\n[val] SUMMARY: {len(clauses)} clauses | {tot} assertions "
          f"| {grounded} grounded / {ambiguous} AMBIGUOUS "
          f"({100 * ambiguous / tot if tot else 0:.0f}% flagged as possibly-hallucinated)", flush=True)


if __name__ == "__main__":
    main()
