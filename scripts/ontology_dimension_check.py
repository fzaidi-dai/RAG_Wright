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
    ("Force Majeure",
     "What EVENTS does this force-majeure clause list as excusing performance? Return each event category as a "
     "short lowercase phrase (e.g. act of god, war, pandemic, government action, labor dispute, supply failure, "
     "natural disaster); [] if none.",
     {"act_of_god", "war", "pandemic", "government_action", "labor_dispute", "supply_failure", "natural_disaster"}),
    ("Royalties",
     "On what BASIS is the royalty / payment in this clause calculated? Return a short lowercase phrase (e.g. "
     "percentage of net sales, percentage of gross sales, per unit, fixed, tiered); [] if unclear.",
     {"pct_net_sales", "pct_gross_sales", "per_unit", "fixed", "tiered"}),
    ("Confidentiality",
     "What EXCEPTIONS / permitted disclosures to the confidentiality obligation does this clause list? Return "
     "each as a short lowercase phrase (e.g. required by law, publicly available, independently developed, prior "
     "possession, received from third party); [] if none.",
     {"required_by_law", "publicly_available", "independently_developed", "prior_possession", "third_party_source"}),
    ("Condition Precedent",
     "What KIND of condition must be satisfied under this condition-precedent clause? Return a short lowercase "
     "phrase (e.g. regulatory approval, financing, third party consent, due diligence, board approval); [] if "
     "unclear.",
     {"regulatory_approval", "financing", "third_party_consent", "due_diligence", "board_approval"}),
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
