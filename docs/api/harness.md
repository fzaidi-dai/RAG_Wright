# Agent harness reference — `rag_wright.harness`

> **Generated** from the live `rag_wright.harness.__all__` by `scripts/build_api_docs.py` — do not edit by hand. The agent harness tier (ADR-0139): what a product builds its agent on. It reaches the rest of the engine only through `rag_wright.api`. See the package docstring.

## Types

### `AgentDefinition(surface: 'ToolSurface', planning: 'dict[str, str]', workers: 'tuple[Any, ...]', context_schema: 'Any', retry_on: 'tuple[type[BaseException], ...]', message_for: 'Any', model_id_for: 'Any', prompts: 'AgentPrompts', repeatable: 'frozenset[str]', question_argument: 'dict[str, str]', framings: 'tuple[str, ...]', carry_forward: 'Any', rules: 'tuple[Any, ...]', decision_points: 'tuple[Any, ...]', read_framing: 'Any') -> None`

One product's agent, as the substrate needs to see it.

**Frozen**: a definition a middleware could edit mid-run is one where the tier split differs between two calls in the same run.

### `AgentPrompts(chat: 'str', orchestrator: 'str', memory: 'str', skills: 'dict[str, str]', skills_preamble: 'str', not_a_filesystem: 'str', looking_for_subjects: 'str', subject_file_endings: 'tuple[str, ...]', worker_filesystem_note: 'str', orchestrator_description: 'str', label: 'str') -> None`

Every word a product says to its model: the tier prompts, the memory line and the authored skills.

**Frozen**: a prompt a middleware could edit mid-run is one where two turns were given different instructions.

### `RunResult(run_id: 'str', context: 'RunScope', state: 'dict[str, Any]', outcome: 'RunOutcome | None' = None) -> None`

One user-initiated agent turn, and the scope it ran under.

`run_id` is the run's identity: `run-contract.md` obligation 1 requires a run to outlive the view that started it, so it is returned rather than held in memory, and it is what `resume_run` addresses.

### `RunOutcome(*values)`

What became of a run, named in CODE because none of it is a judgement (T-5a.15).

**Every transition here is decidable from the state alone**, which is why it is not in a prompt: the project's own rule is that a warning in a prompt is not a constraint, and `read_the_question` was instructed in four places and obeyed zero times.

**The distinction between `PARTIAL` and `NOTHING_CAME_BACK` was measured, not designed.** Three reproduced failures showed two different shapes: when the whole-turn clock fires, and when a caller's own ceiling fires, the finished work is still in the checkpoint -- so there is something to show. When the retries are exhausted the model never answered, and the checkpoint holds only the user's question. A single "proceed with what returned" rule would have rendered the second as a partial answer, which is worse than failing.

Members: `COMPLETE` (`'complete'`), `PARTIAL` (`'partial'`), `NOTHING_CAME_BACK` (`'nothing_came_back'`), `WAITING_ON_A_PERSON` (`'waiting_on_a_person'`)

### `MemoryWithoutTenancy`

A memory operation reached the store with no run context.

**Raised rather than defaulted, and that is the entire point of this module.** The fallback it replaces is silent and its consequence is a merge, not an error — so this must be loud enough that nobody is tempted to widen it into a default.

### `WorkerReport(*, status: str, need: str = '') -> None`

What every worker carries, whatever else it returns (`AR-16`).

**Structured, because the orchestrator chains it.** `status` lets a program branch; `need` is how a worker says it cannot finish without a person, since it has no way to ask.

### `RunEvent(name: 'str', data: 'dict[str, Any]', namespace: 'str' = '') -> None`

One thing worth telling the client about.

`name` is the event type the client routes on; `namespace` says which agent produced it, joined with `|` so it survives an SSE field without needing JSON.

### `ModelTooSlow`

A model call passed a wall-clock ceiling. **Never raised directly** -- one of the two below is.

There are two ceilings with OPPOSITE retry semantics, and they had one exception type between them until T-5a.12q. That cost a live run `compliance_worker` SEVEN identical dispatches: the whole-turn breach escaped the worker, arrived at the parent as a failed `task` tool call, and `ToolRetryMiddleware(retry_on=TRANSIENT)` re-ran the worker's entire job. Catch this base when you mean "some deadline fired"; never put it in a retry set.

### `ModelAttemptTooSlow`

ONE attempt at a model call ran long. **Transient by construction.**

It is raised inside the retry middleware and handled there: the attempt is abandoned and another is made. Measured, that is usually faster than waiting -- median 4.6s, p90 14.0s, one call at 124s in the same run. It never escapes the agent, so no enclosing retry layer ever sees it.

### `ModelTurnTooSlow`

A whole model turn passed its ceiling, retries included. **Terminal by construction.**

**Deliberately NOT in `TRANSIENT`, and that is the whole point of the split.** This one escapes the agent: to a parent it looks like a failed `task` tool call, and a retry there does not re-try a model call -- it re-runs the worker's entire job, repeating every tool call and every token it already spent. A ceiling that gets retried is not a ceiling.

**Not recoverable either**, so it is not in the allow-list `message_for` reads: a model told "you were too slow" would try again, inside the very turn that was too slow.

### `Rule(id: 'str', applies_to: 'tuple[Tier, ...]', instruction: 'str', criterion: 'str', detect: 'Callable[[dict[str, Any]], str | None] | None' = None, carried_by: 'tuple[str, ...]' = (), observed: 'str' = '', evidence: 'tuple[str, ...]' = <factory>) -> None`

One thing we tell a model not to do, and the way we find out whether it did.

`instruction` is rendered into the prompt; `criterion` is rendered into the rubric a grader is given. They are deliberately different sentences for different readers -- the model is told what to do, the grader is asked what happened -- and keeping both on one object is what stops them drifting apart.

### `DecisionPoint(id: 'str', question: 'str', made_by: 'str', options: 'tuple[str, ...]', observed: 'bool' = False, why_not: 'str' = '', blocked_on: 'str' = '') -> None`

One place the system chooses, and what it is choosing between.

`observed` is the honest half: a point declared here without an observation is a decision we know is being taken and still cannot see. Naming it costs nothing and keeps the gap countable, which is the opposite of how six of these came to be invisible.

## Hook protocols

Callables you pass to the engine; any function with this signature conforms.

### `ToolSurface: (**kwargs)`

What a product offers an agent run, and the two things the substrate asks about it.

### `RunScope: (**kwargs)`

The only thing the substrate needs to know about a run's tenancy.

A product's own context type satisfies this by having the two members; it keeps whatever else it wants (`AgentContext` keeps typed ids, a user id and `for_tenant`).

## Type aliases

## Constants

### `CANNOT_ASK`

str(object='') -> str str(bytes_or_buffer[, encoding[, errors]]) -> str

Create a new string object from the given object. If encoding or errors is specified, then the object must expose a data buffer that will be decoded using the given encoding and error handler. Otherwise, returns the result of object.__str__() (if defined) or repr(object). encoding defaults to sys.getdefaultencoding(). errors defaults to 'strict'.

### `DO_WHAT_WAS_ASKED`

str(object='') -> str str(bytes_or_buffer[, encoding[, errors]]) -> str

Create a new string object from the given object. If encoding or errors is specified, then the object must expose a data buffer that will be decoded using the given encoding and error handler. Otherwise, returns the result of object.__str__() (if defined) or repr(object). encoding defaults to sys.getdefaultencoding(). errors defaults to 'strict'.

### `ANY_WORKER`

str(object='') -> str str(bytes_or_buffer[, encoding[, errors]]) -> str

Create a new string object from the given object. If encoding or errors is specified, then the object must expose a data buffer that will be decoded using the given encoding and error handler. Otherwise, returns the result of object.__str__() (if defined) or repr(object). encoding defaults to sys.getdefaultencoding(). errors defaults to 'strict'.

### `ORCHESTRATING_PATH`

str(object='') -> str str(bytes_or_buffer[, encoding[, errors]]) -> str

Create a new string object from the given object. If encoding or errors is specified, then the object must expose a data buffer that will be decoded using the given encoding and error handler. Otherwise, returns the result of object.__str__() (if defined) or repr(object). encoding defaults to sys.getdefaultencoding(). errors defaults to 'strict'.

## Functions

### `validate_scope(context: 'Any') -> 'None'`

Refuse a scope the substrate cannot key or bound. Raises `ValueError`.

Called where a run begins rather than where a namespace is built: a bad scope should fail the run, not the first memory write, which is far from the cause.

### `start_run(agent: 'Any', context: 'RunScope', *, message: 'str', continuation: 'Any' = None, skills: 'dict[str, str] | None' = None, run_id: 'str | None' = None, settings: 'Any' = None, accounting: 'Any' = None) -> 'RunResult'`

Begin a run under `context`, and supply it to every invocation.

The caller passes who is asking, what they asked, and the scope it runs under. **That scope is the PRODUCT's to derive** (engine ADR-0139): the substrate must not know what authenticated this run, so it takes the resolved scope rather than three identity rows it would have to re-read.

**The obligation `AR-6` placed here therefore moves to the caller, and is not weaker for it**: the context must come from the authenticated session of THIS request, never from one remembered across requests. On the route that is structural -- `tenant: TenantDep` is a per-request dependency and the product's own `for_tenant` rule runs in the handler body -- which is why the door is still impossible to forget rather than merely documented.

A `run_id` is minted when the caller has none, so a run is addressable from the moment it exists rather than from whenever somebody remembers to name it (`run-contract.md` obligation 1).

**Tracing and accounting attach HERE, for the same reason the context does** (T-5a.4b): this is the only door, so it is the only place that cannot be forgotten. `settings=None` leaves both off and `accounting=None` is a no-op — the precedent `recording_usage` set, because accounting must never be the reason a user's work does not happen.

**`continuation` must already be RESOLVED** (`AR-12`, T-5a.10). It is typed `Any` only because the harness must not import its own models into this module's signature dance; `render_continuation` refuses anything but a `ResolvedContinuation` by name, so a caller that skipped the workspace check gets `UnresolvedContinuation` here rather than a prompt full of the browser's claims.

### `resume_run(agent: 'Any', context: 'RunScope', *, answer: 'Any', run_id: 'str', settings: 'Any' = None, accounting: 'Any' = None) -> 'RunResult'`

Answer a run that paused for a person, and re-supply its context.

**This is the invocation that goes wrong.** A resume is a second `invoke`, and the context does not survive from the first -- so it must be supplied again here, exactly as `start_run` supplies it.

**The caller owes a FRESHLY RESOLVED context, and this is the call where that matters most.** A resume can arrive long after the run paused; re-reading the session rather than reusing the paused run's tenancy is what makes a withdrawn membership or a suspended customer end access AT THE RESUME rather than at some expiry -- the same property `get_tenant` buys on the request path. Passing a context stored alongside the paused run would read as working and quietly lose it.

### `stream_run(agent: 'Any', context: 'RunScope', *, message: 'str', continuation: 'Any' = None, skills: 'dict[str, str] | None' = None, run_id: 'str | None' = None, settings: 'Any' = None, accounting: 'Any' = None) -> 'AsyncIterator[tuple[str, Any, str, Any]]'`

Begin a run and yield `(run_id, namespace, mode, chunk)` as it happens.

**The streaming sibling of `start_run`, and it carries the same guarantees** — the context, the trace and both ledgers — because they attach here rather than at the call site. A surface that streamed through `agent.astream` directly would lose all three and look like it worked.

`subgraphs=True` is what makes a subagent's output distinguishable from the main agent's; without it every event arrives flat and `AR-18`'s cards have nothing to index on. Two stream modes are requested, so langgraph yields **3-tuples** rather than 2 — verified, not assumed.

**`continuation` is built the same way `start_run` builds it**, through `_opening_turn`. Two doors would mean two chances to forget the view, and the streaming one is the door the product actually uses — so a continuation that only worked on `start_run` would be a feature with no users (`AR-12`).

### `read_run(agent: 'Any', context: 'RunScope', *, run_id: 'str') -> 'RunResult | None'`

Where a run got to, by its id alone — or `None` if it is not this matter's (T-5a.4c-iii).

**`run-contract.md` obligation 1, cashed:** *"the run is addressable, and its region renders from an id alone, never from in-memory state that a reload destroys."* A reload, a second tab, or a deploy all arrive here.

**`None` covers three cases deliberately** — no such run, another matter's run, and a run id that was never started. Not by comparing afterwards: the thread id is derived from the workspace, so another matter's run does not resolve to a thread at all. The checkpointer itself has no tenancy and cannot be given any, so the isolation has to be in the key.

**State, not replay.** This returns where the run got to; it does not re-emit the events that got it there. A reloading client renders the state and reconnects for what follows, which is enough for obligation 1 and is measurably cheaper than storing and replaying a transcript. If the surface turns out to need replay, the checkpointer keeps the history and `alist` is where that would come from — decided then, with a real surface to measure against, rather than guessed now.

### `outcome_of(snapshot: 'Any') -> 'RunOutcome'`

What a run's stored state says became of it.

**Order matters and each branch is a measured case**, not a guess:

1. An interrupt outranks everything: a run holding a question for a person has neither failed nor finished, and calling it partial would tell the user work was lost while a question is outstanding. 2. A final answer -- an AI message with content and no tool calls left to run -- is `COMPLETE`. 3. Otherwise: **tool results present means `PARTIAL`** (work finished, nothing composed it), and **no tool results means `NOTHING_CAME_BACK`**.

**It reports what has finished, not whether the run is alive.** `snapshot.next` is still set after a run is cut off -- the graph would continue if resumed -- so it cannot distinguish a live run from a dead one, and nothing here pretends otherwise.

### `thread_for(context: 'RunScope', run_id: 'str') -> 'str'`

The checkpointer thread a run lives in, **scoped to its matter**.

**The checkpointer has no tenancy and cannot be given any.** `aget_tuple` keys on `thread_id` and `checkpoint_ns` alone — verified in the installed saver — so a bare run id is readable by anybody who can name it. That is exactly the hole T-2.4d closed on the jobs route, where a UUID in a URL was the whole access control and anybody holding a job id read its result across customers.

So the workspace is part of the key rather than a comparison somebody must remember afterwards. Another matter's run id does not resolve to a thread at all, which makes "not yours" and "no such run" the same non-answer **by construction** — the non-oracle property this codebase applies to documents, jobs, workspaces, sessions and checks, arrived at structurally for once instead of by a check.

The run id stays the PUBLIC identity: it is what `usage_run.correlation_id` joins on and what a client keeps to reconnect. The thread id is ours and never leaves the process.

### `build_chat_agent(context: 'RunScope', resources: 'Any', settings: 'Any', *, model: 'Any' = None, definition: 'Any', checkpointer: 'Any' = None, store: 'Any' = None, ledger: 'Any' = None) -> 'Any'`

The chat-manager for one run, holding the manifest bound to that run's matter.

**The model is resolved through DeepAgents**, not built here: `AGENT_MODEL_SPEC` plus a registered `ProviderProfile` is the only combination that applies our OpenRouter configuration (T-5a.4a-ii found that `init_chat_model` silently ignores the profile).

**A `context_schema` is required, not conventional.** It comes from `definition.context_schema` -- the product's own context type, which the substrate never names -- and `start_run` refuses an agent without one, because an agent compiled with `context_schema=None` accepts `context=` and drops it silently.

**The checkpointer defaults to in-memory, which is a real limitation.** `run-contract.md` obligation 1 says a run outlives the view that started it, and an in-memory saver does not outlive the process. A durable checkpointer is T-5a.4c's business, where re-attaching to a run by id is the requirement that forces it; passing one here is how that lands.

### `build_orchestrator(context: 'Any', resources: 'Any', settings: 'Any', *, model: 'Any' = None, tools: 'Any' = None, system_prompt: 'str | None' = None, extra_middleware: 'list | None' = None, definition: 'Any', checkpointer: 'Any' = None, store: 'Any' = None, ledger: 'Any' = None) -> 'Any'`

The planning tier, compiled as an agent in its own right.

**`context_schema` is declared rather than inherited.** A compiled child does receive the parent's context without declaring it — pinned in `test_harness_grounding.py` — but declaring it is what lets this be driven DIRECTLY, which T-5a.12 does and which `start_run` refuses without the schema.

`model`, `tools`, `system_prompt` and `extra_middleware` are overridable for tests and evals; **production passes none of them.** `system_prompt` and `extra_middleware` exist so T-5a.12b can run the two orchestration arms through the REAL builder rather than assembling its own agent -- an eval that builds its own would be measuring something the product does not ship.

### `agent_chat_model(settings: 'Any', model_id: 'str') -> 'Any'`

The agent's chat model, built by the engine and bounded by our clocks.

**`model_id` is a PARAMETER (T-7.58).** It used to be read from one product's setting name, in the substrate, so a second product's model could not be reached at all. Which profile to reason with is the product's declaration; bounding the call with our clocks is the substrate's job and is all that is left here.

**No `ProviderProfile`, and that removed a trap rather than just a dependency.** The old path handed DeepAgents a spec STRING and registered a profile to configure it. `deepagents._models.resolve_model` returns a pre-built `BaseChatModel` *unchanged* and applies its profile registry only to strings -- including, in its own words, "the OpenAI Responses API default". That default was ON, and with it on OpenRouter returns `object: "response"` and reports **no cost at all**, which is why this module carried `use_responses_api=False`. Passing an object retires the counter-measure with its cause.

**It also retires a latent bug.** The old `agent_chat_model` registered the profile under `agent_model_spec(settings)` and then resolved the `AGENT_MODEL_SPEC` constant -- so configuring a different model registered the profile under one string and asked for another, which is the exact failure the registration's own comment warned about ("a profile registered under one string while the agent asks for another ... resolves with no api_key, no base_url and no reasoning flag"). It was masked only because the setting's default equalled the constant.

### `accounted_run(settings: 'Any', *, run_id: 'str', accounting: 'Any' = None, name: 'str' = 'agent-run') -> 'AsyncIterator[None]'`

Time and trace one agent run, and hand it to `accounting` when it ends.

`accounting` is anything with `collecting()`, `record(run_id=, since=, duration_ms=)` and `trace_metadata` -- the product supplies it (`operations.usage.AgentRunAccounting`). `None` disables recording without disabling the run.

**`collecting()` is opened OUTSIDE the trace and the timer is started before both**, which preserves the original order: the engine work a tool causes is collected through the caller's ContextVar for the whole run, including the setup the trace does not cover.

**Opened here rather than on the route**, because a streamed response produces its body after the endpoint returns: a request-scoped dependency would be resting the ledger on when Starlette closes its exit stack. The door already owns the context and the trace; it owns this too, and every caller inherits it.

### `scope_namespace(context: 'Any', leaf: 'str') -> 'tuple[str, ...]'`

The run's own writable memory: `scope + (leaf,)`.

### `shared_namespace(context: 'Any', leaf: 'str') -> 'tuple[str, ...] | None'`

The one wider namespace a run may promote into, or `None` when it has none.

### `thread_key(context: 'Any', run_id: 'str') -> 'str'`

The run's thread id: its scope and its run id, composed.

**Not the run id alone.** The run id is the PUBLIC identity a client keeps; the thread is ours and never leaves the process, and keying it by scope is what makes a guessed run id from another tenant resolve to nothing by construction.

### `worker_spec(name: 'str', description: 'str', prompt: 'str', tools: 'list', report: 'type', ledger: 'Any' = None, *, retry_on: 'tuple[type[BaseException], ...]' = (), message_for: 'Any' = None, signposts: 'Any' = None, repeatable: 'frozenset[str]' = frozenset(), question_argument: 'dict[str, str] | None' = None, framings: 'tuple[str, ...]' = (), carry_forward: 'Any' = None, read_framing: 'Any' = None) -> 'dict[str, Any]'`

A `SubAgent` spec, deliberately the opposite choice from the orchestrator's `CompiledSubAgent`.

A worker has no middleware and no subagents of its own, so letting the parent build it keeps one model resolution and one place that knows how a tier is compiled.

**No `ask_the_user` in any worker's tools**, and that absence is the mechanism (`AR-16`): a prompt saying "do not ask" is a suggestion; a tool the worker was never given is a fact.

### `clarification_tool() -> 'BaseTool'`

A tool whose entire body is a question.

Returned from a factory for symmetry with the manifest's bindings, though it closes over nothing: a clarification has no tenancy to bind, because it touches no matter's data.

### `translate(namespace: 'Any', mode: 'str', chunk: 'Any') -> 'list[RunEvent]'`

Turn one streamed item into zero or more events for the client.

**Zero is a normal answer.** Most of what a graph emits is bookkeeping — middleware hooks, state patches — and forwarding it would tie the surface to node names that change whenever the middleware stack does.

### `what_was_finished(result: 'Any') -> 'list[RunEvent]'`

What to tell the client about a run that did not finish (T-5a.15b).

**The route already stated the principle and did not honour it for failures:** *"a stream that stops is not a stream that finished. Without a terminal event a client cannot tell a completed answer from a dropped connection."* On a cut-off run the stream simply stopped -- no partial, no explanation, not even the terminating frame -- so it looked exactly like a dropped connection.

**There is nothing to store, only something to say.** Three reproduced failures showed that when our whole-turn clock fires, and when a caller's own ceiling fires, the finished work is still in the checkpoint. This reads it back out.

**And a run that produced NOTHING says so**, rather than being dressed as a partial answer. That distinction was measured, not designed: when the retries are exhausted the model never answered at all, and reporting an empty gap as work is worse than reporting a failure.

**No tenancy, ever** -- the module's rule, and an SSE frame is more exposed than a prompt. Only tool names and the results they returned.

### `error_middleware(*, retry_on: 'tuple[type[BaseException], ...]', message_for: 'Any') -> 'list[Any]'`

The stack, inner-first.

`ToolRetryMiddleware` carries `on_failure="error"` so a failure that exhausts its retries becomes an exception the error middleware can see — rather than a message the model receives without anybody having retried.

**Both arguments are the PRODUCT's and are required (T-7.58).** This imported one product's retry set and `message_for` directly, so the substrate held that product's judgement about what is recoverable -- and a greenfield product retrying a mill feed got the first product's client exception types and its domain errors. **No defaults**, deliberately: a default retry set is one a second product inherits without noticing, and `retry_on` defaulting to anything is how `Exception` creeps back in and a scope violation gets re-attempted with backoff.

The deadline exceptions are the HARNESS's own (above in this module), because `ADeadlineForOneModelTurn` is what raises them. A product chooses whether its own `retry_on` includes `ModelAttemptTooSlow`; ours does, and deliberately omits `ModelTurnTooSlow`.

### `dispatched_more_than_once(recording: 'dict[str, Any]') -> 'str | None'`

A worker covering the whole matter, run once per document.

### `reached_for_the_filesystem(recording: 'dict[str, Any]') -> 'str | None'`

Tried to open the matter's documents as files.

### `wrote_a_program_for_a_single_call(recording: 'dict[str, Any]') -> 'str | None'`

An `eval` whose program did the work of one tool call.

### `guessed_an_identifier(recording: 'dict[str, Any]') -> 'str | None'`

Tried an id that was never supplied.

### `blocked_without_saying_what_it_needs(recording: 'dict[str, Any]') -> 'str | None'`

Reported `blocked` with an empty `need`, which tells the caller nothing.
