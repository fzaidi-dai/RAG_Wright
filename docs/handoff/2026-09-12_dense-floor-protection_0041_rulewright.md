# RuleWright handoff: Leg B dense floor-protection (issue 0041) + the clause_type finding (ask #3)

Date: 2026-09-12 · on `origin/main` · ADR-0104 · **No API change to the graph. New optional `dense_floor_n` knob on `property_boosted_retrieval` (default 3).**

---

## Your diagnosis was right

Leg B's pool comes only from `span_hybrid_search` — ArcadeDB's server-side `vector.fuse({fusion:'RRF'})`, equal-weight reciprocal rank. On a short query with a ubiquitous token ("payment **terms**"), the sparse leg ranks the generic "terms" spans high and RRF lets them crowd the dense-#1 span out of the pool; the property rerank only re-orders *within* the pool, so a span that never entered it is never recovered. That's the "dense #1 lands at 11 / outside top 30" failure.

## The fix — dense floor (your option 2)

The **top-`dense_floor_n` pure-dense spans are guaranteed into the returned `k`.** Fusion still decides order; dense guarantees membership.

- New store method `span_dense_search(dense_query, k, documents=)` — the dense leg alone (`vector.neighbors`, no sparse, no RRF), same row shape + `documents` scoping. **Validated against live ArcadeDB.**
- `property_boosted_retrieval` unions the top-`dense_floor_n` dense spans into the pool, then reserves slots so each survives the cut to `k` — a non-floor span is skipped only when the remaining slots are needed for a floor span not yet included. **Constraint matches are never displaced** (they sit high in the rerank, reached before slots run low); only the weak, non-matching tail is.
- **`dense_floor_n` defaults to 3**, conservative: it recovers the buried dense match without disturbing the 5/7 queries that already work (where dense #1 is in top-k, nothing is reserved). `dense_floor_n=0` disables it.

## Validated (by RuleWright, engine `8013b40`, post-0040 corpus)

The exact 7-query probe at `dense_floor_n=3` confirmed both conditions: **dense #1 reaches the returned set on 7/7** (payment-terms: absent-from-top-30 → rank 6 at k=8; liability cap: rank 11 → 8), and the **5 working queries are unchanged** at both k=8 and k=30. Retrieval is deterministic (4/4 runs). `dense_floor_n=3` and `k=8` are the confirmed defaults. (Engine-side: `span_dense_search` live-validated against real ArcadeDB; the mechanism is hermetically tested.)

**Do NOT re-order floor-protected spans, and do NOT raise `k`.** A floor-reserved span lands last in `k`, and the first hypothesis was that its end position hurt the generator. RuleWright measured it and rejected that: at k=8 the liability answer rate is 3/3 with the span *last*, at k=30 it sits *earlier* yet the rate *falls* to 1/3 — the dominant factor is the **volume of competing evidence**, not position, and it's question-specific (liability 7/10; "which law"/"what insurance" 4/4 at the same k). So placing floor spans at their dense rank would fix the wrong thing (and change ordering semantics for every query). The residual, question-specific abstention is downstream of retrieval (generation over a large evidence set), not a retrieval change.

## Ask #3 — why passing `clause_type` changed nothing (it's by design)

`clause_type` feeds only the relevance **judge** (the assemble stage: relevant / not_relevant / uncertain). Leg B's pool is whole-index (`functions=()`, ADR-0047), and the rerank uses typed constraints **extracted from the query text** (`aquery_constraints`), never the caller's `clause_type`. So a caller-supplied `clause_type` cannot influence pool membership or rerank order — it only classifies what already surfaced.

To bias *retrieval* toward a clause type, pass a typed `(dimension, value)` **constraint** (that does boost the rerank); `clause_type` is a judge signal, not a retrieval signal. If you want a first-class "route retrieval to this clause type" input, that's a new feature — tell us and we'll scope it.

Reference: ADR-0104, `capabilities/property_boosted_retrieval.py` (`dense_floor_n`, `_select_with_dense_floor`), `store/arcadedb.py::span_dense_search`, engine issue `docs/engine-issues/0041-...`. Full suite: 1565 passed.
