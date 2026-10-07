"""ING-4c (ADR-0124): LIVE contract-pipeline parity harness -- the current reference pipeline vs its rebuild on
`build_ingestion` (the rebuilt phase + the record-by-record diff land with ING-4c).

Phase `reference`: ingest the chosen contracts through the CURRENT `contract_ingestion_pipeline` (public API:
`load_reference_pack` + `ainvoke_subgraph`) into scratch database A, measuring model usage per contract
(`measure_usage`: calls, tokens, cost per model). Its caches live in `data/eval/contract_parity/cache`, so the rebuilt
phase -- given identical inputs -- is served from them (a cache miss there is itself a parity failure).

Contract sets (restrictively-licensed CUAD: local only, never shipped):
  pilot : the committed table-bearing fixture + one CUAD PDF (2 contracts)
  A     : 5 contract PDFs + 5 CUAD text contracts

Run: `uv run python -u eval/contract_parity_live.py --phase reference --set pilot`
Writes `data/eval/contract_parity/<phase>_<set>.json`. PAID: model calls go to the configured provider.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "eval" / "contract_parity"
CACHE = OUT / "cache"
DB = {"reference": "rw_scratch_contract_ref"}
_PDFS = [ROOT / "tests" / "fixtures" / "table-bearing-contract.pdf"] + sorted(
    (ROOT / "data" / "cuad" / "subset" / "pdf").glob("*.pdf"))[:4]
_TXTS = sorted((ROOT / "data" / "cuad" / "extracted" / "CUAD_v1" / "full_contract_txt").glob("*.txt"))[:5]
SETS = {"pilot": _PDFS[:2], "A": _PDFS + _TXTS}


def log(msg: str) -> None:
    print(msg, flush=True)


def _doc_id(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem)[:60] + "_" + path.suffix.lstrip(".")


def _workspace(db: str):
    from rag_wright.api import EngineConfig, StoreConfig, open_workspace
    from rag_wright.store.arcadedb import ArcadeDBStore

    ArcadeDBStore.from_env(database=db, reset=True).close()  # a clean scratch database
    return open_workspace(EngineConfig(store=StoreConfig(
        host=os.environ["ARCADEDB_HOST"], port=os.environ["ARCADEDB_PORT"], user=os.environ["ARCADEDB_USER"],
        password=os.environ["ARCADEDB_PASSWORD"])), corpus=db)


def _document(path: Path):
    from rag_wright.api import parse_document, source_document

    if path.suffix.lower() == ".txt":
        return source_document(_doc_id(path), text=path.read_text(errors="ignore"))
    return parse_document(_doc_id(path), path, cache_dir=CACHE / "parsed")


def run_reference(paths: list[Path]) -> dict:
    from rag_wright.api import ainvoke_subgraph, load_reference_pack, measure_usage

    load_reference_pack()
    ws = _workspace(DB["reference"])
    rows = []
    log(f"[ref] start N={len(paths)}")
    for i, path in enumerate(paths, 1):
        t = time.time()
        with measure_usage() as usage:
            out = asyncio.run(ainvoke_subgraph("contract_ingestion_pipeline",
                                               {"document": _document(path), "cache_dir": str(CACHE)}, resources=ws))
        provisions = len(out.get("clause_records", []) or [])
        row = {"doc": path.name, "provisions": provisions, "calls": usage.calls, "cost_usd": round(usage.cost_usd, 5),
               "calls_without_cost": usage.calls_without_cost, "input_tokens": usage.input_tokens,
               "output_tokens": usage.output_tokens, "seconds": round(time.time() - t, 1),
               "by_model": {m: {"calls": u.calls, "cost_usd": round(u.cost_usd, 5)} for m, u in usage.by_model.items()},
               "dead_letter": str(out.get("dead_letter") or "") or None,
               "clause_failures": len(out.get("clause_failures", []) or [])}
        rows.append(row)
        log(f"[ref] {i}/{len(paths)} {path.name[:50]:50} provisions={provisions} calls={usage.calls} "
            f"cost=${usage.cost_usd:.4f} ({usage.calls_without_cost} uncosted) {row['seconds']}s"
            + (f" DEAD-LETTER {row['dead_letter'][:80]}" if row["dead_letter"] else ""))
    total_prov = sum(r["provisions"] for r in rows)
    total_calls = sum(r["calls"] for r in rows)
    summary = {"contracts": len(rows), "provisions": total_prov, "calls": total_calls,
               "cost_usd": round(sum(r["cost_usd"] for r in rows), 5),
               "calls_without_cost": sum(r["calls_without_cost"] for r in rows),
               "calls_per_provision": round(total_calls / total_prov, 3) if total_prov else None}
    log(f"[ref] summary {summary}")
    return {"rows": rows, "summary": summary}


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["reference"], required=True)
    ap.add_argument("--set", choices=sorted(SETS), required=True)
    args = ap.parse_args()
    paths = [p for p in SETS[args.set] if p.exists()]
    result = run_reference(paths)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{args.phase}_{args.set}.json").write_text(json.dumps(result, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
