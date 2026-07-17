#!/usr/bin/env python
"""Diagnostic (T33/T41): ACORD relevance-structure analysis — does a clause-anchored graph help?

Designs the clause-relation graph from the data instead of guessing. Cheap: re-embeds only the relevant
clauses (~620), no extraction run. For each query it measures three things:

  - ANCHOR AVAILABILITY: did dense+sparse retrieve at least SOME of the query's relevant clauses (top-50)?
    Graph-expansion needs an anchor to start from; queries with zero anchors need better base retrieval,
    not a graph.
  - CLUSTER vs QUERY GAP: mean pairwise cosine WITHIN the relevant set (cluster tightness) vs mean
    query->relevant cosine. If the relevant clauses are more similar to EACH OTHER than to the query, a
    clause-anchored graph (expand from a retrieved cluster member) beats query-anchored retrieval — the
    exact "relationally-relevant but query-textually-different" case.
  - MISSED-RELEVANT REACHABILITY: for each missed relevant clause, its max cosine to a RETRIEVED anchor.
    High => a clause-similarity / category edge + expansion recovers it (graph leg helps); low => it is not
    reachable from what we retrieved (ceiling closer to real).

    PYTHONPATH=. uv run python scripts/diagnose_acord_relstructure.py
"""

from __future__ import annotations

import numpy as np
from dotenv import load_dotenv

from eval.acord import load_corpus, load_test_queries
from rag_wright.store.arcadedb import ArcadeDBStore

ACORD_DB = "ragwright_acord"
K = 50


def _corpus_id(chunk_id: str) -> str:
    return chunk_id.rsplit(":", 2)[0]


def _unit(v):
    n = np.linalg.norm(v)
    return v / n if n else v


def main() -> None:
    load_dotenv()
    from FlagEmbedding import BGEM3FlagModel

    queries = load_test_queries()
    corpus = {c.clause_id: c.text for c in load_corpus()}
    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False, devices=["cpu"])

    rel_ids = sorted({cid for q in queries for cid in q.relevant if cid in corpus})
    rel_vecs_raw = model.encode(
        [corpus[cid] for cid in rel_ids], batch_size=16, max_length=1024, return_dense=True
    )["dense_vecs"]
    rel_vec = {cid: _unit(rel_vecs_raw[i]) for i, cid in enumerate(rel_ids)}

    q_enc = model.encode(
        [q.text for q in queries], batch_size=16, max_length=1024, return_dense=True, return_sparse=True
    )
    q_dense = [v.tolist() for v in q_enc["dense_vecs"]]
    q_sparse = [{int(k): float(v) for k, v in lw.items()} for lw in q_enc["lexical_weights"]]

    store = ArcadeDBStore.from_env(database=ACORD_DB, reset=False)
    rng = np.random.default_rng(0)
    all_rel = list(rel_vec.values())

    tights, q_gaps, recalls = [], [], []
    zero_anchor = 0
    missed_reach, missed_total = [], 0
    for i, q in enumerate(queries):
        retrieved = {_corpus_id(r["chunk_id"]) for r in store.hybrid_search(q_dense[i], q_sparse[i], k=K)}
        R = [r for r in q.relevant if r in rel_vec]
        A = [r for r in R if r in retrieved]
        M = [r for r in R if r not in retrieved]
        recalls.append(len(A) / len(R) if R else 0.0)
        if len(R) >= 2:
            cos = [float(rel_vec[a] @ rel_vec[b]) for x, a in enumerate(R) for b in R[x + 1 :]]
            tights.append(float(np.mean(cos)))
        qv = _unit(np.asarray(q_dense[i]))
        q_gaps.append(float(np.mean([qv @ rel_vec[r] for r in R])) if R else 0.0)
        if not A:
            zero_anchor += 1
        for m in M:
            missed_total += 1
            if A:
                missed_reach.append(max(float(rel_vec[m] @ rel_vec[a]) for a in A))

    base = float(np.mean([_unit(all_rel[a]) @ _unit(all_rel[b])
                          for a, b in rng.integers(0, len(all_rel), (2000, 2)) if a != b]))
    mr = np.asarray(missed_reach)
    print(f"\n[rel-structure] {len(queries)} queries, K={K}")
    print(f"  mean recall@{K}                = {np.mean(recalls):.3f}")
    print(f"  queries with a retrieved anchor= {len(queries) - zero_anchor}/{len(queries)} "
          f"(zero-anchor: {zero_anchor})")
    print(f"  relevant-set cluster tightness = {np.mean(tights):.3f}   (random-pair baseline {base:.3f})")
    print(f"  query->relevant mean cosine    = {np.mean(q_gaps):.3f}")
    print(f"  => clause-cluster is {'TIGHTER than' if np.mean(tights) > np.mean(q_gaps) else 'not tighter than'} "
          f"the query->clause link (gap {np.mean(tights) - np.mean(q_gaps):+.3f})")
    print(f"  missed relevants (with an anchor): {len(mr)} of {missed_total}")
    if len(mr):
        print(f"  missed->anchor max cosine      = mean {mr.mean():.3f}, "
              f">=0.6: {(mr >= 0.6).mean():.0%}, >=0.7: {(mr >= 0.7).mean():.0%}, >=0.8: {(mr >= 0.8).mean():.0%}")
    store.close()


if __name__ == "__main__":
    main()
