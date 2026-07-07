# ADR-0006: Empirical model profiles for structured output under reasoning

Date: 2026-07-07. Status: Accepted. Records the working structured-output profiles for the
structured-reasoning models, confirmed by a live forced-schema call at T12 (A-T2, risk 3). Fills the
placeholders T11 shipped in `src/rag_wright/models/profiles.py`.

## Context

The model-profile seam (T11, ADR-0001, CLAUDE.md standing rule) drives every structured-output call
through one construction point, keyed by model id, carrying the structured-output method and an
optional structured-only `extra_body`. T11 shipped the seam with placeholder model ids and no
`extra_body`, deferring the empirical part to T12: which OpenRouter slug, which method, and whether a
forced schema call survives the model's reasoning mode. Risk 3 is exactly this: many open reasoning
models reject a forced tool or schema choice while thinking.

T12 probed the live OpenRouter catalog and made forced-schema calls through the seam
(`seam.build_structured(model_id, schema).invoke(...)` returning a Pydantic instance).

## Decision

Register these profiles (all `method="function_calling"`, the broadly-supported default):

| Role | Model id (OpenRouter) | Structured-only `extra_body` |
|---|---|---|
| structured-reasoning, primary | `deepseek/deepseek-v4-pro` | none |
| structured-reasoning, secondary | `qwen/qwen3.7-plus` | `{"reasoning": {"enabled": false}}` |
| general / local default | `google/gemma-4-31b-it` | none |

### Findings

- **DeepSeek V4 Pro** honors the forced tool call while reasoning: bare `function_calling` (no
  `extra_body`) returned a valid contract instance on the first call. It needs no thinking-disable
  workaround, which is a concrete reason it is the primary for this call class.
- **Qwen 3.7 Plus** reproduces risk 3 exactly. Bare `function_calling` fails with
  `<400> InternalError.Algo.InvalidParameter: The tool_choice parameter does not support being set to
  required or object in thinking mode`. Adding the structured-only `extra_body`
  `{"reasoning": {"enabled": false}}` disables thinking on the forced structured call alone and
  returns a valid instance. Its free-text and reasoning calls are unaffected, because the seam binds
  `extra_body` to the structured runnable only (grounded in `with_structured_output` forwarding
  kwargs into the tool binding, T11).

The two prioritized models straddle the failure surface the seam was built for, and the primary is
the one that does not need the workaround. `{"reasoning": {"max_tokens": 0}}` was rejected as an
invalid thinking budget, so `enabled: false` is the confirmed disable shape for this provider.

### Slug corrections to T11 placeholders

`qwen/qwen-3.7-plus` was wrong (real: `qwen/qwen3.7-plus`); `google/gemma-4-class` was a descriptor,
not a slug (real: `google/gemma-4-31b-it`, a Gemma 4 class model with tool support). Each id stays
env-overridable (`RAG_MODEL_*`), so a future model change is config, not code.

## Consequences

- Extraction (T23), grading, and synthesis can force a schema on the structured-reasoning models
  through the seam and get a validated instance; the profile, not the call site, carries the
  per-model workaround. No provider/model flag appears in capability code.
- The empirical `extra_body` values live in `PROFILES` (config) and here (a dated ADR), never in a
  node. When a model's provider behavior changes, this table and the profile move together.
- The live proof is an opt-in test, `tests/foundation/test_model_seam_structured.py -m model`, kept
  out of the default hermetic suite by `conftest.py`; the seam's mechanics (extra_body binds to the
  structured call only) remain unit-tested without network in T11.
- These slugs are current as of the date above; re-confirm against the live catalog if a model is
  retired.
