"""GP-1B.4: docling-graph model A/B -- Granite vs Gemma vs DeepSeek on party extraction over a dev slice of
CUAD contracts. Metric: party recall/precision vs (name-filtered) CUAD 'Parties' gold + wall-clock latency.
CUAD 'Parties' gold mixes real names with role labels, so gold is filtered to name-like entries and matched
on normalized (legal-suffix-stripped) containment -- a RELATIVE comparison on the same noisy gold, plus the
hard latency/local-vs-API signal, to pick the extraction model for GP-1B.5. Extraction runs concurrently
(async + semaphore + asyncio.to_thread; CLAUDE.md concurrent-LLM rule).

Run: uv run --no-sync python -m scripts.dg_model_ab   (N=<n> to size the slice)
"""

from __future__ import annotations

import asyncio
import os
import re
import statistics
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv("/Users/farhan/work/RAG_Wright/.env")

from rag_wright.packs.contracts.capabilities.dg_extraction import extract_parties, ollama_model, openrouter_model
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.packs.contracts.spans.cuad_labels import parse_cuad

CUAD = Path("data/cuad/extracted/CUAD_v1.json")
N = int(os.environ.get("N", "12"))
_SUFFIX = (r"(?:inc|incorporated|corp|corporation|llc|l\.?l\.?c|ltd|limited|company|co|gmbh|s\.?a|plc|lp|"
           r"l\.?p|n\.?a)")
_ROLES = {"distributor", "company", "customer", "consultant", "seller", "buyer", "co-trustee", "supplier",
          "licensee", "licensor", "client", "vendor", "contractor", "agent", "party", "parties", "end-user",
          "the seller", "the buyer", "the customer", "the company", "purchaser", "reseller", "manufacturer"}


def _norm_name(s: str) -> str:
    s = s.lower().strip().strip(":")
    s = re.sub(r"[.,]", " ", s)
    s = re.sub(rf"\b{_SUFFIX}\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _is_name(s: str) -> bool:
    """A CUAD 'Parties' entry is a scorable name (not a bare role label) if it carries a legal suffix or
    has 2+ tokens (an org or a person name), and is not itself a role word."""
    low = s.lower().strip().strip(":").strip()
    if low in _ROLES:
        return False
    if re.search(rf"\b{_SUFFIX}\b", low):
        return True
    return len(low.split()) >= 2


def score_parties(extracted: set[str], gold: set[str]):
    """(recall, precision) of extracted party names vs name-filtered gold, on normalized containment. None
    when the contract's gold has no scorable names."""
    g = {n for n in (_norm_name(x) for x in gold if _is_name(x)) if n}
    e = {n for n in (_norm_name(x) for x in extracted) if n}
    if not g:
        return None

    def hit(a: str, bs: set[str]) -> bool:
        return any(a and b and (a in b or b in a) for b in bs)

    recall = sum(1 for gx in g if hit(gx, e)) / len(g)
    precision = (sum(1 for ex in e if hit(ex, g)) / len(e)) if e else 0.0
    return recall, precision


async def _run_model(model, slice_):
    sem = asyncio.Semaphore(2 if model.provider == "ollama" else 8)  # local Ollama shares one GPU

    async def one(text, gold):
        async with sem:
            t0 = time.time()
            try:
                cp = await asyncio.to_thread(extract_parties, text, model)
                names = [p.name for p in cp.parties] if cp else []
            except Exception:  # noqa: BLE001 - a bad extraction counts as empty, run continues
                names = []
            return time.time() - t0, score_parties(set(names), set(gold)), len(names)

    return await asyncio.gather(*[one(text, gold) for _cid, text, gold in slice_])


def main() -> None:
    contracts = []
    for c in parse_cuad(CUAD):
        gold = [a.text for a in c.answers if a.clause_type == "Parties"]
        if gold and c.context:
            contracts.append((c.contract_id, c.context, gold))
        if len(contracts) >= N:
            break
    print(f"[ab] dev slice: {len(contracts)} contracts", flush=True)

    modal_url = os.environ.get("MODAL_GRANITE_URL")
    if modal_url:  # GP-1B.4c: only the Modal-hosted 32B (the others already measured)
        models = {"granite4:small-h 32B (Modal A10)": ollama_model("g32b", "granite4:small-h", base_url=modal_url)}
    else:  # GP-1B.4b: Granite-variant deep-dive (micro baseline vs 7B tiny vs 4.1-8B), DeepSeek reference
        models = {
            "granite4:micro (local)": ollama_model("gmicro", "granite4:micro"),
            "granite4:tiny-h (local)": ollama_model("gtiny", "granite4:tiny-h"),
            "granite-4.1-8b (OR)": openrouter_model("g41-8b", "ibm-granite/granite-4.1-8b"),
            "deepseek (OR, ref)": openrouter_model("deepseek", model_for(ModelRole.STRUCTURED_REASONING)),
        }
    print(f"{'model':16} {'recall':>7} {'prec':>7} {'lat/doc':>8} {'scored':>7}", flush=True)
    for label, m in models.items():
        res = asyncio.run(_run_model(m, contracts))
        scored = [sc for _dt, sc, _n in res if sc is not None]
        recs = [sc[0] for sc in scored]
        precs = [sc[1] for sc in scored]
        lats = [dt for dt, _sc, _n in res]
        print(f"{label:16} {statistics.mean(recs):7.3f} {statistics.mean(precs):7.3f} "
              f"{statistics.mean(lats):7.1f}s {len(scored):>7}", flush=True)


if __name__ == "__main__":
    main()
