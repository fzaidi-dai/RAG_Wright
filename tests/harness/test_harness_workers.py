"""The workers: what a worker is given, what it hands back, and that alone it is the worker under a parent.

Ported from RuleWright under ADR-0139's handover (PS-48g); the substrate is `rag_wright.harness.workers` with the
tier builders above it, driven through the fabric product. RuleWright's tests of its own roster (which worker holds
which of its tools, its reading tool, its report fields and its prompts) stay in RuleWright.

**The fault these guard was structural, and it made the tier design decorative.** An orchestrator holding every
tool its worker held never delegated, because delegating cost a turn and bought nothing. And DeepAgents adds a
`general-purpose` subagent with *"access to all tools as the main agent"*, a second route round every specialist.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import inspect
import textwrap
from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from rag_wright.harness import agent as agent_module
from rag_wright.harness import tiers as tiers_module
from rag_wright.harness.agent import build_chat_agent
from rag_wright.harness.decisions import RecordTheDecisions, WhatTheRunDecided
from rag_wright.harness.errors import error_middleware
from rag_wright.harness.tiers import _recording, build_orchestrator
from rag_wright.harness.workers import (
    FRAMEWORK_DEFAULTS,
    NOT_ON_WORKERS,
    WorkerReport,
    build_worker,
    worker_middleware,
    worker_spec,
)
from tests.harness.fabric import (
    FabricContext,
    FabricFindings,
    _a_context,
    _fabric,
    _fabric_message_for,
    _resources,
    _settings,
    scripted,
)


def _specs(ledger: Any = None) -> list[dict]:
    definition = _fabric()
    return [worker(_a_context(), _resources(), ledger) for worker in definition.workers]


def _never_invoked() -> Any:
    """A model for a tier that is built and never run."""
    return scripted(AIMessage(content="never invoked"))


def _offered_subagents(agent: Any) -> set[str]:
    """The subagents a compiled tier's `task` tool OFFERS: the entries of its "Available agent types" list.

    **The list, not the description.** The description also mentions `general-purpose` in its usage notes, so a
    substring search reports it as available when it is not -- prose read as code."""
    description = agent.nodes["tools"].bound.tools_by_name["task"].description
    listed = description.split("Available agent types", 1)[1].split("\n\n", 1)[0]
    return {line.removeprefix("- ").split(":")[0] for line in listed.splitlines() if line.startswith("- ")}


# --- what a worker is given -------------------------------------------------------------------------------


def test_NO_WORKER_IS_GIVEN_THE_CLARIFICATION_TOOL() -> None:
    """**The mechanism, not the instruction.** A prompt saying "do not ask" is a suggestion; a tool the worker was
    never given is a fact. Derived over the roster, so a worker added later is covered the day it is written."""
    specs = _specs()

    assert specs, "there are no workers; this guard is checking nothing"
    offenders = {s["name"] for s in specs if any(t.name == "ask_the_user" for t in s["tools"])}
    assert not offenders, f"these workers can interrupt on their own: {offenders}"


def test_EVERY_WORKER_RETURNS_A_STRUCTURED_REPORT_BEYOND_THE_ENVELOPE() -> None:
    """**Chainable, not prose**: the interpreter's workflow spreads a subagent's result (`{...f, ...v}`), and a
    paragraph cannot be spread. **Beyond the envelope**, because a worker returning bare `WorkerReport` satisfies
    `issubclass` while carrying no findings at all."""
    for spec in _specs():
        report = spec.get("response_format")
        assert report is not None and issubclass(report, WorkerReport), f"{spec['name']} has no structured report"
        assert set(report.model_fields) > set(WorkerReport.model_fields), (
            f"{spec['name']} returns only `status` and `need`, so every finding would come back as prose"
        )


def test_EVERY_REPORT_CAN_SAY_IT_IS_BLOCKED() -> None:
    """Taking the asking away only works if there is something to say instead."""
    for spec in _specs():
        assert {"status", "need"} <= set(spec["response_format"].model_fields)


def test_NO_REPORT_LETS_A_WORKER_NAME_THE_SCOPE() -> None:
    """**A surface the tenancy guards never see.** A `response_format` becomes a tool the worker's model fills,
    generated from the report rather than from the product's surface; a field the model fills is a field an
    injected passage can fill."""
    forbidden = {f.name for f in dataclasses.fields(FabricContext)} | {"scope", "shared_scope"}

    for spec in _specs():
        leaked = forbidden & set(spec["response_format"].model_fields)
        assert not leaked, f"{spec['name']}'s report lets the model name the scope: {sorted(leaked)}"


def test_EVERY_WORKER_IS_TOLD_WHAT_THE_PRODUCT_SAYS_ABOUT_ITS_FILESYSTEM() -> None:
    """**A field nothing read** (PS-48g). `AgentPrompts.worker_filesystem_note` is what a worker is told about its
    own filesystem; a worker that is not told runs `ls('/')` to orient, and the run is marked down for reaching
    for the subject. The note was declared and never used, so a product that wrote one told its workers nothing."""
    note = _fabric().prompts.worker_filesystem_note

    for spec in _specs():
        assert note in spec["system_prompt"], f"{spec['name']} is not told about its own filesystem"


def test_A_PROMPT_THAT_ALREADY_CARRIES_THE_NOTE_IS_NOT_TOLD_TWICE() -> None:
    """A product that wrote the note into its worker prompts by hand, as the first one did, keeps one copy."""
    note = _fabric().prompts.worker_filesystem_note
    spec = worker_spec("w", "d", f"Match compositions.\n\n{note}", [], FabricFindings, definition=_fabric())

    assert spec["system_prompt"].count(note) == 1


def test_WITHOUT_A_DEFINITION_THE_PROMPT_IS_THE_PRODUCTS_OWN() -> None:
    """The note comes from the definition; with none there is nothing to add."""
    assert worker_spec("w", "d", "Match compositions.", [], FabricFindings)["system_prompt"] == "Match compositions."


# --- the roster a tier offers -------------------------------------------------------------------------


def test_THE_ORCHESTRATOR_OFFERS_EVERY_WORKER_AND_NOTHING_ELSE() -> None:
    """**Read off the compiled tier**, because a subagent list is easy to drop and `general-purpose` is added
    unless it is disabled. **With a model the product passed**, which is what every scripted test and eval does:
    the catch-all was disabled only for a model the engine built itself, so a passed one left it in (PS-48g)."""
    offered = _offered_subagents(build_orchestrator(_a_context(), _resources(), _settings(), definition=_fabric(),
                                                    model=_never_invoked()))

    assert offered == {spec["name"] for spec in _specs()}, f"offered: {sorted(offered)}"


def test_THE_CHAT_MANAGER_OFFERS_THE_ORCHESTRATOR_AND_NOTHING_ELSE() -> None:
    """The same catch-all on the top tier would be a route round the orchestrator itself."""
    offered = _offered_subagents(build_chat_agent(_a_context(), _resources(), _settings(), definition=_fabric(),
                                                  model=_never_invoked()))

    assert offered == {"orchestrator"}, f"offered: {sorted(offered)}"


def test_A_WORKER_IS_NOT_BOUND_THE_TOOLS_A_TIER_WITHHOLDS() -> None:
    """**Measured, and the reason `WithheldTools` is not on workers.** A worker is bound the filesystem tools and
    NOT `task`, `execute` or `eval`. If the library changes that, withholding becomes necessary and this fails
    rather than leaving a worker with an interpreter."""
    seen: dict[str, list[str]] = {}

    def spy(label: str, *messages: AIMessage) -> Any:
        model = scripted(*messages)

        def bind_tools(tools: Any, **kwargs: Any) -> Any:
            seen.setdefault(label, sorted(getattr(t, "name", str(t)) for t in tools))
            return model

        object.__setattr__(model, "bind_tools", bind_tools)
        return model

    @tool
    def only_mine(x: str) -> str:
        """A worker tool."""
        return "ok"

    parent = create_deep_agent(
        model=spy("parent", AIMessage(content="", tool_calls=[{"name": "task", "id": "1", "type": "tool_call",
                                                               "args": {"description": "go", "subagent_type": "w"}}]),
                  AIMessage(content="done")),
        tools=[],
        subagents=[{"name": "w", "description": "w", "system_prompt": "w", "tools": [only_mine],
                    "model": spy("worker", AIMessage(content="done"))}])
    asyncio.run(parent.ainvoke({"messages": [{"role": "user", "content": "go"}]}))

    bound = set(seen["worker"])
    assert not bound & {"execute", "eval", "task"}, f"a worker is now bound {sorted(bound & {'execute', 'eval', 'task'})}"
    assert {"ls", "glob", "grep", "read_file"} <= bound, "a worker no longer has the filesystem tools"


# --- the middleware audit -----------------------------------------------------------------------------


def test_A_WORKER_CARRIES_THE_SAME_ERROR_MODEL_AS_A_TIER() -> None:
    """**The defect the tier split introduced.** The call caps and the error model sat on the two tiers above and
    not on the tier doing the work, so a worker that looped was unbounded and a transient failure inside one was
    never retried."""
    carried = {type(m).__name__ for m in worker_middleware()}
    required = {type(m).__name__ for m in error_middleware(retry_on=_fabric().retry_on,
                                                           message_for=_fabric_message_for)}

    assert required <= carried, f"workers are missing {sorted(required - carried)}"
    assert "TheSubjectIsNotAFilesystem" in carried, (
        "a worker has the same filesystem tools as a tier and is the one most likely to try to open a subject"
    )


#: Lowercase helpers in a builder, and the middleware each constructs. **A factory missing from here is a
#: middleware the audit cannot see**, which is the failure the audit exists to prevent, one level up.
FACTORIES = {
    "withhold": "WithheldTools",
    "code_interpreter": "CodeInterpreterMiddleware",
    "chat_thinking": "HowMuchToThink",
    "dispatch_bound": "OnlySoManyDispatches",
    "orchestrator_thinking": "HowMuchToThink",
    "orchestrator_skill": "SkillsMiddleware",
}

#: Lowercase calls in a builder that build something other than middleware, or whose middleware is carried by
#: workers already (`error_middleware`, `_remembering`, `_recording`).
NOT_MIDDLEWARE = {
    "agent_chat_model", "chat_manager_tools", "orchestrator_tools", "orchestrator_subagent",
    "build_memory_backend", "memory_permissions", "create_deep_agent", "error_middleware", "worker",
    "build_orchestrator", "len", "dict", "_remembering", "_recording", "catch_all_disabled", "direct_names",
    "forces_a_tool",
    "frozenset",
}


def _calls_in(builder: Any) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(builder)))
    return {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def test_EVERY_FACTORY_IN_A_BUILDER_NAMES_WHAT_IT_BUILDS() -> None:
    """**Anti-vacuity for the audit below.** It reads capitalised calls, so a middleware built inside a lowercase
    helper is invisible unless `FACTORIES` names it. Every lowercase call is accounted for or plainly not one."""
    for builder in (agent_module.build_chat_agent, tiers_module.build_orchestrator):
        unexplained = {n for n in _calls_in(builder) if n[:1].islower()} - set(FACTORIES) - NOT_MIDDLEWARE
        assert not unexplained, f"{builder.__name__} calls {sorted(unexplained)}; classify each one here"


def test_EVERY_TIER_MIDDLEWARE_IS_ON_WORKERS_OR_EXPLAINED() -> None:
    """**Derived both ways, so the audit maintains itself.** A middleware added to a tier forces a decision about
    workers on the same commit, rather than being discovered when a worker loops."""
    def built_in(builder: Any) -> set[str]:
        names = _calls_in(builder)
        return {n for n in names if n[:1].isupper()} | {made for f, made in FACTORIES.items() if f in names}

    on_tiers = built_in(agent_module.build_chat_agent) | built_in(tiers_module.build_orchestrator)
    # Capitalised calls that construct something other than middleware: the run's record and its saver.
    on_tiers -= {"WhatTheRunDecided", "InMemorySaver"}
    # **Built the way production builds a worker**, with a run ledger: bare, the recorder is missing.
    carried = {type(m).__name__ for m in worker_middleware(WhatTheRunDecided(), "composition_worker")}

    unexplained = {n for n in on_tiers if n not in carried and n not in NOT_ON_WORKERS}
    assert not unexplained, f"{sorted(unexplained)} is on a tier but neither on workers nor in NOT_ON_WORKERS"
    for name, reason in NOT_ON_WORKERS.items():
        assert len(reason) > 40, f"{name}'s reason is too short to mean anything: {reason!r}"


def _wrapping(agent: Any) -> set[str]:
    """Every middleware in a compiled agent's `wrap_model_call` chain, which `agent.nodes` cannot show."""
    node = agent.nodes["model"].bound
    names: set[str] = set()
    seen: set[int] = set()

    def walk(function: Any, depth: int = 0) -> None:
        if function is None or depth > 40 or id(function) in seen:
            return
        seen.add(id(function))
        for cell in getattr(function, "__closure__", None) or ():
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            owner = getattr(value, "__self__", None)
            if isinstance(value, AgentMiddleware):
                names.add(type(value).__name__)
            elif isinstance(owner, AgentMiddleware):
                names.add(type(owner).__name__)
            elif callable(value):
                walk(value, depth + 1)

    walk(getattr(node, "func", None) or getattr(node, "_func", None))
    return names


def test_OUR_MIDDLEWARE_ADDS_TO_THE_FRAMEWORKS_RATHER_THAN_REPLACING_IT() -> None:
    """**Observed, because reading node names would say the opposite.** Only `before_*`/`after_*` hooks become
    nodes, so the summarizer -- which compacts a context that outgrows its bounds -- is invisible in `agent.nodes`.
    Passing `middleware=` must APPEND to the framework's stack, and this checks it rather than trusting it."""
    @tool
    def mine(x: str) -> str:
        """A tool."""
        return "ok"

    class Marker(AgentMiddleware):
        def before_model(self, state: Any, runtime: Any) -> None:
            return None

    defaults = _wrapping(create_deep_agent(model=scripted(AIMessage(content="x")), tools=[mine]))
    with_ours = _wrapping(create_deep_agent(model=scripted(AIMessage(content="x")), tools=[mine],
                                            middleware=[Marker()]))

    assert "_DeepAgentsSummarizationMiddleware" in defaults, f"no summarizer in the defaults: {sorted(defaults)}"
    assert set(FRAMEWORK_DEFAULTS) & defaults, "the recorded defaults no longer match the framework's"
    assert defaults <= with_ours, f"our middleware REPLACED the defaults: lost {sorted(defaults - with_ours)}"


# --- a worker alone is the worker under the parent ----------------------------------------------------


def _production_subagents(agent: Any) -> dict:
    """The subagents a real orchestrator compiled, dug out of its `task` tool. **Fragile on purpose, and only in a
    test**: writing down what the base stack contains is what went stale; digging fails loudly instead."""
    task = agent.nodes["tools"].bound.tools_by_name["task"]
    function = getattr(task, "func", None) or task.coroutine
    closure = dict(zip(function.__code__.co_freevars, (c.cell_contents for c in function.__closure__), strict=True))
    assert "subagent_graphs" in closure, "the `task` tool no longer closes over `subagent_graphs`"
    return closure["subagent_graphs"]


def _tools_of(agent: Any) -> list[str]:
    node = agent.nodes["tools"]
    return sorted(getattr(getattr(node, "bound", node), "tools_by_name", {}))


def _system_prompt_of(agent: Any) -> str:
    """The system prompt baked into a compiled agent, read from its model node's closure."""
    inner = getattr(agent.nodes["model"], "bound", agent.nodes["model"])
    function = getattr(inner, "func", None) or getattr(inner, "afunc", None)
    closure = dict(zip(function.__code__.co_freevars, (c.cell_contents for c in function.__closure__), strict=True))
    assert "system_message" in closure, "the model node no longer closes over `system_message`"
    message = closure["system_message"]
    return str(getattr(message, "content", message) or "")


def _production() -> dict:
    return _production_subagents(build_orchestrator(_a_context(), _resources(), _settings(), definition=_fabric(),
                                                    model=_never_invoked()))


def test_BUILD_WORKER_COMPILES_WHAT_PRODUCTION_RUNS() -> None:
    """**An eval that builds its own agent measures something the product does not ship.** `create_deep_agent`
    prepends a base stack to every raw subagent spec; compiling one with `create_agent` skipped it, and the
    isolated worker was offered three tools where the real one was offered ten."""
    production = _production()
    assert set(production) == {spec["name"] for spec in _specs()}

    for spec in _specs():
        theirs, ours = production[spec["name"]], build_worker(spec, _settings(), model=_never_invoked())
        assert _tools_of(ours) == _tools_of(theirs), f"{spec['name']}: {_tools_of(ours)} vs {_tools_of(theirs)}"
        assert sorted(ours.nodes) == sorted(theirs.nodes)


def test_THE_COMPILED_WORKER_IS_NOT_BARE() -> None:
    """**Anti-vacuity.** The comparison above would pass forever on two empty surfaces."""
    spec = _specs()[0]
    offered = set(_tools_of(build_worker(spec, _settings(), model=_never_invoked())))

    assert {t.name for t in spec["tools"]} <= offered, "the worker lost its OWN tools"
    assert {"ls", "read_file", "glob", "grep"} <= offered, "the framework's base stack is absent"


def test_A_WORKER_ALONE_GETS_THE_SAME_SYSTEM_PROMPT_AS_UNDER_A_PARENT() -> None:
    """**The half the first fix missed.** Compiling on the main-agent path prepends a generic preamble a
    subagent never sees: 2,260 characters, and two workers then measured 9% CHEAPER orchestrated than alone."""
    production = _production()

    for spec in _specs():
        ours = _system_prompt_of(build_worker(spec, _settings(), model=_never_invoked()))
        assert ours == _system_prompt_of(production[spec["name"]]), f"{spec['name']}'s prompts differ"


def test_THE_PROMPT_GUARD_IS_READING_A_REAL_PROMPT() -> None:
    """**Anti-vacuity.** Two empty strings compare equal."""
    spec = _specs()[0]
    prompt = _system_prompt_of(build_worker(spec, _settings(), model=_never_invoked()))

    assert prompt.strip(), "the worker's system prompt is empty"
    assert spec["system_prompt"] in prompt, "this is not the worker's own prompt"


# --- the surface a worker's model is offered ----------------------------------------------------------


class _Offered(AgentMiddleware):
    """Records the tools a tier's model is offered on each turn. **What arrives at the model**, not what the tool
    node holds: `WithheldTools` overrides the request, so a withheld tool stays in the node unseen."""

    def __init__(self, into: set[str]) -> None:
        super().__init__()
        self._into = into

    @property
    def name(self) -> str:
        return "Offered"

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        self._into.update(getattr(t, "name", str(t)) for t in (request.tools or []))
        return await handler(request)


def _offered_to(spec: dict) -> set[str]:
    offered: set[str] = set()
    running = {**spec, "model": scripted(AIMessage(content="done")),
               "middleware": [*spec["middleware"], _Offered(offered)]}
    asyncio.run(build_worker(running, _settings(), model=_never_invoked()).ainvoke(
        {"messages": [{"role": "user", "content": "go"}]}, context=_a_context()))
    return offered


def test_NO_WORKER_IS_OFFERED_THE_PLANNING_TOOL() -> None:
    """**A worker executes one brief; it does not plan.** Measured: one worker made 56 calls and 35 were
    `write_todos`, rewriting a task list until the run hit its ceiling."""
    offenders = {spec["name"] for spec in _specs() if "write_todos" in _offered_to(spec)}

    assert not offenders, f"these workers are offered the planning tool: {sorted(offenders)}"


def test_THE_PLANNING_TIER_KEEPS_IT() -> None:
    """**What stops the fix above from being a removal.** A product's TODO panel renders the orchestrator's list."""
    offered: set[str] = set()
    context = _a_context()
    agent = build_orchestrator(context, _resources(), _settings(), definition=_fabric(),
                               model=scripted(AIMessage(content="done")), extra_middleware=[_Offered(offered)])
    asyncio.run(agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, context=context))

    assert "write_todos" in offered, "the planning tier lost `write_todos`"


def test_A_WORKER_KEEPS_WHAT_IT_NEEDS() -> None:
    """**Anti-vacuity.** A withhold list that took everything would pass the test above by leaving the worker unable
    to work. Recall is `glob`/`grep` over its notes, kept deliberately."""
    offered = _offered_to(_specs()[0])

    assert {"ls", "glob", "grep", "read_file"} <= offered, f"the worker lost recall: {sorted(offered)}"
    assert "match_compositions" in offered, f"the worker lost its own tools: {sorted(offered)}"


# --- every worker carries the product's policy, not the substrate's inert defaults --------------------


def test_EVERY_WORKER_CARRIES_THE_PRODUCTS_POLICY() -> None:
    """**Asserted on the consequence, not on the call.** Every policy parameter of `worker_spec` has an inert
    default, and a worker built without them is silently less capable while every test stays green -- the first
    product's three workers were in exactly that state. So the middleware of every worker in the roster is
    interrogated."""
    definition = _fabric()

    for spec in _specs(WhatTheRunDecided()):
        middleware = spec["middleware"]

        retry_sets = [tuple(m.retry_on) for m in middleware if getattr(m, "retry_on", None) is not None]
        assert retry_sets and all(retry_sets), f"{spec['name']} retries nothing"
        assert all(set(r) == set(definition.retry_on) for r in retry_sets)

        remembering = [m for m in middleware if hasattr(m, "_seen")]
        assert remembering, f"{spec['name']} carries nothing that reads the run record"
        for m in remembering:
            assert m._seen.repeatable == definition.repeatable
            assert m._seen.question_argument == definition.question_argument

        deciding = [m for m in middleware if isinstance(m, RecordTheDecisions)]
        assert deciding, f"{spec['name']} records no decisions"
        assert all(tuple(m._framings) == definition.framings for m in deciding)
        assert all(m._read_framing is definition.read_framing for m in deciding)

        signposting = [m for m in middleware if hasattr(m, "_signposts")]
        assert signposting and all(m._signposts == definition.prompts for m in signposting), (
            f"{spec['name']} would answer a wrong filesystem call with a refusal naming none of the product's tools"
        )


def test_THE_LEDGER_A_TIER_BUILDS_KNOWS_THE_PRODUCTS_DECISIONS() -> None:
    """**A ledger with no decision points still records every decision -- against an empty option space**, so a
    choice reads as one between nothing and nothing. Asserted on the ledger and observer the builders make."""
    definition = _fabric()
    ledger = WhatTheRunDecided(points=definition.decision_points)

    assert ledger.of("run-1").record("F1", tier="composition_worker", chose="fibre").options == ("fibre", "finish")

    observers = [m for m in _recording(ledger, definition) if isinstance(m, RecordTheDecisions)]
    assert observers, "the orchestrator's stack carries no decision observer"
    for observer in observers:
        assert tuple(observer._framings) == definition.framings
        assert observer._read_framing is definition.read_framing
