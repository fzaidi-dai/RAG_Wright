# ADR-0045: Structured output via client-side XML-tag parsing, app-wide and LLM-agnostic

Date: 2026-08-08
Status: Accepted

## Context

Every LLM call that must return a typed value has, until now, gone through the model-profile seam's
`build_structured` (ADR-0006, ADR-0034): server-side **guided decoding** (`with_structured_output` ->
`response_format` json_schema / json_mode / function_calling), where the serving stack constrains the decode to
a grammar. The empirical-per-model profile machinery exists precisely because this path is fragile: it must be
tuned per model id (function_calling emits empty on Granite; json_schema is needed there, ADR-0034), and open
models reject a forced schema while in reasoning mode (ADR-0006).

Finalizing the product substrate (self-hosted Gemma 4 on vLLM, ADR-0039) exposed a harder failure: server-side
guided decoding is **not portable across serving stacks at all**.

- On self-hosted **Gemma 4 / vLLM**, grammar-constrained decoding (json_schema/json_mode -> xgrammar) **runs
  away to `max_model_len`** instead of terminating; function_calling needs `--tool-call-parser gemma4`, which
  has a concurrency `<pad>` bug (vllm#39392). Plain **free-text generation terminates cleanly** on the same
  stack.
- On **OpenRouter pinned to Cerebras**, a json_schema call costs **~60s**, while the identical free-text call is
  **~1.5s**.

So the one thing that is fast and correct on *every* stack we use is plain free-text generation. The structure
we need is small and shallow (query-side schemas are flat: scalars, enums, `str | None`, `list[<scalar>]`).

An earlier, capability-local version of this idea already shipped for generation only (`answer_generator`'s
`TaggedFreeTextAnswerModel`, gated by a `client_side_structured` profile flag + a `RAG_CLIENT_SIDE_STRUCTURED`
env force). This ADR generalizes it into the app's default mechanism.

We also considered client-side **JSON** parsing (free-text "reply in JSON" + `json.loads`). Rejected: the
load-bearing field is long **legal prose** full of quotes, brackets, and newlines, which is exactly what breaks
JSON string escaping. A tagged body needs no escaping.

## Decision

**Structured output is obtained by client-side XML-tag parsing, not server-side guided decoding, everywhere the
query side needs a typed value.** The model answers in free text using light `<field>...</field>` tags; we parse
those tags into the Pydantic contract on our side, with bounded retries on a validation miss.

- **One reusable seam:** `models/tag_structured.py::build_tag_structured(model_id, schema)` is a **drop-in for
  `models/seam.py::build_structured`** — same `(model_id, schema) -> runnable`, whose `.invoke(prompt)` returns
  a validated `schema` instance. `.invoke` accepts a plain string **or** a LangChain message sequence
  (`[SystemMessage, HumanMessage]`), appending the tag instructions as a trailing human turn, so it drops in
  wherever `build_structured` was used. A caller switches mechanism by switching the factory.
- **Scope built now (query side):** flat schemas — scalars (str/int/float/bool), enum/Literal, `str | None`,
  and `list[<scalar>]`. That covers every query-side caller. `list[<BaseModel>]` (the one nested case,
  ingestion's `PropertyExtraction`) is a **documented extension point**, raising `NotImplementedError` until a
  later task builds it.
- **Query-side callers routed:** the query function classifier, query understanding (the step-2 emit; the reason
  step stays free-text), highlight field-extract, the OKF reader relevance judge, and answer generation.
  Generation is now **universally** the tag path (`answer_model_for` always returns `TaggedFreeTextAnswerModel`);
  the `client_side_structured` profile flag and the `RAG_CLIENT_SIDE_STRUCTURED` env force are no longer
  consulted for generation. `SeamAnswerModel` remains for an explicit direct-construct opt-in.
- **Degrade contract preserved:** every caller's existing "a failed structured emit degrades gracefully, never a
  hard error" contract is kept — a persistent client-side parse failure is caught and mapped to that caller's
  degrade value (`[]` for the classifier, out-of-taxonomy/low-confidence for understanding, no extracted value
  for highlight, an abstention for generation).

This **supersedes**, for the query side, the reliance on the per-model guided-decoding profiles of ADR-0006 and
ADR-0034: the structured method no longer has to be tuned per model, because we never ask the server to
constrain the decode. The `ModelProfile` structured-method fields remain valid for any caller that still uses
`build_structured` directly (ingestion, below), and the profiles stay the record of empirical provider flags.

## Consequences

- **LLM-agnostic:** the same query-side code runs on self-hosted Gemma 4 / vLLM, OpenRouter (any provider,
  including Cerebras), or a local open model, with no per-model structured-output tuning. Cerebras re-tests after
  each routing change confirmed correct typed outputs at ~0.5–3s/call (vs ~60s for server-side json_schema).
- **No BERT classifier on the query side:** the LLM classifier is the routing mechanism (it was already; this
  keeps it), and it now needs no GPU-resident guided-decoding support — one more reason the query side needs no
  dedicated GPU.
- **Parsing is our responsibility, not the server's:** correctness now depends on the tag instructions + parser,
  covered by hermetic tests in `tests/models/test_tag_structured.py` (coercion, absent-default, list split,
  case-insensitivity, message-list, retry-on-validation-error, nested-raises).
- **Ingestion is not changed by this ADR.** Ingestion still calls `build_structured` (server-side) for now.
  Moving ingestion to Gemma 4 + tag-parse and A/B-ing it against the DeepSeek server-side path is a later task,
  and needs the `list[BaseModel]` nested extension first.
- **Escape hatch retained:** `SeamAnswerModel` / `build_structured` are still importable and constructable
  directly for a deliberate server-side comparison; they are just no longer the default anywhere on the query
  side.
