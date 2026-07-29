"""KG-5 (FR-Q, ADR-0033): the typed-KG-enhanced retrieval pipeline -- measure its recall.

The pipeline: oracle FUNCTION -> the function-filtered pool -> BGE cross-encoder rerank (the deterministic
base) refined by the TYPED contract KG as a constraint-match feature. The KG is never a standalone retriever
(reachability != rankability) -- it is a FEATURE over the reranked pool.

Per query: decompose into (dimension,value) property constraints; for each pooled clause count how many its
TYPED edges (`ragwright_acord_pivot`) satisfy -- GROUNDED-only (drop the grounding-judge-flagged AMBIGUOUS
edges); the KG-enhanced ranking sorts by (constraint-match, BGE). Reported: recall@10/@20/@50 + nDCG@10 of the
KG-enhanced pipeline (deterministic; only the query-constraint decomposition uses an LLM).

  uv run python -m eval.kg_property_rerank
"""

from __future__ import annotations

import os
import re
import statistics
from pathlib import Path

from dotenv import load_dotenv

from eval.acord import load_corpus, load_test_queries
from eval.acord_retrieval import ndcg_at_k
from eval.harness import recall_at_k
from rag_wright.capabilities.reranking import BGEReranker
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.value_match import constraint_match_count
from rag_wright.spans.property_extractor import SeamPropertyExtractor
from rag_wright.store.arcadedb import SPAN_TYPE, ArcadeDBStore, _sql_str, _str_array
from rag_wright.util.concurrent import map_concurrent

DB = os.environ.get("PIVOT_DB", "ragwright_acord_pivot")
CONSTRAINT_MODEL = os.environ.get("CONSTRAINT_MODEL", "deepseek/deepseek-v4-flash")
PROGRESS = Path("data/models/kgpr_progress.log")
_SLUG = re.compile(r"[^A-Za-z0-9._-]+")


def _progress(msg: str) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(msg + "\n", encoding="utf-8")
    print(msg, flush=True)


def _clause_key(acord_id: str, text: str) -> str:
    """The typed-KG clause_id for an ACORD clause (matches the KG-3 population scheme)."""
    return str(ChunkId.of("acord-" + _SLUG.sub("-", acord_id), 0, text))


def property_match(query_constraints: set, typed_edges: list[dict], *, grounded_only: bool = False) -> int:
    """How many of the query's (dimension,value) constraints the clause's TYPED edges satisfy -- KG-5a:
    canonicalized (jurisdiction) + subsumption-aware (`value_match`), not brittle exact set-intersection.
    `grounded_only` drops AMBIGUOUS edges (the grounding-judge-flagged ones). Pure -- unit-tested, no store."""
    props = {
        (e.get("dimension"), e.get("value"))
        for e in typed_edges
        if not grounded_only or e.get("confidence") != "AMBIGUOUS"
    }
    return constraint_match_count(query_constraints, props)


def main() -> None:
    load_dotenv()
    store = ArcadeDBStore.from_env(database=DB, reset=False)
    corpus = {c.clause_id: c.text for c in load_corpus()}
    queries = load_test_queries()
    reranker = BGEReranker()

    def oracle_f1(gold: list[str]) -> str | None:
        reach: dict[str, int] = {}
        for g in gold:
            for f in {r["function"] for r in store._query(
                    f"SELECT function FROM {SPAN_TYPE} WHERE parent_okf_path = {_sql_str(g)}")}:
                reach[f] = reach.get(f, 0) + 1
        return max(reach, key=reach.get) if reach else None

    def pool_for(fn):
        return [] if not fn else list(dict.fromkeys(r["parent_okf_path"] for r in store._query(
            f"SELECT parent_okf_path FROM {SPAN_TYPE} WHERE function IN {_str_array([fn])}")))

    functions = {q.query_id: oracle_f1(sorted(q.relevant)) for q in queries}
    pools = {q.query_id: pool_for(functions[q.query_id]) for q in queries}

    # query -> (dimension,value) property constraints (the KG feature's query side)
    cextract = SeamPropertyExtractor(model_id=CONSTRAINT_MODEL)

    def _constraints(q):
        fn = functions[q.query_id]
        if not fn or fn == "NONE":
            return set()
        rec = cextract(chunk_id=ChunkId.of("query", 0, q.text), function=fn, text=q.text, span_id="")
        return {(a.dimension.value, a.value) for a in rec.assertions}

    _progress(f"[constraints] decomposing {len(queries)} queries via {CONSTRAINT_MODEL} ...")
    constraints = dict(zip([q.query_id for q in queries],
                           map_concurrent(queries, _constraints, max_concurrency=8, label="[constraints]", echo=True)))

    r10, r20, r50, ndcg = [], [], [], []
    n_constrained = 0
    for i, q in enumerate(queries, 1):
        pool = pools[q.query_id]
        if not pool:
            for lst in (r10, r20, r50, ndcg):
                lst.append(0.0)
            continue
        qc = constraints[q.query_id]
        if qc:
            n_constrained += 1
        bge = reranker.score(q.text, [corpus.get(c, "") for c in pool])
        b = dict(zip(pool, bge))
        m = {c: property_match(qc, store.clause_typed_edges(_clause_key(c, corpus.get(c, ""))), grounded_only=True)
             for c in pool}
        # KG-enhanced ranking: typed constraint-match primary, BGE secondary
        ranked = sorted(pool, key=lambda c: (-m[c], -b[c], c))
        cond = [c for c in ranked if c in q.graded]
        r10.append(recall_at_k(cond, q.relevant, 10))
        r20.append(recall_at_k(cond, q.relevant, 20))
        r50.append(recall_at_k(ranked, q.relevant, 50))
        ndcg.append(ndcg_at_k(cond, q.graded, 10))
        _progress(f"[kg-rerank] q {i}/{len(queries)}  pool={len(pool)}  |qc|={len(qc)}  running "
                  f"r@10={statistics.mean(r10):.3f} r@20={statistics.mean(r20):.3f} r@50={statistics.mean(r50):.3f}")

    print(f"\n=== KG-enhanced pipeline  queries={len(queries)} ({n_constrained} with >=1 constraint)  "
          f"function -> BGE -> typed-KG (grounded) ===", flush=True)
    print(f"  recall@10 = {statistics.mean(r10):.3f}", flush=True)
    print(f"  recall@20 = {statistics.mean(r20):.3f}", flush=True)
    print(f"  recall@50 = {statistics.mean(r50):.3f}   (ACORD store bar = 0.667)", flush=True)
    print(f"  nDCG@10   = {statistics.mean(ndcg):.3f}", flush=True)
    store.close()


if __name__ == "__main__":
    main()
