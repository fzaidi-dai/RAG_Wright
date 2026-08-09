"""Function-gate ON/OFF GRADED RECALL on the unified KG (ADR-0046 ACORD qrels; the ADR-0047 evidence).

Answers: does the precomputed clause `function` pre-filter (Leg B `span_hybrid_search(function=f)`) buy recall?
Two legs, identical except the function filter:
  ON  = span_hybrid_search(function=oracle_fn)  -- oracle picks the function covering the most gold clauses, so
        this is the gate's BEST case (its recall CEILING with a perfect classifier).
  OFF = span_hybrid_search(function=None)        -- whole-index BGE pool, no function pre-filter.
Recall unit = clause (parent_chunk_id): retrieve spans, dedup to distinct clauses, recall@{10,20,50} vs grade>=2.

  raw    - raw BGE-hybrid pool recall (no reranker, no LLM). Isolates the gate's pure effect on reachability.
  rerank - + BGE cross-encoder rerank of the pool (bar context: ACORD recall@50 bar 0.667). Slow on CPU.

  PYTHONPATH=. uv run --no-sync python scripts/legb_function_gate_recall.py {raw|rerank}

Result (2026-08-09, 57 ACORD queries): ON ~= OFF at every K in BOTH modes (OFF marginally higher), and the
single-function CEILING is 0.969 -- the gate does not improve recall and imposes a ceiling whole-index lacks.
-> ADR-0047: retire the precomputed clause function.
"""
from __future__ import annotations

import json
import sys
from collections import Counter

from dotenv import load_dotenv

QRELS = "data/eval/acord_unify/acord_prod_qrels.json"
POOL_K = 400   # spans retrieved per leg (dedups to ~75-100 clauses; rerank mode reranks these)


def _dedup_pcids(hits):
    seen, out = set(), []
    for h in hits:
        p = h.get("parent_chunk_id")
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _rerank_to_clauses(reranker, query, hits, texts):
    scored = [(h.get("parent_chunk_id"), texts.get(h.get("span_id"), "")) for h in hits]
    scored = [(p, t) for p, t in scored if t]
    if not scored:
        return []
    s = reranker.score(query, [t for _, t in scored])
    seen, out = set(), []
    for p, _ in sorted(zip((p for p, _ in scored), s), key=lambda ps: -ps[1]):
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "raw"
    load_dotenv()
    from eval.harness import recall_at_k
    from rag_wright.capabilities.embedding import BGEM3Embedder
    from rag_wright.store.arcadedb import ArcadeDBStore, _sql_str

    qrels = json.load(open(QRELS))
    store = ArcadeDBStore.from_env(database="ragwright_cuad_full")
    emb = BGEM3Embedder()
    reranker = None
    if mode == "rerank":
        from rag_wright.capabilities.reranking import BGEReranker
        reranker = BGEReranker()

    # oracle map: pcid -> set(functions) for every relevant clause (batched IN)
    all_rel = sorted({p for q in qrels.values() for p in q["relevant"]})
    pcid_fns: dict[str, set[str]] = {}
    for i in range(0, len(all_rel), 100):
        idlist = "[" + ",".join(_sql_str(p) for p in all_rel[i:i + 100]) + "]"
        for r in store._query(f"SELECT parent_chunk_id, function FROM Span WHERE parent_chunk_id IN {idlist}"):
            pcid_fns.setdefault(r["parent_chunk_id"], set()).add(r.get("function") or "")
    print(f"[{mode}] {len(qrels)} queries | {len(all_rel)} relevant clauses | POOL_K={POOL_K}", flush=True)

    ks = (10, 20, 50)
    on = {k: 0.0 for k in ks}
    off = {k: 0.0 for k in ks}
    ceil_sum = 0.0
    n = 0
    for q in qrels.values():
        rel = set(q["relevant"])
        if not rel:
            continue
        n += 1
        dense, sparse = emb.encode_dense(q["text"]), emb.encode_sparse(q["text"])
        reach = Counter()
        for p in rel:
            for f in pcid_fns.get(p, set()):
                if f:
                    reach[f] += 1
        oracle = reach.most_common(1)[0][0] if reach else None
        ceil_sum += (reach[oracle] / len(rel)) if oracle else 0.0
        on_hits = store.span_hybrid_search(dense, sparse, k=POOL_K, function=oracle)
        off_hits = store.span_hybrid_search(dense, sparse, k=POOL_K, function=None)
        if mode == "rerank":
            texts = store.span_texts(list({h["span_id"] for h in on_hits} | {h["span_id"] for h in off_hits}))
            on_clauses = _rerank_to_clauses(reranker, q["text"], on_hits, texts)
            off_clauses = _rerank_to_clauses(reranker, q["text"], off_hits, texts)
        else:
            on_clauses = _dedup_pcids(on_hits)
            off_clauses = _dedup_pcids(off_hits)
        for k in ks:
            on[k] += recall_at_k(on_clauses, rel, k)
            off[k] += recall_at_k(off_clauses, rel, k)
        if n % 10 == 0:
            print(f"[{mode}] {n}/{len(qrels)}  r@50 ON={on[50]/n:.3f} OFF={off[50]/n:.3f}", flush=True)

    print(f"\n=== function-gate ON vs OFF ({mode}) | {n} queries | ACORD recall@50 bar=0.667 ===", flush=True)
    print(f"function-reachability CEILING (oracle): {ceil_sum/n:.3f}  (ON cannot exceed this)", flush=True)
    print(f"{'k':>4} {'ON (oracle-fn gate)':>20} {'OFF (whole-index)':>20} {'delta(OFF-ON)':>14}", flush=True)
    for k in ks:
        print(f"{k:>4} {on[k]/n:>20.3f} {off[k]/n:>20.3f} {(off[k]-on[k])/n:>+14.3f}", flush=True)
    store.close()


if __name__ == "__main__":
    main()
