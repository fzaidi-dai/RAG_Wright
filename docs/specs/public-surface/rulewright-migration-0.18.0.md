# RuleWright: moving to rag-wright 0.18

What 0.18.0 changes for RuleWright: PS-48, the harness's substrate behaviour tests ported from RuleWright onto the
engine's fabric product (ADR-0139 handover, amended per sub-task; ledger row PS-48 in `plan.md`). Do the 0.14 guide
first; 0.15 to 0.17.3 needed nothing of RuleWright beyond what each release note said. Nothing here is edited from
the engine repo.

**`rag_wright.api` and `rag_wright.pack_sdk` did not change.** Everything below is the third tier,
`rag_wright.harness`: two breaking changes (sections 1 and 2), new public names (3), instruments that move out of
your `evals/` (4), behaviour you will see change (5), small edits (6), and the tests you can retire (7).

## 1. Breaking, and needed NOW: the id detector takes your arguments

You run the engine checkout as an editable install, so this already breaks your import. `guessed_an_identifier`
named `read_compliance_check(check_id)`, one product's tool, inside the engine. It is gone; the detector is built
from the tool and argument names you declare:

```python
# src/rulewright/agent/rules.py
from rag_wright.harness import guessed_identifiers

detect=guessed_identifiers({"read_compliance_check": "check_id"}),
```

## 2. Breaking: `RecordTheDecisions` no longer takes `worker_names=`

It was stored and never read: a decision's options come from your declared `DecisionPoint`s (your D2 keeps
`options=(ANY_WORKER, "none")`, which is unaffected). Drop the argument wherever you pass it; today that is only your
harness tests, which section 7 retires.

## 3. Import from `rag_wright.harness`, never from a submodule

The 17 names your agent, evals and scripts reached through harness submodules are now exported. A submodule is
the harness's own and may change without notice; a guard keeps the engine's own docs and stand-in product off them.

| name | was imported from |
|---|---|
| `WhatTheRunDecided`, `RecordTheDecisions` | `harness.decisions` |
| `run_key` | `harness.seen` |
| `build_worker` | `harness.workers` |
| `withhold` | `harness.withhold` |
| `ConfirmTheView` | `harness.confirm` |
| `DISPATCHES_PER_RUN` | `harness.dispatch` |
| `harness_tracing_on` | `harness.tracing` |
| `bridged_tools`, `js_name` | `harness.interpreter` |
| `scope_namespace_of`, `shared_namespace_of` | `harness.memory` |
| `rule_named` | `harness.rules` |
| `chat_manager_tools`, `direct_names`, `orchestrator_direct`, `worker_operations` | `harness.tiers` |

So `from rag_wright.harness.decisions import WhatTheRunDecided` becomes `from rag_wright.harness import
WhatTheRunDecided`, and so on. `python -c "import rag_wright.harness as h; print(sorted(h.__all__))"` lists all 77.

## 4. Your eval instruments are the engine's: delete your copies

| delete | use instead (all from `rag_wright.harness`) | what changed |
|---|---|---|
| `evals/recording.py` | `RecordTheContext`, `every_call`, `spend`, `compare`, `transcript`, `describe` | nothing in the recorded shape; it no longer needs the private `models._as_recorded` |
| `evals/faults.py` | `Fault`, `InjectFaults` | nothing |
| `evals/preflight.py` | `what_will_run(definition, context)`; print its `.summary` before spending | takes your definition and a context; reports the worker names the orchestrator offers (yours are the same as your factory names) |
| `stub_orchestrator`, `STUB_ORCHESTRATOR_REPLY` and the orchestrator half of `only_the_chat_manager` in `evals/routing.py` | `stub_orchestrator(..., definition=, reply=)`, `only_the_chat_manager(definition, reply=)` | the stub's description is `definition.prompts.orchestrator_description`, its recorder carries your framings; pass your own reply text as `reply=` |

**Keep `_stubs` and the `planted` arm in `evals/routing.py`**: stubbing your operations is yours. Open your block
inside the engine's:

```python
from rag_wright.harness import only_the_chat_manager

with only_the_chat_manager(rulewright_agent(), reply=STUB_ORCHESTRATOR_REPLY), _your_operation_stubs(planted):
    ...
```

`docs/harness.md` section 8 describes all three instruments.

## 5. Behaviour you will see change

Each was a substrate defect a ported test found; the ADR-0139 amendment for the sub-task records it.

- **`AgentPrompts` refuses a skill deepagents would skip** (a): a path that is not `/skills/<name>/SKILL.md`, or
  frontmatter without `name` and `description`, raises `ValueError` at construction. Before, the model was silently
  never told the skill existed. Yours comply; a new one must.
- **A failure you both retry and explain is retried, not explained** (b). One in your `TRANSIENT` that
  `message_for` also answers now gets its retries.
- **A model passed to `build_chat_agent(model=)` is every tier's** (c): the orchestrator no longer builds a real
  client underneath a scripted chat-manager, so your scripted tests and evals are fully scripted. **And it gets the
  production roster** (g1): DeepAgents' catch-all `general-purpose` subagent used to stay on any tier built with a
  passed model; now it is removed exactly as for a configured one. Your `_a_model_nothing_calls` docstring's warning
  about the catch-all no longer applies.
- **Emptiness is decided by counts** (f): a result carrying integer `*_count` fields is "answered nothing" when all
  are zero, whatever they are called. Your `matched_count` / `possible_count` behave as before.
- **`worker_spec(definition=)` appends `definition.prompts.worker_filesystem_note` to each worker's prompt** (g1),
  unless the prompt already carries it. You paste `{WORKER_FILESYSTEM_NOTE}` into each worker prompt by hand; that
  copy is detected and not doubled, so you may delete it or keep it.
- **Your orchestrator can read its own skills** (g2). The filesystem signpost answered every `read_file` on
  `/skills/...` with your "not files" text, so the procedures you seed (batching clarifications among them) were
  advertised and unreadable. Expect skill reads in your traces where there were refusals.

## 6. Small edits

- **Type your tool runtimes**: `runtime: ToolRuntime[AgentContext]`, not a bare `ToolRuntime` (c). A bare one is
  `ToolRuntime[None]` and makes the serializer warn on a resume with your dataclass context. The shipped skill
  `building-an-agent-harness` teaches the typed form.
- **Your memory guard is vacuous**: it scans a folder that no longer exists. The engine's guard
  (`tests/harness/test_harness_memory.py`) reads the whole engine source and asserts it read something; delete yours
  with the memory tests in section 7.

## 7. Retire the tests the engine now runs

Each engine file below runs RuleWright's substrate tests against the fabric product. Delete the RuleWright tests it
ported and keep the ones its docstring says stay (quoted here).

| RuleWright file | engine file | keep in RuleWright |
|---|---|---|
| `test_harness_events.py` | `test_harness_events.py` | -- |
| `test_harness_skills.py` | `test_harness_skills.py` | your skill's text and prompts |
| `test_harness_clarify.py` | `test_harness_clarify.py` | where the tool sits in your manifest |
| `test_harness_model.py` | `test_harness_model.py` | your model setting and worker ceiling |
| `test_model_deadline.py` | `test_harness_deadline.py` | -- |
| `test_error_model.py` | `test_harness_error_model.py` | your taxonomy (which exceptions are recoverable, transient, never) |
| `test_harness_runs.py` | `test_harness_runs.py` | your context type and the route that derives it |
| `test_harness_confirm_view.py`, `test_harness_continuation.py` | `test_harness_confirm.py` | what counts as a new subject on your surfaces; your renderer and prompt |
| `test_harness_decisions.py` | `test_harness_decisions.py` | your D1-D10 table |
| `test_harness_rules.py` | `test_harness_rules.py` | your registry's delivery and provenance, your prompts, tests on your committed run recordings |
| `test_eval_recording.py` | `test_harness_recording.py` | -- (delete with `evals/recording.py`) |
| `test_harness_grading.py` | `test_harness_grading.py` | -- |
| `test_harness_seen.py` | `test_harness_seen.py` | your declarations (cacheable tools, question arguments) |
| `test_harness_memory.py` | `test_harness_memory.py` | your memory prompt's framing |
| `test_harness_workers.py` | `test_harness_workers.py` | your roster, reading tool, report fields, prompts |
| `test_harness_tiers.py` | `test_harness_tiers.py` | your classification, operation signatures, prompts |
| `test_harness_batching.py` | `test_harness_batching.py` | -- |
| `test_harness_dispatch_bound.py` | `test_harness_dispatch.py` | your prompt wording, your routing variants |
| `test_harness_agent.py` | `test_harness_agent.py` | your prompt's wording |
| `test_harness_filesystem_confusion.py` | `test_harness_filesystem.py` | your listing tool's description and field order |
| `test_harness_interpreter.py` | `test_harness_interpreter.py` | that your operations appear in the bridge |
| `test_harness_withholding.py` | `test_harness_withhold.py` | -- |
| `test_harness_accounting_live.py` | `test_harness_live.py` (the OpenRouter cost read-back) | the two tests of your usage ledger |
| `test_eval_faults.py` | `test_harness_faults.py` | that your wheel ships no eval code |
| `test_eval_preflight.py` | `test_harness_preflight.py` | -- (your routing tests that use your `_stubs` stay with them) |

## 8. The skills that changed

Re-read if you link them (ADR-0134; from your installed engine): **`building-an-agent-harness`** -- tool runtimes
typed with the product's context (c), and the eval instruments (`InjectFaults`, the recorder, the preflight) as the
tier's rather than yours to build (h).

## Verify

`uv run python -c "import rulewright.agent.rules"` imports (section 1); your fast and db suites green on
`>=0.18.0`; `grep -rn "rag_wright.harness\.[a-z_]* import" src evals scripts` finds nothing; bump the floor.
