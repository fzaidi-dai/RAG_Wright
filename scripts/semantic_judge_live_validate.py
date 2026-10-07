"""JUDGE-SEMANTIC (ADR-0040) LIVE validation on self-hosted vLLM-Granite (A100, ADR-0039) -- NO OpenRouter.

Extract a diverse real-clause sample via vLLM-Granite (so the deterministic Layer-1/2 gates run), then for the
surviving SEMANTIC assertions run the Layer-3 LLM judge on the SAME Granite endpoint and report:
  (1) does the judge AGREE with the extractor's own reading? (low refute rate = not over-aggressive), and
  (2) DISCRIMINATION PROBE: judged against a FLIPPED value (mutual->unilateral), does it refute? (high = it
      actually reads the clause, not rubber-stamping).

  VLLM_URL=https://farhan-zaidi--rw-granite-vllm-serve.modal.run N=12 uv run python -m scripts.semantic_judge_live_validate
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

from dotenv import load_dotenv


def _log(msg: str) -> None:
    print(msg, flush=True)


def _warm(base: str, key: str) -> None:
    for _ in range(120):
        try:
            urllib.request.urlopen(urllib.request.Request(
                base + "/models", headers={"Authorization": f"Bearer {key}"}), timeout=5)
            return
        except Exception:  # noqa: BLE001 - still cold-starting the A100
            time.sleep(5)


def main() -> None:
    load_dotenv()
    from langchain_openai import ChatOpenAI

    from rag_wright.packs.contracts.capabilities.dg_extraction import ExtractionModel, extract_clause
    from rag_wright.contracts.identifiers import ChunkId
    from rag_wright.packs.contracts.schemas.property import CLOSED_VOCAB
    from rag_wright.contracts.provenance import ConfidenceTag
    from rag_wright.packs.contracts.spans.clause_kg_extractor import DGClausePropertyExtractor
    from rag_wright.packs.contracts.spans.semantic_judge import (
        SEMANTIC_DIMENSIONS,
        build_semantic_judge_fn,
        semantic_judge,
    )
    from rag_wright.util.concurrent import map_concurrent

    vbase = os.environ["VLLM_URL"].rstrip("/") + "/v1"
    vkey = os.environ.get("VLLM_API_KEY", "rw-vllm-dev-key")
    n = int(os.environ.get("N", "12"))
    model_id = "ibm-granite/granite-4.1-8b"

    # a DIVERSE sample: distinct functions so the semantic dims are exercised across clause types
    seen, clauses = set(), []
    for line in open("data/models/cuad_clause_cache.jsonl", encoding="utf-8"):
        r = json.loads(line)
        f = r.get("function", "NONE")
        if f not in ("NONE", "", None) and f not in seen and 120 < len(r["text"]) < 1100:
            seen.add(f)
            clauses.append(r)
        if len(clauses) >= n:
            break

    _log(f"[live] warming vLLM-Granite ({model_id}); {len(clauses)} distinct-function clauses")
    _warm(vbase, vkey)

    # extraction path (docling-graph) -> DGClausePropertyExtractor applies reground + symbolic_validate
    ex_model = ExtractionModel(label="vllm", provider="hosted_vllm", model=model_id,
                               base_url=vbase, api_key=vkey, inference="remote")
    extractor = DGClausePropertyExtractor(lambda t: extract_clause(t, ex_model, temperature=0.0))

    # the Layer-3 judge on the SAME vLLM-Granite endpoint (structured json_schema via guided decoding)
    def vllm_structured_factory(mid, schema):
        return ChatOpenAI(model=mid, temperature=0.0, base_url=vbase, api_key=vkey,
                          timeout=120, max_retries=4).with_structured_output(schema, method="json_schema")

    judge_fn = build_semantic_judge_fn(model_id, structured_factory=vllm_structured_factory)

    _log(f"[live] extracting {len(clauses)} clauses via vLLM-Granite ...")

    def _extract(c):
        cid = ChunkId.of(c["clause_id"].rsplit(":", 2)[0], 0, c["text"])
        return extractor(chunk_id=cid, function=c["function"], text=c["text"])

    records = map_concurrent(clauses, _extract, max_concurrency=6)

    agree = refute = flip_refute = flip_total = judged = 0
    for i, (c, rec) in enumerate(zip(clauses, records), 1):
        text = c["text"]
        alive = [a for a in rec.assertions
                 if a.dimension in SEMANTIC_DIMENSIONS and a.confidence != ConfidenceTag.AMBIGUOUS]
        _log(f"\n[{i}/{len(clauses)}] {'=' * 80}\n[{c['function']}] {text[:220].strip()}...")
        if not alive:
            _log("   (no surviving semantic assertions)")
            continue

        judged_rec = semantic_judge(rec, text, judge_fn)
        judged_conf = {(a.dimension, a.value): a.confidence for a in judged_rec.assertions}
        for a in alive:
            judged += 1
            verdict = judged_conf[(a.dimension, a.value)]
            kept = verdict != ConfidenceTag.AMBIGUOUS
            agree += kept
            refute += not kept
            mark = "KEPT (supported)" if kept else "REFUTED -> AMBIGUOUS"
            _log(f"   {a.dimension.value} = {a.value}   ->  {mark}")

            # discrimination probe: judge a FLIPPED value on the same clause -- a good judge should refute it
            vocab = CLOSED_VOCAB.get(a.dimension, frozenset())
            flip = next((v for v in sorted(vocab) if v != a.value), None)
            if flip is not None:
                v = judge_fn(a.dimension, flip, text)
                flip_total += 1
                flipped_refuted = v is not None and not v.supported
                flip_refute += flipped_refuted
                _log(f"       probe: {a.dimension.value} = {flip} (flipped)  ->  "
                     f"{'refuted (good)' if flipped_refuted else 'SUPPORTED (judge did not discriminate)'}")

    _log(f"\n{'=' * 90}\n[live] SUMMARY: {judged} semantic assertions judged | "
         f"extractor-reading KEPT {agree} / REFUTED {refute} "
         f"({100 * refute / judged if judged else 0:.0f}% refute of its own reading) | "
         f"flip-probe: {flip_refute}/{flip_total} flipped values correctly refuted "
         f"({100 * flip_refute / flip_total if flip_total else 0:.0f}% discrimination)")


if __name__ == "__main__":
    main()
