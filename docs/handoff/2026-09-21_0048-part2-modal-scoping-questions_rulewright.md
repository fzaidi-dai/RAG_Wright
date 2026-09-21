# RuleWright: scoping questions for issue 0048 part 2 (move the model to self-hosted Modal)

Date: 2026-09-21 · re engine issue 0048 · **Questions, not a change. We need these answered before deploying the Modal endpoint for the joint reproduction.**

---

## Why we're asking

0048's latency tail is variance on a shared, multi-tenant OpenRouter endpoint we can't control. Rather than only instrument around it (part 1, ADR-0113, already shipped), the plan is to move the model to a **self-hosted Config B (Qwen3.8-27B FP8) on Modal**, where latency is ours to measure and scale, and run your `a_pair` eval against it with the engine's new span instrumentation up.

Two steps, isolating one variable each:
- **Step 1:** shift the model OpenRouter → Modal (single endpoint, everything Qwen/vLLM supports turned on — tool-calling + JSON-schema + reasoning), both your `agent-llm` and the engine's calls through one API. Measure.
- **Step 2 (later):** co-locate the product + engine as a Modal app on the same subnet, retest.

The vLLM serve config we must build for Step 1 (tool-call parser, reasoning parser, guided decoding, GPU count) depends on your answers below.

## The four questions

1. **Routing scope.** For this run, should **both** the product's `agent-llm` (the deep-agent orchestrator) **and** the engine's retrieval/answer calls (`sweep_matter`, `ask_contract_question`, span-relevance) point at the Modal endpoint — or **only** the engine's calls, with `agent-llm` left on OpenRouter?
   - If `agent-llm` runs on Modal, the vLLM server must enable tool-calling (`--enable-auto-tool-choice --tool-call-parser hermes` for Qwen3). If only engine calls, it doesn't. (Your stated intent is "everything through Modal," so we assume both — please confirm.)

2. **How the eval selects the engine's model/endpoint.** What exactly do we set to route the engine's calls to a custom `https://<app>.modal.run/v1` — `RAG_SERVING=vllm` + `VLLM_BASE_URL`/`VLLM_API_KEY`, a specific engine model string, or a product config file? And **separately**, how does the product point `agent-llm` at a custom base_url?

3. **`agent-llm`'s call shape.** Does it use standard OpenAI tool-calling (so vLLM's Qwen3 `hermes` parser fits), and does it need reasoning ON? Any `max_tokens` cap on those calls, or are they unbounded? (Unbounded + reasoning is relevant to the runaway/timeout tail.)

4. **Peak concurrency.** Roughly how many simultaneous model calls does one `a_pair` turn drive at peak, and how many `a_pair` items run in parallel? We size the Modal endpoint's `--max-num-seqs` and GPU count to that (a single A100 gives ~53× concurrency at 16K, but long agent outputs can saturate decode — we may deploy 2 GPUs).

Plus: please confirm the **seeded eval DB** connection (host / db name / creds) the engine's retrieval path should hit during the run.

## What we do with the answers

Q1+Q3 fix the vLLM serve flags (tool-call parser, reasoning parser); Q4 fixes `--max-num-seqs` and GPU count; Q2 tells us how to point both surfaces at the endpoint; the DB confirms retrieval targets the seeded corpus. Then we deploy the unified Modal endpoint (Config B), wire its model profile, hand you the `VLLM_BASE_URL` + model string, and do the Step-1 joint run.

Reference: engine issue `docs/engine-issues/0048-...`, ADR-0113 (instrumentation, shipped), ADR-0110 (Config B), ADR-0111 (the OpenRouter pin under measurement).
