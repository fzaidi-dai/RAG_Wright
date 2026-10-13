# RuleWright: moving to rag-wright 0.10

What 0.10.0 (released 2026-10-14: PyPI and GitHub release `0.10.0`) changes for RuleWright. Do the 0.9
guide first. Nothing here is edited from the engine repo. Section 1 is the big one: the harness is now the engine's.

## 1. The agent harness is `rag_wright.harness` (PS-32, ADR-0139)

**What changed.** The engine took in your `harness-handover/` (25 modules, regenerated 2026-10-14) as
`rag_wright.harness`, its third public tier. It declares `__all__` (42 names; reference `docs/api/harness.md`),
reaches the engine only through `rag_wright.api` (an import contract), and nothing else in the engine imports it.
Its acceptance ran in the engine suite: your greenfield conformance test (7/7), your genericity guard (all three
axes, plus a plant-verified import walk), your accounting and thinking tests, the engine's vocabulary guard, and a
live interrupt-and-resume run of the fabric product on `qwen3.8-27b-modal-or`.

**What to do in RuleWright.**

1. **Delete `src/rulewright/harness/` and import from `rag_wright.harness`.** Module paths are unchanged
   (`rulewright.harness.runs` is `rag_wright.harness.runs`); prefer the package's public names.
2. **Import the definition types from the engine.** `AgentDefinition`, `AgentPrompts` and the `ToolSurface`
   protocol are the tier's now (`rag_wright.harness`): delete the dataclasses in `rulewright/agent/definition.py` and
   `rulewright/agent/prompts.py` and keep only your values (`PLANNING`, `rulewright_agent()`,
   `rulewright_prompts()`). Your `ToolSurface` class satisfies the protocol as it stands. The fields are unchanged.
3. **Two worker prompt fragments are public**: `_CANNOT_ASK` and `_DO_WHAT_WAS_ASKED` are `CANNOT_ASK` and
   `DO_WHAT_WAS_ASKED`.
4. **Tracing is the engine's configuration.** The harness no longer reads `settings.rulewright_trace_level` or the
   Langfuse fields on your settings: it uses `RAG_TRACE_LEVEL` and `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` /
   `LANGFUSE_HOST` from the environment, the same switch every engine generation obeys. Export them from your
   settings at startup, the way `export_model_env` exports the model credentials. `public_key_of` and
   `_callback_handler` are gone. The harness builds its model with `agent_chat_model(..., trace=False)`, so each
   agent turn is one generation (labelled `agent-llm`, grouped under the run id), never two.
5. **The model.** `rag_wright.harness.agent_chat_model(settings, model_id)` keeps its signature and calls
   `rag_wright.api.agent_chat_model` directly; your `engine.seam.agent_chat_model` wrapper is no longer on the
   harness's path, so `export_model_env(settings)` must run before the first run (startup).
6. **The question encoder** is `rag_wright.api.query_embedder()`, built once per process inside the harness;
   `question_encoder_for_process` is no longer used by it. The off switch is `RAG_QUESTION_ENCODER=off` (was
   `RULEWRIGHT_QUESTION_ENCODER`).
7. **`GenerationRecorder` was fixed** (a defect found at adoption, also present in your copy): it closed every
   open span as "abandoned" whenever another call started or ended, so with parallel workers an in-flight call was
   mislabelled and its real end emitted a second, zero-duration generation. Now only a retry of the same model turn
   (same parent run) abandons an earlier attempt, and a failed call is closed as an error (`on_llm_error`). Expect
   fewer "abandoned" rows in Langfuse than before, and no zero-duration duplicates.
8. **Comments.** Eight comment lines that named contract vocabulary were reworded for the engine's vocabulary guard;
   no code changed with them.
9. **Your 33 product-composed harness tests stay yours.** They test the substrate through your `AgentContext`,
   tools and prompts, so they belong in RuleWright; point them at `rag_wright.harness`. The engine runs the two
   substrate-only ones and the acceptance files.
10. **Your surface guard.** Set `[harness] published = true` and pin `surface` to `rag_wright.harness.__all__`.

## 2. The embedder on `rag_wright.api` (PS-39)

`ws.embedder` (read-only, from the workspace's embedding profile) and `query_embedder()` on `rag_wright.api`. Your six
sites: the five process-wide ones are a path change (`rag_wright.api.query_embedder` is the same function), and the
builder that holds a workspace uses `ws.embedder`. Take them out of `PENDING_RELEASE` when the floor moves.

## 3. Tracing helpers moved to `rag_wright.api` (PS-32)

`tracing_on`, `trace_level`, `start_generation`, `finish_generation` and `flush_tracing` are public on
`rag_wright.api`. The first three left `rag_wright.pack_sdk.__all__` (the tiers stay disjoint) but stay importable
there, so your `packs/contracts/capabilities/dg_extraction.py` keeps working; move it to `rag_wright.api` when
convenient (the engine's reference pack did).

## 4. Dependencies

The engine now declares `langchain` and `langchain-core` (floors at the versions it was tested with), which the
harness imports; nothing new is installed.
