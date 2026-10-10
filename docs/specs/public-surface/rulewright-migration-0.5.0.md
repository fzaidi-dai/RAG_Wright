# RuleWright: moving to rag-wright 0.5 (its own Qwen server)

What the release after 0.4.0 adds for RuleWright, written from a read-only scan of the RuleWright repo
(2026-10-10). Do the 0.3 and 0.4 guides first. Nothing here is edited from the engine repo.

**Short version.** Nothing breaks. RuleWright can now deploy its own Qwen server from the engine and point any of
its model settings at it by changing a string. The scikit-learn floor it asked for is in.

## 1. Nothing breaks

- **scikit-learn.** The engine now requires `scikit-learn>=1.9.1` (RuleWright's request, ADR-0129); RuleWright
  already declares the same floor.
- **Model profiles** can carry an explicit endpoint. Existing profiles have none, so every model RuleWright uses
  today resolves exactly as before.

## 2. Running on its own Qwen server (optional)

Today RuleWright's model settings are model-id strings: `rulewright_answer_model`, `rulewright_query_model` and
`rulewright_ingest_model`, all `qwen3.8-27b-modal-or` (Qwen through OpenRouter). Its `config.py` already notes that
pointing at its own server "is a string change here, not a code change". The engine now makes that string come from
a server it deploys:

1. **One-time setup.** `uv add 'rag-wright[modal]'`, `uv run modal token new`, and a real `VLLM_API_KEY` in its
   settings (there is no default key; the server is deployed with it).
2. **Deploy once,** from an operator script or a startup job, with the locked setup of the `qwen-vllm-modal` skill:
   `server = await adeploy_model_server(ModelServerSpec(name="rulewright-qwen", max_num_seqs=<target>))`.
   `max_num_seqs` is RuleWright's real concurrency target (ADR-0110); keep its client concurrency below it.
3. **Every service process reconnects at startup** without redeploying, then waits for readiness:
   `server = await amodel_server(spec)`, then `await await_model_server(server)`.
4. **Use `server.model_id`** (`qwen3.8-27b-modal@rulewright-qwen`) as the value of those three settings, or in a
   customer workspace's `EngineConfig.models` (0.4 guide, section 2). It is an ordinary model id: the seam, answer
   generation and the ported pack's docling-graph extraction (`default_extraction_model`, which resolves through
   the profile) all reach the server's own endpoint. `VLLM_BASE_URL` and `RAG_SERVING` are not needed for it.
5. **A server per customer,** if wanted: one `ModelServerSpec` name per customer gives one server and one model id
   each, used in that customer's `EngineConfig.models`.

Two cautions. The ported pack's `vllm_model()` helper (`packs/contracts/capabilities/dg_extraction.py`) reads
`VLLM_BASE_URL` directly and so cannot see a deployed server's endpoint; nothing calls it, so do not start using it.
And a server scales to zero after 10 idle minutes: the next request waits through a cold start (minutes), so call
`await_model_server` before sending traffic; `stop_model_server(name)` removes it entirely.

The engine's full walk-through is `docs/configuration.md`, "A self-hosted model server"; the design is ADR-0130.

## 3. Verify

Run RuleWright's suite against the engine checkout. If it moves a setting to a server: deploy (or reconnect), run
one question inside `measure_usage()`, and check the call was metered on the server's model id; then stop the
server if it was a test deployment.

The engine's `examples/model_server.py` is that check, written against the public API only, and the place to start
(read it from the engine checkout; it is not in the wheel):

- `uv run python examples/model_server.py deploy` brings a server up (`adeploy_model_server`), opens a workspace
  whose `general` role is `server.model_id`, asks one question inside `measure_usage()`, checks the call was metered
  on the server's model id, then stops the server (`stop_model_server`, always, even on failure) and confirms it no
  longer resolves.
- `uv run python examples/model_server.py reconnect` is what each RuleWright service process does at startup:
  `amodel_server` then `await_model_server`, one question, and the server left running.
