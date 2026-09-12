# ADR-0104: dense floor-protection in Leg B (a strong dense match always survives RRF)

**Status:** accepted · **Date:** 2026-09-12 · **Resolves:** engine issue 0041 · **Related:** ADR-0033 (typed property-boost rerank), ADR-0047 (whole-index pool, no function gate)

## Context

Leg B (`property_boosted_retrieval`) builds its candidate pool **only** from `span_hybrid_search` — ArcadeDB's server-side `vector.fuse({fusion:'RRF'})`, which combines the dense and sparse legs by **equal-weight reciprocal rank**. On a short query whose key token is ubiquitous in the document ("What are the payment **terms**?"), the sparse leg ranks dozens of generic "terms" spans high, and equal-weight RRF lets them crowd the dense-#1 span out of the pool entirely. The property rerank (`typed_constraint_match_rank`, stable sort) only re-orders *within* the pool, so a span that never entered the pool is never recovered — and the product correctly abstains on an answerable question.

RuleWright measured this on a clean post-0040 corpus (68 provisions, 195 spans), 7 realistic queries: 5 are fine (dense #1 lands at rank 1–4), but "payment terms" put the dense #1 span **outside the top 30**, and the liability-cap span (`cap_quantum`, dense **rank 1 of 190**) landed at **rank 11** — cut by the `k=8` default. They eliminated ALL-CAPS embedding effects, embedding miss, the soft function tag, and pool size (`pool_k=200` gave the same rank). It is the fusion weighting.

## Decision

**Add a dense floor: the top-`dense_floor_n` pure-dense spans are guaranteed into the returned `k`.** Fusion still decides *order*; dense guarantees *membership*.

- New store method `ArcadeDBStore.span_dense_search(dense_query, k, documents=)` — the dense leg of the hybrid search alone (`vector.neighbors`, no sparse leg, no RRF), same row shape and `documents` scoping. **Validated against live ArcadeDB.**
- `property_boosted_retrieval` unions the top-`dense_floor_n` dense spans into the pool (so they are scored/reranked), then `_select_with_dense_floor` takes the reranked top-`k` but **reserves slots** so every dense-floor span survives: a non-floor span is skipped only when the remaining slots are needed for floor spans not yet included. Constraint-matching spans sit high in the reranked order, so they are reached before slots run low — the floor only ever displaces the weak, non-matching tail.
- `dense_floor_n` defaults to **3** (conservative): it recovers the buried dense #1 (and #2/#3) without displacing the working queries' results. The 5/7 queries where dense #1 is already in the top-k see no change (the guarantee is already satisfied, so nothing is reserved). `dense_floor_n=0` disables it.

## Ask #3 finding (documented, no behaviour change)

RuleWright observed that passing `clause_type` explicitly changed the retrieved set for none of the queries. That is **by design**, and worth stating: in `production_typed_property_retrieval`, a caller's `clause_type` feeds only the relevance **judge** (the assemble stage: relevant / not_relevant / uncertain). The pool is whole-index (`functions=()`, ADR-0047), and the rerank uses typed constraints **extracted from the query text** (`aquery_constraints`), never the caller's `clause_type`. So `clause_type` cannot influence pool membership or rerank order — it only classifies what already surfaced. A caller wanting to bias retrieval toward a clause type should pass a typed `(dimension, value)` constraint (or we would need a separate routing input); `clause_type` is a judge signal, not a retrieval signal.

## Consequences

- A strong dense match can no longer be silently dropped by RRF crowding on a short common-token query — the failure mode that made an answerable question return "not found."
- Conservative by construction: only the non-matching tail is displaced, and only when a floor span is missing; the working queries are untouched at `dense_floor_n=3`.
- `dense_floor_n` is the tuning knob. The server-side RRF is left as-is (not reweighted / not moved client-side) — a smaller, lower-risk change than re-architecting the fusion, and it leaves the fusion ordering that works on 5/7 queries intact.

Full suite: 1565 passed, 44 skipped.

## Verified (RuleWright, engine `8013b40`, post-0040 corpus, 195 spans / 70 clauses)

The exact 7-query probe, `dense_floor_n=3`:
- **dense #1 reaches the returned set on 7/7** (the payment-terms case: absent-from-top-30 → rank 6 at k=8; the liability cap: rank 11 → rank 8).
- **the 5 working queries are unchanged** (ranks 1, 4, 2, 2, 2, identical before/after, at both k=8 and k=30).
- **deterministic**: 4 consecutive runs return the cap span every time at position 8.
- `dense_floor_n=3` confirmed as the right default on this corpus (the floor engages only where a span would otherwise be cut). Ask #3 closed (a caller's `clause_type` is judge-only, as documented).

## Follow-on: do NOT re-order floor-protected spans (measured)

A floor-reserved span lands at the **end** of the returned `k` (fused order preserved, membership guaranteed). The first hypothesis was that this end position hurts the generator, and that placing the span at its dense rank would help. **RuleWright measured it and the evidence rejects that.** On the liability-cap question at k=8 the answer rate is 3/3 with the cap span at position 8 (last); at k=30 the span sits *earlier* (position 11) yet the answer rate *falls* to 1/3. So the dominant factor is the **volume of competing evidence**, not the span's position in it — and it is question-specific (liability 7/10, but "which law governs" and "what insurance" are 4/4 at the same k with the floor active). RuleWright's conclusion, adopted here: **do not re-order floor-protected spans** (it would fix the wrong thing and change ordering semantics for every query), and do not raise `k` as a workaround (it makes this worse). The contract stays "fusion decides order, dense guarantees membership"; the residual, question-specific abstention is downstream of retrieval (generation over a large evidence set) and is not a retrieval change to make. `dense_floor_n=3` and `k=8` confirmed as the defaults on this evidence.
