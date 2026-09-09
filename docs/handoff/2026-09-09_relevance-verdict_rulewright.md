# RuleWright handoff: engine issue 0023 resolved with a relevance VERDICT (API changed)

Date: 2026-09-09 · **Re:** engine-issue 0023 · Engine commit: `47e117c` (supersedes the RRF `3d6fab0`) · ADR-0088 (supersedes ADR-0087)

---

## TL;DR

`not_found` is now reachable — not via a score to threshold, but a per-span **relevance verdict**. `typed_property_retrieval` now returns, for every retrieved span, `relevant | not_relevant | uncertain` against the condition. **Yes, the API changed** (result shape + one new opt-in param) — details below. Retest against the new `JudgedSpan` shape, not `retrieval_score` (that field is gone).

## Why it's a verdict, not a score (recap)

Every score needs a corpus-specific threshold that fails silently — RRF is `1/(60+position)` (row number), cosine's distribution moves with model/domain/chunking, cross-encoder is a better number and still a knob. A verdict removes the knob, matching the engine's compliance judge and answer-abstention. The judge decides; typed constraints (`matched[]`) are passed in as **evidence, not a verdict** (so a Source Code Escrow clause carrying an incidental `cap_basis` match is correctly `not_relevant`).

## API changes — read this

1. **`TypedPropertyRetrieval.results` changed type: `list[RankedSpan]` → `list[JudgedSpan]`.** This is a **breaking change** for any consumer.
   - `JudgedSpan = { span: RankedSpan, relevance: RelevanceVerdict | None }`.
   - `RankedSpan` is unchanged (`span_id, text, function, match_score, matched[], rank`) — it's now nested under `.span`.
   - Access moves: `r.span_id` → `j.span.span_id`, `r.match_score` → `j.span.match_score`, etc.; the verdict is `j.relevance.verdict` / `.rationale` / `.confidence`.
   - `RelevanceVerdict = { verdict: "relevant"|"not_relevant"|"uncertain", rationale: str, confidence: float }`.

2. **The MCP tool `retrieve_typed_property_spans` output shape changed** to match: each `results[]` item is now `{ "span": { span_id, text, function, match_score, matched[], rank }, "relevance": { verdict, rationale, confidence } | null }`.

3. **`production_typed_property_retrieval` gained `judge_model_id`** (opt-in). Omit it → spans pass through **unjudged** (`relevance: null`), behaviour otherwise unchanged. Pass it (e.g. `qwen/qwen3.8-27b`) → every returned span is judged.

4. **The graph input now carries the condition.** Invoke with `{"query": <question>, "clause_type": <type>, "value_condition": <value|None>}`. The judge rules primarily on `clause_type` + `value_condition`; `query` rides as context. No `clause_type` in the input → judging is skipped (spans pass through unjudged), never an error.

5. **`retrieval_score` is REMOVED** from `RankedSpan` (the reverted RRF fix from `3d6fab0`). If your grading plumbing referenced it, switch to the verdict. `span_hybrid_search` no longer projects the fused `score` either.

## How to grade (unchanged division of labor)

The engine returns the FACT (`relevance.verdict` per span, + `matched[]`); you keep the POLICY (matched / possible / not_found). A natural mapping:
- `relevant` + `matched[]` non-empty → **matched**
- `relevant` + `matched[]` empty, or `uncertain` → **possible**
- all returned spans `not_relevant` → **not_found**

`uncertain` is the conservative default (also what a judge failure/timeout yields), so a judge failure never fabricates a `not_found` — it surfaces the span for review. No threshold anywhere.

## Cost / scale

One LLM verdict per returned span, judged **concurrently** (wall-clock is concurrency-bound, not count-bound). `k` is your lever — there is no second engine-side bound. Your measured envelope (3×8 = 24 spans ≈ 2¢/sweep) holds; the judge model is `judge_model_id`.

## Verified

Live on the exact cases where scores failed: a Renewal Term clause vs "Renewal Term" → `relevant` (0.99) where RRF gave `0.01639`; an escrow clause with an incidental `cap_basis` match vs "Cap On Liability" → `not_relevant` (0.98). Full engine suite 1486 passed.

## Two related notes

- **Engine issue 0024 filed** (in this tracker): the multi-condition sweep shares one `value_condition` across conditions — a real decomposition gap. 0023's verdict masks its symptom (the judge reads the text, ignores the misattributed constraint), but per-condition constraint attribution is still wrong for anything that later trusts `matched[]` as a per-condition signal. Not blocking.
- `span_relevance_judgment` is now a canonical FR-C capability (composed inside `typed_property_retrieval`).

Reference: ADR-0088, `capabilities/span_relevance_judgment.py`, `subgraphs/typed_property_retrieval.py`.
