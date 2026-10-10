"""JUDGE-SEMANTIC focused DISCRIMINATION diagnostic on vLLM-Granite -- isolates the judge from extraction
sparsity. Hand-labeled clear cases: for each, judge the CORRECT value (expect supported) and a WRONG value
(expect refuted). Reports whether the judge discriminates at all, per structured method.

  VLLM_URL=... METHOD=json_schema uv run python -m scripts.semantic_judge_probe
"""

from __future__ import annotations

import os
import time
import urllib.request

from dotenv import load_dotenv

# (clause_text, dimension, correct_value, wrong_value) -- unambiguous readings, cleaner dims than ip_ownership
CASES = [
    ("Each party shall indemnify and hold harmless the other party from any third-party claims.",
     "MUTUALITY", "mutual", "unilateral"),
    ("Licensee shall indemnify Licensor against all losses. Licensor has no such obligation to Licensee.",
     "MUTUALITY", "unilateral", "mutual"),
    ("This Agreement shall be governed exclusively by the laws of the State of New York.",
     "LAW_MULTIPLICITY", "single", "multiple"),
    ("All intellectual property created hereunder shall be jointly owned by both parties in equal shares.",
     "IP_OWNERSHIP", "joint", "assigned"),
    ("Either party may terminate this Agreement for convenience upon thirty (30) days written notice.",
     "TERMINATION_RIGHT", "either_party", "one_party"),
    ("Only the Supplier may terminate this Agreement for convenience; the Customer has no such right.",
     "TERMINATION_RIGHT", "one_party", "either_party"),
]


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
    from langchain_openai import ChatOpenAI

    from rag_wright.packs.contracts.schemas.property import PropertyDimension
    from rag_wright.packs.contracts.spans.semantic_judge import build_semantic_judge_fn

    vbase = os.environ["VLLM_URL"].rstrip("/") + "/v1"
    vkey = os.environ["VLLM_API_KEY"]
    method = os.environ.get("METHOD", "json_schema")
    model_id = "ibm-granite/granite-4.1-8b"
    print(f"[probe] METHOD={method} ; warming {model_id}", flush=True)
    _warm(vbase, vkey)

    def factory(mid, schema):
        return ChatOpenAI(model=mid, temperature=0.0, base_url=vbase, api_key=vkey,
                          timeout=120, max_retries=4).with_structured_output(schema, method=method)

    judge = build_semantic_judge_fn(model_id, structured_factory=factory)

    correct_ok = wrong_ok = 0
    for i, (text, dim_name, correct, wrong) in enumerate(CASES, 1):
        dim = PropertyDimension[dim_name]
        vc = judge(dim, correct, text)
        vw = judge(dim, wrong, text)
        c_ok = vc is not None and vc.supported          # correct value should be SUPPORTED
        w_ok = vw is not None and not vw.supported        # wrong value should be REFUTED
        correct_ok += c_ok
        wrong_ok += w_ok
        print(f"\n[{i}/{len(CASES)}] {dim_name}: {text[:70]}...", flush=True)
        print(f"   correct {dim.value}={correct}: supported={None if vc is None else vc.supported} "
              f"{'OK' if c_ok else 'MISS'}   ({'' if vc is None else vc.reason[:80]})", flush=True)
        print(f"   wrong   {dim.value}={wrong}: supported={None if vw is None else vw.supported} "
              f"{'OK' if w_ok else 'MISS'}   ({'' if vw is None else vw.reason[:80]})", flush=True)

    n = len(CASES)
    print(f"\n[probe] SUMMARY (METHOD={method}): correct-supported {correct_ok}/{n} | "
          f"wrong-refuted {wrong_ok}/{n} | discrimination {correct_ok + wrong_ok}/{2 * n}", flush=True)


if __name__ == "__main__":
    main()
