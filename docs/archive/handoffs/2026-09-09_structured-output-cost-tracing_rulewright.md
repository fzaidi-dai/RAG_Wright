# RuleWright handoff: engine issue 0025 resolved — forced-structured calls now traced (with real cost)

Date: 2026-09-09 · **Re:** engine-issue 0025 · Engine commit: `07db6f7` · ADR-0089 · No API change

---

## TL;DR

`build_structured` (the forced-structured path) now emits a Langfuse **generation** per call — model, tokens, real OpenRouter cost, latency, and a `label` name — so the relevance judge and every other structured caller are measurable. Your judged-sweep cost report is now complete, not a constraint-extraction-only floor. **No API changed**; this is purely instrumentation.

## What you'll see now

Per judged sweep, alongside the `query-constraints` generations (`stage=astream_text`) you'll now also see one generation per judged span:

- `name` = **`span-relevance`** (the judge), `stage` = `build_structured`, `model` = the judge model you passed, `usage_details` = input/output tokens, `cost_details.total` = OpenRouter's **actual** cost.
- Correlated under your `traced_run` session (issue 0018), so it splits cleanly between concurrent sweeps.

The two things you asked for are both honored: **real provider cost** (read off the raw response, not a token estimate — a figure that agrees with the invoice), and **emitted under the caller's trace**.

Both cost-report failure modes you hit are now closed:
- **Volume:** the ~89% under-count is gone — every judge call emits.
- **Model mix:** each call reports its own model, so a judge misconfigured onto a different model than the extractor is now visible in the report (it was the invisible calls that hid the miswiring before).

## Scope — wider than the judge

The fix is at the seam, so **every** `build_structured` caller is now traced, not just the relevance judge: the compliance judge and any other forced-schema call emit generations the same way (`stage=build_structured`; they'll carry a generic name unless the call site sets a `label`). If a particular structured call matters to you by name in reports, tell us and we'll add its `label`.

## Cost, all three paths

Cost accounting is now complete across the model paths, each passing the provider's real cost:

| path | `stage` | covers |
|---|---|---|
| streaming free-text | `astream_text` | query constraint extraction, answer generation, function classification |
| forced structured | `build_structured` | the relevance judge (`span-relevance`), the compliance judge |
| docling-graph | `litellm` | party / clause extraction (legacy) |

`cost` is null only when the backend doesn't surface it (e.g. self-hosted vLLM), in which case Langfuse prices from its own table.

## No action required

No dependency bump beyond picking up `07db6f7`, no config change, no code change on your side. Re-run the standard cost reading for a graded sweep and the judge line items will be there. Nothing else moved.

Reference: ADR-0089, `docs/OBSERVABILITY.md` (three emission paths), `models/seam.py::build_structured`.
