"""ING-4c (ADR-0124): LIVE contract-pipeline parity harness -- the current reference pipeline vs its rebuild on
`build_ingestion` (the rebuilt phase + the record-by-record diff land with ING-4c).

Phase `reference`: ingest the chosen contracts through the CURRENT `contract_ingestion_pipeline` (public API:
`load_reference_pack` + `ainvoke_subgraph`) into scratch database A, measuring model usage per contract
(`measure_usage`: calls, tokens, cost per model). Its caches live in `data/eval/contract_parity/cache`, so the rebuilt
phase -- given identical inputs -- is served from them (a cache miss there is itself a parity failure).

Phase `rebuilt` (ING-4c): the same contracts through the REBUILT pipeline into scratch database B, sharing A's caches.
CREDIT GUARD: any non-decision-model (e.g. Qwen) call means a cache miss -- the run STOPS at that contract.
JEV-VARIANCE GUARD: Jev's residue answers are not deterministic (measured: 5 of 181 answers changed across 3
identical calls), and the reference run predates the decision cache. So before any extraction, each contract's
provision anchors are checked against A's clause anchors; a contract whose grouping differs is SKIPPED (zero model
calls) and reported with the differing anchors, instead of paying to re-extract shifted provisions.

Phase `diff`: A vs B, record by record, on business keys (never database ids), for every node + edge type; the
new generic `Document` nodes / `EmbeddedIn` / `AttachedTo` edges are reported separately (B-only by design).

Contract sets (restrictively-licensed CUAD: local only, never shipped):
  pilot : the committed table-bearing fixture + one CUAD PDF (2 contracts)
  A     : 5 contract PDFs + 5 CUAD text contracts

Run: `uv run python -u eval/contract_parity_live.py --phase reference|rebuilt|diff --set pilot|A`
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
DB = {"reference": "rw_scratch_contract_ref", "rebuilt": "rw_scratch_contract_new"}
_GENERIC_NEW = {"Document", "EmbeddedIn", "AttachedTo"}  # ING-4b types the rebuilt path adds (B-only by design)
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


class JevVariance(Exception):
    """The rebuilt provision grouping differs from the reference's (a differing Jev residue answer)."""


def _guard_provisions(variance: dict) -> None:
    """Wrap the reference pack's grouper: compare the unit anchors with A's clause anchors BEFORE extraction."""
    import rag_wright.subgraphs.contract_ingestion_pipeline as cip
    from rag_wright.store.arcadedb import ArcadeDBStore

    ref = ArcadeDBStore.from_env(database=DB["reference"])
    original = cip.provision_units

    async def guarded(spans, *, decider=None):
        units = await original(spans, decider=decider)
        if units:
            doc = units[0].anchor.span_id.split(":")[0]
            want = {r["span_id"] for r in ref._query(f"SELECT span_id FROM Clause WHERE span_id LIKE '{doc}:%'")}
            got = {u.anchor.span_id for u in units}
            if got != want:
                variance[doc] = {"reference": len(want), "rebuilt": len(got),
                                 "only_reference": sorted(want - got), "only_rebuilt": sorted(got - want)}
                raise JevVariance(doc)
        return units

    cip.provision_units = guarded


def run_reference(paths: list[Path], phase: str = "reference") -> dict:
    from rag_wright.api import ainvoke_subgraph, load_reference_pack, measure_usage

    load_reference_pack()
    ws = _workspace(DB[phase])
    variance: dict = {}
    if phase == "rebuilt":
        _guard_provisions(variance)
    rows = []
    tag = "ref" if phase == "reference" else "new"
    log(f"[{tag}] start N={len(paths)}")
    for i, path in enumerate(paths, 1):
        t = time.time()
        with measure_usage() as usage:
            try:
                out = asyncio.run(ainvoke_subgraph("contract_ingestion_pipeline",
                                                   {"document": _document(path), "cache_dir": str(CACHE)}, resources=ws))
            except JevVariance:
                out = {}
        doc = _doc_id(path)
        if doc in variance:
            v = variance[doc]
            rows.append({"doc": path.name, "skipped": "jev_variance", **v, "calls": usage.calls,
                         "by_model": {m: u.calls for m, u in usage.by_model.items()}})
            log(f"[{tag}] {i}/{len(paths)} {path.name[:50]:50} SKIPPED jev-variance: provisions ref={v['reference']} "
                f"rebuilt={v['rebuilt']} only_ref={len(v['only_reference'])} only_rebuilt={len(v['only_rebuilt'])} "
                f"calls={usage.calls} by_model={ {m: u.calls for m, u in usage.by_model.items()} }")
            continue
        provisions = len(out.get("clause_records", []) or [])
        row = {"doc": path.name, "provisions": provisions, "calls": usage.calls, "cost_usd": round(usage.cost_usd, 5),
               "calls_without_cost": usage.calls_without_cost, "input_tokens": usage.input_tokens,
               "output_tokens": usage.output_tokens, "seconds": round(time.time() - t, 1),
               "by_model": {m: {"calls": u.calls, "cost_usd": round(u.cost_usd, 5)} for m, u in usage.by_model.items()},
               "dead_letter": str(out.get("dead_letter") or "") or None,
               "clause_failures": len(out.get("clause_failures", []) or [])}
        rows.append(row)
        log(f"[{tag}] {i}/{len(paths)} {path.name[:50]:50} provisions={provisions} calls={usage.calls} "
            f"cost=${usage.cost_usd:.4f} ({usage.calls_without_cost} uncosted) {row['seconds']}s "
            f"by_model={ {m: u['calls'] for m, u in row['by_model'].items()} }"
            + (f" DEAD-LETTER {row['dead_letter'][:80]}" if row["dead_letter"] else ""))
        paid = {m: u["calls"] for m, u in row["by_model"].items() if not m.startswith("jev")}
        if phase == "rebuilt" and sum(paid.values()):
            log(f"[{tag}] CREDIT GUARD: {paid} non-decision calls on {path.name} = a cache miss (parity break). STOP.")
            return {"rows": rows, "summary": {"stopped": True, "at": path.name}}
    done = [r for r in rows if not r.get("skipped")]
    total_prov = sum(r["provisions"] for r in done)
    total_calls = sum(r["calls"] for r in rows)
    summary = {"contracts": len(done), "skipped_jev_variance": [r["doc"] for r in rows if r.get("skipped")], "provisions": total_prov, "calls": total_calls,
               "cost_usd": round(sum(r["cost_usd"] for r in done), 5),
               "calls_without_cost": sum(r["calls_without_cost"] for r in done),
               "calls_per_provision": round(total_calls / total_prov, 3) if total_prov else None}
    log(f"[{tag}] summary {summary}")
    return {"rows": rows, "summary": summary}


_KEYS = ("clause_id", "value_key", "span_id", "entity_id", "chunk_id", "contract_id", "doc_id")
_SKIP = {"@rid", "@type", "@cat", "@in", "@out", "@props", "dense", "sparse_indices", "sparse_weights"}


def _rows(store, type_name: str, is_edge: bool, skip_docs: tuple = ()) -> list[str]:
    if is_edge:
        ends = ", ".join(f"outV().{k} AS out_{k}, inV().{k} AS in_{k}" for k in _KEYS)
        rows = store._query(f"SELECT *, {ends} FROM {type_name}")
    else:
        rows = store._query(f"SELECT * FROM {type_name}")
    out = (json.dumps({k: v for k, v in r.items() if k not in _SKIP and v is not None}, sort_keys=True, default=str)
           for r in rows)
    return sorted(j for j in out if not any(d in j for d in skip_docs))


def _skipped_only_values(store, edge_types: set, skip: tuple) -> tuple:
    """Shared `PropertyValue` nodes carry no document id, so attribute them by their users: the value keys used
    ONLY by clauses of the skipped contracts are left out of the diff with them."""
    users: dict = {}
    for et in edge_types:
        for r in store._query(f"SELECT outV().clause_id AS c, inV().value_key AS v FROM {et}"):
            if r.get("v"):
                users.setdefault(r["v"], set()).add(r.get("c") or "")
    return tuple(f'"value_key": "{v}"' for v, cs in users.items() if all(c.startswith(skip) for c in cs))


def run_diff(set_name: str) -> dict:
    from collections import Counter

    from rag_wright.store.arcadedb import ArcadeDBStore

    a, b = (ArcadeDBStore.from_env(database=DB[p]) for p in ("reference", "rebuilt"))
    edge_types = {r["name"] for s in (a, b) for r in s._query("SELECT name FROM schema:types WHERE type = 'edge'")}
    types = sorted(a.type_names() | b.type_names())
    rebuilt = json.loads((OUT / f"rebuilt_{set_name}.json").read_text())
    skip = tuple(_doc_id(Path(r["doc"])) for r in rebuilt["rows"] if r.get("skipped"))
    skip_values = _skipped_only_values(a, edge_types, skip) if skip else ()
    out, ok = {"skipped_jev_variance": list(skip), "values_only_in_skipped": len(skip_values)}, True
    log(f"[diff] start N={len(types)} types (leaving out {len(skip)} jev-variance contracts: {list(skip)}, and "
        f"{len(skip_values)} property values only they use)")
    for i, t in enumerate(types, 1):
        drop = skip + (skip_values if t == "PropertyValue" else ())
        ra = _rows(a, t, t in edge_types, drop) if t in a.type_names() else []
        rb = _rows(b, t, t in edge_types, drop) if t in b.type_names() else []
        ca, cb = Counter(ra), Counter(rb)
        only_a, only_b = list((ca - cb).elements()), list((cb - ca).elements())
        out[t] = {"a": len(ra), "b": len(rb), "only_in_a": len(only_a), "only_in_b": len(only_b),
                  "examples_a": only_a[:3], "examples_b": only_b[:3]}
        generic_new = t in _GENERIC_NEW
        if (only_a or only_b) and not generic_new:
            ok = False
        if ra or rb:
            log(f"[diff] {i}/{len(types)} {t:28} A={len(ra):5} B={len(rb):5} only_A={len(only_a):4} only_B={len(only_b):4}"
                + ("  (new generic type, B-only by design)" if generic_new else "")
                + ("" if generic_new or not (only_a or only_b) else "  <-- DIFFERS"))
    log(f"[diff] PARITY {'IDENTICAL' if ok else 'DIFFERS'} (reference-owned types)")
    return {"types": out, "identical": ok}


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["reference", "rebuilt", "diff"], required=True)
    ap.add_argument("--set", choices=sorted(SETS), required=True)
    args = ap.parse_args()
    paths = [p for p in SETS[args.set] if p.exists()]
    result = run_diff(args.set) if args.phase == "diff" else run_reference(paths, args.phase)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{args.phase}_{args.set}.json").write_text(json.dumps(result, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
