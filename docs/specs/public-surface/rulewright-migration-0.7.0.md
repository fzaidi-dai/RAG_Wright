# RuleWright: moving to rag-wright 0.7 (the model server's cold start)

What the release after 0.6.0 changes for RuleWright (PS-20, ADR-0132; PS-22, ADR-0133). Do the 0.3 to 0.6 guides first. Nothing here
is edited from the engine repo.

**Short version.** Nothing breaks; the public API is unchanged. A scaled-down server now usually comes back in
seconds instead of minutes, and two operational facts change. Model calls get a total time limit that follows your
timeout setting and a loop guard; check how RuleWright scores a failed call (section 3).

## 1. What changed

- The Qwen server is restored from a Modal GPU memory snapshot. A restore is ready about 13 s after its container
  starts; Modal still boots some containers in full and builds a new snapshot (4 to 9 minutes), mostly the first
  few after a deploy and now and then later.
- The server's URL is unchanged (`https://<workspace>--<app>-serve.modal.run`), so `amodel_server`, any stored
  `VLLM_BASE_URL` and existing model ids keep working. Redeploy once to move a running server to the new script.
- Only `/v1/*` and `/health` are reachable on that URL; every other path returns 404.

## 2. What to do in RuleWright

- **Do not stop a server to save money between uses if you want fast cold starts.** Stopping it discards its
  snapshots, and the next deploy pays the snapshot-building boots again. Scaling to zero after 10 idle minutes
  costs nothing and keeps them. Redeploying keeps them too.
- **Keep the startup timeout sized for the slow case.** `await_model_server(..., timeout_s=...)` must still cover a
  snapshot-building boot (about 9 minutes worst case seen), or handle `TimeoutError` as the 0.6 guide says.
- **Where a user must not wait,** keep the server busy or warm during working hours rather than relying on the
  snapshot: an occasional cold start still takes minutes.

## 3. The model-call deadline and reasoning loops (PS-22, ADR-0133)

- **What changed.** One model call's total time limit, across its retries, used to be a fixed 180 s whatever
  `RAG_STRUCTURED_TIMEOUT_S` said. It now follows that setting (three tries plus backoff, never under 180 s), and
  `RAG_MODEL_DEADLINE_S` sets it directly. The thinking Qwen profiles also cap a structured call at 8,192 output
  tokens, so a reasoning loop ends as a failed parse that the engine retries, instead of running into the limit; a
  `max_tokens` you pass yourself still wins.
- **What to do.** Find every place RuleWright catches a failed model call and turns it into a result (for example
  "not addressed" in a compliance verdict). A `ModelCallTimeout`, or any exception, there should be recorded as an
  error or dead-lettered, not reported as a verdict: a loop or a timeout is not evidence. For reasoning-on bulk
  jobs, raise `RAG_STRUCTURED_TIMEOUT_S` (or set `RAG_MODEL_DEADLINE_S`); the total now follows.
- **Keep temperature 0** for structured calls: Qwen's recommended sampling did not remove loops and made verdicts
  less repeatable (ADR-0133).

## 4. Verify

`examples/model_server.py deploy` in the engine checkout exercises the same path (deploy, one question metered on
the server's model id, stop). The engine's live gate for this change is recorded in ADR-0132.
