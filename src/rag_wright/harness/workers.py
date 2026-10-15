"""The workers: one skill each, and what each hands back.

**A worker earns its place by ISOLATING CONTEXT, not by tidiness.** The docs are explicit: *"Subagents
solve the context bloat problem. The main agent receives only the final result, not the dozens of tool
calls that produced it."* So the test for a worker is whether its job produces large intermediate results
the parent does not need — which is why the light tools (a document listing, a job status, a spend figure)
stay on the tiers that plan, and only the content-heavy jobs become workers.

**The division is by the QUESTION A USER ASKS**, not by the module a tool came from. The workers are the
product's; an illustrative roster:

| worker | tools | the question it answers |
|---|---|---|
| `finding_worker` | `read_the_question`, `sweep_scope`, `ask_a_question` | *which documents say X, and what exactly do they say* |
| `overview_worker` | `ask_about_the_scope`, `trace_an_entity` | *what about the scope as a whole; which entities are involved* |
| `checking_worker` | `list_standards`, `read_a_standard`, `read_a_check` | *does this meet the standard, and what did we decide before* |

**`find` and `read` are ONE worker, on evidence.** A division by verb — find / read / judge — splits them,
and that failure was measured: a worker that could find documents and not read them reported
`status="blocked"` and asked the user *"is there a document-retrieval tool I should use?"*, a question no
person can answer.

**A worker returns STRUCTURED, CHAINABLE data — not a prose summary.** The workflow pattern in the
interpreter's own prompt makes this unambiguous:

```js
task({description: "...", subagentType: "verifier"}).then((v) => ({ ...f, ...v }))
```

`{...f, ...v}` spreads the subagent's return value. **A paragraph cannot be spread.** The "keep responses
concise" advice in the context-engineering guide is about a different path — a worker's result reaching a
*conversational* parent as a `ToolMessage` it re-reads every turn. In the coded path the result lands in a
JS variable and never enters the model's context at all. Compression is then the ORCHESTRATOR's job, and
the REPL prompt says so: *"Keep large intermediate sets in JS variables and return only a compact summary
or a small slice, not the entire dataset"* — an instruction to the program, not to the subagent.

**Batched clarification rides along.** Every report carries `status` and `need`, because a worker still
cannot ask: it is never given `ask_the_user`, and `BatchTheQuestions` on the orchestrator collects what the
workers could not decide and asks once.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class WorkerReport(BaseModel):
    """What every worker carries, whatever else it returns.

    **Structured, because the orchestrator chains it.** `status` lets a program branch; `need` is how a
    worker says it cannot finish without a person, since it has no way to ask.
    """

    status: str = Field(description="`done` when you finished, `blocked` when you cannot without an answer")
    need: str = Field(
        default="",
        description="When blocked: the ONE question a person must answer, in their own vocabulary",
    )










#: The rule every worker prompt repeats, in the words the failure taught us.
CANNOT_ASK = """**You cannot ask the user anything.** If you need something only a person can decide,
finish with `status: "blocked"` and put the one question in `need`. The tier above you collects those,
sometimes answers them itself, and asks the user once rather than five times. Do your best with everything
else first and report what you got as far as."""


DO_WHAT_WAS_ASKED = """**Do exactly what your task asks, and no more.** Your task is the only instruction
you get; the tier above you decided what it needs. If it names particular items, work on those and do not
search more widely. If it asks about the whole subject, then search. Re-doing work the caller did not ask
for is not thoroughness -- when several of you run at once it multiplies, and the same item gets read
again and again."""







#: Middleware a TIER has that a worker deliberately does not, and why. Audited rather than assumed.
#:
#: A guard reads the middleware out of both tier builders and fails unless every one of them is either on
#: workers too or named here -- so a middleware added to a tier forces a decision about workers on the same
#: commit, instead of being discovered when a worker loops.
NOT_ON_WORKERS: dict[str, str] = {
    "ToolSurface": (
        "**Not middleware at all** -- it is the PRODUCT's tool surface, handed to the orchestrator's code "
        "interpreter so the substrate never imports the product's operations. It is here "
        "because the audit above reads CAPITALISED calls in a tier builder and cannot tell a middleware "
        "class from any other constructor, which is the right trade: it would rather make somebody "
        "classify a new name than miss a middleware. A worker has no code interpreter, so there is nothing "
        "to hand one. **This entry goes when no tier builder names the surface any more.**"
    ),
    "OnlySoManyDispatches": (
        "There is nothing to bound: a worker has no `task` tool at all -- measured, it is bound the six "
        "filesystem tools and NOT `execute`, `eval` or `task` -- so it cannot hand work down. "
        "**The ORCHESTRATOR is a different case and this middleware does not fit it yet**: "
        "it counts dispatches PER RUN, and an orchestrator legitimately dispatches three different "
        "workers, so this bound would forbid a correct plan. The fan-out it needs guarding against is "
        "PER WORKER -- one worker was dispatched 7 times in one run, which scored "
        "`dispatched_once_each: 1.000` at the time -- and that is a per-subagent bound, raised rather "
        "than built here."
    ),
    "HowMuchToThink": (
        "A worker keeps the PROFILE's reasoning, which is on, and that is the current behaviour left "
        "deliberately unchanged. The A/B that turned reasoning off measured ONE question on "
        "the CHAT-MANAGER -- 1,905 reasoning tokens buying the same tool call as 42 -- and says nothing "
        "about a tier that reads source material and returns a structured report. Extending it here on that "
        "evidence would be the kind of unmeasured change that has had to be reverted before. **The cost is real and "
        "unmeasured on this tier**: a worker turn is uncapped and can outlive its own deadline the same "
        "way, which is what the 222.9s call (out=1,627) looks like."
    ),
    "ConfirmTheView": (
        "The view belongs to the conversation. A worker never talks to the user and has no "
        "screen to have moved, so it would be confirming something it cannot see."
    ),
    "BatchTheQuestions": (
        "Only the ORCHESTRATOR batches questions. A worker that batched would be asking, which is the one "
        "thing it must not do -- it reports `status=\"blocked\"` instead."
    ),
    "CodeInterpreterMiddleware": (
        "Only the orchestrator gets an interpreter; workers use plain tool calls. One "
        "active interpreter per agent is easier to keep when only one tier has one."
    ),
    "WithheldTools": (
        "There is nothing to withhold: a worker is bound `ls`, `glob`, `grep`, `read_file`, `write_file` "
        "and `edit_file` -- and NOT `execute`, `eval` or `task` (measured). A guard checks that stays "
        "true rather than trusting it."
    ),
    "TodoListMiddleware": (
        "The product's TODO panel renders the ORCHESTRATOR's list; a worker's would be a second, invisible "
        "list competing with it. A worker does not plan -- it executes one brief and reports "
        "(`DO_WHAT_WAS_ASKED`). Measured on a live run: one worker made 56 calls "
        "and 35 of them were `write_todos`, rewriting a task list until the run hit its ceiling, while "
        "the two workers that finished used it zero times.\n\n"
        "Until `deepagents` 0.7 the framework forced a todo list onto every agent, so this was expressed "
        "by withholding `write_todos` in `WITHHELD_FROM_WORKERS`. 0.7 stopped auto-adding it, so the "
        "tier that wants one asks for it and a worker simply never gets one."
    ),
}

#: What EVERY deep agent carries before anything of ours, observed rather than assumed.
#:
#: **Passing `middleware=` ADDS; it does not replace** -- `create_deep_agent`'s own docstring says
#: subagents *"automatically receive a default middleware stack BEFORE any custom middleware"*, and
#: building one with and without a marker middleware confirms every default survives.
#:
#: **Node names undercount this badly**, which is the trap: only `before_*`/`after_*` hooks become graph
#: nodes, so the two visible entries are 2 of 6. `_DeepAgentsSummarizationMiddleware` -- which compacts a
#: context that outgrows its bounds -- wraps `model_call` and shows up nowhere in `agent.nodes`. Reading
#: node names would have said workers had no summarization, and removing it on that basis would have
#: uncapped their context.
FRAMEWORK_DEFAULTS = (
    "TodoListMiddleware", "PatchToolCallsMiddleware", "FilesystemMiddleware",
    "SubAgentMiddleware", "_DeepAgentsSummarizationMiddleware", "AnthropicPromptCachingMiddleware",
)


def worker_middleware(ledger: Any = None, name: str = "", *,
                      signposts: Any = None,
                      repeatable: frozenset[str] = frozenset(),
                      question_argument: dict[str, str] | None = None,
                      framings: tuple[str, ...] = (),
                      read_framing: Any = None,
                      carry_forward: Any = None,
                      retry_on: tuple[type[BaseException], ...] = (),
                      message_for: Any = None) -> list:
    """What every worker carries.

    **The audit that produced this list, and the defect it closes.** Workers were never bare -- every deep
    agent carries `FRAMEWORK_DEFAULTS`, summarization included. What they lacked was OURS:
    `error_middleware()`, the call caps and the whole four-level error model, which sat on the two tiers above
    and not on the tier doing the work. Before the worker split the orchestrator did that work itself, inside its own
    caps; after the split it delegates into a tier that had none. A worker that looped was unbounded, a
    transient 429 inside one was never retried, and a recoverable failure never became a message it could
    act on.

    **`TheSubjectIsNotAFilesystem` belongs here too**, and that is not obvious: a worker is bound the same
    filesystem tools as a tier (measured), and it is the tier most likely to be handed `report-1.pdf` by a
    sweep and try to open it. The namespace collision is *more* likely here, not less.

    See `NOT_ON_WORKERS` for what a tier has that a worker does not, and why.
    """
    # **One record, two readers**: the first answers an identical repeat without running the
    # operation, the second uses it to say why a filesystem call is a wrong turn.
    from rag_wright.harness.decisions import DISPATCHER_OF_A_WORKER, RecordTheDecisions
    from rag_wright.harness.errors import error_middleware
    from rag_wright.harness.filesystem import TheSubjectIsNotAFilesystem
    from rag_wright.harness.seen import (
        question_encoder,
        OneAnswerPerTurn,
        RememberWhatWasCalled,
        WhatTheRunHasSeen,
    )
    from rag_wright.harness.withhold import WITHHELD_FROM_WORKERS, withhold

    seen = WhatTheRunHasSeen(repeatable=repeatable,
                             question_argument=question_argument or {})
    # **The dispatch is recorded HERE, by the tier that was dispatched.** A sandbox `task()`
    # never routes through the parent's `ToolNode`, so counting dispatches at the orchestrator is blind in
    # exactly the arm where the fan-out happened. A worker's opening turn is one human message in both.
    recording = ([RecordTheDecisions(ledger, tier=name, is_worker=True, framings=framings,
                                     read_framing=read_framing,
                                    dispatched_by=DISPATCHER_OF_A_WORKER)]
                 if ledger is not None and name else [])
    return [withhold(*WITHHELD_FROM_WORKERS),
            OneAnswerPerTurn(), *recording,
            RememberWhatWasCalled(seen, encoder=question_encoder(),
                                  carry_forward=carry_forward),
            TheSubjectIsNotAFilesystem(seen, signposts=signposts),
            *error_middleware(retry_on=retry_on, message_for=message_for)]


def worker_spec(name: str, description: str, prompt: str, tools: list, report: type,
            ledger: Any = None, *, retry_on: tuple[type[BaseException], ...] = (),
            message_for: Any = None, signposts: Any = None,
            repeatable: frozenset[str] = frozenset(),
            question_argument: dict[str, str] | None = None,
            framings: tuple[str, ...] = (),
            carry_forward: Any = None,
            read_framing: Any = None,
            definition: Any = None) -> dict[str, Any]:
    """A `SubAgent` spec, deliberately the opposite choice from the orchestrator's `CompiledSubAgent`.

    A worker has no middleware and no subagents of its own, so letting the parent build it keeps one model
    resolution and one place that knows how a tier is compiled.

    **Pass `definition=`, the product's `AgentDefinition`.** Every policy parameter after `ledger` defaults
    to something inert, and all of them but `signposts` are fields of that definition (`signposts` is its
    `prompts`). With a definition, each parameter left at its inert default is taken from it; a value passed
    explicitly still wins. Without one, a worker runs with no retries, no repeat suppression and no
    decision recording, and nothing fails to say so.
    """
    if definition is not None:
        retry_on = retry_on or definition.retry_on
        message_for = message_for if message_for is not None else definition.message_for
        signposts = signposts if signposts is not None else definition.prompts
        repeatable = repeatable or definition.repeatable
        question_argument = question_argument if question_argument is not None else definition.question_argument
        framings = framings or definition.framings
        carry_forward = carry_forward if carry_forward is not None else definition.carry_forward
        read_framing = read_framing if read_framing is not None else definition.read_framing
    return _spec(name, description, prompt, tools, report, ledger, retry_on=retry_on,
                 message_for=message_for, signposts=signposts, repeatable=repeatable,
                 question_argument=question_argument, framings=framings,
                 carry_forward=carry_forward, read_framing=read_framing)


def _spec(name: str, description: str, prompt: str, tools: list, report: type, ledger: Any, *,
          retry_on: tuple[type[BaseException], ...], message_for: Any, signposts: Any,
          repeatable: frozenset[str], question_argument: dict[str, str] | None,
          framings: tuple[str, ...], carry_forward: Any, read_framing: Any) -> dict[str, Any]:
    """The spec itself, once every policy value is settled.

    **No `ask_the_user` in any worker's tools**, and that absence is the mechanism: a prompt
    saying "do not ask" is a suggestion; a tool the worker was never given is a fact.
    """
    return {"name": name, "description": description, "system_prompt": prompt,
            "tools": tools, "response_format": report,
            "middleware": worker_middleware(ledger, name,
                                            retry_on=retry_on, message_for=message_for,
                                            signposts=signposts, repeatable=repeatable,
                                            question_argument=question_argument, framings=framings,
                                            carry_forward=carry_forward,
                                            read_framing=read_framing)}








def build_worker(spec: dict[str, Any], settings: Any, *, model: Any = None,
                 model_id: str | None = None) -> Any:
    """Compile ONE worker as a standalone agent, with no parent above it.

    **So a worker can be tested for what it is, not for what an orchestrator makes of it.** Until this
    existed a worker could only be exercised through `task`, so a run that went wrong told us nothing about
    WHERE: the worker, the decomposition, or the mode. A worker that cannot finish its own job in a short
    known sequence is broken, and that should be findable in seconds rather than discovered twenty minutes
    into an orchestrated run.

    **Compiled by the library's own SUBAGENT path, through a throwaway parent.** Two wrong versions
    preceded this, and each one made the isolated worker differ from the real one in a way that quietly
    biased every measurement taken against it:

    1. `create_agent` -- which IS what `create_sub_agent` calls, so it looked right. But the augmentation
       happens one level higher: `create_deep_agent` PREPENDS a base stack to every raw `SubAgent` spec
       (`TodoListMiddleware`, `FilesystemMiddleware`, a summarizer, `PatchToolCallsMiddleware`) before
       `SubAgentMiddleware` ever sees it. The worker was offered THREE tools where the real one is offered
       TEN.
    2. `create_deep_agent` on the MAIN-AGENT path, which fixed the tools and broke the prompt: it prepends
       a generic preamble ("You are a deep agent... ## Core Behavior... ## Progress Updates") that
       `create_sub_agent` never adds. The isolated worker carried 2,260 characters no subagent has ever
       seen -- and two workers then measured 9% CHEAPER orchestrated than alone, which was the clue.

    **So the spec is handed to `create_deep_agent` as a SUBAGENT and the compiled child is taken back
    out.** There is no third way that does not amount to re-implementing the library's augmentation here,
    and a second implementation of it is exactly what went stale twice (one
    implementation and two callers). `test_harness_workers.py` compares the result against a REAL
    orchestrator's compiled subagents -- tools, graph and system prompt -- so a throwaway parent that
    diverged from the real one would fail rather than mislead.

    **Found by the isolated-versus-orchestrated context diff**, which is the only thing that
    could have seen either: both agents answer, both look healthy, and the difference shows up only as a
    token ratio that is slightly wrong in the wrong direction.
    """
    from deepagents import create_deep_agent

    from rag_wright.harness.agent import agent_chat_model

    # **The model SPEC, exactly as `build_orchestrator` passes it**, and the profile registered first.
    # `_harness_profile_for_model` is keyed by model, so a pre-resolved chat model resolves a DIFFERENT
    # profile -- and the profile is what excludes `execute` from the turn.

    parent = create_deep_agent(
        model=model if model is not None else agent_chat_model(settings, _required(model_id)),
        # The parent is scaffolding and never runs: it exists so the library compiles the child the way
        # it compiles every child. Its own tools and prompt are irrelevant and deliberately empty.
        tools=[],
        system_prompt="This agent exists only to compile its subagent and is never invoked.",
        subagents=[spec],
    )
    return _compiled_subagent(parent, spec["name"])


def _required(model_id: str | None) -> str:
    """Refuse to guess a model id. **The substrate has no default profile to fall back on**: reading one
    from a setting would mean knowing one product's setting name."""
    if not model_id:
        raise ValueError(
            "build_worker needs either a built `model=` or a `model_id=`. The substrate no longer resolves "
            "a model id itself -- pass `definition.model_id_for(settings)`."
        )
    return model_id


def _compiled_subagent(parent: Any, name: str) -> Any:
    """The child runnable `create_deep_agent` built, taken out of the `task` tool it was put into.

    **Fragile on purpose.** The alternative is to re-implement the augmentation, which has now gone stale
    twice. If the library stops closing over `subagent_graphs` this raises immediately and says what to
    re-check, rather than handing back a worker that differs from the real one in some way nobody notices.
    """
    node = parent.nodes["tools"]
    task = getattr(node, "bound", node).tools_by_name["task"]
    function = getattr(task, "func", None) or task.coroutine
    closure = dict(zip(function.__code__.co_freevars,
                       (cell.cell_contents for cell in function.__closure__), strict=True))
    graphs = closure.get("subagent_graphs")
    if not graphs or name not in graphs:
        raise RuntimeError(
            f"could not take {name!r} back out of the compiled parent (found {sorted(graphs or [])}). "
            "DeepAgents has changed how `task` holds its subagents, so `build_worker` is no longer "
            "compiling what production runs -- re-ground against `create_deep_agent` before trusting any "
            "isolated measurement."
        )
    return graphs[name]


