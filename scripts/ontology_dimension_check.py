"""ONT-2 (ADR-0049 step 2) vocab CHECK, not source: for a proposed new property dimension, sample real clauses
of its clause type from the KG and have an INDEPENDENT model (DeepSeek) extract the facet free-form, so we can
confirm our domain-knowledge vocab COVERS what appears and catch any value we missed (or proposed-but-absent).
The corpus validates the design; it does not dictate it (generic-customer lens, ADR-0049).

  uv run --no-sync python -m scripts.ontology_dimension_check
"""
from __future__ import annotations

import os
from collections import Counter

from dotenv import load_dotenv
from pydantic import BaseModel


class Facet(BaseModel):
    values: list[str]  # each a short lowercase phrase for the facet, [] if the clause doesn't carry it


# (clause function, facet question, proposed canonical vocab) -- the two HIGH dimensions
CHECKS = [
    ("Dispute Resolution",
     "What dispute-resolution METHOD(s) does this clause specify (how disputes are resolved)? Options include "
     "arbitration, litigation (courts), mediation, expert determination, negotiation/escalation. Return each as a "
     "short lowercase phrase; [] if the clause specifies no method.",
     {"arbitration", "litigation", "mediation", "expert_determination", "negotiation"}),
    ("Security Interest",
     "What COLLATERAL or assets does this clause grant / describe a security interest in? Return each asset "
     "category as a short lowercase phrase (e.g. inventory, equipment, accounts receivable, intellectual "
     "property, real property, all assets, deposit accounts); [] if none.",
     {"accounts_receivable", "inventory", "equipment", "ip", "real_property", "all_assets", "deposit_accounts"}),
]


def log(m: str) -> None:
    print(m, flush=True)


def main() -> None:
    os.environ.setdefault("RAG_SERVING", "openrouter")
    os.environ.pop("OPENROUTER_PROVIDER", None)  # DeepSeek 404s under a Cerebras pin
    load_dotenv()
    from rag_wright.models.profiles import DEFAULT_STRUCTURED_REASONING
    from rag_wright.models.seam import build_structured
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.util.concurrent import map_concurrent

    n = int(os.environ.get("SAMPLE", "18"))
    db = os.environ.get("QA_DB", "ragwright_cuad_full")
    store = ArcadeDBStore.from_env(database=db)
    extractor = build_structured(DEFAULT_STRUCTURED_REASONING, Facet)

    for function, question, proposed in CHECKS:
        rows = store._query(
            f"SELECT span_id FROM Clause WHERE function = '{function}' LIMIT {n}")
        span_ids = [r["span_id"] for r in rows if r.get("span_id")]
        texts = store.span_texts(span_ids)
        items = [(sid, texts.get(sid) or "") for sid in span_ids if texts.get(sid)]
        log(f"\n[check] {function}: {len(items)} sampled clauses")

        def _extract(item):
            _sid, text = item
            try:
                out = extractor.invoke(f"{question}\n\nCLAUSE:\n{text[:1600]}")
                return [v.strip().lower() for v in out.values if v.strip()]
            except Exception as e:  # noqa: BLE001
                log(f"  err: {str(e)[:50]}")
                return []

        results = map_concurrent(items, _extract, max_concurrency=6, label=f"[check:{function[:8]}]",
                                 echo=True, timeout_s=90, timeout_retries=1)
        found: Counter = Counter()
        for vals in results:
            for v in (vals or []):
                found[v] += 1
        log(f"[check] {function} -- free-form facet values the corpus actually carries (value | count):")
        for v, c in found.most_common(30):
            log(f"    {c:3d}  {v}")
        log(f"[check] {function} -- PROPOSED canonical vocab: {sorted(proposed)}")

    store.close()


if __name__ == "__main__":
    main()
