# TexWright: building T-6 on the harness tier

What TexWright needs to know before it starts T-6, the agent: which of its carded work the engine's harness tier
already ships, and what stays TexWright's. Written from a review of the harness guide as a new product would read it
(PS-69), checked against T-6.0..T-6.7 as carded on 2026-11-19. Nothing here is edited from the engine repo; re-card
T-6 in your own repo.

**Engine floor: `>=0.19.0`.** Every symbol below is on `rag_wright.harness` in 0.19.0. The guidance that names
them (the skill section and the worked example) is on the engine's `main` and reaches the wheel with the next
release; until then read those two from GitHub, not from the copies linked from your installed package.

## 1. Read these first, in this order

1. `examples/harness_quickstart.py`: a whole product on the tier in one file (context, tools, a worker, prompts,
   the definition, one entry point). The engine's tests run it (`tests/harness/test_harness_example.py`), so it is a
   working composition. Run it live with `DESK_AGENT_MODEL=<profile id>`.
2. The `building-an-agent-harness` skill, section **"If you consume the tier"**: a table of each lesson in the
   skill and whether it is yours or the tier's, and the order to build in. Most of the rest of the skill describes
   what the tier already does: read it to know and verify that, not to build it.
3. `docs/harness.md`: how the pieces compose, with section 9 on routing; `docs/api/harness.md` for the names.
4. `rulewright-migration-0.19.0.md` sections 2, 3 and 6: whether to keep routing on, writing the descriptions it
   reads, and the test fixture.

## 2. Re-card these T-6 tasks

| card | what the tier already ships | what stays yours |
|---|---|---|
| **T-6.6** (routing) | The card's premise, that there is "no structural way" to decide between answering and delegating, is stale since 0.19: the tier routes the chat-manager's turn (D1), a verbatim brief (B1) and the orchestrator's workers (D2) with its own decision model (ADR-0146). Nothing to register. | Your direct tools' descriptions, `AgentPrompts.orchestrator_description` and each worker's description (they are what the decision model reads); `RoutingThresholds`, tuned on your own gold sets; or `routing=None` if your deployment must not send messages to the provider |
| **T-6.2b** (your own event contract) | `translate` turns `stream_run`'s items into `RunEvent`s | Your client routing on the event names; nothing to define |
| **T-6.2e** (withholding, caps, retry order) | `withhold` (a tool taken away rather than forbidden in a prompt), the dispatch bound (`DISPATCHES_PER_RUN`), `error_middleware` (the retry and error-level order), all inside `build_chat_agent` / `build_orchestrator` | `retry_on` (your transient errors) and `message_for` on your `AgentDefinition` |
| **T-6.2f** (asking, the subject, durability), in part | `clarification_tool` (the one asking tool), workers that cannot ask (`CANNOT_ASK`: they report `status: "blocked"` with their `need`), `ConfirmTheView` (a changed subject confirmed in code), `MemoryWithoutTenancy` | **The durable checkpointer**, in its own schema, passed as `checkpointer=` (the default is in memory and loses a paused run on restart); your continuation types; the memory `store=` |
| **T-6.0** (where the agent's skills live) | `AgentPrompts.skills` is a `{path: text}` dict | Keep the files anywhere version-controlled and load them into that dict; the paths are what the agent sees |

Also the tier's, in case another card assumed otherwise: tracing, metering and run duration (`accounted_run`,
`harness_tracing_on`, `spend`), and the run's decision ledger (`RecordTheDecisions`).

## 3. What T-6 is, after re-carding

Yours, in the skill's consuming order: pin the tier's surface (`__all__`) with a guard; your context type (`scope`,
`shared_scope`) and the one entry point that builds it from the authenticated principal; your `ToolSurface` over
your operations and the `planning` map; one tier end to end, live; your checkpointer; your rules, decision points
and gold sets; then measure, and tune your descriptions and `RoutingThresholds` on your recorded runs.

## 4. Your tests

A hermetic test that builds a real tier calls the decision model when `OPENROUTER_API_KEY` is loaded. Use the
fixture in `rulewright-migration-0.19.0.md` section 6, or `routing=None` on the definitions your tests build.

## Verify

Your re-carded T-6 names none of section 2's shipped pieces as work to build; the worked example runs live on your
agent model; your first tier runs end to end with the context built from a real principal.
