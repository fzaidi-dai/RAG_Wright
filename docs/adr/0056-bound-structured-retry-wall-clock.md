# ADR-0056: Bound the structured-call retry to one layer (no stacked retry budgets)

> **Status: SUPERSEDED (ADR-0057).** Retry bounding is subsumed by the async engine architecture (ADR-0057).


Status: Accepted
Date: 2026-08-18
Component: `models/seam.py` (`build_structured`), surfaced through `spans/clause_function_classifier.py`
Related: ADR-0006 (retry configured at the single seam), ADR-0001 (langchain_openai grounding), the
`dg_extraction` per-call cap precedent (`_DEFAULT_TIMEOUT_S=90`, `_DEFAULT_MAX_RETRIES=1`). Raised by:
RuleWright (product), engine issue 0003.

## Context

`build_structured` stacked two independent retry layers:

- `build_model` set the OpenAI SDK client's `max_retries=6` with a per-request `timeout=120s`.
- `build_structured` then wrapped `.with_retry(stop_after_attempt=3)` **around** that client.

Each layer is individually reasonable, but they compose multiplicatively: one logical structured call had a
worst case of `120 x 6 x 3 = 2160s = 36 minutes`. RuleWright reproduced a single-document ingest stalling 515s
and counting at 0% CPU with idle keep-alive sockets — externally indistinguishable from a deadlock, but in fact
a bounded wait retried into an unbounded-looking one. The blocked frame (a `faulthandler` dump) was a socket
read under the `.with_retry` layer, not a lock.

Two things made it invisible rather than merely slow: the retries were **silent** (nothing logged between
attempts), and the correct **graceful degradation already existed but could not fire** —
`clause_function_classifier._call` catches every exception and leaves that sub-batch's spans empty, which is
right, but the exception it waits for is the timeout at the bottom of the 36-minute stack. This makes the
product's single-document ingest gate (NFR-1) unmeasurable: the median is ~11s, the tail is unbounded.

Only the structured path hits this: plain `build_model` (free-text) has a single SDK layer. The `dg_extraction`
path already has an analogous per-call cap (300s->90s, `max_retries=1`); the classifier reaches `build_structured`
and inherited the 120x6x3 composition instead.

## Decision

Collapse the structured path to **one bounded retry layer**, so the worst-case wall clock of a logical
structured call is `timeout x attempts` — a single product — rather than `timeout x sdk_retries x attempts`.

- **Disable the SDK's own retry loop for structured calls:** `build_structured` builds its client with
  `max_retries=0` and a tighter per-request `timeout` (`_STRUCTURED_TIMEOUT_S = 60s`; a hang fails at 60s, not
  120s). Plain `build_model` is unchanged (`max_retries=6`, `timeout=120s`) — free-text paths keep their SDK
  resilience.
- **Make the LangChain `.with_retry` the SOLE retry layer**, broadened from `(ValueError,)` to the full
  transient set the SDK would otherwise have retried — `ValueError` (the OpenRouter-504 "operation was aborted"
  surfaced as a plain ValueError) plus `APITimeoutError`, `APIConnectionError`, `RateLimitError`,
  `InternalServerError`. Bounded by `stop_after_attempt = _STRUCTURED_RETRY_ATTEMPTS = 3`.
- **Worst case: `60 x 3 = 180s = 3 min`** (down from ~36 min), so the classifier's empty-sub-batch degrade is
  reachable in ~3 minutes.
- **Log every failed attempt** (`_with_bounded_retry`: elapsed + exception type) so a retrying call is visibly
  working rather than a silent hang — removing most of the diagnostic cost the issue flagged.

### Trade-off (accepted)

Disabling the SDK loop loses its `Retry-After` (429) header handling. The tenacity **exponential-jitter
backoff** on `.with_retry` (`wait_exponential_jitter=True`, already used) substitutes for it: it spaces out
429/5xx retries in the SDK's place. This is a deliberate, documented substitution — the backoff is a
general-purpose spacing that is sufficient here, and the alternative (keeping the SDK loop) reintroduces the
multiplication this ADR exists to remove.

### Plain (free-text) path — bounded too, same issue class

Plain `build_model` (free-text: generation, reasoning, the ADR-0045 tag-parse structured path, vision-to-text,
RLM chunk/synthesis) has only the single SDK retry layer, but at `max_retries=6, timeout=120s` its worst case
for a persistent upstream stall was `120 x (6+1) = 840s ≈ 14 min` — the same idle-socket hang, just not
multiplied. It is not a narrow path, so it is bounded in the same change:

- `_MAX_RETRIES` 6 -> **2** (back to the OpenAI SDK's own default; the over-generous 6 was the main lever).
- `_TIMEOUT_S` 120 -> **90** (a hang fails at 90s; kept generous for legitimately longer free-text prose).
- Worst case -> `90 x (2+1) = 270s ≈ 4.5 min`.

Unlike the structured path there is **no trade-off**: the SDK loop stays (just shorter), so its exponential
backoff and `Retry-After` (429) handling are preserved.

## Consequences

- Structured-call worst case is bounded and predictable (`_STRUCTURED_TIMEOUT_S x _STRUCTURED_RETRY_ATTEMPTS`),
  guarded by a test so a future constant bump is a conscious change.
- Retries are visible in logs; an apparent hang is now a diagnosable retrying call.
- The graceful degradation (empty sub-batch, document still ingests) fires within ~3 min instead of ~36.
- Both paths are now bounded: structured `60 x 3 = 180s` (~3 min), plain free-text `90 x 3 = 270s` (~4.5 min),
  each guarded by a test so a future constant bump is a conscious change.
- No API/contract change; `build_structured` still returns a `Runnable` invoked the same way. The returned
  object is now a bounded-retry wrapper around the structured handle, not the handle itself.

## Verification

Hermetic tests (`tests/models/test_seam_retry.py`, no network): a transient is retried up to exactly the bounded
attempt count (not the old 6x3); a non-transient is surfaced immediately without retry; each failed attempt is
logged; a success passes through; and both worst-case bounds are guarded (structured `timeout x attempts <= 200s`,
plain `timeout x (max_retries + 1) <= 300s`).
`tests/models/test_profile_seam.py` additionally asserts the structured base client is built with `max_retries=0`
and the structured timeout (the SDK loop is off). Models + foundation + spans + capabilities + subgraphs suites
pass; ruff clean. RuleWright's `repro_ingest_deadlock.py` (its watchdog dumps stacks and lets the call continue,
so a self-recovering stall reports its true duration) is the product-side end-to-end check.
