# RuleWright → engine: answers for 0048 part 2 (Modal scoping)

Date: 2026-09-21 · answers to `2026-09-21_0048-part2-modal-scoping-questions_rulewright.md` · **Product changes for Step 1 are DONE and committed** (see "What we changed" below). Numbers here are measured, not estimated.

---

## Q1 — Routing scope: **both**, and the measurement makes it non-optional

Both `agent-llm` and the engine's calls go to Modal. Confirmed.

Routing only the engine would measure the smaller half. From a 900s `a_pair` trace, reconstructed per call:

| source | calls | total | median | max |
|---|---|---|---|---|
| product `agent-llm` | 23 | **767.7s** | 22.2s | **257.7s** |
| engine `astream_text` | 5 | 233.4s | 47.0s | 56.3s |
| engine `span-relevance` | 8 | 75.2s | 4.9s | 42.4s |
| engine `query-constraint` | 1 | 9.1s | — | — |

All 43 calls: **p50 23.1s, p90 47.5s, max 257.7s; 17 over 40s, 1 over 100s.** The product's share is the
larger one and carries the outlier, so leaving `agent-llm` on OpenRouter would leave the dominant variance
source in place and make the before/after unreadable.

**So tool-calling is REQUIRED** (`--enable-auto-tool-choice --tool-call-parser hermes`). Every tier we run
is a tool-caller.

## Q2 — How each surface is pointed

**Product.** Two environment variables, both already read from settings:

```
OPENROUTER_BASE_URL=https://<app>.modal.run/v1     # config.py: openrouter_base_url
OPENROUTER_API_KEY=<modal key>                     # config.py: openrouter_api_key
RULEWRIGHT_AGENT_MODEL=openai:<served-model-name>  # NEW, see below
```

The first two are named for OpenRouter for historical reasons; they are just "the endpoint" and "the key",
and nothing hardcodes a URL.

**`RULEWRIGHT_AGENT_MODEL` is new and is the answer to a problem you would otherwise have hit.** The model
id used to be a module constant (`AGENT_MODEL_SPEC`), so pointing us at Modal would have required either a
product change or forcing your deployment to serve under `qwen/qwen3.8-27b`. It is now configuration, on a
standing principle: **a model id must never be a constant**, because adopting a better model at the same
cost/performance should be a new profile and a changed id, not a refactor. **You can serve under whatever
name you like; tell us the string.** The `openai:` prefix selects langchain's OpenAI-*compatible* client —
it does not mean OpenAI, and both OpenRouter and vLLM speak it.

**Engine.** Your side. `qwen3.8-27b-modal` already exists with `backend="vllm"` and reads
`VLLM_BASE_URL`/`VLLM_API_KEY`. The `.env` is shared, so setting those reaches us both.

## Q3 — `agent-llm`'s call shape

- **Standard OpenAI chat-completions tool calling.** `use_responses_api=False` deliberately (the Responses
  shape drops OpenRouter's per-call cost), so the Qwen3 `hermes` parser fits.
- **Reasoning ON.** This needed a product fix, described below.
- **`max_tokens`: no cap.** Unbounded today. The only bounds are time: a 60s socket timeout, a 60s
  per-attempt deadline and a 190s whole-turn deadline. **Your instinct here is right** — we measured a
  104.5s / 8192-token runaway generation earlier in this work. Unbounded output plus reasoning is a real
  contributor to the tail. We are treating a cap as a separate change rather than smuggling it into this
  one, because it changes agent behaviour and wants its own measurement.
- **Guided decoding is nice-to-have, not load-bearing for us.** Our grader no longer uses
  `response_format` at all (on langchain 1.4 a bare schema resolves to a `ToolStrategy` the model may
  decline, and ours did — we now parse a nonce-fenced block). Workers still use `response_format`, but
  there it is *also* a tool call. **Tool-calling is the hard requirement.**

## Q4 — Peak concurrency: **14–16**, measured

Reconstructed from four independent `a_pair` traces by overlapping each call's real interval:

| run | wall | distinct calls | **peak concurrent** |
|---|---|---|---|
| a_pair | 900s | 37 | **16** |
| a_pair | 754s | 36 | **16** |
| a_pair | 338s | 37 | **15** |
| a_pair | 352s | 33 | **14** |
| no_worker | 41s | 4 | 1 |

**Eval items run one at a time** (`run_decomposition.py`, `max_concurrency=1`), so 14–16 is one item's
peak, not an aggregate across items.

It is higher than our two workers imply because the engine fans out internally (relevance judgements)
inside `sweep_matter`/`ask_contract_question`. **Size for ~16 with headroom**, and note that our unbounded
agent outputs saturate decode rather than prefill — which argues for the 2-GPU option you floated.

## The seeded eval DB

ArcadeDB at **`localhost:2480`**, database **`rulewright_dev`**, credentials in the shared `.env`
(`ARCADEDB_USER` / `ARCADEDB_PASSWORD`) — not reproduced here.

**Step 1 is fine as-is**: the joint run executes from the product checkout on this machine, so `localhost`
resolves. **Step 2 is not**, and it is worth raising now rather than discovering it then: co-locating the
product and engine as a Modal app puts the workload somewhere that cannot reach `localhost:2480`. That is
a networking decision (tunnel, managed ArcadeDB, or seeding a copy alongside), not a code one.

## What we changed on our side, and one thing that would have bitten you

Committed, `1166` fast and `378` db tests green:

1. **`RULEWRIGHT_AGENT_MODEL`** — the model id is configuration. The provider *and* harness profiles are
   now registered under the configured id too; a profile keyed by one string while the agent asks for
   another silently never applies, and the model then resolves with no key, no base URL and no reasoning
   flag, failing far from the cause. Guarded by a test that plants exactly that divergence.

2. **A vLLM endpoint is now told to think.** This is the one that would have looked like a model
   regression and been a missing flag: `_init_kwargs` gated **every** `extra_body` behind
   `is_openrouter(base_url)`, so pointing us at Modal would have sent **no reasoning instruction at all**.
   Your issue 0020 measured what that costs — *"intermittently returns empty content (~1/3 of runs)"* —
   which is why your own vLLM profile sets `chat_template_kwargs.enable_thinking` explicitly in both
   directions. We now send the vLLM spelling off OpenRouter and the OpenRouter spelling on it; the two are
   not interchangeable, and sending the wrong one is the same as sending none.

3. **Our zero-duration defect is fixed** (the one your part-1 note warned us about). `agent-llm`
   observations are now opened before the call and ended after, verified live: 3/3 with real durations
   matching their own `metadata.latency_ms` to the tenth of a second. **The asymmetry you warned about no
   longer exists** — both surfaces report true latency, so the joint run's trace is readable end to end.

## What we need back

The **`VLLM_BASE_URL`**, the **served model name** (any string — we set `RULEWRIGHT_AGENT_MODEL` to match),
and confirmation that the deployment serves **tool calls**. Then we run:

```
cd /Users/farhan/work/RuleWright
PYTHONPATH=. RULEWRIGHT_TRACE_LEVEL=generations \
  uv run python evals/run_decomposition.py --arm prompted --live --only a_pair --out /tmp/ap.json
```

One caution on reading the result: `a_pair` has produced **five different outcomes in five runs** on the
current endpoint (361.6s all-green, 264.5s with a provenance miss, a 190s timeout, 552.8s all-green, a
900s ceiling). A single Modal run will not settle anything. Plan on several, and we should agree in advance
what "better" looks like — we would suggest **p90 latency and the timeout rate**, not the median.
