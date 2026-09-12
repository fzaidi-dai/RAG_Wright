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

## What we validated, and what we need you to validate

- **Live-validated** here: `span_dense_search` runs correctly against a real ArcadeDB span index (correct rows, dense order).
- **Hermetically tested**: a buried dense span is guaranteed into `k`; a working query is unchanged; a constraint match is never displaced.
- **Not run here**: the 7-query before/after on the post-0040 corpus — it needs real span vectors, and our only live DB is a pre-0040 build whose schema predates some edge types. **Please run your exact 7-query probe** (the one in the issue) against your post-0040 corpus, `dense_floor_n=3`, and confirm: (a) the dense-#1 span reaches the returned `k` on all 7, and (b) the 5 currently-working queries are unchanged. `dense_floor_n` is the knob — if a working query regresses, lower it; if a failing query needs its 2nd/3rd dense span too, raise it. Send us the table and we'll set the default from it.

## Ask #3 — why passing `clause_type` changed nothing (it's by design)

`clause_type` feeds only the relevance **judge** (the assemble stage: relevant / not_relevant / uncertain). Leg B's pool is whole-index (`functions=()`, ADR-0047), and the rerank uses typed constraints **extracted from the query text** (`aquery_constraints`), never the caller's `clause_type`. So a caller-supplied `clause_type` cannot influence pool membership or rerank order — it only classifies what already surfaced.

To bias *retrieval* toward a clause type, pass a typed `(dimension, value)` **constraint** (that does boost the rerank); `clause_type` is a judge signal, not a retrieval signal. If you want a first-class "route retrieval to this clause type" input, that's a new feature — tell us and we'll scope it.

Reference: ADR-0104, `capabilities/property_boosted_retrieval.py` (`dense_floor_n`, `_select_with_dense_floor`), `store/arcadedb.py::span_dense_search`, engine issue `docs/engine-issues/0041-...`. Full suite: 1565 passed.
