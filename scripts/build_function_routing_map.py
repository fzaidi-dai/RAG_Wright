"""KG-5e (FR-Q): build + cache the `dimension -> function` co-occurrence prior for the query-side router.

Derived from a HELD-OUT corpus (CUAD, `ragwright_cuad`) so no ACORD eval data enters the router -- the two
corpora share the property-dimension schema and the CUAD-type function taxonomy, so the prior transfers. One
global MATCH over the typed KG yields every `(clause_id, function, dimension)` edge; `build_cooccurrence`
counts each once per clause. Cached to `data/models/dimension_function_map.json` for `eval/kg_primary.py`.

  CUAD_DB=ragwright_cuad uv run python -m scripts.build_function_routing_map
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

from rag_wright.packs.contracts.schemas.function_routing import build_cooccurrence
from rag_wright.store.arcadedb import ArcadeDBStore
from rag_wright.packs.contracts.capabilities.contract_kg_store import CLAUSE_TYPE

CUAD_DB = os.environ.get("CUAD_DB", "ragwright_cuad")
OUT = Path(os.environ.get("ROUTE_MAP", "data/models/dimension_function_map.json"))


def main() -> None:
    load_dotenv()
    store = ArcadeDBStore.from_env(database=CUAD_DB, reset=False)
    rows = store._query(
        "MATCH {type: " + CLAUSE_TYPE + ", as: c}.outE(){as: e}.inV(){as: v}"
        " RETURN c.clause_id AS clause_id, c.function AS function, e.dimension AS dimension"
    )
    cooc = build_cooccurrence((r.get("clause_id"), r.get("function"), r.get("dimension")) for r in rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cooc, indent=0), encoding="utf-8")

    n_dims = len(cooc)
    n_pairs = sum(len(fs) for fs in cooc.values())
    funcs = {f for fs in cooc.values() for f in fs}
    print(f"[route-map] {len(rows)} edges from {CUAD_DB} -> {n_dims} dimensions, {len(funcs)} functions, "
          f"{n_pairs} (dim,function) pairs -> {OUT}", flush=True)
    # show a few dimensions' top functions as a sanity read
    for d in sorted(cooc)[:8]:
        top = sorted(cooc[d].items(), key=lambda x: -x[1])[:3]
        print(f"  {d:22s} -> " + ", ".join(f"{f}({c})" for f, c in top), flush=True)
    store.close()


if __name__ == "__main__":
    main()
