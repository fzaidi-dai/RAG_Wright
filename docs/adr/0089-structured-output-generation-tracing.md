# ADR-0089: the forced-structured path emits a Langfuse generation (with real cost)

**Status:** accepted · **Date:** 2026-09-09 · **Issue:** engine 0025 (RuleWright) · **Builds on:** issue 0017 (LLM tracing), issue 0021 (streaming cost recovery), ADR-0088 (the relevance judge, the first path where structured output is most of the bill)

## Context

Issue 0017 instrumented `astream_text` (free-text) and issue 0021 recovered its cost; the docling-graph `litellm` path already passed real cost. But `models/seam.py::build_structured` — the forced-structured (`with_structured_output`) path — recorded **nothing**: `_ainvoke_bounded` just returns `runnable.ainvoke(x)`, no `record_generation` and no Langfuse callback. So every forced-structured caller was invisible to cost/latency/model-mix reporting.

ADR-0088 made this acute: the relevance judge is one `build_structured` call per returned span — the dominant cost of a sweep — and none of it was measurable. RuleWright's reading showed 2 reported calls against 18 made (~89% under-count), and it under-reported the **model mix**: a judge misconfigured onto gemma reported as all-qwen, because the two visible calls were the qwen extractions and the sixteen invisible ones were gemma. A cost report the provider dashboard contradicts is not doing its job.

## Decision

`build_structured` records one generation per completed call, on both `.invoke` and `.ainvoke`, covering **every** structured caller at once (the ADR-0058 boundary: fix the seam, not each caller).

The obstacle is that `with_structured_output(include_raw=False)` returns only the parsed object — no usage, no cost. So when tracing is on, `build_structured` forces `include_raw=True` internally, reads tokens off `raw.usage_metadata` and the provider's **actual** cost off `raw.response_metadata["token_usage"]["cost"]` (as `ainvoke` surfaces it — issue 0021: OpenRouter returns cost on the non-streaming path without any flag), emits the generation via `record_generation` (`stage="build_structured"`, `name=label`), then returns the caller's shape unchanged. `cost` is None only when the backend does not surface it.

To keep this transparent:
- **Off (untraced):** `include_raw` stays the caller's value, no wrapping, no behavior change — existing callers and tests are untouched.
- **On, caller wanted the parsed value:** we forced `include_raw=True`, so a `RunnableLambda(_raise_on_parse_error)` re-raises the exact parse error the native `include_raw=False` path would have, preserving the bounded-retry contract identically; the wrapper then returns `parsed`.
- **Emit under the caller's trace** (issue 0018) via the ambient `traced_run`, so it correlates by session, and use `label` as the generation name so a reader tells `span-relevance` from `query-constraints`.

## Consequences

- Cost accounting is now complete across all three model paths (`astream_text`, `build_structured`, `litellm`), each passing the provider's real cost. The relevance judge and the compliance judge (both `build_structured`) are measurable; model-mix misattribution cannot recur (each call reports its own model). Live-verified: a judge call emits `stage=build_structured`, `label=span-relevance`, `model`, `usage`, `cost≈0.00096`.
- The build-time tracing check means a runnable built while untraced and reused after tracing is enabled would not instrument; the judges build a fresh runnable per call, and tracing is a process-level env set at startup, so this is a benign edge.
- Docs: `docs/OBSERVABILITY.md` updated to three emission paths and cost-on-all-paths.
