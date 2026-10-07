"""KG-5e (FR-Q): the query-side FUNCTION ROUTER, driven by the granite-extracted DIMENSIONS.

KG-5c found that query-side function routing is the dominant retrieval lever (the oracle->real gap is
-0.22 recall@20, dwarfing every reranker lever) and that the clause-trained LegalBERT classifier is
out-of-distribution on short query text. This routes instead from the query's typed DIMENSIONS -- which
KG-5b showed the granite extraction gets right even where `clause_type` is unreliable -- through a
corpus-derived `dimension -> function` co-occurrence prior. No extra LLM call, no clause-trained model:
the typed extraction we already compute builds the pool.

The prior is built from a HELD-OUT corpus (CUAD) so no ACORD eval data enters the router; the two corpora
share the property-dimension schema and the CUAD-type function taxonomy, so the prior transfers.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable


def build_cooccurrence(clause_rows: Iterable[tuple[str, str, str]]) -> dict[str, dict[str, int]]:
    """`dimension -> {function -> clause-count}` from `(clause_id, function, dimension)` typed-edge rows.

    Counted once per (clause, dimension, function) so a clause with several values for one dimension does not
    over-weight its function. An empty/`NONE` function is dropped (not a routable target). Pure -- no store.
    """
    seen: set[tuple[str, str, str]] = set()
    cooc: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for clause_id, function, dimension in clause_rows:
        if not function or function == "NONE" or not dimension:
            continue
        key = (clause_id, dimension, function)
        if key in seen:
            continue
        seen.add(key)
        cooc[dimension][function] += 1
    return {d: dict(fs) for d, fs in cooc.items()}


def _function_marginal(cooc: dict[str, dict[str, int]]) -> dict[str, float]:
    """P(function) proxy: each function's share of all edges in the prior -- the base rate that lift/PMI
    divide out so a distinctive dimension beats a merely-common function."""
    tot: dict[str, float] = defaultdict(float)
    grand = 0.0
    for col in cooc.values():
        for fn, c in col.items():
            tot[fn] += c
            grand += c
    return {fn: c / grand for fn, c in tot.items()} if grand else {}


def route_functions(
    query_dimensions: Iterable[str],
    cooc: dict[str, dict[str, int]],
    *,
    k: int,
    score: str = "conditional",
    min_support: int = 1,
) -> list[str]:
    """The top-`k` functions for a query, scored by summing a per-dimension signal over the query's dimensions:

    - ``conditional`` (default): ``P(function | dimension)`` -- biased toward high-frequency functions.
    - ``lift``: ``P(function|dimension) / P(function)`` -- corrects for the function base rate (a dimension
      routes to the function it is DISTINCTIVE of, not merely the most common one that has it).
    - ``pmi``: ``log(P(function|dimension) / P(function))`` -- the log-odds form of lift.

    `min_support` drops sparse `(dimension, function)` evidence (< that many clauses) so lift/PMI are not blown
    up by a single-clause coincidence. A dimension with no corpus signal contributes nothing; no dimensions
    (or none seen) -> ``[]``. Ties break on the function name so the routing is deterministic.
    """
    marginal = _function_marginal(cooc) if score in ("lift", "pmi") else {}
    total_score: dict[str, float] = defaultdict(float)
    for d in query_dimensions:
        col = cooc.get(d)
        if not col:
            continue
        total = sum(col.values())
        if not total:
            continue
        for fn, c in col.items():
            if c < min_support:
                continue
            p_f_given_d = c / total
            if score == "conditional":
                total_score[fn] += p_f_given_d
            else:
                p_f = marginal.get(fn, 0.0)
                if p_f <= 0:
                    continue
                total_score[fn] += p_f_given_d / p_f if score == "lift" else math.log(p_f_given_d / p_f)
    return [fn for fn, _ in sorted(total_score.items(), key=lambda x: (-x[1], x[0]))[:k]]
