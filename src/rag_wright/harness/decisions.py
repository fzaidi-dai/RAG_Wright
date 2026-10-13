"""What the run DECIDED, recorded as a decision (T-5a.13a, ANFR-3, ANFR-6).

**The cornerstone rests on this module.** `docs/design/robustness-before-speed.md` §2a says a wrong
decision -- routing, classification, dispatch, escalation, a guardrail verdict -- is a stop-the-line event,
and rarity is not a defence. That is unenforceable today: of the ten decisions in its table, six are
unjudged and most are *invisible*, taken inside a model turn that is also doing other work, with no record
of what the options were or which was taken. **You cannot audit what was never written down**, and "rare"
is exactly the regime where watching does not work.

**A decision is DERIVED from what a tier did, never declared by the model.** Asking a model to announce
its choice would change the prompt -- the thing T-5a.12af reverted a week of work for, because nothing
could detect whether the change helped -- and it would ride `response_format`, which the model may simply
decline (T-5a.12y: it declined on every tier of a run whose answer was right). So this mirrors `rules.py`:
a registry of points, each with a deterministic observation at a seam we already own. *A grader asked "did
it re-dispatch?" can be wrong. One that counts cannot.*

**A dispatch is recorded by the tier that WAS dispatched, not by the parent's `task` call.** A sandbox
`task()` does not route through the parent's `ToolNode`-managed tool, so a transcript-based count is blind
in the coded arm -- which is the arm where the fan-out happened. Measured live: `compliance_worker`
dispatched **7 times**, recorded as 1, and `dispatched_once_each` returned 1.000 on that run. A dispatched
tier's opening turn carries exactly one human message, and that is true in both arms. Same definition as
`evals/recording.py`, for the same reason.

**It records; it never judges.** Whether a decision was right is a property of an acceptable SET, held by
the eval (§2a: *"wrong means outside the acceptable set, not different from a reference"*). Putting that
here would collapse the very split that lets a wrong decision be seen: an agent can decide well and answer
badly, or decide badly and answer well by luck.

**And it never breaks a model call.** Rule 4, taken from the engine's tracing for the same reason -- a
recorder that can fail a run is worse than no recorder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

from rag_wright.harness.seen import run_key

#: The workers a dispatch can name, from the roster itself rather than a second copy of it. A list that
#: **The option space D2 chooses from, when no roster is supplied.** A product's workers are its own, and
#: this module is substrate: it used to derive `WORKER_NAMES` from `rulewright.agent.workers.WORKERS` at
#: import, which put this product's roster in the decision taxonomy. `RecordTheDecisions` now takes the
#: names it should record, and this is what a caller that supplies none gets.
ANY_WORKER = "<worker>"

#: **Who decided that this tier should run.** A worker's dispatch is the orchestrator's decision; the
#: orchestrator's is the chat-manager's. Recorded rather than inferred, because attributing a re-dispatch
#: to the tier that was told to run again blames the wrong decision-maker entirely.
#:
#: **The orchestrator was missing from the first version, and a 24-dispatch loop was invisible for it**
#: (measured 2026-09-26). §2a's table assigns `D3` to the orchestrator, so the observer fired only for
#: workers -- and the chat-manager makes the same accept-or-re-dispatch decision about the orchestrator.
#:
#: **It is a CONSTRUCTOR argument now, not a table keyed by this product's worker names** (T-7.58). Every
#: site that builds the observer already knows what kind of tier it is building for: `workers.py` passes a
#: worker's own name, `tiers.py` passes `"orchestrator"`, `agent.py` passes `"chat-manager"`. Looking the
#: answer up in a roster was asking a question the caller had already answered.
DISPATCHER_OF_A_WORKER = "orchestrator"
DISPATCHER_OF_THE_ORCHESTRATOR = "chat-manager"

#: The only way a tier escalates to a person (`clarify.clarification_tool`). **Workers deliberately do not
#: hold it** -- `AR-16`: *"a prompt saying 'do not ask' is a suggestion; a tool the worker was never given is
#: a fact"* -- so an escalation is the orchestrator's decision and nobody else's.
ESCALATION = "ask_the_user"



#: Longest input value kept. A whole brief makes the ledger unreadable without saying more than its
#: opening does; `evals/recording.py` keeps the brief in full for the diff, and this is the audit trail.
KEEP_CHARS = 300
#: How many bounced reports to keep the reason for. A worker that retried twenty times says the same thing
#: twenty times; `tries` already carries the count, and the ledger rides a trace payload.
RETRIES_KEPT = 3


@dataclass(frozen=True)
class DecisionPoint:
    """One place the system chooses, and what it is choosing between.

    `observed` is the honest half: a point declared here without an observation is a decision we know is
    being taken and still cannot see. Naming it costs nothing and keeps the gap countable, which is the
    opposite of how six of these came to be invisible.
    """

    id: str
    question: str
    #: Who chooses. Not always a model -- `D9` is a deterministic classifier and must stay one.
    made_by: str
    options: tuple[str, ...]
    observed: bool = False
    #: Why this is deliberately NOT recorded here. **Empty means it is a GAP**, and the two are different
    #: things: `observed=False` alone conflated *"nobody records this yet"* with *"recording this would be
    #: wrong"*, which made five gaps look like seven and invited someone to close the wrong two.
    why_not: str = ""
    #: What would have to EXIST before this could be recorded. **A decision nothing makes cannot be
    #: observed**, and calling it an unobserved gap invites an observer for a decision nobody takes -- which
    #: would record the model's implicit guess as though a guardrail had ruled on it.
    blocked_on: str = ""



#: **The four-way partition moved to the PRODUCT with the points themselves** (T-7.58).
#: `DELIBERATELY_NOT_RECORDED`, `NO_DECIDER_YET` and `UNOBSERVED` were derived here from a module-level
#: `POINTS`, so they described RuleWright's recording state as though it were any product's -- and `_BY_ID`
#: gave every ledger this product's option spaces. They are `rulewright/agent/decision_points.py` now;
#: `WhatTheRunDecided` takes the points and looks an option space up on the record it makes.
#:
#: The CATEGORIES are the substrate's contribution and are worth restating, because collapsing them is how
#: `D8` came to look like a small coding task and `D9` like a gap: a point is recorded, a real recording
#: gap, left out on purpose (`why_not`), or blocked on a mechanism (`blocked_on`) -- exactly one of the
#: four, and `DecisionPoint` carries the fields that decide which.


@dataclass(frozen=True)
class Decision:
    """One decision, as it was taken.

    **`confidence` is `None` for anything a model decided, and that is the finding rather than a missing
    field** (§2a). An LLM turn returns a 51% call and a 99% call indistinguishably, which is precisely why
    a rare wrong decision stays silent until it bites; a classifier that returns a probability per option
    is what would buy a threshold to escalate below. Recording the absence is what makes it countable.
    """

    point: str
    #: Who took it. A worker's dispatch is the ORCHESTRATOR's decision, so this is not always the tier the
    #: observation was made on -- which is the whole reason attribution is recorded rather than inferred.
    tier: str
    options: tuple[str, ...]
    chose: str
    confidence: float | None
    inputs: dict[str, Any]
    #: Position within the run, so two decisions can be ordered without a clock.
    n: int


@dataclass
class DecisionLedger:
    """Every decision one run took, in order, across all of its dispatches."""

    decisions: list[Decision] = field(default_factory=list)
    #: Every tier this run started, in order and with repeats. **Kept separately from the decisions**,
    #: because the orchestrator's FIRST dispatch is already `D1` and recording it again as a `D2` would
    #: count one decision twice -- while repeat detection still needs to know it happened.
    dispatches: list[str] = field(default_factory=list)
    #: The product's decision points, passed down from `WhatTheRunDecided` so one ledger and its records
    #: cannot disagree about what the option space for a point is.
    points: tuple[Any, ...] = ()

    def record(
        self,
        point: str,
        *,
        tier: str,
        chose: str,
        inputs: dict[str, Any] | None = None,
        confidence: float | None = None,
    ) -> Decision:
        made = Decision(
            point=point,
            tier=tier,
            options=next((p.options for p in self.points if p.id == point), ()),
            chose=chose,
            confidence=confidence,
            inputs=_clipped(inputs or {}),
            n=len(self.decisions) + 1,
        )
        self.decisions.append(made)
        return made

    def supersede(self, made: Decision, *, chose: str, inputs: dict[str, Any]) -> Decision:
        """Replace a row with a corrected one, IN PLACE and keeping its `n`.

        **A retry is not a new decision.** The alternative -- appending -- is what made one worker's report
        read as four decisions on 2026-09-26. Keeping `n` matters because it is the run's only ordering:
        moving the row to the end would say the worker reported after everything that followed its first try.
        """
        corrected = Decision(point=made.point, tier=made.tier, options=made.options, chose=chose,
                             confidence=made.confidence, inputs=_clipped(inputs), n=made.n)
        self.decisions[self.decisions.index(made)] = corrected
        return corrected

    def by_point(self, point: str) -> list[Decision]:
        return [made for made in self.decisions if made.point == point]

    def dispatched(self) -> list[str]:
        """Every tier this run started, in order and with repeats kept."""
        return list(self.dispatches)


@dataclass
class WhatTheRunDecided:
    """One ledger per run, and one object that holds them all.

    Keyed by `run_key` -- the same definition of *a run* the call record already uses (`seen.py`), so a
    re-dispatch means "again in this run" and can never mean "somebody else asked the same thing". A
    second notion of run identity is a second thing to get wrong.
    """

    runs: dict[str, DecisionLedger] = field(default_factory=dict)
    #: The PRODUCT's decision points (T-7.58). `record()` reads each point's option space from here; this
    #: was a module-level `_BY_ID` over ten points phrased in RuleWright's vocabulary, so a second
    #: product's ledger recorded its decisions against our options. A point nobody declared records with
    #: no options rather than raising -- the CHOSEN value is the part that matters.
    points: tuple[Any, ...] = ()

    def of(self, key: str) -> DecisionLedger:
        return self.runs.setdefault(key, DecisionLedger(points=self.points))


def _clipped(inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (value[:KEEP_CHARS] if isinstance(value, str) else value)
        for key, value in inputs.items()
    }


def _why_it_bounced(request: Any) -> str:
    """Why the PREVIOUS report did not land, in the validator's own words.

    `ToolStrategy` appends its complaint as a `ToolMessage` -- *"Failed to parse structured output for tool
    'ClauseFindings': ... matched.0.document_id Field required"* -- so the reason is already in the history
    the retry was asked from. **Read rather than inferred**: guessing from the argument keys would have said
    nothing, because the live failure was a MISSING NESTED field and every top-level key was present.
    """
    for message in reversed(getattr(request, "messages", None) or []):
        if getattr(message, "type", "") != "tool":
            continue
        said = _text(getattr(message, "content", ""))
        return said if said.startswith("Error") else ""
    return ""


def _tool_calls(response: Any) -> list[dict[str, Any]]:
    """Every tool call in the reply to the turn just taken.

    Read from `ModelResponse.result` rather than from the NEXT turn's history, which is off by one: a call
    made on turn 1 first appears in the transcript on turn 2. Same reasoning as `RecordTheContext._reply`.
    """
    return [
        call
        for message in (getattr(response, "result", None) or [])
        for call in (getattr(message, "tool_calls", None) or [])
    ]


class RecordTheDecisions(AgentMiddleware):
    """Write this tier's decisions into the run's ledger, changing nothing else.

    Attached per tier, because attribution is the point: *"an alarm that cannot name its cause is one we
    will learn to ignore"* (§2a).
    """

    def __init__(self, ledger: WhatTheRunDecided, *, tier: str, is_worker: bool = False,
                 dispatched_by: str | None = None, worker_names: tuple[str, ...] = (),
                 framings: tuple[str, ...] = (),
                 #: `(tool_name, parsed_result) -> {chose, confidence, inputs} | None`: the product reads
                 #: its OWN framing tool's reply. `None` records no `D5`, which is honest for a product
                 #: that has no such tool.
                 read_framing: Any = None) -> None:
        """**What kind of tier this is, told rather than looked up** (T-7.58).

        `is_worker` and `dispatched_by` replaced membership in this product's roster: every caller already
        knows which it is building for, so the lookup was asking a question the caller had answered.
        `worker_names` is only `D2`'s recorded option space, and a caller that supplies none gets
        `ANY_WORKER` -- the decision is still recorded, with its chosen value, which is the part that
        matters.

        **`framings` is the same shape of fact and was the last of this product's tool names in here.**
        It was `FRAMINGS = ("sweep_matter", "ask_contract_question")`, so `D6` -- which framing a worker
        chose for its task -- could only ever fire for RuleWright. A caller that supplies none records no
        `D6`, which is honest: a product that does not distinguish framings has no such decision to make.
        """
        super().__init__()
        self._framings = framings
        self._read_framing = read_framing
        self._ledger = ledger
        self._tier = tier
        self._is_worker = is_worker
        self._dispatched_by = dispatched_by
        self._worker_names = worker_names
        #: Observations that RAISED and were swallowed. **Counted because the swallow made a bug in an
        #: observer indistinguishable from an observation correctly not made**: a planted `IndexError`
        #: inside `_escalated` left all thirty tests green, since `_observe` caught it and nothing recorded
        #: the fact. A guard that cannot fail is the defect this phase pays for most often, and the
        #: never-break-a-run rule was quietly creating one. Production still degrades to silence; the
        #: silence is now countable.
        self.skipped: list[str] = []

    @property
    def name(self) -> str:
        """Distinct per tier: `create_agent` refuses two middlewares sharing a name, which is what broke a
        second `withhold(...)` on one agent."""
        return f"{type(self).__name__}({self._tier})"

    def wrap_tool_call(self, request, handler):
        result = handler(request)
        self._observe(self._framing, request, result)
        return result

    async def awrap_tool_call(self, request, handler):
        result = await handler(request)
        self._observe(self._framing, request, result)
        return result

    def wrap_model_call(self, request, handler):
        self._observe(self._opening, request)
        response = handler(request)
        self._observe(self._replied, request, response)
        self._observe(self._reported, request, response)
        self._observe(self._escalated, request, response)
        self._observe(self._framed, request, response)
        return response

    async def awrap_model_call(self, request, handler):
        self._observe(self._opening, request)
        response = await handler(request)
        self._observe(self._replied, request, response)
        self._observe(self._reported, request, response)
        self._observe(self._escalated, request, response)
        self._observe(self._framed, request, response)
        return response

    # --- the observations -------------------------------------------------------------------------

    def _opening(self, request) -> None:
        """A tier being handed a brief: `D2` the first time, `D3` when it has been here before.

        A dispatch opens the subagent afresh, so its first turn carries exactly one human message. Later
        turns carry the history as well, which is what keeps a worker's four turns one dispatch.
        """
        decided_by = self._dispatched_by
        if decided_by is None:
            return
        messages = getattr(request, "messages", None) or []
        if len(messages) != 1 or getattr(messages[0], "type", "") != "human":
            return

        here = self._ledger.of(run_key(request))
        brief = _text(getattr(messages[0], "content", ""))
        again = self._tier in here.dispatches
        here.dispatches.append(self._tier)

        if again:
            here.record("D3", tier=decided_by, chose="re_dispatch",
                        inputs={"tier": self._tier, "brief": brief})
            return
        # **The orchestrator's first dispatch is `D1`, already recorded at the chat tier.** Recording a
        # `D2` for it as well would count one decision twice, and `D2` is *"which WORKERS this question
        # needs"* -- the orchestrator is not one of them.
        if self._is_worker:
            here.record("D2", tier=decided_by, chose=self._tier, inputs={"brief": brief})

    def _framing(self, request, result) -> None:
        """`D5`, read off `read_the_question`'s reply -- **with its confidence**.

        **`semantic_fallback` is a real alternative, not a failure**: the tool's own words are *"true when the
        corpus recognised no type at all"*, which is the generic tier doing its job. Recording it
        as a framing of `""` would make a fallback indistinguishable from a reading nobody looked at.

        A tool result is a JSON string, and a malformed one is DECLINED rather than crashing -- but the
        decline is visible, because `_observe` counts what it swallows.
        """
        import json

        if self._read_framing is None:
            return
        call = getattr(request, "tool_call", None) or {}
        content = getattr(result, "content", None)
        if not isinstance(content, str):
            return
        try:
            reading = json.loads(content)
        except (ValueError, TypeError):
            return
        if not isinstance(reading, dict):
            return

        # **The product reads its own tool's reply** (T-7.58). This checked
        # `call.get("name") != FRAMING_TOOL` against the literal `read_the_question` and then pulled
        # `clause_types`, `semantic_fallback`, `confidence` and `summary` -- one product's tool and four of
        # its payload keys, inside the registry. What stays here is generic and worth keeping: find the
        # call, parse its JSON safely, DECLINE a malformed one visibly, and record the decision.
        read = self._read_framing(str(call.get("name") or ""), reading)
        if not read:
            return
        confidence = read.get("confidence")
        self._ledger.of(run_key(request)).record(
            "D5", tier=self._tier,
            chose=str(read.get("chose") or "no framing"),
            confidence=float(confidence) if isinstance(confidence, int | float) else None,
            inputs=dict(read.get("inputs") or {}),
        )

    def _framed(self, request, response) -> None:
        """`D6`: sweep the matter, or read a named document.

        **Only the FIRST of the two is a decision.** A sweep followed by four reads is one framing choice and
        five tool calls -- reading the documents a sweep matched is executing the plan, not reconsidering it.
        Recording each would turn an ordinary worker turn into five decisions and misdescribe what happened.
        Same judgement `D1` needed, where a turn spent on the tier's own tools had not decided anything yet.

        **Once per TIER per run**, because two workers in one run each choose their own framing and
        collapsing them would hide one.
        """
        if not self._is_worker:
            return
        here = self._ledger.of(run_key(request))
        if any(made.tier == self._tier for made in here.by_point("D6")):
            return
        chose = next((call.get("name") for call in _tool_calls(response)
                      if call.get("name") in self._framings), None)
        if chose is None:
            return
        here.record("D6", tier=self._tier, chose=str(chose))

    def _escalated(self, request, response) -> None:
        """`D4`: escalate to a person, or proceed with a gap.

        **Both branches, and the second only became observable when `D7` landed.** Escalating is a call to
        `ask_the_user`. *Proceeding* is answering while a worker's `blocked` need is still unmet -- which is
        a decision, not an absence, and is the shape `A1`'s family is about: a gap accepted silently looks
        exactly like no gap. Before `D7` there was nothing in the record to notice it against.

        Recorded once per run: a tier that asked has not proceeded, and recording both would double-count
        one decision and make the escalation rate unreadable.
        """
        if self._tier != "orchestrator":
            return
        here = self._ledger.of(run_key(request))
        if here.by_point("D4"):
            return

        calls = _tool_calls(response)
        asked = next((call for call in calls if call.get("name") == ESCALATION), None)
        if asked is not None:
            here.record("D4", tier=self._tier, chose=ESCALATION,
                        inputs={"question": _text((asked.get("args") or {}).get("question", ""))})
            return

        # **Only an ANSWER counts as proceeding.** A turn still making tool calls has not decided anything
        # yet, and recording `proceed` on it would fire on every intermediate step.
        if calls or not _said_something(response):
            return
        unmet = [made for made in here.by_point("D7")
                 if made.chose == "blocked" and str(made.inputs.get("need") or "").strip()]
        if unmet:
            here.record("D4", tier=self._tier, chose="proceed",
                        inputs={"unmet": str(unmet[-1].inputs.get("need", ""))})

    def _reported(self, request, response) -> None:
        """`D7`, from the worker's own structured report.

        **Read the way `rules.py::_report_of` already reads it** -- *"the structured response arrives as a
        tool call named after the schema"* -- rather than inventing a second shape for one fact. A worker
        makes many tool calls and exactly one report; the report is the one carrying `status`.
        """
        if not self._is_worker:
            return
        for call in _tool_calls(response):
            args = call.get("args") or {}
            if "status" not in args:
                continue
            here = self._ledger.of(run_key(request))
            nth = max(1, here.dispatches.count(self._tier))
            earlier = next((made for made in here.by_point("D7")
                            if made.tier == self._tier and made.inputs.get("dispatch") == nth), None)
            landed = {"need": _text(args.get("need") or ""), "dispatch": nth}
            if earlier is None:
                here.record("D7", tier=self._tier, chose=str(args.get("status") or ""),
                            inputs={**landed, "tries": 1})
                return
            rejected = [*(earlier.inputs.get("rejected") or []), _why_it_bounced(request)]
            here.supersede(earlier, chose=str(args.get("status") or ""),
                           inputs={**landed,
                                   "tries": int(earlier.inputs.get("tries") or 1) + 1,
                                   "rejected": [why for why in rejected if why][-RETRIES_KEPT:]})
            return

    def _replied(self, request, response) -> None:
        """`D1`, resolved on the turn that actually resolves it.

        **Neither branch is the first turn.** A chat-manager that lists the documents and then delegates
        took one decision -- `delegate` -- and filing `answer_directly` at turn 1 would record the
        opposite of what happened. So the decision lands on the first turn that either hands the request
        over or answers without a tool call; a turn spent on its own tools has not decided yet.
        """
        if self._tier != "chat-manager":
            return
        here = self._ledger.of(run_key(request))
        if here.by_point("D1"):
            return

        calls = _tool_calls(response)
        handed_over = next((call for call in calls if call.get("name") == "task"), None)
        if handed_over is not None:
            here.record("D1", tier=self._tier, chose="delegate",
                        inputs={"to": str((handed_over.get("args") or {}).get("subagent_type", "")),
                                "brief": _text((handed_over.get("args") or {}).get("description", ""))})
        elif not calls:
            here.record("D1", tier=self._tier, chose="answer_directly",
                        inputs={"turns": len(getattr(request, "messages", None) or [])})

    def _observe(self, observe: Any, *args: Any) -> None:
        """**A recorder must never fail a run.** Planted in the tests rather than asserted in a comment."""
        try:
            observe(*args)
        except Exception as failed:
            import logging

            # **Counted, not merely logged.** The swallow made a BUG in an observer indistinguishable from
            # an observation correctly not made: a planted `IndexError` in `_escalated` left all thirty
            # tests in this file green. Production still degrades to silence; the silence is countable now,
            # so a test asserting "nothing was recorded" can assert it was for the right reason.
            self.skipped.append(f"{getattr(observe, '__name__', observe)}: {type(failed).__name__}")
            logging.getLogger(__name__).warning("decision_ledger_skipped_an_observation", exc_info=True)


def _said_something(response: Any) -> bool:
    """Whether the reply carries content -- an answer rather than a step on the way to one."""
    return any(
        str(getattr(message, "content", "") or "").strip()
        for message in (getattr(response, "result", None) or [])
    )


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return str(content or "")
