# ADR-0025: ACORD recall is expert-graded and method-invariant — pivot to function-classify → property-graph → rerank

Status: accepted
Date: 2026-07-23

## Context

A deep recall investigation (this session) on ACORD full-corpus retrieval established, and then reconciled
with the already-recorded T41 finding (ADR-0022), that the recall shortfall is **real, method-invariant, and
data-grounded** — not a cheap-model artifact and not fixable by better retrieval alone:

- **Two-leg hybrid recall@50 ~= 0.38, method-invariant.** MiniLM+lexical (0.373), BGE-M3 dense+sparse (recorded
  0.379, ADR-0022), KeyBERT, and every RRF fusion — over descriptions AND full bodies — all plateau at
  ~0.37-0.45 @50 / ~0.79 @150. A larger embedder does not move it. RRF fusion of lexical+dense is the best
  recall stage (dense=synonym, sparse=exact-term are complementary), but caps at that ceiling.
- **reachability (0.776) != rankability (~0.4).** FR-K's OKF-navigation premise (ADR-0022) rested on a per-gold
  *reachability* ceiling of 0.776; the *realizable ranking* recall is ~0.4. The gap is the standing
  reachability-vs-rankability lesson, and it applies to FR-K's own justification. The OKF navigator is shelved.
- **The gold is trustworthy, not arbitrary.** ACORD's qrels are dense attorney grading (~1,088 clauses judged
  per query, 0-4 scale; gold = grade >= 2). The clauses that outrank the gold are attorney-graded 0
  (irrelevant), not unjudged. So the ceiling is a genuinely hard, expert-validated distinction.

Manual by-hand analysis (paralegal perspective) then found the shape of the hard distinction: every ACORD query
is a **FUNCTION** (clause type, e.g. "liability cap") plus a **PROPERTY** (qualifier, e.g. "unilateral",
"excludes UCC"). Our retriever fails because it matches the *topic word* ("liability") over a long body, not the
clause's *function* (operative language) or its *property* (a specific local detail). The dominant miss is
**function-confusion** (insurance / indemnity / termination clauses that merely say "liability" outrank the
gold); the residual is the fine property distinction. Both signals are LOCAL (a short operative span), which is
why whole-body bag-of-words drowns them.

## Decision

Pivot the retrieval approach to match how the task is actually solved by hand:

1. **Operative-span indexing (small-to-big).** Re-chunk clauses into operative spans (enumerated sub-clauses,
   semicolon lists, legal sentence boundaries); each span points to its parent clause. The span is the classified
   and retrieved unit; the parent clause is what we return/rerank.
2. **FUNCTION = a local, single-label classifier over spans** (the 41 CUAD `ClauseCategory` types + NONE).
   Local, cheap, deterministic — the correct tool for a high-volume bounded classification, categorically more
   scalable than an LLM routing call per clause. Operative-span granularity makes it single-label.
3. **PROPERTY = a new, property-targeted knowledge graph.** Extract the recurring *structured* properties as
   typed relations (LIMITS_LIABILITY_OF, EXCLUDES, CARVES_OUT, CAP_AMOUNT, ...) into a NEW graph (reusing the
   graph-extraction / entity-disambiguation machinery, NOT the existing generic entity graph). Once function is
   known and the clause is in hand, the property becomes a precise structured graph query.
4. **RERANKER for the tail.** Fuzzy / comparative / novel properties ("buyer-favorable", "no less favourable to
   a material extent") do not graph cleanly and fall to a zero-shot reranker (BGE `BGEReranker` by default; an
   LLM reranker via the seam is the one optional external step). ACORD's own baseline shows the LLM reranker is
   the proven precision lever (GPT-4o-rerank 0.812 nDCG@10 vs the 0.379 retriever).

Storage (FR-S.1, one store): clauses stay the source of truth in the existing clause OKF bundle; **spans (text,
function tag, dense+sparse embeddings, parent pointer) and the property graph live in ArcadeDB.** No span-OKF.

## Consequences

- Query time is mostly local + deterministic (span hybrid-search filtered by function -> property graph query ->
  parent clauses), with the LLM reserved for one-time ingestion extraction (cacheable) and the fuzzy-tail
  reranker. Correct cost structure.
- The FR-K embedding-free OKF navigation experiment (T45-T50) is superseded and shelved. Its format (the clause
  OKF bundle) is retained as the human-readable clause source of truth; its navigator is not used.
- The sub-foldering/clustering, leaf-aware reachability, and description-sift experiments from this session are
  reverted (findings recorded here); `scikit-learn` is retained (the function classifier's linear head).
- New identifier: `span_id` (embeds `parent_chunk_id`) is the join key across the span store and property graph;
  fix before building (identifier rule).

## References

- ADR-0022 (FR-K embedding-free OKF navigation, experimental) — the reachability=0.776 premise this revises.
- ADR-0011 (CUAD is not a retrieval benchmark; ACORD for queries) — the graded-qrels eval basis.
- ADR-0007 (ArcadeDB store schema) — the dense-`LSM_VECTOR` / sparse-`LSM_SPARSE_VECTOR` machinery the `Span`
  type reuses.
- `contracts/ontology.py` `ClauseCategory` (41 CUAD types) — the function taxonomy.
