"""What the run DECIDED, as distinct from what it produced.

Ported from RuleWright under ADR-0139's handover (PS-48d); the substrate is `rag_wright.harness.decisions`
(`RecordTheDecisions`, `WhatTheRunDecided`), driven with the fabric product's own tools, workers, framings and
framing reader. RuleWright's tests of its own decision table (which of its D1-D10 are recorded, left out on purpose,
or blocked on a mechanism) stay in RuleWright.

**A wrong decision -- routing, dispatch, escalation, a guardrail verdict -- is a stop-the-line event, and you cannot
audit what was never written down.** Every test here drives a REAL agent, because a hand-built request proves the
method and not the wiring; and every guard has its negative half -- delegating beside answering, a re-dispatch beside a
first dispatch, isolation beside sharing.
"""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langchain_core.tools import tool as make_tool

from rag_wright.harness import agent as agent_module
from rag_wright.harness.decisions import RecordTheDecisions, WhatTheRunDecided
from tests.harness.fabric import (
    FABRIC_FRAMINGS,
    _a_context,
    _fabric,
    _fabric_reads_its_framing,
    _fabric_worker,
    _FakeModel,
    _resources,
    _settings,
    scripted,
)


def _call(name: str, args: dict, ident: str) -> dict:
    return {"name": name, "args": args, "id": ident, "type": "tool_call"}


def _recorder(ledger: WhatTheRunDecided, tier: str, **kwargs: Any) -> RecordTheDecisions:
    return RecordTheDecisions(ledger, framings=FABRIC_FRAMINGS, read_framing=_fabric_reads_its_framing, tier=tier,
                              **kwargs)


@make_tool
def list_mills() -> dict:
    """The mills this account can order from."""
    return {"mills": ["mill-a", "mill-b"]}


@make_tool
def FabricFindings(status: str, need: str = "", swatches: list | None = None) -> dict:
    """A worker's structured report, as a resolvable tool.

    **Present so the graph can finish.** A worker delivers this through `response_format`, not as a real tool --
    but a scripted model emitting an unresolvable tool call leaves the run asking for another reply. The
    OBSERVATION happens at the model seam either way, which is what this is testing."""
    return {"recorded": status}


@make_tool
def task(description: str, subagent_type: str) -> str:
    """Hand work to a subagent. Named and shaped as DeepAgents' own dispatch tool."""
    return "the orchestrator reports: done"


def _drive(ledger: WhatTheRunDecided, tier: str, replies: list[AIMessage], *,
           opening: str = "which blends suit outerwear?", thread: str = "run-1", tools: list | None = None) -> Any:
    """One tier, driven through a real agent with the ledger attached the way a product's agent attaches it.

    **The tier's KIND is passed the way the builders pass it**: the observer is told whether it is a worker and who
    dispatched it, rather than looking the answer up in a product's roster."""
    from rag_wright.harness.decisions import DISPATCHER_OF_A_WORKER, DISPATCHER_OF_THE_ORCHESTRATOR

    is_worker = tier not in ("chat-manager", "orchestrator")
    dispatched_by = (DISPATCHER_OF_A_WORKER if is_worker
                     else DISPATCHER_OF_THE_ORCHESTRATOR if tier == "orchestrator" else None)
    agent = create_agent(scripted(*replies), tools=tools if tools is not None else [list_mills, task],
                         system_prompt="s",
                         middleware=[_recorder(ledger, tier, is_worker=is_worker, dispatched_by=dispatched_by)])
    return agent.invoke({"messages": [{"role": "user", "content": opening}]},
                        config={"configurable": {"thread_id": thread}})


def test_PRODUCTION_CARRIES_NO_SCORING_OF_ITS_OWN() -> None:
    """**A decision is judged separately from the outcome it led to**, and the split collapses the moment the
    ledger learns what a RIGHT decision is. The ledger records; the eval judges. Guarded on identifiers and
    imports, not prose, so a comment explaining the split does not trip it."""
    import inspect

    from rag_wright.harness import decisions

    named = [name for name in dir(decisions)
             if any(word in name.lower() for word in ("expected", "gold", "correct", "score"))]
    assert not named, f"the ledger module defines {named}; judging belongs to the eval, not to the run"

    imports = [line.strip() for line in inspect.getsource(decisions).splitlines()
               if line.strip().startswith(("import ", "from ")) and "eval" in line]
    assert not imports, f"the ledger module reaches into the eval: {imports}"


# --- D1: answer directly, or hand it to the orchestrator ------------------------------------------------


def test_DELEGATING_IS_RECORDED_AS_D1() -> None:
    ledger = WhatTheRunDecided()
    _drive(ledger, "chat-manager", [
        AIMessage(content="", tool_calls=[_call("task", {"description": "find blends", "subagent_type": "orchestrator"},
                                                "a")]),
        AIMessage(content="two blends suit outerwear"),
    ])

    made = ledger.of("run-1").by_point("D1")
    assert len(made) == 1, f"expected one D1, got {made}"
    assert made[0].chose == "delegate"
    assert made[0].tier == "chat-manager"


def test_ANSWERING_DIRECTLY_IS_RECORDED_AS_D1() -> None:
    """**The negative half.** A ledger that only ever records delegation cannot tell a run that chose to answer from
    a run where the observer never fired."""
    ledger = WhatTheRunDecided()
    _drive(ledger, "chat-manager", [AIMessage(content="this account orders from two mills")])

    made = ledger.of("run-1").by_point("D1")
    assert len(made) == 1
    assert made[0].chose == "answer_directly"


def test_A_TURN_THAT_ONLY_USES_ITS_OWN_TOOLS_HAS_NOT_DECIDED_YET() -> None:
    """**The trap this observer exists to avoid.** Listing the mills and then delegating is one decision --
    `delegate` -- not a decision to answer directly followed by a change of mind."""
    ledger = WhatTheRunDecided()
    _drive(ledger, "chat-manager", [
        AIMessage(content="", tool_calls=[_call("list_mills", {}, "a")]),
        AIMessage(content="", tool_calls=[_call("task", {"description": "find blends", "subagent_type": "orchestrator"},
                                                "b")]),
        AIMessage(content="two blends suit outerwear"),
    ])

    assert [d.chose for d in ledger.of("run-1").by_point("D1")] == ["delegate"]


def test_A_DECISION_TAKEN_BY_A_MODEL_CARRIES_NO_CONFIDENCE() -> None:
    """**The finding, not a missing field.** An LLM turn returns a 51% call and a 99% call indistinguishably;
    recording `None` is what makes that visible and countable."""
    ledger = WhatTheRunDecided()
    _drive(ledger, "chat-manager", [AIMessage(content="two mills")])

    assert ledger.of("run-1").by_point("D1")[0].confidence is None


# --- D2 and D3: which workers, and whether to accept what one returned ----------------------------------


def test_A_DISPATCH_IS_RECORDED_BY_THE_TIER_THAT_WAS_DISPATCHED() -> None:
    """**Because the parent's transcript cannot see a sandbox dispatch**: a sandbox `task()` does not route through
    the parent's tool node, and a worker dispatched 7 times was once recorded as 1. A dispatched tier's opening turn
    carries exactly one human message in both arms."""
    ledger = WhatTheRunDecided()
    _drive(ledger, "composition_worker", [AIMessage(content="cotton-poplin-120gsm")],
           opening="match every outerwear requirement", tools=[list_mills])

    made = ledger.of("run-1").by_point("D2")
    assert len(made) == 1, f"a worker's opening turn is a dispatch; got {made}"
    assert made[0].chose == "composition_worker"
    assert made[0].tier == "orchestrator", "a dispatch is the ORCHESTRATOR's decision, not the worker's"


def test_A_WORKER_S_LATER_TURNS_ARE_NOT_FURTHER_DISPATCHES() -> None:
    """**The negative half, and the one that inflates a count if it is wrong.** A worker taking four turns was
    dispatched once."""
    ledger = WhatTheRunDecided()
    _drive(ledger, "composition_worker", [
        AIMessage(content="", tool_calls=[_call("list_mills", {}, "a")]),
        AIMessage(content="", tool_calls=[_call("list_mills", {}, "b")]),
        AIMessage(content="done"),
    ], opening="match every outerwear requirement", tools=[list_mills])

    assert len(ledger.of("run-1").by_point("D2")) == 1


def test_A_RE_DISPATCH_IS_RECORDED_AS_D3() -> None:
    """A worker re-dispatched after it had already answered is a D3, and until it was recorded nothing said a D3
    had even been taken."""
    ledger = WhatTheRunDecided()
    _drive(ledger, "composition_worker", [AIMessage(content="cotton-poplin-120gsm")],
           opening="match every outerwear requirement", tools=[list_mills])
    _drive(ledger, "composition_worker", [AIMessage(content="cotton-poplin-120gsm")],
           opening="match every outerwear requirement again", tools=[list_mills])

    here = ledger.of("run-1")
    assert [d.chose for d in here.by_point("D2")] == ["composition_worker"]
    redispatched = here.by_point("D3")
    assert len(redispatched) == 1, f"the second dispatch is a D3; ledger holds {here.decisions}"
    assert redispatched[0].chose == "re_dispatch"
    assert redispatched[0].inputs.get("tier") == "composition_worker"


def test_TWO_DIFFERENT_WORKERS_ARE_TWO_DISPATCHES_AND_NO_RE_DISPATCH() -> None:
    """**The negative half.** Dispatching two different workers is a plan, not a repetition -- a rule that fires on
    sound alternatives is a rule people learn to disable."""
    ledger = WhatTheRunDecided()
    _drive(ledger, "composition_worker", [AIMessage(content="a")], tools=[list_mills])
    _drive(ledger, "mill_worker", [AIMessage(content="b")], tools=[list_mills])

    here = ledger.of("run-1")
    assert [d.chose for d in here.by_point("D2")] == ["composition_worker", "mill_worker"]
    assert here.by_point("D3") == []


def test_RE_DISPATCHING_THE_ORCHESTRATOR_IS_RECORDED_AS_D3() -> None:
    """**Measured live, and the ledger could not see it**: a chat-manager delegated to the orchestrator 24 times in
    one item. The chat-manager makes the same accept-or-re-dispatch decision about the orchestrator that the
    orchestrator makes about a worker."""
    ledger = WhatTheRunDecided()
    _drive(ledger, "orchestrator", [AIMessage(content="Complete.")], tools=[list_mills])
    _drive(ledger, "orchestrator", [AIMessage(content="Complete.")], tools=[list_mills])

    made = ledger.of("run-1").by_point("D3")
    assert len(made) == 1, f"the second delegation is a D3; ledger holds {ledger.of('run-1').decisions}"
    assert made[0].chose == "re_dispatch"
    assert made[0].inputs.get("tier") == "orchestrator"
    assert made[0].tier == "chat-manager", (
        "re-dispatching the orchestrator is the CHAT-MANAGER's decision; attributing it to the orchestrator would "
        "blame the tier that was told to run again"
    )


def test_THE_FIRST_DELEGATION_IS_NOT_DOUBLE_COUNTED() -> None:
    """**`D1` already records it.** Recording the orchestrator's first turn as a `D2` as well would count one
    decision twice."""
    ledger = WhatTheRunDecided()
    _drive(ledger, "orchestrator", [AIMessage(content="Complete.")], tools=[list_mills])

    here = ledger.of("run-1")
    assert here.by_point("D3") == []
    assert here.by_point("D2") == [], f"the orchestrator is not a worker and must not appear in D2: {here.decisions}"


def test_A_WORKER_RE_DISPATCH_IS_STILL_THE_ORCHESTRATORS_DECISION() -> None:
    ledger = WhatTheRunDecided()
    _drive(ledger, "composition_worker", [AIMessage(content="a")], tools=[list_mills])
    _drive(ledger, "composition_worker", [AIMessage(content="b")], tools=[list_mills])

    redispatched = ledger.of("run-1").by_point("D3")
    assert len(redispatched) == 1
    assert redispatched[0].tier == "orchestrator"
    assert redispatched[0].inputs.get("tier") == "composition_worker"


# --- the properties that make it usable ------------------------------------------------------------------


def test_ONE_RUN_S_DECISIONS_NEVER_REACH_ANOTHER() -> None:
    """Keyed by the run, so a re-dispatch means "again in THIS run" and never "someone else asked the same"."""
    ledger = WhatTheRunDecided()
    _drive(ledger, "composition_worker", [AIMessage(content="a")], thread="run-1", tools=[list_mills])
    _drive(ledger, "composition_worker", [AIMessage(content="b")], thread="run-2", tools=[list_mills])

    assert len(ledger.of("run-1").by_point("D2")) == 1
    assert len(ledger.of("run-2").by_point("D2")) == 1
    assert ledger.of("run-1").by_point("D3") == [], "a second RUN is not a re-dispatch"


def test_THE_LEDGER_NEVER_BREAKS_A_MODEL_CALL() -> None:
    """A recorder that can fail a run is worse than no recorder. **The OBSERVATION is made to fail**, not the guard
    around it -- breaking the guard would prove only that a broken guard breaks things."""
    ledger = WhatTheRunDecided()

    def explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the observer is broken")

    middleware = _recorder(ledger, "chat-manager")
    middleware._replied = explode  # type: ignore[method-assign]
    middleware._opening = explode  # type: ignore[method-assign]
    agent = create_agent(scripted(AIMessage(content="still answered")), tools=[list_mills], system_prompt="s",
                         middleware=[middleware])
    state = agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config={"configurable": {"thread_id": "run-1"}})

    assert "still answered" in str(state["messages"][-1].content)
    assert ledger.of("run-1").decisions == []


def test_TWO_TIERS_CAN_SHARE_ONE_AGENT_WITHOUT_A_NAME_COLLISION() -> None:
    """`create_agent` refuses two middlewares with one name; distinct per tier, and the graph stays legible."""
    assert _recorder(WhatTheRunDecided(), "chat-manager").name != _recorder(WhatTheRunDecided(), "orchestrator").name


def test_AN_OBSERVER_THAT_RAISES_IS_COUNTED_RATHER_THAN_VANISHING() -> None:
    """**The swallow made guards unfalsifiable.** `_observe` catches everything, because no observation is worth a
    dead run -- so a BUG in an observer looked exactly like an observation correctly not made. The run still
    degrades to silence; the silence is now countable."""
    ledger = WhatTheRunDecided()
    middleware = _recorder(ledger, "orchestrator")

    def explode(*args: Any, **kwargs: Any) -> None:
        raise IndexError("an observer bug")

    middleware._escalated = explode  # type: ignore[method-assign]
    agent = create_agent(scripted(AIMessage(content="answered")), tools=[list_mills], system_prompt="s",
                         middleware=[middleware])
    state = agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config={"configurable": {"thread_id": "run-1"}})

    assert "answered" in str(state["messages"][-1].content), "a swallowed observer killed the run"
    assert middleware.skipped, "the observer raised and nothing recorded that it had"
    assert "IndexError" in middleware.skipped[0]


# --- the wiring, which is where a recorder like this usually dies ----------------------------------------


def _recorders(middleware: list) -> list[RecordTheDecisions]:
    return [m for m in middleware if isinstance(m, RecordTheDecisions)]


def test_ONE_LEDGER_REACHES_EVERY_TIER_OF_A_RUN(monkeypatch) -> None:
    """**The failure this guard exists for: a recorder nothing attaches.** A per-tier ledger would put the worker's
    dispatch and the chat-manager's D1 in different objects, so nothing could say that the worker this run
    re-dispatched had already answered."""
    handed: dict[str, Any] = {}
    real = agent_module.orchestrator_subagent

    def capture(context, resources, settings, **kwargs):
        handed.update(kwargs)
        return real(context, resources, settings, **kwargs)

    monkeypatch.setattr(agent_module, "orchestrator_subagent", capture)
    agent_module.build_chat_agent(_a_context(), _resources(), _settings(), model=_FakeModel(), definition=_fabric())

    ledger = handed.get("ledger")
    assert isinstance(ledger, WhatTheRunDecided), (
        f"the chat-manager handed the orchestrator {ledger!r}; the tiers do not share a ledger"
    )
    carried = _recorders(_fabric_worker(_a_context(), _resources(), ledger)["middleware"])
    assert len(carried) == 1, "a worker built for this run carries no decision recorder"
    assert carried[0]._ledger is ledger, "the worker records into a different ledger from its parent"
    assert carried[0]._tier == "composition_worker"


def test_A_TIER_DRIVEN_DIRECTLY_STILL_RECORDS() -> None:
    """**An eval drives `build_orchestrator` itself**, and an instrument that measures decisions while recording
    none is the exact shape of measurement to avoid. The roster is the definition's, so the spy is installed there."""
    from dataclasses import replace

    from rag_wright.harness.tiers import build_orchestrator

    built: list = []

    def spy(context, resources, ledger=None):
        built.append(ledger)
        return _fabric_worker(context, resources, ledger)

    build_orchestrator(_a_context(), _resources(), _settings(), model=_FakeModel(),
                       definition=replace(_fabric(), workers=(spy,)))

    assert any(isinstance(b, WhatTheRunDecided) for b in built), (
        f"a directly-driven orchestrator gave its workers {built}; nothing would be recorded"
    )


def test_THE_REAL_CHAT_AGENT_RECORDS_ITS_OWN_D1() -> None:
    """**The builders' path, not a hand-assembled one.** Only this proves that the middleware being attached and it
    observing correctly are facts about the same agent."""
    ledger = WhatTheRunDecided()
    agent = agent_module.build_chat_agent(_a_context(), _resources(), _settings(), ledger=ledger, definition=_fabric(),
                                          model=scripted(AIMessage(content="this account orders from two mills")))
    agent.invoke({"messages": [{"role": "user", "content": "who do we order from?"}]},
                 context=_a_context(), config={"configurable": {"thread_id": "w1:run-3"}})

    made = ledger.of("w1:run-3").by_point("D1")
    assert [d.chose for d in made] == ["answer_directly"], (
        f"the built chat-manager recorded {[(d.point, d.chose) for d in ledger.of('w1:run-3').decisions]}"
    )
    assert made[0].tier == "chat-manager"


# --- D7: a worker reporting done or blocked ----------------------------------------------------------------


def _reports(status: str, need: str = "") -> AIMessage:
    """A worker's report as it arrives at the seam: a tool call named after the schema."""
    return AIMessage(content="", tool_calls=[{"name": "FabricFindings",
                                              "args": {"status": status, "need": need, "swatches": []},
                                              "id": "report", "type": "tool_call"}])


def test_A_WORKER_REPORTING_DONE_IS_RECORDED_AS_D7() -> None:
    """`WorkerReport.status` exists so the orchestrator can branch on it; recording it is the first evidence that
    pointing a prompt at it does anything."""
    ledger = WhatTheRunDecided()
    _drive(ledger, "composition_worker", [_reports("done"), AIMessage(content="reported")],
           tools=[list_mills, FabricFindings])

    made = ledger.of("run-1").by_point("D7")
    assert len(made) == 1, f"the worker's report was not recorded: {ledger.of('run-1').decisions}"
    assert made[0].chose == "done"
    assert made[0].tier == "composition_worker", "a worker's own report is its decision, not the orchestrator's"


def test_A_WORKER_REPORTING_BLOCKED_CARRIES_WHAT_IT_NEEDS() -> None:
    """`blocked` with an empty `need` is a defect; the ledger records the `need` so it and the rule detecting that
    defect agree about one fact."""
    ledger = WhatTheRunDecided()
    _drive(ledger, "composition_worker", [_reports("blocked", "which season is the resort collection in?"),
                                          AIMessage(content="reported")],
           tools=[list_mills, FabricFindings])

    made = ledger.of("run-1").by_point("D7")
    assert made[0].chose == "blocked"
    assert "resort collection" in str(made[0].inputs.get("need", "")), (
        f"the need is not recorded, so nobody can tell a real question from an empty one: {made[0].inputs}"
    )


def test_A_REPORT_THAT_DID_NOT_LAND_IS_ONE_DECISION_NOT_FOUR() -> None:
    """**Measured live: `D7` was recorded FOUR times for one dispatch of one worker.** A report that lands ends the
    worker's loop, so a repeat inside one dispatch means the earlier one did not land. One decision per dispatch,
    `chose` from the report that landed, and the retries kept as a COUNT -- the waste must stay visible."""
    ledger = WhatTheRunDecided()
    _drive(ledger, "composition_worker",
           [_reports("done"), _reports("done"), _reports("done"), _reports("blocked", "which season?"),
            AIMessage(content="reported")],
           tools=[list_mills, FabricFindings])

    made = ledger.of("run-1").by_point("D7")
    assert len(made) == 1, f"four attempts at one report were filed as {len(made)} decisions: {made}"
    assert made[0].chose == "blocked", "the status recorded is the one the worker last reported"
    assert made[0].inputs.get("tries") == 4, f"the retries are invisible: {made[0].inputs}"
    assert "which season?" in str(made[0].inputs.get("need", "")), "the landed report's need is lost"


def test_THE_REASON_A_REPORT_BOUNCED_IS_KEPT_FROM_THE_REAL_VALIDATOR() -> None:
    """**The worker's own path, because the live failure only exists on it.** A worker built by `worker_spec` passes
    its report schema as `response_format`, and the library's tool strategy is what rejects a report and re-prompts.
    So the reason is READ from the validator's complaint, not inferred from the argument keys."""
    import uuid

    from rag_wright.harness.decisions import DISPATCHER_OF_A_WORKER
    from rag_wright.harness.workers import build_worker

    def a_report(**extra: Any) -> AIMessage:
        return AIMessage(content="", tool_calls=[{"name": "FabricFindings",
                                                  "args": {"status": "done", "need": "", "swatches": [], **extra},
                                                  "id": uuid.uuid4().hex[:6], "type": "tool_call"}])

    spec = dict(_fabric_worker(_a_context(), _resources()))
    ledger = WhatTheRunDecided()
    spec["middleware"] = [_recorder(ledger, "composition_worker", is_worker=True, dispatched_by=DISPATCHER_OF_A_WORKER),
                          *spec.get("middleware", [])]
    agent = build_worker(spec, _settings(), model=scripted(a_report(swatches=[{"name": "poplin"}]), a_report()))
    agent.invoke({"messages": [{"role": "user", "content": "which blends suit outerwear?"}]},
                 context=_a_context(), config={"configurable": {"thread_id": "run-1"}})

    made = ledger.of("run-1").by_point("D7")
    assert len(made) == 1, f"a bounced report and its retry were filed as {len(made)} decisions: {made}"
    assert made[0].inputs.get("tries") == 2
    bounced = " ".join(made[0].inputs.get("rejected") or [])
    assert "swatches" in bounced, f"the ledger says a retry happened but not why: {made[0].inputs}"


def test_A_REPORT_THAT_LANDED_FIRST_TIME_SAYS_SO() -> None:
    """The negative half: `tries` is only evidence of waste if the ordinary case reads 1."""
    ledger = WhatTheRunDecided()
    _drive(ledger, "composition_worker", [_reports("done"), AIMessage(content="reported")],
           tools=[list_mills, FabricFindings])

    assert ledger.of("run-1").by_point("D7")[0].inputs.get("tries") == 1


def test_A_WORKER_DISPATCHED_TWICE_REPORTS_TWICE() -> None:
    """**Per DISPATCH, not per run.** A worker sent out again has genuinely reported twice, and collapsing the two
    would hide the second report exactly as filing four retries invents three."""
    ledger = WhatTheRunDecided()
    for status in ("blocked", "done"):
        _drive(ledger, "composition_worker", [_reports(status), AIMessage(content="reported")],
               tools=[list_mills, FabricFindings])

    made = ledger.of("run-1").by_point("D7")
    assert [row.chose for row in made] == ["blocked", "done"], f"a re-dispatched worker's second report was lost: {made}"
    assert all(row.inputs.get("tries") == 1 for row in made)


def test_AN_ORDINARY_TOOL_CALL_IS_NOT_A_REPORT() -> None:
    """**The negative half.** A worker makes many tool calls and exactly one report."""
    ledger = WhatTheRunDecided()
    _drive(ledger, "composition_worker", [AIMessage(content="", tool_calls=[_call("list_mills", {}, "a")]),
                                          AIMessage(content="done")], tools=[list_mills])

    assert ledger.of("run-1").by_point("D7") == []


def test_ONLY_A_WORKER_REPORTS() -> None:
    """The chat-manager and the orchestrator do not return a `WorkerReport`; a `D7` for them would attribute a
    decision to a tier that cannot make it."""
    ledger = WhatTheRunDecided()
    _drive(ledger, "chat-manager", [_reports("done"), AIMessage(content="reported")], tools=[list_mills, FabricFindings])

    assert ledger.of("run-1").by_point("D7") == []


# --- D4: escalate to a person, or proceed with a gap ---------------------------------------------------------


@make_tool
def ask_the_user(question: str) -> str:
    """Named and shaped as `clarify.clarification_tool`'s own tool -- the only way a tier escalates. Workers do not
    have it, so escalation is the orchestrator's decision and nobody else's."""
    return "the user says: the resort collection"


def test_ESCALATING_TO_A_PERSON_IS_RECORDED_AS_D4() -> None:
    ledger = WhatTheRunDecided()
    _drive(ledger, "orchestrator", [AIMessage(content="", tool_calls=[_call("ask_the_user",
                                                                            {"question": "which collection?"}, "q")]),
                                    AIMessage(content="asked")], tools=[ask_the_user])

    made = ledger.of("run-1").by_point("D4")
    assert len(made) == 1, f"the escalation was not recorded: {ledger.of('run-1').decisions}"
    assert made[0].chose == "ask_the_user"
    assert made[0].tier == "orchestrator"
    assert "which collection" in str(made[0].inputs.get("question", "")), (
        f"the question is not recorded, so nobody can tell a real escalation from a reflex: {made[0].inputs}"
    )


def test_PROCEEDING_DESPITE_A_BLOCKED_WORKER_IS_ALSO_A_D4() -> None:
    """**The other branch, and the one that hides.** A tier that answers anyway has decided not to escalate -- a gap
    accepted silently looks exactly like no gap. Observable only because `D7` is recorded."""
    ledger = WhatTheRunDecided()
    _drive(ledger, "composition_worker", [_reports("blocked", "which collection?"), AIMessage(content="reported")],
           tools=[list_mills, FabricFindings])
    _drive(ledger, "orchestrator", [AIMessage(content="Two blends suit outerwear.")], tools=[ask_the_user])

    made = ledger.of("run-1").by_point("D4")
    assert len(made) == 1, f"proceeding past a blocked worker was not recorded: {ledger.of('run-1').decisions}"
    assert made[0].chose == "proceed"
    assert "which collection" in str(made[0].inputs.get("unmet", "")), (
        f"the unmet need is not carried, so the gap cannot be reported to the user: {made[0].inputs}"
    )


def test_AN_ANSWER_WITH_NOTHING_BLOCKED_IS_NOT_A_D4() -> None:
    """**The negative half**, asserted for the right reason: no `D4`, and no observer that crashed instead."""
    ledger = WhatTheRunDecided()
    _drive(ledger, "composition_worker", [_reports("done"), AIMessage(content="reported")],
           tools=[list_mills, FabricFindings])
    watching = _recorder(ledger, "orchestrator")
    agent = create_agent(scripted(AIMessage(content="Two blends suit outerwear.")), tools=[ask_the_user],
                         system_prompt="s", middleware=[watching])
    agent.invoke({"messages": [{"role": "user", "content": "blends?"}]}, config={"configurable": {"thread_id": "run-1"}})

    assert ledger.of("run-1").by_point("D4") == []
    assert watching.skipped == [], f"nothing was recorded because the observer failed: {watching.skipped}"


def test_ESCALATING_IS_NOT_ALSO_RECORDED_AS_PROCEEDING() -> None:
    """A tier that asked has not proceeded; recording both would make the escalation rate unreadable."""
    ledger = WhatTheRunDecided()
    _drive(ledger, "composition_worker", [_reports("blocked", "which collection?"), AIMessage(content="reported")],
           tools=[list_mills, FabricFindings])
    _drive(ledger, "orchestrator", [AIMessage(content="", tool_calls=[_call("ask_the_user",
                                                                            {"question": "which collection?"}, "q")]),
                                    AIMessage(content="asked")], tools=[ask_the_user])

    assert [d.chose for d in ledger.of("run-1").by_point("D4")] == ["ask_the_user"]


def test_NOTHING_IS_SKIPPED_ON_THE_ORDINARY_PATH() -> None:
    """**The half that makes the skip counter useful.** No `D4` because there was nothing to record, not because the
    observer crashed."""
    ledger = WhatTheRunDecided()
    middleware = _recorder(ledger, "orchestrator")
    agent = create_agent(scripted(AIMessage(content="Two blends suit outerwear.")), tools=[ask_the_user],
                         system_prompt="s", middleware=[middleware])
    agent.invoke({"messages": [{"role": "user", "content": "blends?"}]}, config={"configurable": {"thread_id": "run-1"}})

    assert middleware.skipped == [], f"an observation was skipped on a clean run: {middleware.skipped}"
    assert ledger.of("run-1").by_point("D4") == []


# --- D6: which of the product's framings a worker took first ------------------------------------------------


@make_tool
def match_compositions(requirements: str) -> dict:
    """Match a requirement set across every composition."""
    return {"matched": ["cotton-poplin-120gsm"]}


@make_tool
def read_swatch(swatch: str, question: str) -> dict:
    """Read one named swatch."""
    return {"found": True, "answer": "120 gsm"}


def _worker_calling(*names: str, tier: str = "composition_worker") -> WhatTheRunDecided:
    args = {"read_swatch": {"swatch": "cotton-poplin-120gsm", "question": "weight?"},
            "match_compositions": {"requirements": "outerwear"}}
    ledger = WhatTheRunDecided()
    replies = [AIMessage(content="", tool_calls=[_call(name, args.get(name, {}), f"c{n}")])
               for n, name in enumerate(names)]
    _drive(ledger, tier, [*replies, AIMessage(content="answered")], tools=[match_compositions, read_swatch, list_mills])
    return ledger


def test_MATCHING_ACROSS_EVERYTHING_IS_RECORDED_AS_D6() -> None:
    made = _worker_calling("match_compositions").of("run-1").by_point("D6")

    assert len(made) == 1, "the framing choice was not recorded"
    assert made[0].chose == "match_compositions"
    assert made[0].tier == "composition_worker"


def test_READING_A_NAMED_SWATCH_IS_THE_OTHER_BRANCH() -> None:
    """**A real alternative rather than a failure**: a worker handed a swatch should read it, not search for it."""
    made = _worker_calling("read_swatch").of("run-1").by_point("D6")

    assert len(made) == 1
    assert made[0].chose == "read_swatch"


def test_ONLY_THE_FIRST_OF_THE_TWO_IS_A_DECISION() -> None:
    """**A match followed by reads is one framing choice and several tool calls**: reading what the match found is
    executing the plan, not reconsidering it."""
    made = _worker_calling("match_compositions", "read_swatch", "read_swatch").of("run-1").by_point("D6")

    assert [d.chose for d in made] == ["match_compositions"], f"a plan being executed was recorded again: {made}"


def test_EACH_WORKER_MAKES_ITS_OWN_D6() -> None:
    """Two workers in one run each choose their own framing, and collapsing them would hide one."""
    ledger = _worker_calling("match_compositions")
    _drive(ledger, "mill_worker", [AIMessage(content="", tool_calls=[_call("read_swatch",
                                                                           {"swatch": "s", "question": "q"}, "x")]),
                                   AIMessage(content="answered")],
           tools=[match_compositions, read_swatch, list_mills])

    made = ledger.of("run-1").by_point("D6")
    assert {(d.tier, d.chose) for d in made} == {("composition_worker", "match_compositions"),
                                                 ("mill_worker", "read_swatch")}


def test_A_TIER_THAT_IS_NOT_A_WORKER_MAKES_NO_D6() -> None:
    """The chat-manager and orchestrator do not choose a framing; a `D6` for them would attribute a decision to a
    tier that cannot make it."""
    ledger = WhatTheRunDecided()
    watching = _recorder(ledger, "orchestrator")
    agent = create_agent(scripted(AIMessage(content="", tool_calls=[_call("match_compositions",
                                                                           {"requirements": "outerwear"}, "a")]),
                                  AIMessage(content="answered")),
                         tools=[match_compositions], system_prompt="s", middleware=[watching])
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config={"configurable": {"thread_id": "run-1"}})

    assert ledger.of("run-1").by_point("D6") == []
    assert watching.skipped == [], f"nothing was recorded because an observer failed: {watching.skipped}"


# --- D5: how the product's framing tool read the question ------------------------------------------------------


def _reading(**fields: Any) -> Any:
    """The fabric's `read_the_brief`, returning whatever its classifier is said to have found."""
    payload = {"fibres": [], "fallback": False, "confidence": 0.0, **fields}

    @make_tool
    def read_the_brief(brief: str) -> dict:
        """Read an enquiry into the fibres this account stocks."""
        return payload

    return read_the_brief


def _worker_reads(tool: Any, *, tier: str = "composition_worker") -> WhatTheRunDecided:
    ledger = WhatTheRunDecided()
    _drive(ledger, tier, [AIMessage(content="", tool_calls=[_call("read_the_brief", {"brief": "outerwear?"}, "r")]),
                          AIMessage(content="answered")], tools=[tool])
    return ledger


def test_A_NAMED_FRAMING_IS_RECORDED_AS_D5_WITH_ITS_REAL_CONFIDENCE() -> None:
    """**The only decision in the ledger that can arrive WITH a confidence**: a framing a product's classifier
    decides comes back with a number, where every model-taken decision carries `None`. Dropping it would lose the
    one row that already has the property a threshold-and-escalate design needs."""
    made = _worker_reads(_reading(fibres=["cotton"], confidence=0.82)).of("run-1").by_point("D5")

    assert len(made) == 1, "the framing was not recorded"
    assert made[0].chose == "cotton"
    assert made[0].confidence == 0.82, f"the confidence was dropped: {made[0].confidence}"
    assert made[0].tier == "composition_worker"


def test_NO_FRAMING_IS_THE_OTHER_BRANCH_AND_SAYS_SO() -> None:
    """A fallback -- the classifier recognised nothing -- is a real alternative, not a failure, and recording it as a
    framing of `""` would make it indistinguishable from a reading nobody looked at."""
    made = _worker_reads(_reading(fibres=[], fallback=True, confidence=0.11)).of("run-1").by_point("D5")

    assert made[0].chose == "no framing"
    assert made[0].confidence == 0.11
    assert made[0].inputs.get("fallback") is True


def test_SEVERAL_READINGS_ARE_ALL_CARRIED() -> None:
    """A reading can name more than one, and keeping only the first would misdescribe the search that followed."""
    made = _worker_reads(_reading(fibres=["cotton", "linen"], confidence=0.7)).of("run-1").by_point("D5")

    assert made[0].inputs.get("fibres") == ["cotton", "linen"]


def test_ANOTHER_TOOL_S_RESULT_IS_NOT_A_FRAMING() -> None:
    """**The negative half.** Reading every tool result as a framing would put a `D5` on every match and read."""
    ledger = WhatTheRunDecided()
    watching = _recorder(ledger, "composition_worker")
    agent = create_agent(scripted(AIMessage(content="", tool_calls=[_call("match_compositions",
                                                                           {"requirements": "outerwear"}, "s")]),
                                  AIMessage(content="answered")),
                         tools=[match_compositions], system_prompt="s", middleware=[watching])
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config={"configurable": {"thread_id": "run-1"}})

    assert ledger.of("run-1").by_point("D5") == []
    assert watching.skipped == [], f"an observer failed rather than declining: {watching.skipped}"


def test_A_RESULT_THAT_IS_NOT_JSON_IS_DECLINED_RATHER_THAN_CRASHING() -> None:
    """A tool result is a string, and a malformed one must not take the run down."""
    @make_tool
    def read_the_brief(brief: str) -> str:
        """Read an enquiry, badly."""
        return "not json at all"

    assert _worker_reads(read_the_brief).of("run-1").by_point("D5") == []
