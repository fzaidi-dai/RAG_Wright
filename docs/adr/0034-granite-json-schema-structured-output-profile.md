# ADR-0034: Granite 4.1-8b uses `json_schema` structured output (model-profile seam)

Date: 2026-07-29
Status: Accepted; superseded for the query side by ADR-0045 (query-side structured output is now client-side
XML-tag parsing, not server-side guided decoding). Still applies to any caller that uses `build_structured`
directly (ingestion, explicit opt-in).

## Context

KG-5e adds a query-side function router (lever b): a taxonomy-constrained LLM classifier that maps a query to
clause functions from the closed `FUNCTION_LABELS` set, via a single forced structured-output call
(`capabilities/query_function_classifier.py`). We run it on the same model as the KG extraction,
`ibm-granite/granite-4.1-8b`, through the OpenRouter seam.

The default structured-output method is `function_calling` (ADR-0006: more broadly supported than
`json_schema`). On granite this **silently fails**: the forced call returns a *valid but empty* result —
`clause_types=[]` — for even unambiguous queries like "England Governing Law". The plumbing does not error; the
model just emits nothing useful under the tool-call method. Switching only the structured method to
`json_schema` fixes it cleanly: correct labels on every probe (`Governing Law`, `Audit Rights`, `IP Ownership
Assignment`, …). This matches the standing rule that structured-output behavior is empirical and per-model, and
must be configured through the model-profile seam keyed by model id — never assumed, never hardcoded in node or
agent code.

## Decision

Register a profile for `ibm-granite/granite-4.1-8b` in `models/profiles.py` with
`structured_method="json_schema"`. This is a method-only override applied at the single point where the
structured runnable is built (`build_structured`). It affects *only* forced structured calls through the
LangChain seam. It does **not** touch:

- free-text / reasoning calls (they never set a method), or
- the docling-graph KG extraction path, which uses its own `json_object` structured mode (ADR-0033 /
  kg-extraction-recipe), not this seam.

No thinking-disable `extra_body` is needed for granite (unlike Gemma/Qwen, ADR-0006/0032); the method swap
alone is sufficient.

## Consequences

- The KG-5e LLM function router works, and the adopted `llm_union` router (LLM ∪ LegalBERT top-3) reaches the
  best measured Leg-B routing (recall@20 0.713).
- Provider/method behavior stays in the profile config plus this dated ADR, per the standing seam rule; a
  future granite version or provider change is a one-line profile edit, not a code change.
- `function_calling` remains the default for all other models; only granite is overridden, on evidence.
