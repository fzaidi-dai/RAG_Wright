"""The three tiers, and which operations each one holds.

```
user  <->  CHAT-MANAGER        conversation, threads, UI context, direct tools for unambiguous asks
               |
               +-- ORCHESTRATOR (CompiledSubAgent)   planning, decomposition, fan-out, re-planning
                       |
                       +-- WORKER subagents          one skill, few tools
```

**The chat-manager's direct surface is a requirement, not an optimisation**. Three tiers means three
agents making model calls before any tool runs, and routing *"list my documents"* through all three is
latency and cost on the commonest path — which breaks the rule that a model never
sits in the path of an unambiguous button click.

**It is a SUBSET, never a partition.** As the design put it: *"an operation the orchestrator
cannot see is a plan it cannot make."* So the orchestrator holds everything and the chat-manager holds the
half that needs no planning. `ORCHESTRATOR == set(EXPOSED)`, asserted.

**The classification is a JUDGEMENT and is maintained, which is said plainly rather than dressed up as a
derivation.** The tempting derivation — "the operation names a model or a retrieval graph in its signature"
— separates the set correctly today and is a **proxy**: it would silently misclassify an operation taking a
judge under another name, and it would misclassify a lookup operation that traverses the knowledge graph
through injected callables and belongs on the direct path. So the split is written down, a ratchet in
the product's tests forces every exposed operation into exactly one half, and the proxy stays as a
cross-check.

**The routing decision itself is the chat-manager's model call, and there is no structural way to make it.**
One model call then a direct tool is what the requirement asks for, against three model calls; whether the
model actually takes the direct path is measured, not asserted. The prompt is where the rule
lives, and that is a limitation worth naming rather than hiding.
"""

from __future__ import annotations

from typing import Any

from rag_wright.harness.clarify import clarification_tool


def direct_names(definition: Any) -> set[str]:
    """Operations the chat-manager may call DIRECTLY: a store read or an engine search, no model call.

    **Derived, deliberately.** A set computed at import from one product's registry would hand every
    product that product's tier split, and letting a product pass its own direct set would let it pass one
    that disagrees with its own `planning`, putting a planning operation on the direct surface with nothing
    to catch it. A product declares what is irreducible and the substrate derives
    this, so the two cannot drift by construction.
    """
    return set(definition.surface.exposed_names) - set(definition.planning)

#: Everything the orchestrator can REACH -- the whole exposed surface, deliberately. The user's rule,
#: 2026-09-15: *"an operation the orchestrator cannot see is a plan it cannot make."*
#:
#: Reaching is not the same as HOLDING. Some of these it calls itself; the rest it reaches through a
#: worker, which is what keeps that worker's tool calls out of its context.
def orchestrator_reachable(definition: Any) -> set[str]:
    """Everything the orchestrator can REACH -- the whole exposed surface, deliberately."""
    return set(definition.surface.exposed_names)

#: Kept for callers that predate the split; a product's definition is the authority now.
def orchestrator_names(definition: Any) -> set[str]:
    """Alias retained for readers of the old `ORCHESTRATOR` constant."""
    return orchestrator_reachable(definition)


#: Keyed on the facts the ANSWER depends on, and deliberately not on the context. See below.
_WORKER_OPERATIONS: dict[tuple, frozenset[str]] = {}


def worker_operations(definition: Any, context: Any) -> frozenset[str]:
    """The operations some worker holds, derived from the workers themselves.

    **It takes a real context rather than constructing one** (engine ADR-0139 addendum). Binding each
    worker's tools to read their names needs a context, and a product's context is its own type with its
    own tenancy, which the substrate cannot invent.

    **The context is NOT part of the cache key, and that is the load-bearing detail.** Which operations a
    worker holds is a static fact about the roster; the context only supplies bindings that are built and
    thrown away. Keying on it would make the cache miss on every run, and deriving this uncached added
    ~45 seconds to the fast suite when it was measured.
    """
    key = (definition.workers, frozenset(definition.surface.exposed_names), definition.surface)
    if key not in _WORKER_OPERATIONS:
        _WORKER_OPERATIONS[key] = _worker_operations(definition, context)
    return _WORKER_OPERATIONS[key]


class _NothingIsNeededHere:
    """Stands in for a product's resource bundle while deriving which worker holds which operation.

    Every attribute is `None`. The tools built during that derivation are discarded, so a real bundle
    would be work for nothing -- and naming its fields here would put one product's shape in the
    substrate, which is what this replaced.
    """

    def __getattr__(self, name: str) -> None:
        return None


def _worker_operations(definition: Any, context: Any) -> frozenset[str]:
    """The operations some worker holds, derived from the workers themselves.

    **The caller's cache is not premature.** Deriving this builds every worker and every bound tool, and
    it is consulted on each `build_orchestrator`; uncached it added ~45 seconds to the fast suite. The
    answer is a static fact about which worker holds which operation -- it does not depend on the run's
    tenancy, and the tools built here are thrown away, which is exactly why the context is outside the key.
    """
    # **A stand-in, not the product's bundle.** Building a real bundle here would name one product's
    # fields in the substrate, so a product whose bundle had different fields could not be derived from
    # at all. The tools built here are thrown away; nothing is ever read off this.
    resources = _NothingIsNeededHere()
    held = {tool.name for worker in definition.workers for tool in worker(context, resources)["tools"]}
    return frozenset(name for name in definition.surface.exposed_names
                     if definition.surface.build(context, resources, only=[name])[0].name in held)


def orchestrator_direct(definition: Any, context: Any) -> set[str]:
    """What the orchestrator calls ITSELF.

    **A tool a worker holds is not on the parent's direct surface**, and that one rule is the whole of
    this function. Before it, the orchestrator held everything a worker held -- so delegating cost it a
    turn and bought it nothing, and across six measured runs it never delegated once. The docs give
    context isolation as the reason subagents exist; a parent holding the same tools has no bloat to
    avoid.

    What is left is planning and bookkeeping: what documents exist, what work is running, what it cost.
    Cheap, bounded, and exactly what a plan is built from -- taking the listing away would blind the
    planner.
    """
    return orchestrator_reachable(definition) - set(worker_operations(definition, context))




def chat_manager_tools(context: Any, resources: Any, *, definition: Any) -> list:
    """What the conversation tier holds: the direct operations, plus the ability to ask.

    Built from the same factories as the full manifest rather than filtered afterwards, so a tool here is
    the same bound object the orchestrator would get -- one binding, two callers.
    """
    return [
        *definition.surface.build(context, resources, only=sorted(direct_names(definition))),
        clarification_tool(),
    ]


def orchestrator_tools(context: Any, resources: Any, *, definition: Any) -> list:
    """What the orchestrator calls itself: its direct operations, plus the ability to ask.

    **Not `agent_tools`.** That is the whole model-reachable surface, which the tenancy guards read; this is
    one tier's half of it.
    """
    return [
        *definition.surface.build(context, resources, only=sorted(orchestrator_direct(definition, context))),
        clarification_tool(),
    ]


def orchestrator_thinking() -> Any:
    """The planning tier KEEPS its reasoning, deliberately.

    An A/B measured one question on the chat-manager and found 1,905 reasoning tokens buying the same
    tool call as 42. **That says nothing about this tier.** The orchestrator's job is decomposition --
    which workers a question needs, and whether a result is worth accepting -- and those are the run's
    hardest decisions. Turning its reasoning off on the chat-manager's evidence would be precisely the
    kind of unmeasured change that has had to be reverted before.

    Present rather than absent so the decision is visible: a tier with no `HowMuchToThink` would look
    like one nobody had considered.
    """
    from rag_wright.harness.thinking import HowMuchToThink

    return HowMuchToThink(reasoning=True, tier="orchestrator")


def build_orchestrator(
    context: Any,
    resources: Any,
    settings: Any,
    *,
    model: Any = None,
    tools: Any = None,
    system_prompt: str | None = None,
    extra_middleware: list | None = None,
    #: **The product's agent: its workers and its tools.** The substrate builds none of its own, so a
    #: greenfield product gets its own workers and tools and nobody else's, which
    #: `tests/harness/test_greenfield_conformance.py` asserts.
    #:
    #: **Required, with no default.** A default here would be one product's agent, and a default is a
    #: dependency wearing a convenience.
    definition: Any,
    checkpointer: Any = None,
    store: Any = None,
    ledger: Any = None,
) -> Any:
    """The planning tier, compiled as an agent in its own right.

    **`context_schema` is declared rather than inherited.** A compiled child does receive the parent's
    context without declaring it — pinned in `test_harness_grounding.py` — but declaring it is what lets
    this be driven DIRECTLY, which the orchestration eval does and which `start_run` refuses without the schema.

    `model`, `tools`, `system_prompt` and `extra_middleware` are overridable for tests and evals;
    **production passes none of them.** `system_prompt` and `extra_middleware` exist so an eval can run
    the two orchestration arms through the REAL builder rather than assembling its own agent -- an eval
    that builds its own would be measuring something the product does not ship.
    """
    from deepagents import create_deep_agent
    from langchain.agents.middleware.todo import TodoListMiddleware

    from rag_wright.harness.agent import agent_chat_model
    from rag_wright.harness.batch import BatchTheQuestions
    from rag_wright.harness.decisions import WhatTheRunDecided
    from rag_wright.harness.errors import error_middleware
    from rag_wright.harness.interpreter import code_interpreter
    from rag_wright.harness.skills import orchestrator_skill
    from rag_wright.harness.withhold import WITHHELD_FROM_ORCHESTRATOR, withhold


    # **One ledger, and one is MADE when the caller supplies none.** `build_chat_agent` passes
    # its own down, but the decomposition eval drives this builder directly -- and a harness that measures
    # decisions while recording none would be the exact shape of instrument this phase was reset over.
    decisions = (ledger if ledger is not None
                 else WhatTheRunDecided(points=definition.decision_points))

    return create_deep_agent(
        model=model if model is not None else agent_chat_model(
            settings, definition.model_id_for(settings)),
        tools=tools if tools is not None else orchestrator_tools(context, resources,
                                                                definition=definition),
        system_prompt=(system_prompt if system_prompt is not None
                       else definition.prompts.orchestrator),
        context_schema=definition.context_schema,
        # **No `ConfirmTheView` here.** The view belongs to the conversation, and a tier that never talks
        # to the user has nothing to confirm -- it would ask about a screen it cannot see.
        # **Batching clarifications lives HERE, not on the chat-manager**, which runs no workers and so has nothing to
        # batch: the only question it ever sees is its own.
        middleware=[
            withhold(*WITHHELD_FROM_ORCHESTRATOR),
            orchestrator_thinking(),
            # **The mechanism for batched clarifications and authored product knowledge.** What
            # goes in it is decided by FREQUENCY: the decomposition rules are needed every turn and stay
            # in `ORCHESTRATOR_PROMPT`; batching a clarification is needed only when a worker returns
            # `blocked`, so it is read on demand and costs one line until then.
            orchestrator_skill(preamble=definition.prompts.skills_preamble,
                               label=definition.prompts.label),
            # **`write_todos` is OURS to add now, and that is a change, not a preference.** Up to
            # `deepagents` 0.6.12 the todo middleware was part of the base stack every agent got for
            # free. 0.7 moved it to `langchain.agents.middleware.todo` and stopped adding it, so the
            # upgrade silently took the planning tier's todo list away -- a product's TODO panel renders
            # exactly this list, so losing it would have deleted a product surface with the suite still
            # green everywhere else. `test_THE_PLANNING_TIER_KEEPS_IT` is what caught it.
            #
            # **Only this tier gets it.** Declarative subagents do not inherit the main agent's
            # middleware, so workers stay without a todo list -- which is what `WITHHELD_FROM_WORKERS`
            # was asking for by hand when the framework still forced one on them.
            TodoListMiddleware(),
            # **Only this tier gets an interpreter.** Whether it is USED is a measured
            # question, not a settled one -- an eval compares a coded fan-out against a prompted one.
            # **The interpreter's OWN guidance is left intact, including the fan-out lesson** -- it was
            # removed once and put back. The observation that motivated removing it stands and
            # is not lost: the EVAL's prompted arm withholds `eval` while this teaches dispatching from
            # JavaScript, so that arm is taught to use a tool it is denied. That is an inconsistency in
            # `evals/orchestration.py` to fix in the eval, and was never a reason to change what
            # production tells the model (`docs/design/robustness-before-speed.md` section 4).
            # **The surface is passed, not imported**: the interpreter bridges the product's own
            # surface, never one the substrate chose.
            code_interpreter(context, resources, surface=definition.surface),
            *_remembering(definition), *_recording(decisions, definition), BatchTheQuestions(), *error_middleware(retry_on=definition.retry_on,
                             message_for=definition.message_for),
            *(extra_middleware or []),
        ],
        # **Workers hang off the orchestrator, never off the chat-manager**. A worker on the
        # conversation tier would put planning on the path of an unambiguous listing.
        subagents=[worker(context, resources, decisions) for worker in definition.workers],
        checkpointer=checkpointer,
        store=store,
        name="orchestrator",
    )


def _recording(ledger: Any, definition: Any) -> list:
    """The decision ledger, on the tier the dispatch decisions are ATTRIBUTED to.

    **Attached here even though the dispatches are observed on the workers**, because this tier takes the
    decisions that are not dispatches -- `D4`, escalate or proceed -- and they land next. An orchestrator
    without it would leave the ledger able to say what was dispatched and never who chose it.
    """
    from rag_wright.harness.decisions import (
        DISPATCHER_OF_THE_ORCHESTRATOR,
        RecordTheDecisions,
    )

    return ([RecordTheDecisions(ledger, tier="orchestrator", framings=definition.framings,
                                read_framing=definition.read_framing,
                               dispatched_by=DISPATCHER_OF_THE_ORCHESTRATOR,
                               worker_names=tuple(w.__name__ for w in definition.workers))]
            if ledger is not None else [])


def _remembering(definition: Any) -> list:
    """The run record and the two middlewares that read it.

    **The orchestrator repeats itself too**: it re-dispatched a worker with a byte-identical brief, and
    called `grep('3f1c9d2a', '/')` hunting a check id. 25 of 194 recorded tool calls were exact repeats.
    """
    from rag_wright.harness.filesystem import TheSubjectIsNotAFilesystem
    from rag_wright.harness.seen import (
        question_encoder,
        OneAnswerPerTurn,
        RememberWhatWasCalled,
        WhatTheRunHasSeen,
    )

    seen = WhatTheRunHasSeen(repeatable=definition.repeatable,
                             question_argument=definition.question_argument)
    return [OneAnswerPerTurn(),
            RememberWhatWasCalled(seen, encoder=question_encoder(),
                                  carry_forward=definition.carry_forward),
            TheSubjectIsNotAFilesystem(seen, signposts=definition.prompts)]




def orchestrator_subagent(context: Any, resources: Any, settings: Any, *, definition: Any,
                          **kwargs: Any) -> dict[str, Any]:
    """The orchestrator as the chat-manager sees it.

    A `CompiledSubAgent` rather than a `SubAgent` spec, because this tier is a deep agent in its own right:
    a `SubAgent` spec would have the parent build it, and its prompt, middleware and its
    workers would be the parent's to supply.
    """
    return {
        "name": "orchestrator",
        "description": definition.prompts.orchestrator_description,
        "runnable": build_orchestrator(context, resources, settings, definition=definition, **kwargs),
    }
