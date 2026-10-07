#!/usr/bin/env python
"""issue 0027 backfill: add AFFILIATE_OF edges to an ALREADY-INGESTED KG without re-ingesting.

Affiliation edges are produced during ingestion (ADR-0090), so a corpus ingested BEFORE that change has none.
This script adds them edge-only -- it reuses the ingestion cache_dir and, per contract:

  1. reconstructs the parsed text from the parse manifest (the same text ingestion saw),
  2. extracts corporate affiliations (LEXICALLY PRE-FILTERED -> most contracts cost no LLM call),
  3. resolves the org names through the SAME resolution path ingestion uses,
  4. writes ONLY the AFFILIATE_OF edges + any MISSING affiliate node (`store.add_affiliation_edges`),
     never re-writing or clobbering existing nodes/edges.

Idempotent: a contract already recorded in the affiliation cache (`<cache_dir>/graph_affiliations/`) is skipped,
so re-running adds nothing. The affiliation cache entry is written only AFTER a successful edge write, so an
interrupted run is safely retried. Dry-run against a COPY of the DB first if you want to inspect the writes.

Usage:
    uv run python scripts/backfill_affiliations.py --cache-dir <ingest cache dir> --database <arcadedb db>
        [--vset <verified_set.json>]   # optional CIK registry (match the ingest); default = empty registry
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv


def log(msg: str) -> None:
    print(msg, flush=True)


async def _run(cache_dir: str, database: str, vset_path: str | None) -> None:
    from docling_core.types.doc.document import DoclingDocument

    from rag_wright.packs.contracts.capabilities.dg_extraction import build_verified_registry
    from rag_wright.capabilities.disambiguation import disambiguate
    from rag_wright.capabilities.entity_resolution import resolve_entities
    from rag_wright.packs.contracts.capabilities.graph_extraction import aextract_affiliations, affiliations_to_extraction
    from rag_wright.capabilities.graph_storage import to_graph
    from rag_wright.contracts.identifiers import ChunkId
    from rag_wright.ontology.registry import EntityRegistry
    from rag_wright.store.arcadedb import ArcadeDBStore

    cache = Path(cache_dir)
    party_dir = cache / "graph_parties"
    parse_dir = cache / "parsed"
    affil_dir = cache / "graph_affiliations"
    affil_dir.mkdir(parents=True, exist_ok=True)
    if not party_dir.exists():
        raise SystemExit(f"no ingested contracts found: {party_dir} does not exist (is --cache-dir correct?)")

    registry = build_verified_registry(json.loads(Path(vset_path).read_text(encoding="utf-8"))) \
        if vset_path else EntityRegistry()
    store = ArcadeDBStore.from_env(database=database)
    store.ensure_schema()

    contracts = sorted(p.stem for p in party_dir.glob("*.json"))
    n = len(contracts)
    log(f"[backfill] {n} ingested contracts in {party_dir}")
    edges_added = with_affil = skipped = no_manifest = 0
    try:
        for i, doc_id in enumerate(contracts, 1):
            affil_cache = affil_dir / f"{doc_id}.json"
            if affil_cache.exists():
                skipped += 1
                log(f"[backfill] {i}/{n} {doc_id}: already backfilled -> skip")
                continue
            manifest = next(iter(parse_dir.glob(f"{doc_id}.*.json")), None)
            if manifest is None:
                no_manifest += 1
                log(f"[backfill] {i}/{n} {doc_id}: no parse manifest in {parse_dir} -> skip")
                continue
            text = "\n".join(t.text for t in DoclingDocument.load_from_json(str(manifest)).texts)
            pairs = await aextract_affiliations(text)  # lexical pre-filter inside -> no LLM call for most
            if not pairs:
                affil_cache.write_text(json.dumps([]), encoding="utf-8")  # mark processed (no affiliation)
                log(f"[backfill] {i}/{n} {doc_id}: no affiliation")
                continue
            extraction = affiliations_to_extraction(ChunkId.of(doc_id, 0, text), pairs)
            nodes, edges = to_graph(resolve_entities(disambiguate([extraction]), [extraction], resolver=registry))
            added = store.add_affiliation_edges(nodes, edges)
            affil_cache.write_text(json.dumps(pairs), encoding="utf-8")  # mark processed AFTER a successful write
            edges_added += added
            with_affil += 1
            log(f"[backfill] {i}/{n} {doc_id}: {pairs} -> +{added} AFFILIATE_OF edge(s)")
    finally:
        store.close()
    log(f"[backfill] DONE: {n} contracts | {with_affil} with affiliations | {edges_added} edges added | "
        f"{skipped} already-done | {no_manifest} without a parse manifest")


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill AFFILIATE_OF edges into an already-ingested KG (issue 0027).")
    ap.add_argument("--cache-dir", required=True, help="the ingestion cache dir used for this corpus")
    ap.add_argument("--database", required=True, help="the ArcadeDB database to add edges to")
    ap.add_argument("--vset", default=None, help="optional verified-set JSON for the CIK registry (match the ingest)")
    args = ap.parse_args()
    load_dotenv()
    asyncio.run(_run(args.cache_dir, args.database, args.vset))


if __name__ == "__main__":
    main()
