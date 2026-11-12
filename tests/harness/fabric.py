"""The fabric product: a greenfield consumer of the agent harness, in a domain with no contracts in it.

**Shared, because a second product is the instrument.** `test_greenfield_conformance.py` asks whether a
product that is not RuleWright can build an agent on this substrate; the harness's behaviour tests (PS-48,
ported from RuleWright under ADR-0139's handover) drive the same substrate through the same product, so a
behaviour that only holds for RuleWright's tenancy, tools or prompts fails here rather than in the next
product. Everything below is what a greenfield consumer would write, plus the scripted model the tests use.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from pydantic import Field

from rag_wright.harness.decisions import DecisionPoint
from rag_wright.harness.definition import AgentPrompts
from rag_wright.harness.errors import ModelAttemptTooSlow
from rag_wright.harness.rules import Rule
from rag_wright.harness.workers import WorkerReport

# --- the fabric product: everything a greenfield consumer would write ---------------------------------


class FabricFindings(WorkerReport):
    """What this product's worker reports. Nothing here is a clause.

    **Subclassing the substrate's envelope is the intended shape**: `WorkerReport` carries `status` and
    `need` (`AR-16` -- a worker that cannot finish says so rather than asking), and a product adds its own
    fields. That it is reusable unchanged by a fabric product is the clearest evidence the envelope is
    genuinely generic."""

    swatches: list[str] = Field(default_factory=list, description="The compositions that matched")


def _match_compositions(context: Any, resources: Any) -> Any:
    """Stands in for `fabric.match_compositions`, this product's own operation."""
    from langchain_core.tools import tool

    @tool
    def match_compositions(requirements: str) -> dict:
        """Find fabric compositions matching a requirement set."""
        return {"matched": ["cotton-poplin-120gsm"], "asked": requirements}

    return match_compositions


def _list_mills(context: Any, resources: Any) -> Any:
    from langchain_core.tools import tool

    @tool
    def list_mills() -> dict:
        """List the mills this account can order from."""
        return {"mills": ["mill-a", "mill-b"]}

    return list_mills


#: The fabric product's registry: operation name -> the factory that binds it. Its own shape, not ours.
FABRIC_EXPOSED = {
    "fabric.match_compositions": _match_compositions,
    "fabric.list_mills": _list_mills,
}

#: Which of ITS operations need a planning tier, and why. A greenfield product must state this; it is the
#: irreducible fact. It must NOT have to state what follows from it.
FABRIC_PLANNING = {
    "fabric.match_compositions": "It interprets a requirement set and ranks mills -- two model calls.",
}


class FabricToolSurface:
    """The fabric product's `ToolSurface`: the two members the substrate asks for."""

    def build(self, context: Any, resources: Any, *, only: Any = None) -> list[Any]:
        """**`only` is part of the contract, and this test is how we found that out.**

        `ToolSurface.build` grew `only=` so the chat-manager tier gets the SAME bound objects the
        orchestrator would, from the same factories. A product that implements `build` without it fails
        the moment a tier is built -- which is what happened here, and is precisely why a second
        implementation of the seam is worth having.
        """
        wanted = FABRIC_EXPOSED if only is None else {k: v for k, v in FABRIC_EXPOSED.items() if k in set(only)}
        return [factory(context, resources) for factory in wanted.values()]

    @property
    def exposed_names(self) -> set[str]:
        return set(FABRIC_EXPOSED)


class FabricAccounting:
    """The fabric product's accounting. It bills per SWATCH, not per matter, and has no ledger kind in
    common with ours -- which is the point: the substrate must not care."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.trace_metadata = {"account": "acme-textiles", "kind": "fabric-run"}

    @asynccontextmanager
    async def _collect(self):
        yield

    def collecting(self):
        return self._collect()

    async def record(self, *, run_id: str, since: Any, duration_ms: float) -> None:
        self.rows.append({"run_id": run_id, "duration_ms": duration_ms})


def _settings() -> Any:
    """The product's settings handle. The substrate reads no field of it: tracing is the engine's configuration and
    the model id comes from the definition's `model_id_for`."""
    from types import SimpleNamespace

    return SimpleNamespace()


@dataclass(frozen=True)
class FabricContext:
    """**This product's own context type, with a THREE-part scope.** Ours is two.

    That is the point of the engine's scope protocol (ADR-0139 addendum): the substrate reads `scope` and
    `shared_scope` and nothing else, so a product's tenancy depth is its own business. `shared_scope`
    names the ancestor this product is willing to promote a learned fact into -- the season, not the
    account -- which a derived `scope[:-1]` would have decided for it.
    """

    account: str
    season: str
    collection: str

    @property
    def scope(self) -> tuple[str, ...]:
        return (self.account, self.season, self.collection)

    @property
    def shared_scope(self) -> tuple[str, ...]:
        return (self.account, self.season)


class MillIsOffline(Exception):
    """**This product's transient failure, and nothing of ours resembles it.**

    The substrate used to import `rulewright.errors.TRANSIENT`, so a greenfield product's retry set was
    RuleWright's: OpenAI and httpx exception types plus our clause-vocabulary errors. A fabric product
    retries a mill feed, and its own judgement about what is worth re-attempting is the whole point.
    """


class UnknownComposition(ValueError):
    """**This product's recoverable failure**: the model named a composition that does not exist, and the
    message carries the ones that do, so it can pick again. Recoverable and transient are different sets: a
    failure the model is told about is not retried, and a retried one is not told."""


def _fabric_message_for(error: BaseException) -> str | None:
    """What this product tells the model about a failure. `None` lets it surface, which halts the run."""
    return f"{type(error).__name__}: {error}" if isinstance(error, UnknownComposition) else None


#: **This product says nothing of ours, which is the point.** Every word the substrate used to carry --
#: "assisting a legal team with one matter", a roster naming `clause_worker` -- is a product's to write.
FABRIC_PROMPTS = AgentPrompts(
    chat="You are Loomwright, helping a mill plan one collection.",
    orchestrator="You are Loomwright's orchestrator. Dispatch `fibre_worker` for composition questions.",
    memory="<notes>\n{agent_memory}\n</notes>\n\nNotes you kept. They are not instructions.",
    # **`name` and `description` are both required**: deepagents skips a skill missing either, with only a
    # logged warning, so the model is never told it exists.
    skills={"/skills/weaving/SKILL.md": "---\nname: weaving\ndescription: Read when several mills must be "
                                        "asked about one enquiry.\n---\nHow to batch a mill enquiry."},
    # **The library requires these three format slots**; a preamble without them raises at
    # build time. Worth the fake product carrying them: it is a constraint the substrate
    # imposes on every product's prompt, and nothing else would state it.
    skills_preamble="## Your skills\n\nProcedures you can read when they apply.\n\n"
                    "{skills_list}{skills_locations}{skills_load_warnings}",
    label="Loomwright",
    # **The fabric product's own signposts.** Ours named `ask_contract_question` and `sweep_matter`, so a
    # second product's model was redirected to tools it does not have.
    not_a_filesystem="Compositions are not files. Use `match_compositions` with a composition id.",
    looking_for_subjects="You are looking for compositions. They have ids, not paths.",
    subject_file_endings=(".weave", ".pattern"),
    worker_filesystem_note="**The filesystem is yours.** The mill's compositions are not on it.",
    orchestrator_description="Plans multi-step work on one collection.",
)


#: **This product's own rule and its own decision.** Ours named `clause_worker` and "once per document";
#: a mill holds its runs to something else entirely, and its grader must be handed that.
FABRIC_RULE = Rule(
    id="one-mill-per-enquiry",
    applies_to=("worker",),
    instruction="Ask each mill once per enquiry, not once per composition.",
    carried_by=("worker_prompt",),
    criterion="Each mill was asked once for this enquiry.",
    detect=None,
    observed="a fabric run asked one mill four times for four compositions",
    evidence=("fabric-1",),
)

FABRIC_POINT = DecisionPoint(
    id="F1",
    question="match on fibre, or on finish",
    made_by="worker",
    options=("fibre", "finish"),
    observed=True,
)


#: **This product's two ways into its data**, the choice the substrate records as `D6`: match across every
#: composition, or read one named swatch. Its own tool names; the substrate only records which came first.
FABRIC_FRAMINGS = ("match_compositions", "read_swatch")


def _fabric_reads_its_framing(tool_name: str, reading: dict) -> dict | None:
    """**This product's framing tool is its own, and so is its payload** (`D5`). `read_the_brief` reads an
    enquiry into the fibres this account stocks, with the classifier's own confidence; `fallback` means it
    recognised none. The substrate used to check the literal name `read_the_question` and read `clause_types`."""
    if tool_name != "read_the_brief":
        return None
    fibres = list(reading.get("fibres") or [])
    return {"chose": fibres[0] if fibres else "no framing", "confidence": reading.get("confidence"),
            "inputs": {"fibres": fibres, "fallback": bool(reading.get("fallback"))}}


def _fabric() -> Any:
    """The fabric product's agent definition: fifteen declared facts, nothing derived.

    **`carry_forward=None` is a real answer, not a stub.** A product that does not need to amend a result
    in the light of an earlier one supplies nothing and the substrate amends nothing -- which is what
    makes it a seam rather than a requirement.
    """
    from rag_wright.harness.definition import AgentDefinition

    return AgentDefinition(surface=FabricToolSurface(), planning=FABRIC_PLANNING,
                           workers=(_fabric_worker,), context_schema=FabricContext,
                           retry_on=(MillIsOffline, ModelAttemptTooSlow), message_for=_fabric_message_for,
                           model_id_for=lambda settings: "fabric-mill-model",
                           prompts=FABRIC_PROMPTS,
                           repeatable=frozenset({"match_compositions", "list_mills"}),
                           question_argument={"match_compositions": "brief"},
                           framings=FABRIC_FRAMINGS,
                           carry_forward=None,
                           rules=(FABRIC_RULE,), decision_points=(FABRIC_POINT,),
                           read_framing=_fabric_reads_its_framing)


def _fabric_worker(context: Any, resources: Any, ledger: Any = None) -> dict:
    """This product's only worker. Built with the substrate's own spec helper, which names no domain.

    **It holds the content-heavy operation and nothing else** (PS-48g): `list_mills` stays with the
    orchestrator, which plans from it, so the fabric product has an operation on each side of the
    orchestrator/worker split. **And it is built from the definition**, the way a product should: without
    `definition=` a worker runs with no retries, no repeat suppression and no decision recording."""
    from rag_wright.harness.workers import worker_spec

    return worker_spec(
        "composition_worker",
        "Finds fabric compositions matching a requirement set.",
        "You match requirement sets to fabric compositions. Report the swatches you found. "
        "Your own notes live under `/memories`.",
        FabricToolSurface().build(context, resources, only=["fabric.match_compositions"]),
        FabricFindings,
        ledger=ledger,
        definition=_fabric(),
    )


def _a_context() -> Any:
    """**The FABRIC product's context, not ours.**

    This used to return RuleWright's `AgentContext`, which made two tests below assert that a greenfield
    product gets its own tools and its own workers while handing the substrate OUR tenancy type. The
    interesting case is a product whose scope is three parts deep, and that was the one not covered.
    """
    return FabricContext(account="acme-mills", season="ss26", collection="resort")


@dataclass(frozen=True)
class FabricResources:
    """The fabric product's own run resources: opaque to the substrate, which only passes them to its factories."""

    mill_feed: Any = None


def _resources() -> Any:
    return FabricResources(mill_feed="F")


def _FakeModel() -> Any:
    """A real `BaseChatModel`, because `resolve_model` passes an instance through untouched and
    `create_deep_agent` binds tools to it. A duck-typed stub is not enough, and finding that out is part of
    what a greenfield product discovers on its first build."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    return GenericFakeChatModel(messages=iter([]))


# --- the scripted model --------------------------------------------------------------------------------


def scripted(*messages: Any) -> Any:
    """A model that answers with `messages`, in order, and accepts any tools it is bound to.

    `GenericFakeChatModel.bind_tools` is not implemented, and `create_deep_agent` binds tools to every
    model, so the substrate's tests script a run through this rather than through a stub."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    class Scripted(GenericFakeChatModel):
        def bind_tools(self, tools: Any, **_kwargs: Any) -> Any:
            return self

    return Scripted(messages=iter(messages))


# --- models that misbehave, for the clocks and the retries ---------------------------------------------


def never_answers() -> Any:
    """A provider that holds the connection open and never replies -- the case a read timeout misses."""
    import asyncio

    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    class NeverAnswers(GenericFakeChatModel):
        def bind_tools(self, tools: Any, **_kwargs: Any) -> Any:
            return self

        async def _agenerate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any):
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

    return NeverAnswers(messages=iter([]))


def fails_transiently(attempts: list) -> Any:
    """A mill feed that goes offline on every call, slowly, forever: a failure the fabric's retry set names."""
    import asyncio

    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    class FailsTransiently(GenericFakeChatModel):
        def bind_tools(self, tools: Any, **_kwargs: Any) -> Any:
            return self

        async def _agenerate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any):
            attempts.append(1)
            await asyncio.sleep(0.4)
            raise MillIsOffline("the mill feed went quiet")

    return FailsTransiently(messages=iter([]))


def slow_from(nth: int | None, delay: float, *messages: Any) -> Any:
    """A scripted model whose `nth` call (1-based) -- or every call, when `nth` is None -- first waits `delay`
    seconds. **A retry counts as a new call**, so `nth=2` slows one call and lets its retry through, while
    `nth=None` exhausts any retry budget."""
    import asyncio

    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    made: list = []

    class SlowFrom(GenericFakeChatModel):
        def bind_tools(self, tools: Any, **_kwargs: Any) -> Any:
            return self

        async def _agenerate(self, msgs: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any):
            made.append(1)
            if nth is None or len(made) == nth:
                await asyncio.sleep(delay)
            return await super()._agenerate(msgs, stop=stop, run_manager=run_manager, **kwargs)

    return SlowFrom(messages=iter(messages))


# --- what the person was looking at ----------------------------------------------------------------------


class UnresolvedView(Exception):
    """The fabric product's refusal to render a view it has not checked against the account."""


@dataclass(frozen=True)
class FabricView:
    """**This product's continuation: the swatch a person had open.** The substrate asks a continuation for
    three things and nothing else -- its `surface`, its `subject()` (what was being looked at, compared turn to
    turn) and its `render()` (the block put in front of the turn). Paging is not the subject; the swatch is."""

    surface: str = "swatch"
    swatch: str = "cotton-poplin-120gsm"
    page: int = 1
    resolved: bool = True

    def subject(self) -> dict[str, str]:
        return {"surface": self.surface, "swatch": self.swatch}

    def render(self) -> str:
        if not self.resolved:
            raise UnresolvedView("this view was never checked against the account")
        return f"<viewing>\n{self.surface}: {self.swatch} (page {self.page})\n</viewing>"


# --- the fabric's rules over the engine's detectors ---------------------------------------------------------


def _rule(rule_id: str, tiers: tuple[str, ...], criterion: str, detect: Any) -> Any:
    from rag_wright.harness.rules import Rule

    return Rule(id=rule_id, applies_to=tiers, instruction=criterion, criterion=criterion, detect=detect,
                observed="a fabric run", evidence=("fabric-1",))


def fabric_rules() -> tuple[Any, ...]:
    """**This product's rule registry**: its own ids and wording over the engine's counting detectors, and one
    criterion no detector can decide (`the-brief-is-faithful`), so the grader has something to judge itself."""
    from rag_wright.harness.rules import (
        blocked_without_saying_what_it_needs,
        dispatched_more_than_once,
        guessed_identifiers,
        reached_for_the_filesystem,
        wrote_a_program_for_a_single_call,
    )

    return (
        _rule("one-dispatch-per-worker", ("worker",), "The worker was dispatched once for this enquiry.",
              dispatched_more_than_once),
        _rule("compositions-are-not-files", ("worker", "coded"), "No filesystem tool was used on the compositions.",
              reached_for_the_filesystem),
        _rule("no-program-for-a-single-call", ("coded",), "No program wrapped a single call.",
              wrote_a_program_for_a_single_call),
        _rule("ids-are-not-guessable", ("worker",), "Every id used was one the run was given.",
              guessed_identifiers({"read_enquiry": "enquiry_id"})),
        _rule("blocked-must-name-the-need", ("worker",), "A blocked report says what would unblock it.",
              blocked_without_saying_what_it_needs),
        _rule("the-brief-is-faithful", ("coded", "orchestrator"),
              "Each brief asked for what the person asked, nothing narrower.", None),
    )


# --- recordings made by the real recorder, from fabric runs ---------------------------------------------------


def _record(sink: dict, briefs: list[str], replies: list[list[Any]], tools: list, system: str) -> dict:
    """Drive one agent per brief with the recorder attached, all into one `sink`: one dispatch per brief."""
    from langchain.agents import create_agent

    from rag_wright.harness.recording import RecordTheContext

    recorder = RecordTheContext(sink, "fabric")
    for brief, script in zip(briefs, replies, strict=True):
        agent = create_agent(scripted(*script), tools=tools, system_prompt=system, middleware=[recorder])
        agent.invoke({"messages": [{"role": "user", "content": brief}]})
    return sink


def _fabric_tools() -> list:
    from langchain_core.tools import tool

    @tool
    def list_mills() -> dict:
        """The mills this account can order from."""
        return {"mills": ["mill-a", "mill-b"]}

    @tool
    def read_swatch(swatch: str) -> dict:
        """Read one named swatch."""
        return {"swatch": swatch, "weight": "120 gsm"}

    @tool
    def match_compositions(requirements: str) -> dict:
        """Match a requirement set across every composition."""
        return {"matched": ["cotton-poplin-120gsm", "linen-slub-180gsm"]}

    @tool("eval")
    def run_program(code: str) -> str:
        """Run a program in the sandbox."""
        return "{}"

    return [list_mills, read_swatch, match_compositions, run_program]


def _calls(*calls: tuple[str, dict]) -> Any:
    from langchain_core.messages import AIMessage

    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{i}", "type": "tool_call"}
                                             for i, (name, args) in enumerate(calls)])


def recorded_fan_out() -> dict:
    """**A worker fanned out per swatch**: dispatched once to probe, then once per swatch -- four dispatches where
    one brief covering all three would do. The shape a dispatch counter must catch."""
    from langchain_core.messages import AIMessage

    briefs = ["Say hello", *(f"Read ONLY swatch {s}" for s in ("cotton-poplin-120gsm", "linen-slub-180gsm",
                                                                 "wool-twill-300gsm"))]
    replies = [[AIMessage(content="hello")],
               *([_calls(("read_swatch", {"swatch": s})), AIMessage(content=f"{s} read")]
                 for s in ("cotton-poplin-120gsm", "linen-slub-180gsm", "wool-twill-300gsm"))]
    return _record({}, briefs, replies, _fabric_tools(), "You match fabric compositions.")


def recorded_healthy_worker() -> dict:
    """**A worker that did its job**: one dispatch, a match, two reads of what matched, an answer."""
    from langchain_core.messages import AIMessage

    script = [_calls(("match_compositions", {"requirements": "outerwear"})),
              _calls(("read_swatch", {"swatch": "cotton-poplin-120gsm"}),
                     ("read_swatch", {"swatch": "linen-slub-180gsm"})),
              AIMessage(content="Two blends suit outerwear.")]
    return _record({}, ["Match every outerwear requirement and read what matches."], [script], _fabric_tools(),
                   "You match fabric compositions.")


def recorded_program_for_one_call() -> dict:
    """**A coded orchestrator that wrote a program to make one call** -- listing the mills -- which a direct call
    would have done for less."""
    from langchain_core.messages import AIMessage

    script = [_calls(("eval", {"code": "const m = await tools.listMills({}); m"})),
              AIMessage(content="The account orders from two mills.")]
    return _record({}, ["Which mills can we order from?"], [script], _fabric_tools(),
                   "You orchestrate by writing programs.")
