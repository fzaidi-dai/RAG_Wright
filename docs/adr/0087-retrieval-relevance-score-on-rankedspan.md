# ADR-0087: carry the fused retrieval score on RankedSpan (a relevance floor signal)

**Status:** SUPERSEDED by ADR-0088 (2026-09-09). The `retrieval_score` this ADR added was reverted: RuleWright's retest showed the RRF score with a single contributing retriever is `1/(60+position)` — a relabeling of the row number, not a relevance signal (a correct rank-1 hit scored identically to a nonsense query). No *score* (RRF, cosine, or cross-encoder) removes the corpus-specific threshold knob. ADR-0088 replaces it with a per-span relevance **verdict**. · **Date:** 2026-09-09 · **Issue:** engine 0023 (RuleWright) · **Builds on:** ADR-0033 (property-boosted retrieval), ADR-0047 (whole-index pool), ADR-0008 (ArcadeDB vector functions grounded via vendor docs)

## Context

`typed_property_retrieval` always returns `k` spans, and the only score on `RankedSpan` was `match_score` — a COUNT of satisfied typed constraints, not a relevance. So the product could not distinguish "the nearest thing in the corpus, and none is close" from "on point", and PR-14's `not_found` grade was unreachable: a nonsense query (`purple elephants riding unicycles on the moon`) still returned a full page of clauses. The embedding relevance that ordered the pool was not merely dropped when `RankedSpan` was built — `span_hybrid_search` never projected it.

## Decision

Project the fused relevance from `span_hybrid_search` and carry it through as `RankedSpan.retrieval_score` (float | None), alongside `match_score`. The product applies its own floor (matched / possible / not_found); the engine does NOT pick the threshold (the issue asked for exactly this, and rejected a `min_score` engine parameter for hiding the number).

Signal choice — measured, not assumed:
- ArcadeDB `vector.neighbors` exposes `distance` (cosine, absolute); `vector.fuse` auto-flips it and the fused record exposes `score` (RRF, higher = better) — grounded live against the store, per ADR-0008.
- On the corpus, raw dense **cosine** is furniture-dominated (top hits for every query are the same boilerplate span, ~3% margin) — a poor absolute floor. The **fused RRF score** separates: on-point top ~0.0315 with a descending profile vs a nonsense query flat at ~0.0164 (the single-leg rank-1 RRF floor). RRF score encodes dual-leg (dense+sparse) agreement, which is the genuine relevance signal here, and it is literally "the value that ordered the pool" the issue named. So `retrieval_score` is the fused RRF score.

## Consequences

- `not_found` becomes reachable: a caller floors on `retrieval_score` (e.g. top < ~0.02 → nothing strongly matched). `match_score` and `retrieval_score` are independent — one is constraint satisfaction, the other retrieval relevance. The value propagates to the product through `TypedPropertyRetrieval.results` (list[RankedSpan]) with no further wiring.
- `retrieval_score` is None only when the backend surfaces no fused score (graceful absence, not a crash).
- Where the floor sits is the product's judgement (RuleWright T-4.9), measured on their clean corpus. This engine change was validated on `ragwright_cuad_full`, which is stale (furniture spans + schema drift), so the separation is demonstrated but the threshold must be tuned on a current corpus.
- Only the span path (`RankedSpan`) is in scope; the chunk-level `hybrid_search` is unchanged.
