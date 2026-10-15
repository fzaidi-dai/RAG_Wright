# The agent harness: reference design

**What this is.** The design reference for `rag_wright.harness`, the engine's third public tier, for a developer
building a product's agent on it. It states the idea, every customization point with its API, and where the
substrate goes quiet rather than failing. It was drafted by RuleWright, the first product built on the tier.

**The tables are generated** from the installed tier by `scripts/build_harness_doc.py`, and the test suite fails
if they drift (`tests/arch/test_api_docs_current.py`). Edit the prose; regenerate the tables. Today:
<!-- generated:counts -->
42 public names; `AgentDefinition` has 15 fields and `AgentPrompts` 11, every one required.
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
  chat-manager holds a direct read surface, so an unambiguous request never pays for three model calls.
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
required, so an agent cannot be under-declared and the dataclass says so at construction. The per-worker builder
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
| `message_for` | `Any` | `(error) -> str \| None`: what the model is told about a failure, or `None` to let it surface. |
| `model_id_for` | `Any` | `(settings) -> str`: the ENGINE model id this product's agent reasons with (a registered profile's key). A model id is configuration: adopting a better model is a new profile and a changed setting. |
| `prompts` | `AgentPrompts` | Every word this product says to its model (`AgentPrompts`). |
| `repeatable` | `frozenset[str]` | Tools whose answer is the same twice in one run, so a repeat can be served from the record. |
| `question_argument` | `dict[str, str]` | `{tool: argument}` -- which argument carries a QUESTION, so two phrasings of one ask are one ask. |
| `framings` | `tuple[str, ...]` | Tools that FRAME a request rather than answer it, for the framing decision point. |
| `carry_forward` | `Any` | `(record, tool, content) -> content \| None`, or `None`: the product's chance to amend a result in the light of what this run already found. `None` amends nothing. |
| `rules` | `tuple[Any, ...]` | The rules this product holds its own runs to. The grading machinery is the substrate's; the rubric is not. |
| `decision_points` | `tuple[Any, ...]` | The decisions a run of this product takes; the ledger reads each point's option space from here. |
| `read_framing` | `Any` | `(tool_name, parsed_result) -> {chose, confidence, inputs} \| None`: how this product reads its own framing tool's reply. |
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
| `not_a_filesystem` | `str` | The signpost a wrong filesystem call is answered with by default (it names the product's own tools). |
| `looking_for_subjects` | `str` | The signpost for a filesystem search plainly hunting the subject documents (a path or pattern ending in one of `subject_file_endings`): it says where those documents ARE, rather than where they are not. |
| `subject_file_endings` | `tuple[str, ...]` | File extensions that mean "this model is hunting the subject documents it was shown", not notes. |
| `worker_filesystem_note` | `str` | What a worker is told about its own filesystem. |
| `orchestrator_description` | `str` | How the orchestrator is described to the chat-manager: the `description` of its compiled subagent, which is what the chat-manager reads when deciding whether to hand a request down. |
| `label` | `str` | What the model is shown as the SOURCE of the skills: the product's name. |
<!-- /generated -->

**Two constraints on your text.** `skills_preamble` must contain the three format slots
`{skills_list}{skills_locations}{skills_load_warnings}` or the build raises. And no prompt may name a scope
identifier: an injected instruction in an uploaded document can quote back whatever the prompt contains.

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

Builders:

<!-- generated:builders -->
| name | what it does |
|---|---|
| `build_chat_agent` | The chat-manager for one run, holding the manifest bound to that run's scope. |
| `build_orchestrator` | The planning tier, compiled as an agent in its own right. |
| `worker_spec` | A `SubAgent` spec, deliberately the opposite choice from the orchestrator's `CompiledSubAgent`. |
<!-- /generated -->

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
<!-- /generated -->

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

**A minimal composition**, every name taken from the tables above:

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
   `tests/harness/test_greenfield_conformance.py`, whose scope is three parts deep.
2. **Guards on the consequence** of each permissive default (section 6), each proved by planting.
3. **Your own rules and decision points**, graded by the substrate against the registries you declared.

**The trap:** a conformance test that hands the substrate your own context type while asserting that a different
product works proves nothing about a product whose scope has a different depth. When a seam changes, change the
fake product first.
