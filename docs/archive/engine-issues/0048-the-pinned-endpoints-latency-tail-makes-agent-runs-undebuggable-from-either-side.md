# Engine issue 0048: the pinned endpoint's latency tail, and a joint reproduction we cannot run alone

**Raised by:** RuleWright (product), 2026-09-20, after three live decomposition runs died on timeouts.
**Severity:** not a defect in engine logic. It is a **measurement** problem that now blocks the product:
live evals fail often enough that a prompt change cannot be evaluated.
**Path:** `models/profiles.py` (the `deepinfra/bf16` pin, ADR-0111), `capabilities/*` span instrumentation.

---

## What we see

Three of the last five live `a_pair` runs produced no score at all — one hit our 190s whole-turn model
deadline, one hit the eval's own 900s ceiling, one was abandoned mid-grade. The two that completed took
330.5s and 552.8s. **The runs that succeed finish comfortably; the ones that fail fail completely.** That
is variance, not a slow path.

Reading one 900s trace's generations (43 of them, latency taken from `metadata.latency_ms`):

| source | calls | total | median | max |
|---|---|---|---|---|
| product agents (`agent-llm`, `qwen/qwen3.8-27b`) | 23 | 767.7s | 22.2s | **257.7s** |
| engine `astream_text` (`qwen3.8-27b-modal-or`) | 5 | 233.4s | 47.0s | 56.3s |
| engine `span-relevance` | 8 | 75.2s | 4.9s | 42.4s |
| engine `query-constraint` | 1 | 9.1s | — | — |

Across all 43: **p50 23.1s, p90 47.5s, max 257.7s; 17 calls over 40s, one over 100s.**

**It is not ArcadeDB.** Model time exceeds wall clock once parallel workers are counted, so there is no
room for a large local stall. It is the model calls themselves, on the endpoint ADR-0111 pinned.

**The product's share is the larger one**, and we are not asking you to own it. We are reporting it
because both repos now drive the SAME pinned endpoint with the same model, and a 22s median with a tail
to 257s is the shared fact underneath both our timeouts and your `astream_text` median of 47s.

## The pin may be part of it, and that is worth knowing

ADR-0111 hard-pins `qwen/qwen3.8-27b` to `deepinfra/bf16` with `allow_fallbacks: false`, which is right
for consistency — we asked for the equivalent on our side and kept it. But `allow_fallbacks: false` means
a request **queues behind that one endpoint's load** rather than routing away from it. Our slowest run of
four (552.8s) was the first one taken after the engine pin landed. One run proves nothing; the mechanism
is plausible enough to measure rather than assume, and neither of us can measure it from one side.

## What we would like, and why we cannot do it ourselves

**Run the product's own eval, from the product's checkout, against the same database, with the engine's
instrumentation turned up.** Concretely:

```bash
cd /Users/farhan/work/RuleWright
PYTHONPATH=. RULEWRIGHT_TRACE_LEVEL=generations \
  uv run python evals/run_decomposition.py --arm prompted --live --only a_pair --out /tmp/ap.json
```

That drives `clause_worker` and `compliance_worker`, which call `sweep_matter` and
`ask_contract_question` — i.e. the engine's retrieval and answer path — against the seeded eval corpus.
The engine is an editable path dependency in that environment, so **engine code edited in
`/Users/farhan/work/RAG_Wright` is live in this command with no build step.** Add spans, add timers, and
re-run.

What we are asking for specifically:

1. **Per-call duration on engine spans**, recorded as real observation start/end rather than only inside
   a payload. Today `astream_text` and `span-relevance` appear in our trace with identical start and end
   times, so Langfuse's own latency column reads 0 and the truth is only in metadata.
2. **A split inside `ask_contract_question`**: how much is retrieval (ArcadeDB + embedding) versus
   generation. We can see the generation; we cannot see what preceded it.
3. **Whether the endpoint's queueing time is separable** from generation time on your side — OpenRouter
   returns a generation id we already record (`openrouter_generation_id`), and
   `/api/v1/generation?id=...` resolves it; if the engine records the same id, a slow call can be
   attributed rather than guessed.

## What we are fixing on our side, so you are not chasing our bug

Our own `agent-llm` observations have the **same zero-duration defect** — `harness/tracing.py` creates the
observation after the call returns and ends it immediately, so start equals end and the real figure
survives only as `metadata.latency_ms`. That is why the table above needed a custom query to produce, and
it is ours to fix. We mention it because if you read our traces expecting Langfuse latencies, you will see
zeros and reasonably conclude the calls were instant.

## Why this is worth your time

The product cannot currently tell a prompt change from provider noise, which stalls the agent work
outright. The engine has the same exposure: every `astream_text` in that trace took between 40 and 56
seconds, which will show up in the engine's own evals as soon as they are timed. Whatever the cause,
finding it once serves both repos.

**Context:** engine `deepagents` is now `>=0.7.15` (issue 0047, ADR-0112) and the product removed its uv
override, so both repos resolve the same versions again. Nothing in this issue depends on that, but it
does mean a reproduction from the product's checkout now runs the same stack you have.
