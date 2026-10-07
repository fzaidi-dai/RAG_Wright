"""GP-1B.5: populate the entity graph from REAL granite-4.1-8b extraction (replacing the gold-anchored
GP-1(A) graph). Per contract: docling-graph extract parties (granite-4.1-8b) -> resolve_extracted
(verified-variant registry -> EDGAR CIK) -> to_graph -> write_graph into `ragwright_cuad`. Then
`eval/relational_eval` gives the REAL relational recall vs the 1.000 gold-anchored upper bound (GP-2).

Extraction is concurrent (async + semaphore + asyncio.to_thread; CLAUDE.md concurrent-LLM rule). Private
co-parties resolve to None (unlinked) not the golden PRIVATE:<key>, so CIK-CIK edges are the recoverable
target -- an honest known gap.

Env: LIMIT (0=all CUAD, default 10 = smoke), DB (ragwright_cuad), CONCURRENCY (8).
Run: LIMIT=0 uv run --no-sync python -m scripts.populate_entity_graph_extracted
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv("/Users/farhan/work/RAG_Wright/.env")

from rag_wright.packs.contracts.capabilities.dg_extraction import (
    ContractParties,
    Party,
    build_private_map,
    build_verified_registry,
    extract_parties,
    openrouter_model,
    resolve_extracted,
)
from rag_wright.capabilities.graph_storage import to_graph
from rag_wright.packs.contracts.spans.cuad_labels import parse_cuad

CUAD = Path("data/cuad/extracted/CUAD_v1.json")
VSET = Path("data/edgar/verification_set.json")
CACHE = Path("data/cache/dg_extracted_parties.json")  # {contract_id: [party names]} -- avoids re-extraction
LIMIT = int(os.environ.get("LIMIT", "10"))
DB = os.environ.get("DB", "ragwright_cuad")
CONCURRENCY = int(os.environ.get("CONCURRENCY", "8"))
MODEL = openrouter_model("granite-4.1-8b", "ibm-granite/granite-4.1-8b")


async def _extract_all(contracts) -> list[tuple[str, object]]:
    sem = asyncio.Semaphore(CONCURRENCY)
    done = 0

    async def one(c):
        nonlocal done
        async with sem:
            try:
                cp = await asyncio.to_thread(extract_parties, c.context, MODEL)
            except Exception:  # noqa: BLE001 - one bad extraction must not sink the run
                cp = None
        done += 1
        if done % 25 == 0:
            print(f"[extract] {done}/{len(contracts)}", flush=True)
        return c.contract_id, cp

    return await asyncio.gather(*[one(c) for c in contracts])


def main() -> None:
    contracts = list(parse_cuad(CUAD))
    if LIMIT:
        contracts = contracts[:LIMIT]
    print(f"[extract] {len(contracts)} contracts via {MODEL.model} (conc={CONCURRENCY})", flush=True)

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    if CACHE.exists() and not os.environ.get("FRESH"):  # reuse the extraction (FRESH=1 to re-extract)
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        if LIMIT:
            cached = dict(list(cached.items())[:LIMIT])
        items = [(cid, ContractParties(title="", parties=[Party(name=n) for n in names]))
                 for cid, names in cached.items() if names]
        print(f"[extract] loaded {len(items)} contracts from cache", flush=True)
    else:
        t0 = time.perf_counter()
        results = asyncio.run(_extract_all(contracts))
        CACHE.write_text(json.dumps({cid: [p.name for p in cp.parties] for cid, cp in results if cp}),
                         encoding="utf-8")
        items = [(cid, cp) for cid, cp in results if cp is not None and cp.parties]
        print(f"[extract] {len(items)}/{len(contracts)} contracts yielded >=1 party in "
              f"{time.perf_counter() - t0:.0f}s (cached)", flush=True)

    vset = json.loads(VSET.read_text(encoding="utf-8"))
    registry = build_verified_registry(vset)
    private_map = build_private_map(vset)  # GP-1B.5a: verified-private parties -> golden PRIVATE:<key>
    resolution = resolve_extracted(items, registry=registry, private_map=private_map)
    nodes, edges = to_graph(resolution)
    linked = sum(1 for n in nodes if n.entity_id)
    print(f"[graph] {len(nodes)} nodes ({linked} CIK-linked) + {len(edges)} CONTRACTS_WITH edges", flush=True)

    if os.environ.get("DRY"):
        print("[graph] DRY: not writing to the store", flush=True)
        return
    from rag_wright.store.arcadedb import ArcadeDBStore
    store = ArcadeDBStore.from_env(database=DB)
    store.ensure_schema()
    store._db.execute_transaction([f"DELETE FROM {t}" for t in ("Relationship", "Mentions", "Entity")])
    store.write_graph(nodes, edges)
    print(f"[graph] wrote (replacing gold-anchored graph) -> {store.graph_counts()}", flush=True)
    store.close()


if __name__ == "__main__":
    main()
