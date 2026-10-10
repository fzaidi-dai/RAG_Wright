# ADR-0130: A product deploys the self-hosted model server from the engine and uses it like any model

**Status:** accepted · **Date:** 2026-10-10 · **Related:** ADR-0110 (the locked Qwen config), ADR-0109 (cold start),
ADR-0100 (model strings carry their backend), PS-14 (per-workspace models)

## Context

The self-hosted Qwen server was stood up by hand: the `qwen-vllm-modal` skill's `modal deploy` command, then
`VLLM_BASE_URL` / `VLLM_API_KEY` in the environment. A product could not deploy it through the engine, and every
vLLM profile read one process-wide endpoint, so two servers (one per tenant) needed custom profiles and variables.

## Decision

1. `ModelServerSpec` describes one server; its defaults are the skill's locked Config B (ADR-0110), and the
   concurrency target (`max_num_seqs`) has no default because ADR-0110 leaves it to the deployment.
2. `adeploy_model_server(spec)` runs the deploy script shipped with the skill (Modal's own CLI, the documented
   command), waits for `/health` with progress, and registers a model profile for the server: the
   `qwen3.8-27b-modal` profile's Qwen settings, the server's own endpoint, and the API-key variable the spec names.
   `amodel_server(spec)` reconnects without redeploying; `stop_model_server(name)` stops it.
3. A model profile can carry an explicit `base_url`, which wins over its `base_url_env`; `register_model_profile`
   adds one at run time. So a deployed server is just another model id (`qwen3.8-27b-modal@<name>`) for
   `EngineConfig.models` or any `model=` argument.
4. The API key has no default: deploying refuses when the named variable is unset. `modal` is an optional extra
   (`rag-wright[modal]`).

## Consequences

- Live (2026-10-10): deploy through the API, ready after 226 s, one answer through a workspace metered on the
  server's model id, stopped (the app no longer resolves); 239 s of A100 in all.
- Cold start remains a separate task (PS-20, ADR-0109); the readiness wait reports it, it does not shorten it.
- Registered server profiles are process-wide, like every profile; a restarted process reconnects with
  `amodel_server`.
