"""T58: the single-function gate recall ceiling (model-free).

The function filter is a HARD gate (memory `retrieval-design-t58`): once we commit to `function=F`, gold
clauses whose spans were bucketed elsewhere are unreachable, regardless of ranking or property matching.
This measures that ceiling directly, BEFORE any model at query time: for each test query, what fraction of
its grade>=2 gold clauses have >=1 operative span classified into the best single function bucket -- and how
much does unioning the 2nd-best (confusable sibling) recover?

`best-single` = the recall ceiling assuming a PERFECT query->function classification (an oracle picks the
function that maximizes this query's reach). `best-union2` = the same with the top-2 functions unioned. The
gap between them is the payoff of the union-confusable-siblings mitigation. This needs only the phase-1 span
index (`PHASE=spans`); no property graph.

  uv run python -m eval.function_ceiling
"""

from __future__ import annotations

import os
import statistics

from dotenv import load_dotenv

from eval.acord import load_test_queries
from rag_wright.store.arcadedb import SPAN_TYPE, ArcadeDBStore, _sql_str

DB = os.environ.get("PIVOT_DB", "ragwright_acord_pivot")


def main() -> None:
    load_dotenv()
    store = ArcadeDBStore.from_env(database=DB, reset=False)
    queries = load_test_queries()

    fn_cache: dict[str, set[str]] = {}

    def clause_functions(acord_id: str) -> set[str]:
        if acord_id not in fn_cache:
            rows = store._query(f"SELECT function FROM {SPAN_TYPE} WHERE parent_okf_path = {_sql_str(acord_id)}")
            fn_cache[acord_id] = {r["function"] for r in rows}
        return fn_cache[acord_id]

    rows = []
    for q in queries:
        gold = sorted(q.relevant)
        gold_fns = {g: clause_functions(g) for g in gold}
        n = len(gold)
        # per-function reach: how many gold clauses have >=1 span in function f
        reach: dict[str, int] = {}
        for fns in gold_fns.values():
            for f in fns:
                reach[f] = reach.get(f, 0) + 1
        ranked = sorted(reach.items(), key=lambda kv: -kv[1])
        f1 = ranked[0][0] if ranked else None
        best_single = (ranked[0][1] / n) if ranked and n else 0.0
        f2 = ranked[1][0] if len(ranked) > 1 else None
        if f2:
            reach2 = sum(1 for g in gold if f1 in gold_fns[g] or f2 in gold_fns[g])
            best_union2 = reach2 / n
        else:
            best_union2 = best_single
        rows.append((q.text, n, f1, best_single, f2, best_union2))

    print(f"queries={len(rows)}  bar=0.667 (recall@50, GATE-R)\n")
    print(f"avg best-single-function ceiling = {statistics.mean(r[3] for r in rows):.3f}")
    print(f"avg best-union-top2    ceiling = {statistics.mean(r[5] for r in rows):.3f}")
    print(f"queries with best-single < 0.667: {sum(1 for r in rows if r[3] < 0.667)}/{len(rows)}\n")
    print("worst-first  single -> union2  [F1 | +F2]  query")
    for text, n, f1, bs, f2, bu in sorted(rows, key=lambda r: r[3]):
        print(f"  {bs:.2f} -> {bu:.2f}  n={n:2d}  [{f1} | +{f2}]  {text[:46]}")
    store.close()


if __name__ == "__main__":
    main()
