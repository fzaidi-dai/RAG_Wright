# RuleWright: moving to rag-wright 0.6 (the model-server review)

The engine's answer to RuleWright's model-server review (2026-10-11), and what changes for RuleWright. Do the 0.3,
0.4 and 0.5 guides first. Nothing here is edited from the engine repo. The design is ADR-0131.

**Short version.** One break: nothing defaults `VLLM_API_KEY` any more. Everything else is new API and docs that
answer the five points raised.

## 1. The one break: no default server key

- The deploy script (the skill's raw `modal deploy` path too) refuses to run without `VLLM_API_KEY`, and so does the
  model seam's `vllm` backend: a call to a self-hosted server with the variable unset raises
  `ValueError("set VLLM_API_KEY ...")` instead of sending `rw-vllm-dev-key`. A profile's own `api_key_env` is
  required the same way.
- RuleWright already deploys with a real key, so the only thing to check is that every process that calls the
  server (services, workers, scripts, tests that reach a live server) has `VLLM_API_KEY` set. Hermetic tests that set
  `RAG_SERVING=vllm` need a dummy key in the environment.
- RuleWright's product-side guards stay valid; the engine's own documented path is now guarded too (point 1 of the
  review).

## 2. The answers to the review

| Review point | What the engine did |
|---|---|
| 1. Published default key | Removed everywhere (above), with a test that fails if it returns. |
| 2. Concurrency ceiling not computable | `docs/configuration.md` "Concurrency: the engine's fan-out multiplies with yours" gives the arithmetic. A server's profile now carries its ceiling (`ModelProfile.max_concurrency`, from `max_num_seqs`), and `aingest` warns before it starts when its own peak, `min(document_concurrency, documents) x extract_concurrency`, can exceed it. Correction to the review: `RAG_EXTRACT_WORKERS` is a process-wide cap of 32 threads on the reference pack's docling-graph extraction, not a per-document fan-out; the per-document knob there is `CLAUSE_CONCURRENCY` (8). The warning cannot see RuleWright's own parallel runs, so their sum is still yours to keep below `max_num_seqs`. |
| 3. Cache state invisible | `await amodel_server_status(spec)` returns `deployed`, `running` (a container is up, billing), `healthy` and `weights_cached`. It never wakes a scaled-down server (it asks `/health` only of a running container). The Volumes (`rw-hf-cache` for weights, `rw-vllm-serve-compile-cache`) are documented. |
| 4. Modal's error leaks | `amodel_server` raises `ModelServerNotDeployed` (a `RuntimeError`) naming the server and saying to deploy it. |
| 5. 25-minute startup block | Docs on choosing `await_model_server(..., timeout_s=...)` for startup and what to do on `TimeoutError` (fail readiness, or start on a provider-served model id). The example uses 600 s. |
| `stop_model_server` is sync | `astop_model_server` added; the sync one stays. |

## 3. What to change in RuleWright (optional except section 1)

- Replace the `asyncio.to_thread(stop_model_server, ...)` workaround with `await astop_model_server(name)`.
- Catch `ModelServerNotDeployed` at worker startup instead of Modal's `NotFoundError`.
- Give the worker's `await_model_server` a startup-sized `timeout_s`.
- Use `amodel_server_status` in the pre-flight instead of reading `modal app list`.

## 4. Verify

The engine's `examples/model_server.py` (read it from the engine checkout) now prints the status before deploying
and confirms the server is gone with `amodel_server_status` after stopping; `reconnect` fails with the
`ModelServerNotDeployed` message when nothing is deployed.
