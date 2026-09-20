# RuleWright handoff: engine span instrumentation for issue 0048 (part 1 of 2)

Date: 2026-09-20 · on `origin/main` · ADR-0113 · **Instrumentation only, no engine-logic change. Fixes the "Langfuse latency reads 0" blindness so 0048's latency is measurable from the engine side.**

---

## What you get in Langfuse now

When you re-run your eval with `RULEWRIGHT_TRACE_LEVEL=generations` (engine `RAG_TRACE_LEVEL=generations`), engine model calls now emit **correct latency and the split you asked for**:

1. **Real span durations.** Every engine generation (`astream_text`, `build_structured` → span-relevance / query-constraint / judge, and the docling-graph extraction path) is now **opened before the call and ended after**, so Langfuse's own latency column is the true wall-clock — not 0. (langfuse v4 can't back-date a start, which is why the old post-hoc emit read 0; the real figure had survived only in `metadata.latency_ms`.)
2. **Time-to-first-token.** `astream_text` sets Langfuse `completion_start_time` (first content token), so you can split queue+prefill from decode on the generation itself.
3. **A retrieval span.** `intra_document_qa`'s `serve` + `assemble` (ArcadeDB + rehydration — what precedes generation inside `ask_contract_question`) now emit a Langfuse `span`, sibling to the generation in the same trace. That's your "how much is retrieval vs generation" split (your ask #2).
4. **Provider generation id.** Each engine generation carries `openrouter_generation_id` in its metadata (when the backend surfaces one). Resolve it at `/api/v1/generation?id=` to attribute a slow call to queueing vs generation (your ask #3). It is `None` on self-hosted vLLM (no such endpoint) — the engine's own span durations are then the latency source.

## What this does and doesn't settle

- It makes 0048's latency **measurable** from the engine side. It does **not** yet answer whether the `deepinfra/bf16` pin's `allow_fallbacks:false` worsens the tail — that's part 2, which we measure *with* this instrumentation (and which our plan addresses more directly by moving the model to self-hosted Modal, where latency is ours to control).
- Your own `agent-llm` observations (`harness/tracing.py`) have the **same** zero-duration defect — created after the call and ended immediately. That's product-owned; you flagged you're fixing it. Until you do, your `agent-llm` rows will still read 0 in Langfuse's latency column while the engine rows now read true; don't mistake that asymmetry for the engine being instant.

## The joint run

The engine is an editable path dep in your checkout, so this instrumentation is already live in:
```
cd /Users/farhan/work/RuleWright
PYTHONPATH=. RULEWRIGHT_TRACE_LEVEL=generations \
  uv run python evals/run_decomposition.py --arm prompted --live --only a_pair --out /tmp/ap.json
```
That's the run that will confirm the Langfuse latency column is now non-zero for engine spans and show the retrieval/generation split — the definitive check we deferred to the joint repro. We also have four scoping questions out to you (relayed separately) about pointing both `agent-llm` and the engine's calls at a single Modal endpoint for part 2.

Reference: ADR-0113, `models/tracing.py` (`start_generation`/`finish_generation`/`traced_step`), `models/seam.py` (build_structured + astream_text), `capabilities/dg_extraction.py`, `subgraphs/intra_document_qa.py`, engine issue `docs/engine-issues/0048-...`. Full suite: 1586 passed.
