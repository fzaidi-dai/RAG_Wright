"""GP-2: the relational-archetype GRAPH leg (FR-C.5, SPEC section 12).

Answers each EDGAR relational golden question by traversing the populated entity KG -- `graph_query` over
CONTRACTS_WITH from the anchor's IDENTITY (CIK or PRIVATE:<key>) to the question's hop depth -- and scores
recall of the golden answer identities with the harness metric (`recall_at_k`). This is the graph leg the
two-leg design measures separately (recall is bounded by the union of the text and graph legs); the text
leg is measured elsewhere. Node ids match the golden identities by construction
(`scripts/populate_entity_graph.py` + `eval.multihop._identity`), so the reached ids compare directly.

Relational answers are set-valued (the full neighbor set, not a top-k ranked list), so `k` is large enough
to cover the whole set -- this measures graph reachability + traversal, not ranking.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

from eval.harness import recall_at_k
from eval.multihop import RelationalQuestion
from rag_wright.capabilities.graph_query import graph_query
from rag_wright.ontology.contract_taxonomy import CONTRACTS_WITH

_K = 50  # set-valued answers: k covers the full neighbor set (reachability, not ranking)


def graph_leg_recall(
    questions: list[RelationalQuestion],
    store: Any,
    *,
    relationship_type: str = CONTRACTS_WITH,
    k: int = _K,
) -> dict:
    """Per-hop mean recall@k of the graph leg over the relational set.

    Returns `{'1hop': .., '2hop': .., 'overall': .., 'counts': {hop: n}, 'per_question': [..]}`. Each
    question is answered by `graph_query(anchor.identity, max_hops=hop_count)`; the reached entity ids are
    scored against the golden answer identities.
    """
    by_hop: dict[int, list[float]] = defaultdict(list)
    details: list[dict] = []
    for q in questions:
        answer = graph_query(
            q.anchor.identity, store=store, relationship_type=relationship_type, max_hops=q.hop_count
        )
        retrieved = [ev.entity_id for ev in answer.evidence]
        relevant = {a.identity for a in q.answer_entities}
        recall = recall_at_k(retrieved, relevant, k)
        by_hop[q.hop_count].append(recall)
        details.append({
            "qid": q.qid, "hops": q.hop_count, "recall": recall,
            "n_relevant": len(relevant), "n_retrieved": len(retrieved),
        })
    out: dict = {f"{hop}hop": mean(recalls) for hop, recalls in sorted(by_hop.items())}
    all_recalls = [d["recall"] for d in details]
    out["overall"] = mean(all_recalls) if all_recalls else 0.0
    out["counts"] = {f"{hop}hop": len(recalls) for hop, recalls in sorted(by_hop.items())}
    out["per_question"] = details
    return out


def main() -> None:
    import json
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv("/Users/farhan/work/RAG_Wright/.env")
    from eval.multihop import build_relational
    from rag_wright.store.arcadedb import ArcadeDBStore

    vset = json.loads(Path("data/edgar/verification_set.json").read_text(encoding="utf-8"))
    questions = build_relational(vset)  # rebuilt from the same source the graph was populated from
    store = ArcadeDBStore.from_env(database="ragwright_cuad")
    res = graph_leg_recall(questions, store)

    print(f"[relational/graph] counts: {res['counts']}", flush=True)
    for hop in ("1hop", "2hop"):
        if hop in res:
            print(f"  {hop}: recall@{_K} = {res[hop]:.3f}", flush=True)
    print(f"  overall: recall@{_K} = {res['overall']:.3f}", flush=True)
    for d in res["per_question"]:
        if d["recall"] < 1.0:
            print(f"   MISS {d['qid']} hops={d['hops']} recall={d['recall']:.2f} "
                  f"(relevant={d['n_relevant']} retrieved={d['n_retrieved']})", flush=True)
    store.close()


if __name__ == "__main__":
    main()
