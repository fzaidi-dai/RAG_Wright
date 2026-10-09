#!/usr/bin/env python
"""Build the T10 relational verification set: the load-bearing entities a human verifies.

Canonicalizes the subset's party mentions (T23b method: normalize / reject / cluster), takes the top
hubs by contract count plus their direct co-parties (both endpoints of every intended question), and
attaches local loose CIK candidates with evidence. Writes data/edgar/verification_set.json
(gitignored). The human sets each `resolution` (a CIK / PRIVATE / SKIP); this script never resolves.

    uv run python -m scripts.prep_relational_verification            # or: python scripts/...
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

from rag_wright.corpus.canonicalize import cluster_entities, is_entity, normalize_entity_name
from rag_wright.packs.contracts.ontology.loader import load_entity_rules

from rag_wright.packs.contracts.corpus.edgar import loose_cik_candidates

_RULES = load_entity_rules()  # PS-R5b: the contract domain's non-entity roles
DATA = Path("data")
META_CACHE = DATA / "cuad" / "metadata_cache.json"
SUBSET_MANIFEST = DATA / "cuad" / "subset" / "manifest.json"
TICKERS_CACHE_DIR = DATA / "edgar" / "cache"
OUT = DATA / "edgar" / "verification_set.json"
# Value-based cut, not a round number (a 3+-contract hub has genuine multi-contract relational
# structure worth a non-redundant question; a 2-contract hub's question is thinner). Includes every
# high-value hub (IBM, ScanSource, ...) rather than dropping one at an arbitrary top-N boundary.
MIN_HUB_CONTRACTS = 3


def build() -> dict:
    raw = json.loads(META_CACHE.read_text())
    subset = set(json.loads(SUBSET_MANIFEST.read_text())["selected_ids"])
    metas = [m for m in raw if m["contract_id"] in subset]
    parties_of = {m["contract_id"]: [p for p in m["parties"] if is_entity(p, _RULES)] for m in metas}
    keys_of = {cid: {normalize_entity_name(p) for p in ps} for cid, ps in parties_of.items()}
    clusters = {c.key: c for c in cluster_entities([p for ps in parties_of.values() for p in ps], _RULES)}
    contracts_of: dict[str, set[str]] = collections.defaultdict(set)
    for cid, ks in keys_of.items():
        for k in ks:
            contracts_of[k].add(cid)

    def coparties(k: str) -> list[str]:
        return sorted({x for cid in contracts_of[k] for x in keys_of[cid]} - {k})

    top = sorted(
        (k for k, cs in contracts_of.items() if len(cs) >= MIN_HUB_CONTRACTS),
        key=lambda k: (-len(contracts_of[k]), -len(coparties(k)), k),
    )
    load_bearing = set(top)
    for k in top:
        load_bearing |= set(coparties(k))
    load_bearing &= set(clusters)

    tickers_file = max(TICKERS_CACHE_DIR.glob("*.cache"), key=lambda p: p.stat().st_size)
    tickers = list(json.loads(tickers_file.read_bytes()).values())

    records = []
    cik_to: dict[str, list[str]] = collections.defaultdict(list)
    for k in sorted(load_bearing, key=lambda k: (-len(contracts_of[k]), k)):
        c = clusters[k]
        cands = loose_cik_candidates(c.representative, tickers, top_n=2)
        if cands:
            cik_to[cands[0].proposed_cik].append(k)
        records.append(
            {
                "entity_key": k,
                "representative": c.representative,
                "variants": c.variants,
                "num_contracts": len(contracts_of[k]),
                "is_top_hub": k in top,
                "num_coparties": len(coparties(k)),
                "coparty_keys": coparties(k),
                "candidates": [x.model_dump() for x in cands],
                "resolution": None,
            }
        )
    flags = [
        {"type": "same_cik_collision", "cik": cik, "entities": ents}
        for cik, ents in cik_to.items()
        if len(ents) >= 2
    ]

    def naming(substr: str) -> list[str]:
        return sorted({p for m in metas for p in parties_of[m["contract_id"]] if substr in normalize_entity_name(p)})

    return {
        "how_to": (
            "Set each entity's 'resolution' to a 10-digit EDGAR CIK (confirm on candidate registry_name "
            "+ matched_tokens/former_names EVIDENCE, not the bare CIK), or 'PRIVATE' (first-class, same "
            "weight as a CIK), or 'SKIP'. Axis is verified-vs-unverified, not public-vs-private. Spend "
            "hardest attention on flags[] and the subsidiary pairs; read those off the contract naming."
        ),
        "scope": f"hubs with >= {MIN_HUB_CONTRACTS} contracts + their direct co-parties (both endpoints)",
        "load_bearing_count": len(records),
        "top_hubs": sorted(top),
        "flags": flags,
        "subsidiary_naming": {"scansource": naming("scansource"), "armstrong": naming("armstrong")},
        "entities": records,
    }


def main() -> None:
    doc = build()
    OUT.write_text(json.dumps(doc, indent=2))
    print(f"wrote {OUT}: {doc['load_bearing_count']} load-bearing entities")
    print(f"top hubs: {doc['top_hubs']}")
    print(f"flags: {[f['entities'] for f in doc['flags']]}")


if __name__ == "__main__":
    main()
