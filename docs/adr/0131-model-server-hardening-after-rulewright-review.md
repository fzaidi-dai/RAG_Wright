# ADR-0131: No default server key anywhere; a model's concurrency ceiling travels with its id

**Status:** accepted · **Date:** 2026-10-11 · **Related:** ADR-0130 (deploy the model server from the engine),
ADR-0110 (`max_num_seqs` is the concurrency target), PS-21 (RuleWright's model-server review)

## Context

RuleWright's first use of the model-server API found five gaps. The deploy script (and six dev scripts) fell back to
a key committed in the repo when `VLLM_API_KEY` was unset, and Modal's web endpoint is public and unauthenticated, so
the raw `modal deploy` path the skill documents put a billing A100 behind a published key; the seam sent the same
default. A product could not tell whether its load fits the server, because the engine's own ingestion fan-out
multiplies with the product's and nothing compared it to `max_num_seqs`. Cache state, a not-deployed error and the
startup timeout were also unclear.

## Decision

1. No default key. Every Modal vLLM server script raises on an unset `VLLM_API_KEY` (and bakes it into the image
   env, which the container reads); the seam's `vllm` backend raises instead of sending a default. A test fails if
   the old default string reappears under `src/`, `scripts/` or `docs/*.md`. Breaking, approved.
2. `ModelProfile.max_concurrency` (optional; approved schema change): the most requests the model's server batches
   at once. A deployed server registers its `max_num_seqs` there. `aingest` warns (a `RuntimeWarning` and a progress
   line) when `min(document_concurrency, documents) x extract_concurrency` exceeds the ceiling of any model the
   workspace resolves. It cannot see a product's other parallel runs; the docs give the arithmetic.
3. `amodel_server_status(spec)`: deployed, running, healthy, weights cached. It asks `/health` only of a running
   container (a request to a scaled-down server starts a cold start) and reads the weights Volume (`rw-hf-cache`,
   pinned to the script by a test).
4. `ModelServerNotDeployed` (a `RuntimeError`) replaces Modal's raw `NotFoundError`; `astop_model_server` is the async
   stop.

## Consequences

- A hand deploy or a client without `VLLM_API_KEY` now fails with a message naming the variable.
- The ingestion warning covers the generic pipeline's extractor fan-out; other callers (a pack's own concurrency,
  queries) are covered by the documented arithmetic, not a check.
