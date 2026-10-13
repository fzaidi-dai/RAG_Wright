# RuleWright: moving to rag-wright 0.9

What 0.9.0 (released 2026-10-13: PyPI and GitHub release `0.9.0`) changes for RuleWright. Do the 0.3 to 0.8 guides
first. Nothing here is edited from the engine repo. Section 1 is the one change that can break a product; the rest
are new public routes that let RuleWright delete internal imports and private-attribute reads.

## 1. `ARCADEDB_PORT` is required (PS-38)

**What changed.** `StoreConfig.from_env()` no longer defaults the port to 2480. With `ARCADEDB_PORT` unset it raises
`KeyError` naming the variable, so an unset variable can no longer reach another ArcadeDB instance on the machine
and appear to work (the way RuleWright reached the engine's instance before the move).

**What to do in RuleWright.** Nothing for the product itself: `.env` and `.env.example` set it, and your typed
`Settings` default to 2481. Your four MCP servers (`src/rulewright/packs/*/mcp/*_server.py`) build their store with
`StoreConfig.from_env()`, so they now fail loudly in an environment without the variable instead of reaching 2480.
While you are there: they default their database to `ragwright_cuad_full` / `ragwright_compliance`
(`QA_DB` / `COMPLIANCE_DB` unset), engine database names that do not exist on your instance. Unset, a server would
create an empty database and answer "no results". Default them to your own database names and open with
`create=False`.

## 2. An agent's chat model from a profile (PS-36, ADR-0139)

**What changed.** `agent_chat_model(model=None, *, temperature=0.0, **overrides)` on `rag_wright.api` returns the chat
model an agent reasons with, by engine model id (`None`: the `GENERAL` role). The profile decides endpoint, key,
served id and the agent turn's reasoning flag (new profile slot `agent_extra_body`: OpenRouter's `reasoning`,
vLLM's `chat_template_kwargs`). Every call, a subagent's, a streamed one and a tool-bound one included, is recorded
into `measure_usage()` with the provider's real per-call cost (a streamed call keeps the cost LangChain drops) and
traced as one Langfuse generation when tracing is on; a failed call closes its generation with the error type.
Retries are the caller's: pass `max_retries=0` and keep `ModelRetryMiddleware` as the one retry layer.

**What to do in RuleWright.** This is the `models.py` stage of the harness separation. `agent_chat_model` replaces
`DEFAULT_AGENT_MODEL`, `is_openrouter()`, `_init_kwargs` and the `ProviderProfile` registration, and covers what
`CostTap` and `GenerationRecorder` do (check your `cost_basis` tag against it: a call with no reported cost is
counted in `calls_without_cost`, never as $0). What stays yours: the general-purpose-subagent harness profile, and
any endpoint pin (ADR-0125 removed the hard provider pin from the engine's Qwen profiles; a pin is a profile choice to
raise with the engine, not code in the product).

## 3. Removing a database (PS-37)

**What changed.** `drop_workspace(config, *, corpus) -> bool` drops a named database (it refuses a blank name and
cannot be undone) and forgets its cached handle; `temporary_workspace(config, *, prefix="tmp")` opens a fresh
`<prefix>_<12 hex>` database and drops it when the block exits, also when it raises.

**What to do in RuleWright.** Already in use (your probes and measurements). Nothing further.

## 4. New public routes for what RuleWright still reaches internally (PS-38)

| You have | Use instead |
|---|---|
| `resources._store` in your pack's capability factories (`packs/contracts/subgraphs/{contract_ingestion_pipeline,relational_qa,typed_property_retrieval,intra_document_qa}.py`, `packs/compliance/subgraphs/{compliance_ingestion,compliance_check}.py`) | `pack_store(resources, builder)`: a positional-store builder directly (`pack_store(resources, aproduction_document_ingest, cache_dir=...)`), a keyword one through a lambda (`pack_store(resources, lambda store: production_relational_qa(store=store, ...))`). The engine's reference pack made exactly these edits in PS-38. |
| `resources._embedder` | `workspace_embedder(resources)` (`rag_wright.pack_sdk`): the workspace's embedder, from its embedding profile |
| `resources._config` | `resources.config` (read-only) |
| `rag_wright.capabilities.remote_encoders.query_embedder` (`engine/seam.py`, `engine/resources.py`) | **Corrected (PS-39, needs the release after 0.9.0):** `ws.embedder` where you hold a workspace, `query_embedder()` from `rag_wright.api` otherwise. This row first pointed the seam at `rag_wright.pack_sdk`, a tier a product seam may not import; your seam guard was right to refuse it. `rag_wright.api.query_embedder` is the same function as before, so the process-wide sites are a path change only; your packs' `from rag_wright.pack_sdk import query_embedder` keeps working. |
| `rag_wright.capabilities.answer_generator.{answer_model_for, agenerate_answer, EvidenceItem}` | `rag_wright.pack_sdk.answer_model_for`, `rag_wright.pack_sdk.agenerate_answer_with_model`, `rag_wright.api.EvidenceItem` |
| `rag_wright.models.profiles.model_for` | `rag_wright.pack_sdk.model_for`, or `ws.model_id(role)` |
| `rag_wright.models.profiles.PROFILES` (`_require_registered_model_server`) | `rag_wright.pack_sdk.profile_for(model_id).base_url`: set on a deployed server's registered profile, `None` on the generic default an unknown id gets |
| an on-premises decision model (Laya) | `register_decision_profile(DecisionModelProfile(...))` from `rag_wright.api`, then select it by id |
| your own chat endpoint | `register_model_profile(ModelProfile(...))` from `rag_wright.api` |
| publishing your capabilities' ARD manifests | `publish(slug, root=...)` / `publish_all(root=...)` from `rag_wright.api` (slugs must be canonical: `register_canonical_slugs`) |
| typed I/O or a skill runtime on a manifest | `CapabilityInterface` / `SkillRuntime` from `rag_wright.pack_sdk` |

After these, `STILL_INTERNAL` in `tests/test_engine_api_boundary.py` can be emptied, and a pack-wide AST check like
the engine's `tests/api/test_public_gaps_ps38.py::test_the_reference_pack_reads_no_private_workspace_attribute`
keeps the private reads from coming back.

## 5. Revised skills ship in this wheel

The engine skills in `rag_wright/.agents/skills/` were reviewed for a new product (2026-10-13): the consumer skill
(store ownership, databases, agents, PDFs, engine requests), `authoring-a-capability` (the public routes above),
`building-an-ingestion-capability` (record tables vs forms, `table_labels`, how embedded files find their row,
nesting, stable ids), and smaller fixes elsewhere. Your links follow the installed version; re-run the link step if
you skip it on upgrade.
