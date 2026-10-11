# ADR-0132: The model server cold-starts from a Modal GPU memory snapshot, behind a public-route gate

**Status:** accepted · **Date:** 2026-10-11 · **Related:** ADR-0109 (cold-start reduction: direction and the
measurements behind it), ADR-0110 (Config B), ADR-0130/0131 (the model-server API), PS-20

## Context

A cold start of the self-hosted Qwen server (Config B on one A100) took 114-245 s, sometimes more, every time a
scaled-down server got a request. Measured on 2026-10-11 (ADR-0109 addenda): about 30 s of imports in each of vLLM's
two processes, a 12-154 s weight read from the Volume, then compile-cache load and CUDA-graph capture. The levers
that do not remove startup were measured and rejected: the Run:ai streamer, precompiled bytecode, and
`HF_HUB_OFFLINE=1` (it changes vLLM's compile-cache key in every container, so each start recompiles).

## Decision

1. **The server is a Modal class restored from a GPU memory snapshot** (`enable_memory_snapshot` +
   `experimental_options={"enable_gpu_snapshot": True}`, alpha). `@modal.enter(snap=True)` starts vLLM with
   `--enable-sleep-mode`, warms it with two requests and sleeps it at level 1 (weights to host RAM, ~29 GiB, KV
   discarded; the class requests 64 GiB of memory); Modal snapshots the container. `@modal.enter(snap=False)` wakes
   vLLM. `TORCHINDUCTOR_COMPILE_THREADS=1` as Modal requires. Snapshots only at TP=1 (Modal: incompatible with
   several GPUs); TP>1 boots without one.
2. **vLLM listens on 127.0.0.1 only; the public port is `rw_gate.py`**, which forwards `/v1/*` and `/health` and
   returns 404 for everything else. Sleep mode needs `VLLM_SERVER_DEV_MODE=1`, which adds unauthenticated `/sleep`,
   `/wake_up` and `/collective_rpc` routes, and vLLM's `--api-key` guards only `/v1`, `/v2`, `/inference` and
   `/cohere` (v0.30.0 `serve/middleware/authenticate.py`). Modal's published vLLM snapshot examples expose those
   routes publicly; we do not.
3. **The URL does not change**: `@modal.web_server(..., label=f"{APP_NAME}-serve")` keeps
   `https://<workspace>--<app>-serve.modal.run`. The engine looks the server up as
   `modal.Cls.from_name(name, "QwenServer")().serve` (the URL and the container count; Modal's public
   `Function.from_name` refuses a class's `<Class>.*` name). The public API is unchanged.

## Consequences

- **A restore is ready ~13 s after its container starts** (wake ~3.5 s); 21-67 s end to end in our runs, including
  getting a container. One restore took ~112 s (Modal loading the snapshot slowly).
- **Modal decides per container whether to restore or build**, and a build is a full boot plus the snapshot: 4-9
  minutes. It built 3-4 snapshots after each deploy in our runs (one after a GPU preemption) and another later in
  one run. So a scaled-to-zero server usually answers in seconds and occasionally makes a request wait minutes; a
  product that cannot accept that keeps the server warm during working hours.
- **Redeploying keeps the snapshots; stopping the app discards them**, so the next deploy pays the builds again.
- **Alpha-feature risk** stays (Modal labels GPU snapshots alpha). If a restore fails, Modal boots the container in
  full, which is the pre-snapshot behaviour.
- Live gate (engine API only): deploy, a question metered on the server's model id, status while up, public
  `/sleep` and `/collective_rpc` 404, three cold starts, a redeploy then a restored cold start (67 s), stop, status
  not deployed. Prototype runs: restores in 4 of 11 cold starts across two deployments, all after the first builds.
