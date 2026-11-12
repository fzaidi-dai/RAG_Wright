"""The rule registry's mechanism: the detectors, on and off, and the rubric a grader is handed.

Ported from RuleWright under ADR-0139's handover (PS-48d); the substrate is `rag_wright.harness.rules`, driven with a
registry the fabric product declares over the engine's own detectors. RuleWright's tests of its own registry (that
each of its instructions is really delivered, that each rule came from an observed run, its prompts) and the tests
against its committed recordings of real runs stay in RuleWright: those recordings are product material.

**A checker nobody tests is worse than none**: a dispatch counter once returned 1.0 on the very run that fanned out
per document for 230,002 input tokens. So every detector is driven both ways -- a recording that breaks the rule, and
one that holds to it -- because a detector that fires on correct behaviour teaches everyone to ignore it.
"""

from __future__ import annotations

from typing import Any

from rag_wright.harness.rules import broken, guessed_identifiers, rubric_for, rule_named, rules_for
from tests.harness.fabric import (
    fabric_rules,
    recorded_fan_out,
    recorded_healthy_worker,
    recorded_program_for_one_call,
)

#: The fabric product's registry over the engine's detectors: its own ids and wording, the substrate's counting.
RULES = fabric_rules()


def _recording(*, dispatches: list[str] | None = None, calls: list[tuple[str, dict]] | None = None,
               report: dict | None = None) -> dict[str, Any]:
    """A recording in the shape the detectors read: the briefs a tier was dispatched with, and each turn's calls."""
    return {
        "dispatches": dispatches if dispatches is not None else ["match the outerwear brief"],
        "system_prompt": "",
        "report": report,
        "turns": [{"n": i + 1, "messages": [], "said": [{"role": "ai", "text": "",
                                                          "tool_calls": [{"name": name, "args": args}]}]}
                  for i, (name, args) in enumerate(calls or [])],
    }


# --- the detectors, against recordings the real recorder made -----------------------------------------


def test_A_REAL_FAN_OUT_IS_CAUGHT() -> None:
    """**The shape that once scored 1.0 under a counter of tool calls**: a worker dispatched once to probe, then once
    per swatch. Recorded by `RecordTheContext` from a driven run, not built to suit the detector."""
    found = broken(recorded_fan_out(), tier="worker", rules=RULES)

    assert "one-dispatch-per-worker" in found
    assert "4 times" in found["one-dispatch-per-worker"]
    assert "Read ONLY swatch" in found["one-dispatch-per-worker"], (
        "the gap does not show the per-swatch briefs, which are the whole diagnosis"
    )


def test_A_REAL_PROGRAM_FOR_ONE_CALL_IS_CAUGHT() -> None:
    """A coded orchestrator wrote a program to list the mills, which one direct call does for less."""
    assert "no-program-for-a-single-call" in broken(recorded_program_for_one_call(), tier="coded", rules=RULES)


def test_A_HEALTHY_RUN_BREAKS_NOTHING() -> None:
    """**The other direction, and what makes the detectors worth anything.** A detector that fires on correct
    behaviour costs more than no detector: it teaches everyone to ignore it."""
    assert broken(recorded_healthy_worker(), tier="worker", rules=RULES) == {}


# --- each detector, on and off ------------------------------------------------------------------------


def test_ONE_DISPATCH_IS_FINE_AND_TWO_IS_NOT() -> None:
    assert broken(_recording(dispatches=["match the outerwear brief"]), tier="worker", rules=RULES) == {}
    found = broken(_recording(dispatches=["swatch 1 only", "swatch 2 only"]), tier="worker", rules=RULES)
    assert "one-dispatch-per-worker" in found
    assert "swatch 2 only" in found["one-dispatch-per-worker"], "the gap does not show the briefs, the diagnosis"


def test_THE_AGENTS_OWN_NOTES_ARE_NOT_THE_DOCUMENTS() -> None:
    """**`/memories/` really is a filesystem** -- memory recall IS `glob` and `grep` across it. A detector that
    flagged those would forbid the one legitimate use."""
    own = _recording(calls=[("grep", {"pattern": "poplin", "path": "/memories"}),
                            ("ls", {"path": "/memories/collection"})])
    documents = _recording(calls=[("glob", {"pattern": "**/*.weave"})])

    assert broken(own, tier="worker", rules=RULES) == {}
    assert "compositions-are-not-files" in broken(documents, tier="worker", rules=RULES)


def test_A_PROGRAM_THAT_DISPATCHES_IS_NOT_A_WASTED_PROGRAM() -> None:
    """The fan-out program is the mechanism working; a one-call wrapper is not."""
    real = _recording(calls=[("eval", {"code": "const [a,b] = await Promise.all([task({}), task({})]); a"})])
    wrapper = _recording(calls=[("eval", {"code": "const m = await tools.listMills({}); m"})])

    assert broken(real, tier="coded", rules=RULES) == {}
    assert "no-program-for-a-single-call" in broken(wrapper, tier="coded", rules=RULES)


def test_AN_ID_THE_TASK_SUPPLIED_IS_NOT_A_GUESS() -> None:
    """The id in the brief is the one it is supposed to use; one character off is a guess."""
    given = "read back enquiry 3f1c9d2a-5b7e-4a10-9c33-8d21e4f7b6a0"
    used = _recording(dispatches=[given],
                      calls=[("read_enquiry", {"enquiry_id": "3f1c9d2a-5b7e-4a10-9c33-8d21e4f7b6a0"})])
    guessed = _recording(dispatches=[given],
                         calls=[("read_enquiry", {"enquiry_id": "3f1c9d2a-5b7e-4a10-9c33-8d21e4f7b6ab"})])

    assert broken(used, tier="worker", rules=RULES) == {}
    found = broken(guessed, tier="worker", rules=RULES)
    assert "ids-are-not-guessable" in found
    assert "read_enquiry" in found["ids-are-not-guessable"] and "8d21e4f7b6ab" in found["ids-are-not-guessable"]


def test_ONLY_THE_ARGUMENTS_A_PRODUCT_NAMES_ARE_CHECKED() -> None:
    """**Found by this port** (PS-48d): the detector named one product's tool and argument, so for every other
    product it could never fire and every run read as clean. The product names which arguments must carry an id it
    was given; any other argument -- a free-text query, an id a tool returned -- is not this detector's business."""
    other = _recording(dispatches=["match the outerwear brief"],
                       calls=[("match_compositions", {"requirements": "a7b3c9d1-0000-4000-8000-000000000000"}),
                              ("read_swatch", {"swatch_id": "never-supplied"})])

    assert broken(other, tier="worker", rules=RULES) == {}
    assert guessed_identifiers({})(other) is None, "a detector told to check nothing found something"


def test_A_BLOCKED_REPORT_MUST_SAY_WHAT_WOULD_UNBLOCK_IT() -> None:
    assert broken(_recording(report={"status": "done", "need": ""}), tier="worker", rules=RULES) == {}
    assert broken(_recording(report={"status": "blocked", "need": "Which season?"}), tier="worker", rules=RULES) == {}
    assert "blocked-must-name-the-need" in broken(_recording(report={"status": "blocked", "need": ""}),
                                                  tier="worker", rules=RULES)


# --- the rubric a grader is handed --------------------------------------------------------------------


def test_THE_RUBRIC_IS_WHAT_A_GRADER_EXPECTS() -> None:
    """`RubricMiddleware` takes a bullet list on invocation state, one line per rule for the tier."""
    rubric = rubric_for("worker", RULES)

    assert rubric.startswith("- ")
    assert len(rubric.splitlines()) == len(rules_for("worker", RULES))
    assert "dispatched once" in rubric


def test_EVERY_CRITERION_CARRIES_ITS_RULE_ID() -> None:
    """**Because the grader names criteria itself.** Left to invent one it returned a readable name joinable to
    nothing, and a score whose key changes with the grader's phrasing measures nothing over time."""
    for line in rubric_for("worker", RULES).splitlines():
        assert rule_named(line, RULES) is not None, f"no rule id in {line!r}"

    assert rule_named("[one-dispatch-per-worker] The worker was dispatched once", RULES).id == "one-dispatch-per-worker"


def test_A_CRITERION_WE_NEVER_WROTE_RESOLVES_TO_NOTHING() -> None:
    """**`None` is a real answer.** A criterion we never asked about is hallucinated or renamed past recognition;
    scoring it as some rule would leave that rule looking clean forever."""
    assert rule_named("the-answer-was-beautifully-written", RULES) is None
    assert rule_named("worker-dispatched-once-not-per-enquiry", RULES) is None, (
        "a grader's paraphrase of a rule id must not fuzzily match, or the resolver is guessing"
    )


def test_A_TIER_WITH_NO_RULES_GETS_AN_EMPTY_RUBRIC_NOT_A_CRASH() -> None:
    assert rubric_for("orchestrator") == ""
    assert rubric_for("chat-manager", RULES) == ""


def test_THE_SHAPES_THESE_DETECTORS_READ_ARE_THE_SHAPES_THE_RECORDER_WRITES() -> None:
    """**The anti-vacuity guard that matters most here.** If the hand-built recordings above drift from what
    `RecordTheContext` produces, the detectors pass on a shape that never occurs and fail silently on the one that
    does. This drives the real recorder and compares the keys."""
    from langchain.agents import create_agent
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool as make_tool

    from rag_wright.harness.recording import RecordTheContext
    from tests.harness.fabric import scripted

    @make_tool
    def read_enquiry(enquiry_id: str) -> str:
        """Read an enquiry."""
        return "{}"

    real: dict = {}
    agent = create_agent(scripted(AIMessage(content="", tool_calls=[{"name": "read_enquiry",
                                                                      "args": {"enquiry_id": "abc"}, "id": "c1",
                                                                      "type": "tool_call"}]),
                                  AIMessage(content="done")),
                         tools=[read_enquiry], system_prompt="You read enquiries.",
                         middleware=[RecordTheContext(real, "shape")])
    agent.invoke({"messages": [{"role": "user", "content": "read enquiry abc"}]})

    mine = _recording(calls=[("read_enquiry", {"enquiry_id": "abc"})])

    assert set(mine) <= set(real) | {"report"}, f"the hand-built recording has keys the recorder does not write: {set(mine) - set(real)}"
    assert real["dispatches"] == ["read enquiry abc"]
    said = real["turns"][0]["said"][0]
    assert set(said) >= {"role", "text", "tool_calls"}
    assert said["tool_calls"][0] == {"name": "read_enquiry", "args": {"enquiry_id": "abc"}}, (
        "a tool call is not recorded as {name, args}, so every detector reading arguments is reading nothing"
    )
    assert all({"n", "messages", "said"} <= set(turn) for turn in mine["turns"]), "the hand-built turns drifted"
    assert guessed_identifiers({"read_enquiry": "enquiry_id"})(real) is None, (
        "the brief supplied `abc`, yet the detector called it a guess on a REAL recording"
    )
