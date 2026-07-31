"""KG-7: run the Party<->Contract unifying link over a POPULATED contract KG (ADR-0036).

Non-destructive: reads the already-populated Contract + Entity nodes and ADDS `PARTY_TO` edges
(Entity -> Contract). It never re-parses/re-extracts and never touches nodes or other edges; the only edges it
manages are `PARTY_TO` (cleared + rewritten so re-linking is idempotent).

  DRY smoke (default): derive the links from the live data and report match/unmatched counts, NO write.
    DB=ragwright_cuad uv run --no-sync python -m scripts.link_party_clause
  Full run (writes the PARTY_TO edges + verifies the edge count):
    WRITE=1 DB=ragwright_cuad uv run --no-sync python -m scripts.link_party_clause
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from rag_wright.capabilities.party_clause_linking import (
    derive_party_contract_links,
    party_clause_linking,
)

DB = os.environ.get("DB", "ragwright_cuad")
WRITE = os.environ.get("WRITE") == "1"


def main() -> None:
    load_dotenv()
    from rag_wright.store.arcadedb import PARTY_TO_EDGE_TYPE, ArcadeDBStore

    store = ArcadeDBStore.from_env(database=DB)
    contracts = store.all_contracts()
    entities = store.all_entities()
    print(f"[kg-7] {DB}: {len(contracts)} Contract nodes, {len(entities)} Entity nodes", flush=True)

    # Always derive + report first (this is the smoke; it writes nothing).
    result = derive_party_contract_links(contracts, entities)
    total_parties = result.unmatched_parties + len({(l.entity_id, l.contract_id) for l in result.links})
    matched = len(result.links)
    rate = (matched / total_parties * 100) if total_parties else 0.0
    print(f"[kg-7] DRY: {matched} PARTY_TO links across {result.contracts_processed} contracts; "
          f"{result.unmatched_parties} unmatched (private/unlinked) parties "
          f"({rate:.0f}% of party slots matched an Entity)", flush=True)

    if not WRITE:
        print("[kg-7] DRY only (set WRITE=1 to add the edges). No changes made.", flush=True)
        store.close()
        return

    store.ensure_schema()  # adds the PartyTo edge type if missing (non-destructive; existing types untouched)
    written = party_clause_linking(store)  # derive again + write the edges
    edge_count = store._query(f"SELECT count(*) AS n FROM {PARTY_TO_EDGE_TYPE}")
    n = int(edge_count[0]["n"]) if edge_count else 0
    print(f"[kg-7] WROTE {len(written.links)} PARTY_TO edges; store now reports {n} PARTY_TO edges "
          f"(+{written.unmatched_parties} unmatched). Graphs are now connected.", flush=True)
    store.close()


if __name__ == "__main__":
    main()
