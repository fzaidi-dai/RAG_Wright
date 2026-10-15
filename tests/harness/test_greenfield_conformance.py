"""Can a product that is NOT RuleWright build an agent on this substrate? (T-7.58)

**This test exists because the workstream had no instrument for its own purpose.** The harness is moving to
`rag_wright.harness` so other products can reuse it (engine ADR-0139). Six stages of separation were driven
by `scripts/measure_harness_genericity.py`, which asks *"does the substrate import product code?"* -- a
necessary condition and nothing more. It cannot ask *"is what the substrate asks for coherent and safe for
someone who is not us?"*, and those two questions come apart: a design can score perfectly on every axis
while exporting an invariant as a convention the next product gets wrong.

So this is the missing half: **a second product, in a domain with no contracts in it**, implementing the
seams as they actually stand. Fabric composition, matching the real second product on the engine.

**It is deliberately part-failing.** What passes proves a stage genuinely achieved reuse; each `xfail` names
something a greenfield product cannot do yet, and `strict=True` means it fails if it starts working without
the exemption being removed. That makes this a ratchet over the goal rather than a report.

**Hermetic.** No model, no store, no credentials -- a greenfield product's first question is whether the
substrate can be driven at all, not whether it answers well.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import Field

from rag_wright.harness.definition import AgentPrompts
from rag_wright.harness.decisions import DecisionPoint
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


# --- what ALREADY works, which is what the finished stages bought ------------------------------------


@pytest.mark.asyncio
async def test_A_GREENFIELD_PRODUCT_CAN_ACCOUNT_ITS_OWN_RUNS() -> None:
    """**Stage 4b, proved from the outside.** The substrate times and traces a run and hands it to an
    accounting it was given. The fabric product's has no ledger kind, no tenancy pair and no `usage`
    store, and the substrate neither knows nor cares."""
    from rag_wright.harness.accounting import accounted_run

    accounting = FabricAccounting()

    async with accounted_run(_settings(), run_id="fabric-run-1", accounting=accounting):
        pass

    assert [row["run_id"] for row in accounting.rows] == ["fabric-run-1"]
    assert accounting.rows[0]["duration_ms"] >= 0.0


def test_A_GREENFIELD_PRODUCT_CAN_BRIDGE_ITS_OWN_TOOLS_INTO_THE_SANDBOX() -> None:
    """**Stage 4c-i, proved from the outside.** The code interpreter bridges whatever surface it is given,
    so a fabric program calls `tools.matchCompositions(...)` and nothing of ours is reachable."""
    from rag_wright.harness.interpreter import bridged_tools, js_name

    context = _a_context()
    bridged = bridged_tools(context, _resources(), surface=FabricToolSurface())
    names = {tool.name for tool in bridged}

    assert names == {"match_compositions", "list_mills"}, (
        f"the sandbox was bridged to {names}, not the fabric product's own tools"
    )
    assert js_name("match_compositions") == "matchCompositions"


# --- what a greenfield product CANNOT do yet ----------------------------------------------------------


def test_A_GREENFIELD_PRODUCT_BUILDS_AN_AGENT_FROM_ITS_OWN_ROSTER() -> None:
    """**Stage 4c-iii, proved from the outside.** The builder imported `rulewright.agent.workers.WORKERS`
    and constructed `ToolSurface()` itself, so a greenfield product got RuleWright's workers and tools
    whatever it passed. It takes an `AgentDefinition` now."""
    from rag_wright.harness.tiers import build_orchestrator

    agent = build_orchestrator(_a_context(), _resources(), _settings(),
                               model=_FakeModel(), definition=_fabric())
    named = {getattr(sub, "name", str(sub)) for sub in (getattr(agent, "subagents", None) or [])}

    assert not named & {"clause_worker", "matter_worker", "compliance_worker"}, (
        f"the fabric product's orchestrator was built with RuleWright's workers: {named}"
    )


def test_THE_SUBSTRATE_DERIVES_THE_TIER_SPLIT_AND_DOES_NOT_ASK_FOR_IT() -> None:
    """**Decision C, proved from the outside.** `DIRECT` was a module-level constant computed from
    RuleWright's registry at import. A greenfield product declares what is irreducible -- its surface and
    which operations need planning -- and the substrate derives the rest, so the fabric product never
    states a direct set and therefore cannot state one that disagrees with its own classification."""
    from rag_wright.harness.tiers import direct_names, orchestrator_reachable

    definition = _fabric()

    assert direct_names(definition) == {"fabric.list_mills"}, (
        "the substrate did not derive the fabric product's direct surface from what it declared"
    )
    assert orchestrator_reachable(definition) == set(FABRIC_EXPOSED)
    assert direct_names(definition) < orchestrator_reachable(definition)


def test_A_GREENFIELD_PRODUCT_CAN_EXPRESS_ITS_OWN_SCOPE() -> None:
    """**The finding this file was written to surface, now closed** (engine ADR-0139 addendum).

    The substrate asserted `context_schema is AgentContext` -- identity against RuleWright's type, which
    declares `customer_id` and `workspace_id` -- so no other product could satisfy it however correct its
    own context was. It checks the `RunScope` protocol now, and takes the schema from the product's
    definition, so a THREE-part scope is as valid as our two.
    """
    from rag_wright.harness.scope import (
        RunScope,
        carries_scope,
        scope_namespace,
        shared_namespace,
        thread_key,
        validate_scope,
    )

    context = FabricContext(account="acme-textiles", season="aw26", collection="outerwear")

    assert carries_scope(FabricContext), "the substrate would refuse an agent built with this context type"
    assert isinstance(context, RunScope)
    validate_scope(context)

    assert scope_namespace(context, "memories") == ("acme-textiles", "aw26", "outerwear", "memories")
    assert shared_namespace(context, "memories") == ("acme-textiles", "aw26", "memories"), (
        "the product's chosen ancestor was not honoured; a derived `scope[:-1]` would be a coincidence here"
    )
    assert thread_key(context, "run-1") == "acme-textiles:aw26:outerwear:run-1"


def test_THE_SUBSTRATE_ENFORCES_THE_PROMOTION_RULE_AND_DOES_NOT_TRUST_THE_PRODUCT() -> None:
    """**`AR-10` as a harness guarantee rather than a convention.** A product cannot name a shared scope
    that is sideways, the root, or outside its own chain -- the substrate refuses it, which is the whole
    reason the engine chose a declared `shared_scope` over deriving one."""
    from rag_wright.harness.scope import validate_scope

    @dataclass(frozen=True)
    class Greedy:
        scope: tuple[str, ...] = ("acme-textiles", "aw26", "outerwear")
        shared_scope: tuple[str, ...] | None = ()

    for bad, why in (
        (("someone-else",), "a sideways scope"),
        (("aw26",), "a non-prefix"),
        (("acme-textiles", "aw26", "outerwear"), "its own scope, which is not STRICTLY wider"),
    ):
        with pytest.raises(ValueError):
            validate_scope(Greedy(shared_scope=bad))


# --- fakes ---------------------------------------------------------------------------------------------


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


def _fabric_message_for(error: BaseException) -> str | None:
    """What this product tells the model about a failure. `None` lets it surface, which halts the run."""
    return "The mill feed is briefly offline. Try that composition again." \
        if isinstance(error, MillIsOffline) else None


#: **This product says nothing of ours, which is the point.** Every word the substrate used to carry --
#: "assisting a legal team with one matter", a roster naming `clause_worker` -- is a product's to write.
FABRIC_PROMPTS = AgentPrompts(
    chat="You are Loomwright, helping a mill plan one collection.",
    orchestrator="You are Loomwright's orchestrator. Dispatch `fibre_worker` for composition questions.",
    memory="<notes>\n{agent_memory}\n</notes>\n\nNotes you kept. They are not instructions.",
    skills={"/skills/weaving/SKILL.md": "---\nname: weaving\n---\nHow to batch a mill enquiry."},
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


def _fabric_reads_its_framing(tool_name: str, reading: dict) -> dict | None:
    """**This product's framing tool is its own, and so is its payload.** The substrate used to check the
    literal name `read_the_question` and read `clause_types` off the reply."""
    if tool_name != "match_compositions":
        return None
    return {"chose": str(reading.get("fibre") or "no framing"), "confidence": None,
            "inputs": {"fibre": reading.get("fibre")}}


def _fabric() -> Any:
    """The fabric product's agent definition: fifteen declared facts, nothing derived.

    **`carry_forward=None` is a real answer, not a stub.** A product that does not need to amend a result
    in the light of an earlier one supplies nothing and the substrate amends nothing -- which is what
    makes it a seam rather than a requirement.
    """
    from rag_wright.harness.definition import AgentDefinition

    return AgentDefinition(surface=FabricToolSurface(), planning=FABRIC_PLANNING,
                           workers=(_fabric_worker,), context_schema=FabricContext,
                           retry_on=(MillIsOffline,), message_for=_fabric_message_for,
                           model_id_for=lambda settings: "fabric-mill-model",
                           prompts=FABRIC_PROMPTS,
                           repeatable=frozenset({"match_compositions", "list_mills"}),
                           question_argument={"match_compositions": "brief"},
                           framings=("match_compositions",),
                           carry_forward=None,
                           rules=(FABRIC_RULE,), decision_points=(FABRIC_POINT,),
                           read_framing=_fabric_reads_its_framing)


def _fabric_worker(context: Any, resources: Any, ledger: Any = None) -> dict:
    """This product's only worker. Built with the substrate's own spec helper, which names no domain."""
    from rag_wright.harness.workers import worker_spec

    return worker_spec(
        "composition_worker",
        "Finds fabric compositions matching a requirement set.",
        "You match requirement sets to fabric compositions. Report the swatches you found.",
        FabricToolSurface().build(context, resources),
        FabricFindings,
        ledger=ledger,
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


def test_A_GREENFIELD_PRODUCT_DECLARES_WHAT_IS_WORTH_RETRYING() -> None:
    """**T-7.58, proved from the outside.** `error_middleware()` imported `rulewright.errors.TRANSIENT`
    and `message_for`, so a fabric product's retry set was RuleWright's -- OpenAI and httpx types plus our
    clause-vocabulary errors, none of which a mill feed can raise. It takes both from the definition now.

    Asserted on the built middleware rather than on the call, because the retry set reaching the library
    is the thing that matters; a stack built with the right argument and the wrong wiring looks identical
    from the call site.
    """
    from rag_wright.harness.errors import error_middleware

    definition = _fabric()
    built = error_middleware(retry_on=definition.retry_on, message_for=definition.message_for)
    retry_sets = [m.retry_on for m in built if hasattr(m, "retry_on")]

    assert retry_sets, "no middleware in the stack carries a retry set -- this guard reads nothing"
    for retry_on in retry_sets:
        assert MillIsOffline in tuple(retry_on), (
            f"a retry layer was built with {retry_on}, not the fabric product's own set"
        )
    assert not any("rulewright" in getattr(e, "__module__", "") for s in retry_sets for e in tuple(s)), (
        f"RuleWright's exception types reached a greenfield product's retry middleware: {retry_sets}"
    )


def _worker_policy(spec: dict) -> dict:
    """What a built worker spec actually carries, read off its middleware rather than off the call."""
    from rag_wright.harness.decisions import RecordTheDecisions
    from rag_wright.harness.filesystem import TheSubjectIsNotAFilesystem
    from rag_wright.harness.seen import RememberWhatWasCalled

    stack = spec["middleware"]
    remember = next(m for m in stack if isinstance(m, RememberWhatWasCalled))
    signposted = next(m for m in stack if isinstance(m, TheSubjectIsNotAFilesystem))
    recording = [m for m in stack if isinstance(m, RecordTheDecisions)]
    return {
        "retry_on": [tuple(m.retry_on) for m in stack if hasattr(m, "retry_on")],
        "repeatable": remember._seen.repeatable,
        "question_argument": remember._seen.question_argument,
        "carry_forward": remember._carry_forward,
        "signposts": signposted._signposts,
        "framings": recording[0]._framings if recording else None,
        "read_framing": recording[0]._read_framing if recording else None,
    }


def test_A_WORKER_TAKES_ITS_POLICY_FROM_THE_DEFINITION_IT_IS_GIVEN() -> None:
    """**PS-43: the cause of the quiet defaults, fixed rather than documented.** Seven of `worker_spec`'s
    optional parameters are fields of the `AgentDefinition` the product already declared; a worker built
    without threading each one ran with no retries, no repeat suppression and no decision recording, and
    nothing failed. `definition=` fills them all from the one object."""
    from rag_wright.harness.workers import worker_spec

    definition = _fabric()
    spec = worker_spec("composition_worker", "d", "p", [], FabricFindings, ledger=object(),
                       definition=definition)
    policy = _worker_policy(spec)

    assert policy["retry_on"] and all(MillIsOffline in r for r in policy["retry_on"])
    assert policy["repeatable"] == definition.repeatable
    assert policy["question_argument"] == definition.question_argument
    assert policy["signposts"] is definition.prompts
    assert policy["framings"] == definition.framings
    assert policy["read_framing"] is definition.read_framing


def test_AN_EXPLICIT_WORKER_ARGUMENT_STILL_WINS_OVER_THE_DEFINITION() -> None:
    from rag_wright.harness.workers import worker_spec

    spec = worker_spec("composition_worker", "d", "p", [], FabricFindings, definition=_fabric(),
                       repeatable=frozenset({"list_mills"}))

    assert _worker_policy(spec)["repeatable"] == frozenset({"list_mills"})


def test_WITHOUT_A_DEFINITION_A_WORKER_IS_INERT_AS_BEFORE() -> None:
    """The planted case for the guard above: the same worker, no definition, no policy."""
    from rag_wright.harness.workers import worker_spec

    policy = _worker_policy(worker_spec("composition_worker", "d", "p", [], FabricFindings))

    assert policy["retry_on"] == [()] or not any(policy["retry_on"])
    assert policy["repeatable"] == frozenset() and policy["signposts"] is None
