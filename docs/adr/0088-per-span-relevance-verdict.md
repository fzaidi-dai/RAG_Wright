# ADR-0088: a per-span relevance VERDICT on the corpus retrieval path (not a score)

**Status:** accepted · **Date:** 2026-09-09 · **Issue:** engine 0023 (RuleWright) · **Supersedes:** ADR-0087 · **Builds on:** ADR-0033 (property-boosted retrieval), ADR-0045 (client-side structured output), the compliance-judge / answer-abstention verdict pattern

## Context

`typed_property_retrieval` always returns the top-k nearest spans, so `not_found` was unreachable: a nonsense query still returned a full page of clauses. Three successive attempts to hand the product a *score* to floor on all failed for the same underlying reason:

- **RRF `retrieval_score`** (ADR-0087) — with a single contributing retriever it is exactly `1/(60 + position)`, a relabeling of the row number. RuleWright's retest: a correct rank-1 Renewal Term hit scored `0.01639`, identical to a nonsense query. Reverted.
- **cosine distance** — its distribution shifts with the embedding model, domain, document length and chunking, so a threshold tuned on one corpus mis-fires silently on the next.
- **cross-encoder rerank** — a better number, still a number to calibrate per deployment.

Every score hands each deployment a magic threshold that fails silently in both directions. The engine already answers "does this text satisfy this thing?" without a float in two places — `agenerate_answer` abstention and the compliance judge (`(claim, requirement) -> verdict`). Corpus retrieval simply had no equivalent.

## Decision

Return a per-span relevance **verdict**, produced by an LLM judge that combines the typed constraints with a semantic reading. New `agent_skill` `span_relevance_judgment` (mirrors `compliance_judgment`), authored as `skills/span_relevance_judgment/SKILL.md`:

- `Condition(clause_type, value_condition?, question?)` — the structured test. Judge **primarily on `clause_type` + `value_condition`**, with `question` as context only (in a multi-condition sweep one question is shared across conditions, so it must not by itself make a span relevant).
- `RelevanceVerdict(verdict, rationale, confidence)`, verdict vocab **`relevant | not_relevant | uncertain`**. `uncertain` is a real judgement (ambiguous text) AND the conservative default on a judge failure/unreadable output — **recall-safe: a failure never fabricates a `not_found`** (mirrors the compliance judge's `needs_review`).
- The typed `matched[]` constraints are passed to the judge as **evidence, not a verdict**: they are extracted from the question once and reused across conditions, so a satisfied constraint is not proof of relevance to *this* condition (a Source Code Escrow clause carrying `cap_basis = multiple_of_fees` is not a Cap On Liability hit). One code path — the judge decides every span.
- `ajudge_spans` judges the returned spans **concurrently** (async + semaphore + per-span wall-clock bound), so a sweep's wall-clock is concurrency-bound, not count-bound.

Composition: the `typed_property_retrieval` subgraph gains a `judge_relevance` node after `retrieve`, and returns `list[JudgedSpan]` — `JudgedSpan(span: RankedSpan, relevance: RelevanceVerdict | None)`, composing a retrieval fact and a judgement fact exactly as the compliance subgraph composes `ComplianceFinding` (rather than growing a verdict field on the pure `RankedSpan` retrieval contract). Opt-in via `judge_model_id` on `production_typed_property_retrieval`; the graph input carries the `clause_type` / `value_condition`. When no judge is wired, spans pass through with `relevance=None` (a state distinct from the `uncertain` verdict).

## Consequences

- `not_found` is reachable without any threshold: when every returned span is `not_relevant`, the corpus does not contain the condition. The engine returns the FACT (per-span verdict); the product keeps the matched/possible/not_found grouping (POLICY) — the same boundary as `match_score`/`matched[]`. Live-verified on the cases where scores failed: a Renewal Term clause vs "Renewal Term" → `relevant` (0.99) where RRF gave `0.01639`; an escrow clause with an incidental `cap_basis` match vs "Cap On Liability" → `not_relevant` (0.98).
- Cost: one LLM verdict per returned span; `k` (the product's existing lever) bounds it, concurrency bounds wall-clock. No second engine-side bound; no cross-encoder pre-narrow for now (if added, it must DROP spans, never return them unjudged — every returned span carries a verdict).
- `retrieval_score` (ADR-0087) and its `span_hybrid_search` `score` projection are reverted; `RankedSpan` is a pure retrieval contract again.
- **Known gap, not fixed here:** a multi-condition sweep shares one `value_condition` / `question` across conditions (`QueryIntent` returns `clause_types: list[str]` but a single `value_condition`); per-condition question decomposition is a separate engine issue. The judge leans on `clause_type` accordingly.
- **Deferred (ask-first):** promoting `span_relevance_judgment` to its own FR-C canonical slug in the ARD catalog (SPEC.md section 5) is a spec change; today it is composed inside the registered `typed_property_retrieval` and needs no registration to function.
