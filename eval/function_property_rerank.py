"""T58b: the full pivot recall@50 -- function-filter -> property soft-boost -> BGE-rerank.

For each query: oracle function (best-single, as the ceiling), the PURE function-filter pool, and BGE
cross-encoder scores over the pool (one pass). The query is decomposed into property constraints
((dimension,value)) by the SAME extractor on the query text; a clause's property-match count (from the
property graph) is the SOFT BOOST. From the one rerank pass we score two rankings:
  - function+rerank        = sort by bge score            (the T58b ablation, ~0.52)
  - function+property+rerank = sort by (property_match, bge)  (the full pivot)
recall@50 vs bar 0.667 / baseline 0.379 / ceiling 0.939. Progress is FLUSHED per query to
`data/models/fpr_progress.log` (tail it) and echoed. Query decomposition uses Flash (throughput-routed).

  uv run python -m eval.function_property_rerank
"""

from __future__ import annotations

import os
import re
import statistics
import time
from pathlib import Path

from dotenv import load_dotenv

from eval.acord import load_corpus, load_test_queries
from eval.harness import recall_at_k
from rag_wright.capabilities.reranking import BGEReranker
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.packs.contracts.spans.property_extractor import SeamPropertyExtractor
from rag_wright.store.arcadedb import SPAN_TYPE, ArcadeDBStore, _sql_str, _str_array
from rag_wright.util.concurrent import map_concurrent

DB = os.environ.get("PIVOT_DB", "ragwright_acord_pivot")
DECOMP_MODEL = os.environ.get("DECOMP_MODEL", "deepseek/deepseek-v4-flash")
PROGRESS = Path("data/models/fpr_progress.log")
_SLUG = re.compile(r"[^A-Za-z0-9._-]+")


def _progress(msg: str) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(msg + "\n", encoding="utf-8")
    print(msg, flush=True)


def _clause_key(acord_id: str, text: str) -> str:
    return str(ChunkId.of("acord-" + _SLUG.sub("-", acord_id), 0, text))


def main() -> None:
    load_dotenv()
    store = ArcadeDBStore.from_env(database=DB, reset=False)
    corpus = {c.clause_id: c.text for c in load_corpus()}
    queries = load_test_queries()
    reranker = BGEReranker()

    def _oracle_f1(gold: list[str]) -> str | None:
        reach: dict[str, int] = {}
        for g in gold:
            for f in {r["function"] for r in store._query(
                    f"SELECT function FROM {SPAN_TYPE} WHERE parent_okf_path = {_sql_str(g)}")}:
                reach[f] = reach.get(f, 0) + 1
        return max(reach, key=reach.get) if reach else None

    functions = {q.query_id: _oracle_f1(sorted(q.relevant)) for q in queries}

    # decompose each query -> property constraints (concurrent, throughput-routed)
    extractor = SeamPropertyExtractor(model_id=DECOMP_MODEL)

    def _decompose(q):
        fn = functions[q.query_id]
        if not fn or fn == "NONE":  # a NONE-oracle query has no clause-function to route -> no constraints
            return set()
        rec = extractor(chunk_id=ChunkId.of("query", 0, q.text), function=fn, text=q.text, span_id="")
        return {(a.dimension.value, a.value) for a in rec.assertions}

    _progress(f"[decompose] {len(queries)} queries via {DECOMP_MODEL} ...")
    constraints = dict(zip([q.query_id for q in queries],
                           map_concurrent(queries, _decompose, max_concurrency=8, label="[decompose]", echo=True)))

    r50_fr, r50_fpr = [], []  # function+rerank ; function+property+rerank
    t0 = time.perf_counter()
    for i, q in enumerate(queries, 1):
        fn = functions[q.query_id]
        pool = [] if not fn else list(dict.fromkeys(
            r["parent_okf_path"] for r in store._query(
                f"SELECT parent_okf_path FROM {SPAN_TYPE} WHERE primary_tag IN {_str_array([fn])}")))
        if not pool:
            r50_fr.append(0.0)
            r50_fpr.append(0.0)
        else:
            bge = reranker.score(q.text, [corpus.get(c, "") for c in pool])
            qc = constraints[q.query_id]
            match = []
            for c in pool:
                props = {(r["dimension"], r["value"]) for r in store.clause_property_values(_clause_key(c, corpus.get(c, "")))}
                match.append(len(qc & props))
            ranked_fr = [c for c, _ in sorted(zip(pool, bge), key=lambda cb: -cb[1])]
            ranked_fpr = [c for c, _, _ in sorted(zip(pool, match, bge), key=lambda cmb: (-cmb[1], -cmb[2]))]
            r50_fr.append(recall_at_k(ranked_fr, q.relevant, 50))
            r50_fpr.append(recall_at_k(ranked_fpr, q.relevant, 50))
        _progress(f"[rerank] q {i}/{len(queries)}  pool={len(pool)}  |qc|={len(constraints[q.query_id])}  "
                  f"running: fn+rerank={statistics.mean(r50_fr):.3f}  fn+prop+rerank={statistics.mean(r50_fpr):.3f}  "
                  f"elapsed={time.perf_counter()-t0:.0f}s")

    print(f"\n=== queries={len(queries)}  bar=0.667  baseline=0.379  ceiling(single)=0.939 ===", flush=True)
    print(f"function + rerank            recall@50 = {statistics.mean(r50_fr):.3f}", flush=True)
    print(f"function + property + rerank recall@50 = {statistics.mean(r50_fpr):.3f}  "
          f"(delta from property boost = {statistics.mean(r50_fpr)-statistics.mean(r50_fr):+.3f})", flush=True)
    store.close()


if __name__ == "__main__":
    main()
