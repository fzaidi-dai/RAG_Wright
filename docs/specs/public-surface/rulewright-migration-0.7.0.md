# RuleWright: moving to rag-wright 0.7 (the model server's cold start)

What the release after 0.6.0 changes for RuleWright (PS-20, ADR-0132). Do the 0.3 to 0.6 guides first. Nothing here
is edited from the engine repo.

**Short version.** Nothing breaks; the public API is unchanged. A scaled-down server now usually comes back in
seconds instead of minutes, and two operational facts change.

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

## 3. Verify

`examples/model_server.py deploy` in the engine checkout exercises the same path (deploy, one question metered on
the server's model id, stop). The engine's live gate for this change is recorded in ADR-0132.
