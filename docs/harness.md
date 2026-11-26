# The agent harness: reference design

**What this is.** The design reference for `rag_wright.harness`, the engine's third public tier, for a developer
building a product's agent on it. It states the idea, every customization point with its API, and where the
substrate goes quiet rather than failing. It was drafted by RuleWright, the first product built on the tier.

**The tables are generated** from the installed tier by `scripts/build_harness_doc.py`, and the test suite fails
if they drift (`tests/arch/test_api_docs_current.py`). Edit the prose; regenerate the tables. Today:
<!-- generated:counts -->
83 public names; `AgentDefinition` has 17 fields (15 required; optional: `routing`, `budget`); `AgentPrompts` has 11 fields, every one required.
<!-- /generated -->

**What it is not**, so the documents do not overlap:

| document | answers |
|---|---|
| ADR-0139 | *why* the substrate is engine scope, and the shapes agreed when it moved here |
| [`docs/api/harness.md`](api/harness.md) | the generated signatures and docstrings, exhaustively |
| `building-an-agent-harness` (skill) | the *practice*: the doors the domain comes through, the build order, the lessons |
| [`configuration.md`](configuration.md#the-agent-harness-rag_wrightharness) | the tier's environment variables, beside the engine's others |
| this document | the design, and what a product must declare |

---

## 1. The idea

The substrate runs an agent; it does not know your domain. It owns the parts that are expensive to get right and
have nothing to do with what your documents contain:

- **Three tiers.** A chat-manager a person talks to, an orchestrator that plans, and dispatched workers. The
  chat-manager holds a direct read surface, so an unambiguous request never pays for three model calls. The
  orchestrator plans and dispatches and calls no operation itself: it plans from each worker's description, to which
  the engine adds what that worker can call, so every operation in `planning` must be held by a worker, and an agent
  whose definition leaves one to no worker is refused when it is built (PS-84). An operation the chat-manager holds is
  out of reach once a message is delegated, so give it to a worker too if it may arrive with planning work.
- **One entry point per lifecycle event**, because runtime context is per invocation: a resume that forgot it
  would wake every tool with no scope, and the memory namespace would collapse to one global scope.
- **The failure model**: named retry sets, a per-attempt clock inside the retry and a whole-turn ceiling outside
  it, call and tool caps, and the rule that a deadline breach is never retried.
- **Human-in-the-loop under replay.** An interrupt replays the node, so anything before the question runs twice;
  the protocol is built around that rather than against it.
- **Memory with a namespace factory that refuses** rather than falling back, run records that recognise a
  repeated ask, checkpoints, metering, tracing, and run grading against your own rules.

**What it never holds:** your tools, your workers, your prompts, your scope shape, your retry set, your model id,
your rules, your decision points. Those are the fields of an `AgentDefinition`, and the substrate reads nothing
else about you.

**The asymmetry worth internalising.** `AgentDefinition` and `AgentPrompts` are **total**: every field is
required except `routing` and `budget`, which have defaults, so an agent cannot be under-declared and the dataclass says
so at construction. The per-worker builder
`worker_spec` is **permissive**, and that is where silence lives (section 6).

---

## 2. `AgentDefinition`: what a product declares

Hand one object to the builders. If a value follows from two others, the substrate derives it rather than
accepting it: the direct surface is derived from `planning` plus the surface, and the tier split from the
workers. A derived value crossing the boundary would become a convention the next product can break silently.

<!-- generated:definition -->
| field | type | what it is for |
|---|---|---|
| `surface` | `ToolSurface` | What a product offers an agent run, and the two things the substrate asks about it. |
| `planning` | `dict[str, str]` | Operation name -> why it needs planning. The reason is kept: a classification without one cannot be revisited. |
| `workers` | `tuple[Any, ...]` | The worker factories the orchestrator may dispatch. |
| `context_schema` | `Any` | The type a run's context has, passed to the agent as `context_schema`; it must satisfy `RunScope`. The product's context stays its own: typed ids, its own construction rule, whatever else it wants. |
| `retry_on` | `tuple[type[BaseException], ...]` | Which exceptions the retry middleware may re-attempt. **Named, never `Exception`** -- the library's default would retry a scope violation, which is re-attempting a leak with backoff. |
| `message_for` | `Any` | `(error) -> str \| None`: what the model is told about a failure, or `None` to let it surface. Never asked about a failure in `retry_on`: that one is retried, and surfaces once the retries are spent. |
| `model_id_for` | `Any` | `(settings) -> str`: the ENGINE model id this product's agent reasons with (a registered profile's key). A model id is configuration: adopting a better model is a new profile and a changed setting. |
| `prompts` | `AgentPrompts` | Every word this product says to its model (`AgentPrompts`). |
| `repeatable` | `frozenset[str]` | Tools whose answer is the same twice in one run, so a repeat is served from the record, on every tier: the chat-manager, the orchestrator and each worker (PS-73). A tool not listed runs every time it is called. A served reply carries `artifact={"served_from_record": n}`, and once one call is served a second time the model's next call has no tools, so it answers from what it has (PS-74). |
| `question_argument` | `dict[str, str]` | `{tool: argument}` -- which argument carries a QUESTION, so two phrasings of one ask are one ask. |
| `framings` | `tuple[str, ...]` | Tools that FRAME a request rather than answer it, for the framing decision point. |
| `carry_forward` | `Any` | `(record, tool, content) -> content \| None`, or `None`: the product's chance to amend a result in the light of what this run already found. `None` amends nothing. |
| `rules` | `tuple[Any, ...]` | The rules this product holds its own runs to (`Rule`). The grading machinery is the substrate's; the rubric is not. Read by the builders when a run is given `grading=` (PS-72): each tier with a rule is recorded and graded. |
| `decision_points` | `tuple[Any, ...]` | The decisions a run of this product takes; the ledger reads each point's option space from here. |
| `read_framing` | `Any` | `(tool_name, parsed_result) -> {chose, confidence, inputs} \| None`: how this product reads its own framing tool's reply. |
| `routing` | `RoutingThresholds \| None` | When the decision model's routing answers are taken (ADR-0146). Optional: the measured defaults. `None` turns routing off: no tier asks the decision model, and nothing is sent to its provider (HD-10). |
| `budget` | `RunBudget` | What one run may spend across every tier, each tier keeping a reserve for the tiers above it (PS-80). |
<!-- /generated -->

---

## 3. `AgentPrompts`: what your agent says

Every word the model reads is the product's. The substrate ships no prompt of its own.

<!-- generated:prompts -->
| field | type | what it is for |
|---|---|---|
| `chat` | `str` | The chat-manager's system prompt: the tier a person talks to. |
| `orchestrator` | `str` | The orchestrator's, with the product's worker roster already interpolated into it. |
| `memory` | `str` | One line of guidance about the agent's own memory, shown above whatever is in it. |
| `skills` | `dict[str, str]` | `{published path: text}` -- the procedures a tier may read when they apply. |
| `skills_preamble` | `str` | The paragraph that introduces the skills above. |
| `not_a_filesystem` | `str` | The signpost a wrong filesystem call is answered with by default (it names the product's own tools). Every tier reads it, workers included, so it names no route only one tier has (a worker cannot delegate). |
| `looking_for_subjects` | `str` | The signpost for a filesystem search plainly hunting the subject documents (a path or pattern ending in one of `subject_file_endings`): it says where those documents ARE, rather than where they are not. |
| `subject_file_endings` | `tuple[str, ...]` | File extensions that mean "this model is hunting the subject documents it was shown", not notes. |
| `worker_filesystem_note` | `str` | What a worker is told about its own filesystem: `worker_spec(definition=)` appends it to each worker's prompt. |
| `orchestrator_description` | `str` | How the orchestrator is described to the chat-manager: the `description` of its compiled subagent, which is what the chat-manager reads when deciding whether to hand a request down. |
| `label` | `str` | What the model is shown as the SOURCE of the skills: the product's name. |
<!-- /generated -->

**Three constraints on your text.** `skills_preamble` must contain the three format slots
`{skills_list}{skills_locations}{skills_load_warnings}` or the build raises. Each skill is published at
`/skills/<name>/SKILL.md` and opens with YAML frontmatter carrying a `name` and a `description`, or
`AgentPrompts` raises: the skills middleware would skip it with only a logged warning, and the model would
never be told it exists. And no prompt may name a scope identifier: an injected instruction in an uploaded
document can quote back whatever the prompt contains.

**The signposts never stand between a tier and its own files.** `/memories/`, `/large_tool_results/` and `/skills/` are the agent's own space (`filesystem.OWN_ROOTS`), so recall, an offloaded result and a skill read with `read_file` reach the real tool; any other path gets your `not_a_filesystem` or `looking_for_subjects` text.

---

## 4. The two protocols

<!-- generated:tool_surface -->
**`ToolSurface`**: What a product offers an agent run, and the two things the substrate asks about it.

| member | shape |
|---|---|
| `build` | `(self, context: 'Any', resources: 'Any', *, only: 'Any' = None) -> 'list[Any]'` |
| `exposed_names` | a property |
<!-- /generated -->

<!-- generated:run_scope -->
**`RunScope`**: The only thing the substrate needs to know about a run's tenancy.

| member | shape |
|---|---|
| `scope` | `tuple[str, ...]` |
| `shared_scope` | `tuple[str, ...] \| None` |
<!-- /generated -->

**`scope` is opaque to the substrate, and that is the point.** It keys the memory namespaces and the run's
thread; a two-level product and a three-level one differ only in what they put in the tuple. `shared_scope` is
the one wider scope a run may promote a learned fact into, and the substrate enforces that it is a strict,
non-empty prefix of `scope`, so a sideways scope, the root, and anything outside the run's own chain are
unrepresentable rather than discouraged. The memory mounts are `/memories/scope/`, `/memories/shared/` and the
read-only `/memories/product/` (ADR-0139 addendum).

---

## 5. Entry points and builders

Runs (`outcome` is read with `read_run`):

<!-- generated:runs -->
| name | what it does |
|---|---|
| `start_run` | Begin a run under `context`, and supply it to every invocation. |
| `resume_run` | Answer a run that paused for a person, and re-supply its context. |
| `stream_run` | Begin a run and yield `(run_id, namespace, mode, chunk)` as it happens. |
| `read_run` | Where a run got to, by its id alone — or `None` if it is not this scope's. |
<!-- /generated -->

**A run that paused says what it asked** (PS-83). `RunResult.waiting_on` is a `Pending` while the run waits on a
person, and `None` otherwise; `start_run`, `resume_run` and `read_run` all fill it, the last from the checkpoint alone,
so a reloaded page shows the question. One reader covers every pause: `kind` is `ask_the_user` (the chat-manager
asked), `confirm_view` (the view moved) or `batched_needs` (the workers' needs, which the orchestrator asks inside
the chat-manager's dispatch, with no tool call to read); `question` is the one message to show, and `needs` the
questions behind it one by one, for a form. Two questions pending at once (a model that asks twice in one reply) are
one `batched_needs`, and `resume_run`'s one answer goes to each. On the stream, `translate` emits a `waiting` event
with the same three fields, once, however deep the pause was raised.

Builders:

<!-- generated:builders -->
| name | what it does |
|---|---|
| `build_chat_agent` | The chat-manager for one run, holding the manifest bound to that run's scope. |
| `build_orchestrator` | The planning tier, compiled as an agent in its own right. |
| `worker_spec` | A `SubAgent` spec, deliberately the opposite choice from the orchestrator's `CompiledSubAgent`. |
<!-- /generated -->

**Import from `rag_wright.harness`, never from a submodule.** Everything a product's agent, evals and guards need is in its `__all__`, listed in full in [the API reference](api/harness.md): the run's decision ledger (`WhatTheRunDecided`, `RecordTheDecisions`), `run_key`, `build_worker`, `withhold`, the derived tier split (`direct_names`, `worker_operations`, `chat_manager_tools`) among them. A submodule is the harness's own and may change without notice.

---

## 6. Where the substrate goes quiet: build workers with `definition=`

`worker_spec` takes a worker's policy as optional parameters, and each defaults to something inert. The defaults
are right for a substrate that knows no product: it will not invent a retry set, a repeat-suppression list or a
decision registry for you. They also mean that a worker built without them is quietly less capable, with nothing
going red: no retries, every failure surfacing, a filesystem signpost naming no tool, nothing repeat-suppressed,
no decision recording. The first product on the tier shipped all of its workers in that state with its full test
suite green, and found it by reading the call sites.

Every one of those values except the ledger is already in the `AgentDefinition` you declared. **So pass the
definition**, and `worker_spec` takes each parameter you left at its default from it; a value you pass explicitly
still wins:

<!-- generated:worker_spec -->
| parameter | inert default | with `definition=`, taken from |
|---|---|---|
| `ledger` | `None` | not from the definition: the run's, passed down by the builder |
| `retry_on` | `()` | `definition.retry_on` |
| `message_for` | `None` | `definition.message_for` |
| `signposts` | `None` | `definition.prompts` |
| `repeatable` | `frozenset()` | `definition.repeatable` |
| `question_argument` | `None` | `definition.question_argument` |
| `framings` | `()` | `definition.framings` |
| `carry_forward` | `None` | `definition.carry_forward` |
| `read_framing` | `None` | `definition.read_framing` |
| `model_id` | `None` | not from the definition: this worker's own model; without it, `definition.model_id_for` |
| `model_calls` | `None` | not from the definition: this worker's own cap on one dispatch; without it, the harness's `MODEL_CALLS_PER_INVOCATION` / `TOOL_CALLS_PER_INVOCATION` |
| `tool_calls` | `None` | not from the definition: this worker's own cap on one dispatch; without it, the harness's `MODEL_CALLS_PER_INVOCATION` / `TOOL_CALLS_PER_INVOCATION` |
<!-- /generated -->

With `definition=`, the worker's prompt also gains `definition.prompts.worker_filesystem_note`, unless the prompt already carries it, so a worker is told where its own notes live before it runs `ls('/')` to find out.

**A worker can reason with a model of its own** (PS-78): `worker_spec(..., model_id="deepseek/deepseek-v4-flash")`
names an engine model id (a registered profile's key) for that worker alone, and every other tier keeps
`definition.model_id_for(settings)`. The builders make it with `agent_chat_model`, the profile seam; a string handed
to DeepAgents as `model` would be resolved without the profile. Its calls are metered under its own id, so a usage
scope's `by_model` separates the two models with nothing else to do. A graded verdict names the model its tier ran on
(`model`), and the grader stays the product's model (or `Grading.model`), so both arms of an A/B are judged by one
judge.

**The report is all the orchestrator sees of a worker** (PS-86). DeepAgents hands the dispatching tier the worker's
structured report as the `task` reply, serialized, and nothing else: not the worker's own messages, not its tool
results. So design the report (your `WorkerReport` subclass) to carry what the plan needs, keyed the way the plan asks.
Measured on the quickstart: a flat `tickets` list for an eight-symptom question left the orchestrator unable to tell
which ticket matched which symptom, and it re-dispatched the same worker five times for one symptom, then asked the
person whether to dispatch per symptom "to get more detail". The dispatch bound caps that; the report prevents it.

```python
from rag_wright.harness import worker_spec

def my_worker(context, resources, ledger=None):
    return worker_spec("my_worker", "What it does, for the orchestrator.", MY_WORKER_PROMPT,
                       MY_SURFACE.build(context, resources), MyWorkerReport,
                       ledger=ledger, definition=MY_DEFINITION)
```

**If you set the values yourself, guard the consequence, not the call.** Build a real spec for each of your
workers and interrogate the middleware it produced: a non-empty retry set, a run record whose repeatable set is
yours, your question map, your framings, signposts that are not `None`. Then prove the guard by removing the
policy from one worker and watching it fail; a guard that has never failed is untested. The engine's own version
is `test_A_WORKER_TAKES_ITS_POLICY_FROM_THE_DEFINITION_IT_IS_GIVEN` in `tests/harness/test_greenfield_conformance.py`.

**A minimal composition**, every name taken from the tables above. `resources` is whatever your tools need at run
time (a store handle, a client), passed to your factories untouched; `settings` is your own settings object, read
only by your `model_id_for`. The whole of a small product, runnable and run by the engine's tests, is
`examples/harness_quickstart.py`:

```python
from rag_wright.harness import AgentDefinition, AgentPrompts, build_chat_agent, start_run

definition = AgentDefinition(
    surface=MyToolSurface(),             # build(context, resources, only=None) + exposed_names
    planning={"my.long_operation": "it fans out over the whole corpus"},
    workers=(my_worker,),                # factories: (context, resources, ledger=None) -> worker_spec(...)
    context_schema=MyContext,            # satisfies RunScope: scope + shared_scope
    retry_on=(MyUpstreamOffline,),       # named, never Exception
    message_for=my_message_for,          # (error) -> str | None; None lets it surface
    model_id_for=lambda settings: settings.my_agent_model,
    prompts=AgentPrompts(...),           # every field, every word yours
    repeatable=frozenset({"my.read_one"}),
    question_argument={"my.ask": "question"},
    framings=("my.frame_the_request",),
    carry_forward=None,                  # a real answer: amend nothing
    rules=(my_rule,),
    decision_points=(my_point,),
    read_framing=my_read_framing,
)

agent = build_chat_agent(context, resources, settings, definition=definition,
                         checkpointer=my_checkpointer, store=my_store)
result = await start_run(agent, context, message=question, settings=settings,
                         skills=definition.prompts.skills)
```

**A model you pass is every tier's.** `build_chat_agent(..., model=)` and `build_orchestrator(..., model=)` give a scripted or self-built model the same tiers a configured one gets, DeepAgents' catch-all `general-purpose` subagent removed included, so a test's roster is production's.

**Note what is not in that call**: no tier split, no direct surface, no worker-to-operation map; the substrate
derives each from what you declared. `start_run` takes `skills` explicitly because seeding them is the product's
decision: a tier advertised a skill it cannot read spends a `read_file` finding out.

---

## 7. Configuration is the engine's

The tier reads the process environment, not your settings object:

| variable | effect |
|---|---|
| `RAG_TRACE_LEVEL` | `off` / `generations` / `verbose`. `verbose` sends prompts, which are customer content |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | a level alone traces nothing; the keys are needed too |
| `RAG_QUESTION_ENCODER` | `off` stops the harness embedding tool questions to recognise a repeated ask |
| `OPENROUTER_API_KEY`, or the profile's `api_key_env` | the model credentials, read where the engine builds the model |

**Put them in the environment before anything builds an agent**: at application startup, and in any script, eval
or test that builds a real model. A product that forgets gets the model client's *"Missing credentials"* error
with nothing naming the cause. If a wrapper of yours used to export them as a side effect and stops being on the
path, nothing exports them: when a seam stops being called, check what it did besides forwarding.

---

## 8. Verifying your own composition

The order matters:

1. **A second product, about 200 lines, hermetic**: a fake product in an unrelated domain that implements the
   seams and builds a real agent. Write it before your seams are final, so each one is designed against an outside
   implementer rather than audited afterwards. It is the only instrument that measures reusability; import counts
   measure decoupling, which is a different property. The engine's is the fabric product in
   `tests/harness/fabric.py`, whose scope is three parts deep; the harness's own behaviour tests run against it.
2. **Guards on the consequence** of each permissive default (section 6), each proved by planting.
3. **Your own rules and decision points**, graded by the substrate against the registries you declared (below).

**Grading a run against your rules.** Declare each rule on `AgentDefinition.rules` as a `Rule` (`id`,
`applies_to`, the `instruction` the model reads, the `criterion` the grader checks, and a `detect` function when it
can be counted), and pass `grading=Grading(on_evaluation=...)` to `build_chat_agent`. Every tier with a rule of its
own -- the chat-manager, the orchestrator, each worker -- is then recorded and graded when it finishes, and each
verdict reaches `on_evaluation` carrying its `tier` (`chat-manager`, `orchestrator`, or the worker's name). A tier
with no rule is not graded, and without `grading` nothing is built. Each graded tier costs a grader call (two when
it reads its evidence first) every time it finishes, a worker each time it is dispatched, so grade evals and
sampled runs rather than every served one. `applies_to` names `chat-manager`,
`orchestrator` or `worker` (and `coded`, for an eval that grades a coded arm it drives itself). Grading observes and never changes the run: a
grader that fails or overruns reports `GRADER_ERROR` and the run is untouched. `Grading(model=...)` sets the grader's
model; otherwise it is your `model_id_for`.

**A rule's instruction is yours to deliver.** Put it in the prompt of the tier it applies to (`carried_by` records
where), and test that it is there: `instructions_for(tier, rules)` lists what each tier must be told, and
`broken(recording, tier=, rules=)` runs the detectors over a recording offline, with no grader.

**Grading is not answer scoring.** A rule is a property of how a run behaved (an answer came from a tool, a worker was
dispatched once), and its verdict says whether the run kept it. Whether an answer was the right kind for a question
(answered, refused, declined) is an eval: a gold set holding each question's expected class, run with `arun_eval`
and scored with `score_classification` (`docs/evals.md`).

**Recording a run.** `RecordTheContext(sink, label)` is the middleware grading attaches: it writes what a tier was
given and what it did, turn by turn, into `sink` -- the system prompt, every message, every tool call with its
arguments, the briefs it was dispatched with, and tokens per turn -- and that recording is what the detectors and the
grader's evidence tool read. An eval that builds its own agent attaches it with `grading_middleware(tier, sink,
settings, rules=...)`. `every_call`, `spend`, `compare` and `transcript` read a recording back: `compare(alone,
orchestrated)` is the one table that says whether a worker costs the same under a parent as alone.

**Causing a failure on demand.** A failure seen once, live, cannot be regression-tested. `InjectFaults(*faults, seed=)` is a middleware that causes one deterministically: `Fault(nth_model_call=2, delay_s=...)` slows the second model call of a run, `Fault(tool="my_tool", raises=MyError)` fails a tool, and `empty=` / `truncate_to=` spoil a response; `probability=` draws from the seeded generator, and `fired` lists the calls it landed on. It causes the condition, never the model's decision, which the run's decision ledger records. Put it after `error_middleware(...)` in an eval's own agent (or `build_orchestrator(..., extra_middleware=)`), never in an agent you serve: nothing in the engine installs it.

**Measuring one tier.** `what_will_run(definition, context)` reports what a run would build -- whether the orchestrator is real or stubbed, and which workers -- and `report(label, definition, context)` prints it before an eval spends anything. Inside `with only_the_chat_manager(definition):` the chat-manager delegates to `stub_orchestrator`, which keeps your orchestrator's name and description (what the delegation decision is made from), answers each brief as finished work and records into the run's ledger; stub your own operations inside the same block.

**The trap:** a conformance test that hands the substrate your own context type while asserting that a different
product works proves nothing about a product whose scope has a different depth. When a seam changes, change the
fake product first.

---

## 9. Routing with the decision model

**The harness routes three decisions with a decision model** (Jev, the engine's `jev_decision` capability), and the
tier's own model decides only below a threshold or when the decision model is unavailable (ADR-0146). Measured on the
engine's routing gold sets (HD-2, HD-8), the routed tiers match or beat the model-only tiers: D1 92.2% against 90.0%
on twelve direct tools, D2 90.0% against 75.6% per worker, each routing call about $0.00002 and under a second.

| decision | where | the decision model asks | taken at | otherwise |
|---|---|---|---|---|
| **D1** | the chat-manager's opening call of a turn, and its first call after the person answers a question | one choice: each direct tool, `delegate`, `ask_the_user`, `answer` (only when the chat-manager has a direct tool) | `routing.d1` (0.7) | the tier's model routes, as before |
| **B1** | a routed `delegate` with earlier turns | can the message be understood without them? | `routing.b1` (0.6) | the tier's model writes the brief |
| **D2** | the orchestrator's opening call | one yes/no per worker | `routing.d2` (0.5) | the orchestrator plans, as before |

What a route does: a direct tool with no arguments is called in code; a tool with arguments, `ask_the_user` or a
`delegate` whose brief must be written is offered to the model alone; `answer` goes to the model with no tools. **A chat-manager with no direct tools is not offered `answer`** (PS-87): with every operation behind a worker it has nothing to answer from, and with no tools it answered questions the record holds from the model's own knowledge. Its options are `ask_the_user` and `delegate`; below the threshold its model routes, with every tool. A
delegated message that stands on its own (no earlier turns, or B1 yes) is handed over in the person's own words,
`<viewing>` block included. In the orchestrator, no worker means its opening call runs without the hand-off, one
worker is dispatched in code with the brief, and several are planned by the orchestrator as before.

**After a delegation, the chat-manager answers from the report** (PS-76). Once the orchestrator's report is back in
a turn, the chat-manager's next call keeps `ask_the_user` and none of its direct tools, so it presents the report
rather than re-deriving it; a failed dispatch leaves the tools, and the person's next message, or their answer to a
question the report needed put to them, restores them.

**A person's answer to a question is routed too** (PS-80). On a resume the answer arrives as the result of
`ask_the_user`, so the chat-manager's next call opens on it, and D1 is asked of what was asked: the original request,
the question and the answer together. A delegation in the person's own words hands that over as the brief. Only that
call is routed; the call after it is under way again. The one dispatch a run's chat-manager may make is counted per
thing the person asked, so a resumed turn can still delegate though the run delegated before it asked, and the ledger
records the resumed route as a second D1 row with `resumed`. A worker's question asked through the orchestrator
interrupts inside that dispatch, and the resume runs the same `task` call again to carry the answer down; that replay
is the same dispatch, not a second one (PS-81).

**Nothing to turn on.** The decision model is the harness's own: it registers the engine's `jev_decision` capability
and calls it itself, and a product registers nothing. It needs the decision model's credentials, which are the
engine's configuration (`OPENROUTER_API_KEY` by default; a self-hosted deployment registers an on-premises decision
profile with `register_decision_profile`). Without them every tier routes as it did before, and the process logs one
warning. **To turn routing off** -- for example where users' messages must not reach the decision model's provider --
declare `AgentDefinition(..., routing=None)`: no tier asks the decision model and nothing is sent to it.

**Your declarations are the criteria.** The decision model reads each direct tool's own description, the
orchestrator's `orchestrator_description` (what delegating means) and each worker's description, which ends with
what the worker can call: the name and first line of each of its tools, added by `worker_spec`. Write them so they
tell the options apart. The weakest option on the engine's sets is `delegate`, confused with a direct read of the
same subject, so say in `orchestrator_description` what needs planning (matching, comparing, checking against a
standard, several steps), not only what the orchestrator is.

**Thresholds are yours to change**, after measuring on your own gold sets (`arun_eval`, as for any capability):

```python
from rag_wright.harness import AgentDefinition, RoutingThresholds

definition = AgentDefinition(..., routing=RoutingThresholds(d1=0.8))
```

**What is recorded.** D1 carries the decision model's probability as its `confidence`, the `route`, and for a
delegation `brief_by` (`verbatim` or `model`) and B1's `self_contained`, so a rule that the brief is faithful can be
decided by reading `brief_by` rather than by a judge. Each worker's D2 row carries the decision model's yes: as its
`confidence` when the decision model dispatched it, as `decision_model_yes` when the orchestrator did; a brief sent
to no worker is a D2 row `none`.

**A tool the router chose is forced when the model is known to honour it** (HD-9): the configured model's profile
says so (`ModelProfile.forced_tool_choice`, read with `profile_for`), measured per model and never assumed. Offered
alone but not forced, the chat model answered a routed `ask_the_user` with a reply 4 times of 18; forced, 0 of 18.
A product on another model measures it and registers its profile with `forced_tool_choice=True`.

**What it does not do:** a brief needing several workers is still planned by the orchestrator's model. **Data:** the
person's message and the brief are sent to the decision model's provider.

---

## 10. What a run spends: the budget and the tally

**Two bounds, on different things** (PS-80). The per-invocation caps (`MODEL_CALLS_PER_INVOCATION`, 25;
`TOOL_CALLS_PER_INVOCATION`, 40) bound one invocation of one tier: the chat-manager's start or resume, and each
dispatch of the orchestrator or a worker, each counted afresh. They never bounded a run, whose bill grows with every
dispatch. `AgentDefinition.budget`, a `RunBudget`, bounds the run: model calls and tool calls across every tier
together (60 and 100 by default).

**Each tier keeps a reserve for the tiers above it**, so a cost control cannot become a failure. A worker stops at the
budget less two reserves, the orchestrator at the budget less one, the chat-manager at the whole budget: a worker
that spends all it may still leaves the orchestrator and the chat-manager the calls to compose an answer. A tier past
its share of tool calls is called with no tools, so it answers from what it has; one past its share of model calls
ends with a message saying so.

**The orchestrator's dispatches are bounded too** (PS-86). The chat-manager may dispatch the orchestrator once per
thing the person asks (`DISPATCHES_PER_RUN`): each message, and each answer to a question, is a new thing asked, so a
follow-up in the same run may be delegated again (PS-87; counted by answers alone, it could not). The orchestrator
may dispatch workers `RunBudget.dispatches` times (6 by
default), since a plan may need several. Measured before the bound, one question dispatched one worker 17 times from a
single plan. Once spent, the orchestrator's next turn has no tools and is told it has dispatched all it may and to
report now, so it writes its report: with only `task` taken away it was measured spending 22 more calls on its
filesystem and todo list until the run's budget ended it on a cap message; with `ask_the_user` kept it asked the person
whether to dispatch more; with the tools gone and nothing said, its reply was its plan. A worker's question needs no tool
there, since `BatchTheQuestions` asks it in code, and the person's answer, like an answer to `ask_the_user`, re-opens
the allowance, so it can be carried down to the worker. A dispatch past the bound (calls in one turn counted one by
one) never runs, and is answered with what came back; a call refused beside the one that ran in the same turn is told
that reply arrives beside it.

**One worker's own caps** are `worker_spec(..., model_calls=, tool_calls=)`, bounding each dispatch of that worker
alone, for a worker measured to need fewer calls than the harness's caps allow.

**What each tier spent is a product's to read.** `run.state` holds the chat-manager's conversation only; the run's
tally is `WhatTheRunSpent`, keyed by run as the decision ledger is. Per tier (`chat-manager`, `orchestrator`, or a
worker's name) it records how often the tier was started, its model and tool calls, its input and output tokens,
whether the budget stopped it, and the dispatches it made and the ones its bound refused, by name (`dispatched`,
`refused`). Pass one to read it, as a regression test on a tier's calls would:

```python
from rag_wright.harness import RunBudget, WhatTheRunSpent, build_chat_agent, start_run, thread_for

spent = WhatTheRunSpent()
agent = build_chat_agent(context, resources, settings, definition=definition, spent=spent)
run = await start_run(agent, context, message="Which earlier development is closest to SDF 3146?")
worker = spent.of(thread_for(context, run.run_id)).of("precedent_worker")
assert worker.model_calls <= 6
```

Counted where the call reaches the model and the tool runs: a turn the router settled without a model call, or a
repeat served from the run's record, costs nothing and is not counted. A run resumed on a rebuilt agent counts into
the `spent` it is given, so pass the same one to both halves to see the whole run.

