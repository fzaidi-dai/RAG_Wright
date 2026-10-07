"""GP-1(A): populate the entity/relationship graph (Entity + CONTRACTS_WITH) into ragwright_cuad from the
human-verified EDGAR set (data/edgar/verification_set.json) -- which IS the resolved CUAD-party graph the
relational golden set is built from (eval/multihop). Node identity = CIK (verified filer) or
PRIVATE:<entity_key> (verified private), reusing eval.multihop._identity so the graph node ids align with
the golden answers. Cheap / no-LLM first light (GP-1(A), Option A); real text-extraction is GP-1(B).

Env: DB (default ragwright_cuad), RESET (=1 to clear the graph types first), VSET (verification_set path).
Run: uv run --no-sync python -m scripts.populate_entity_graph
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv("/Users/farhan/work/RAG_Wright/.env")

from eval.multihop import _SKIP, _identity  # single source of truth for node identity (golden-set alignment)
from rag_wright.packs.contracts.ontology.contract_taxonomy import CONTRACTS_WITH, ORGANIZATION
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.store.seam import GraphEdge, GraphNode

VSET = Path(os.environ.get("VSET", "data/edgar/verification_set.json"))
DB = os.environ.get("DB", "ragwright_cuad")
RESET = os.environ.get("RESET") == "1"

_EXTRACTED = ConfidenceTag.EXTRACTED.value
_CONTRACTS_WITH = CONTRACTS_WITH
_ORG = ORGANIZATION
_PRIVATE = "PRIVATE"


def build_graph(vset: dict) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Materialize the verified coparty adjacency as Entity nodes + undirected CONTRACTS_WITH edges.
    node_key = CIK (filer) or PRIVATE:<entity_key> (private), mirroring eval.multihop for alignment. A
    coparty outside the verified set is not a ground-truth node, so its edge is dropped (as in multihop)."""
    entities = [e for e in vset["entities"] if e["resolution"] != _SKIP]
    by_key = {e["entity_key"]: e for e in entities}
    id_by_key = {e["entity_key"]: _identity(e) for e in entities}

    nodes: dict[str, GraphNode] = {}
    for e in entities:
        nid = id_by_key[e["entity_key"]]
        cik = "" if e["resolution"] in (_SKIP, _PRIVATE) else e["resolution"]
        nodes.setdefault(nid, GraphNode(
            node_key=nid, entity_id=cik, name=e["representative"], entity_type=_ORG,
            confidence=_EXTRACTED, chunk_id=f"verified:{e['entity_key']}"))

    seen: set[tuple[str, str]] = set()
    edges: list[GraphEdge] = []
    for e in entities:
        src = id_by_key[e["entity_key"]]
        for ck in e["coparty_keys"]:
            if ck not in by_key:
                continue
            tgt = id_by_key[ck]
            if src == tgt:
                continue
            pair = tuple(sorted((src, tgt)))  # undirected: one edge per unordered pair
            if pair in seen:
                continue
            seen.add(pair)
            edges.append(GraphEdge(
                source_key=pair[0], target_key=pair[1], relationship_type=_CONTRACTS_WITH,
                confidence=_EXTRACTED, chunk_id=f"verified:{pair[0]}~{pair[1]}"))
    return list(nodes.values()), edges


def main() -> None:
    from rag_wright.store.arcadedb import ArcadeDBStore

    vset = json.loads(VSET.read_text(encoding="utf-8"))
    nodes, edges = build_graph(vset)
    print(f"[graph] built {len(nodes)} Entity nodes + {len(edges)} CONTRACTS_WITH edges from {VSET.name}",
          flush=True)

    store = ArcadeDBStore.from_env(database=DB)
    store.ensure_schema()
    existing = store.graph_counts()
    if existing["entities"] and not RESET:
        print(f"[graph] ABORT: {existing['entities']} entities already present; set RESET=1 to rebuild.",
              flush=True)
        store.close()
        return
    if RESET and existing["entities"]:
        store._db.execute_transaction([f"DELETE FROM {t}" for t in ("Relationship", "Mentions", "Entity")])
        print(f"[graph] reset: cleared prior graph ({existing})", flush=True)

    store.write_graph(nodes, edges)
    print(f"[graph] wrote -> {store.graph_counts()}", flush=True)

    # verify: 1-hop traversal from the CIK top hubs returns their co-parties
    hubs = [e for e in vset["entities"]
            if e.get("is_top_hub") and e["resolution"] not in (_SKIP, _PRIVATE)]
    for hub in hubs[:4]:
        nid = _identity(hub)
        nb = store.graph_neighbors(nid, relationship_type=_CONTRACTS_WITH, max_hops=1)
        print(f"[verify] {hub['representative']} ({nid}) -1hop-> {len(nb)}: "
              f"{sorted(n['target_name'] for n in nb)}", flush=True)
    store.close()


if __name__ == "__main__":
    main()
