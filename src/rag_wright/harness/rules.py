"""The warnings we write into prompts, as things that can be CHECKED (T-5a.12f, AR-14, ANFR-6).

**A warning nobody checks is a warning nobody follows, and this phase has the evidence twice.** The
instruction to call `read_the_question` first sat in both tool descriptions, the system prompt and the
dispatch — four places — and across four runs the model obeyed it **zero** times; removing the parameter
fixed what a fifth instruction would not (T-5a.12d). Then `CODED_PROMPT_SUFFIX` said in bold that a worker
covering the whole matter *"must be dispatched ONCE, not once per document"*, and a run fanned
`clause_worker` out per document anyway: 40 model calls and **230,002 input tokens, 10.6x its isolated
cost** (T-5a.12e).

**One bad run in five proves nothing about which arm is more prone to it.** The same misreading is
available to the prompted arm; it simply did not happen in a sample of five. So what this module produces
is a **frequency**, measured the same way in both arms, rather than an anecdote.

**One source, two consumers** (ADR-0019 §5). A `Rule` carries the sentence the MODEL reads and the
criterion a GRADER checks, and both are rendered from it. A warning therefore cannot exist in a prompt
without something that checks it — `test_harness_rules.py` fails if a rule's instruction is missing from
the prompt it applies to, which is the drift this codebase has watched go stale six times in fixtures
alone.

**Most of these need no judge at all, and that is the point.** our grading middleware hands the grader `tools` so the
grader can gather evidence rather than reason abstractly — the docs' own example hands it a test runner
instead of asking it to imagine whether the tests pass. Five of the six rules below are decided by counting
something in the recording; only `the-brief-is-faithful` requires reading. A grader asked *"did it fan out
per document?"* can be wrong. One that calls a function which counts cannot.

**Detection comes before enforcement, deliberately.** A `needs_revision` verdict costs a grader call **plus
a full re-run of the agent**, which would change what every arm measurement means. These detectors run over
recordings and change nothing; turning the revision loop on is a later, separate decision.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

#: Which tier a rule binds. A rule is checked only against a recording from a tier it applies to.
Tier = Literal["orchestrator", "worker", "coded"]


@dataclass(frozen=True)
class Rule:
    """One thing we tell a model not to do, and the way we find out whether it did.

    `instruction` is rendered into the prompt; `criterion` is rendered into the rubric a grader is given.
    They are deliberately different sentences for different readers -- the model is told what to do, the
    grader is asked what happened -- and keeping both on one object is what stops them drifting apart.
    """

    id: str
    applies_to: tuple[Tier, ...]
    #: The sentence the MODEL reads. Lifted verbatim from the prompt it already lived in, so adopting this
    #: registry cannot change behaviour: the words in front of the model are the same words.
    instruction: str
    #: What the GRADER is asked, phrased as a property of the run rather than as an order.
    criterion: str
    #: `None` means no deterministic detector exists and the grader must reason. Keep this rare.
    detect: Callable[[dict[str, Any]], str | None] | None = None
    #: WHERE the instruction is actually delivered to the model. Resolved and checked by
    #: `test_harness_rules.py`, so a rule cannot claim to be told to anybody without being told to them.
    #:
    #: **Not always a prompt, and that is deliberate.** `the-matter-is-not-a-filesystem` is delivered as a
    #: SIGNPOST returned on the wrong call, because T-5a.11e decided prompt text was the weaker mechanism
    #: there. A registry that assumed "instruction == prompt line" would have quietly misrepresented that
    #: decision.
    carried_by: tuple[str, ...] = ()
    #: What was actually observed, so a rule can never be added on a hunch.
    observed: str = ""
    evidence: tuple[str, ...] = field(default_factory=tuple)

    @property
    def structural(self) -> bool:
        return self.detect is not None


# --- the detectors ------------------------------------------------------------------------------------
#
# Each takes ONE agent's recording (the shape `evals.recording.RecordTheContext` writes) and returns the
# gap -- what a grader would be told -- or `None` when the rule held.


def _dispatches(recording: dict[str, Any]) -> list[str]:
    """Every brief this agent was started with.

    **Counted from the agent's own opening turns, not from the parent's tool calls**, because
    `langchain_quickjs` states that a sandbox `task()` *"does not route through the parent agent's
    `ToolNode`-managed `task` tool"* -- so a transcript-based count is blind in the coded arm, which is
    exactly the arm where the fan-out happened.
    """
    recorded = recording.get("dispatches")
    if recorded is not None:
        return list(recorded)
    # **Derived when the key is absent**, so a detector can be run against recordings taken before it
    # existed -- including the one from the 10.6x fan-out, which is the only real evidence we have of the
    # failure this rule exists to catch. A dispatch opens with exactly one human message.
    return [
        turn["messages"][0].get("text", "")
        for turn in (recording.get("turns") or [])
        if len(turn.get("messages") or []) == 1 and turn["messages"][0].get("role") == "human"
    ]


def _calls(recording: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"turn": turn["n"], "tool": call["name"], "args": call.get("args") or {}}
        for turn in (recording.get("turns") or [])
        for message in (turn.get("said") or [])
        for call in (message.get("tool_calls") or [])
    ]


def dispatched_more_than_once(recording: dict[str, Any]) -> str | None:
    """A worker covering the whole matter, run once per document."""
    briefs = _dispatches(recording)
    if len(briefs) <= 1:
        return None
    return (f"this worker was dispatched {len(briefs)} times in one run; each dispatch repeats its whole "
            f"job. The briefs began: {[b[:60] for b in briefs[:4]]}")


#: The filesystem tools. The matter's documents are not files, and every one of these on a matter path is
#: a wrong turn that `TheSubjectIsNotAFilesystem` catches -- catches, but never counted.
FILE_TOOLS = ("ls", "glob", "grep", "read_file", "write_file", "edit_file")

#: The roots that really ARE the agent's own filesystem, mirrored from `harness.filesystem`.
#: The agent's OWN paths. A filesystem call against one of these is the agent using its own notes, its
#: own skills or its own spill-over -- not treating the matter as a filesystem, which is what this rule
#: is about. **`/skills` joined the list when the orchestrator's skill was built (T-5a.12ab):** the
#: criterion is "no filesystem tool against the matter's DOCUMENTS", and a skill we authored is not a
#: matter document, so reading one must not read as a violation. Without it the skill would have been
#: unreadable in practice -- every read scoring against the tier that read it.
OWN_FILESYSTEM = ("/memories", "/large_tool_results", "/skills")


def reached_for_the_filesystem(recording: dict[str, Any]) -> str | None:
    """Tried to open the matter's documents as files."""
    def on_a_subject_path(args: dict[str, Any]) -> bool:
        paths = [str(v) for k, v in args.items() if "path" in k or k == "pattern"]
        return bool(paths) and not any(p.startswith(OWN_FILESYSTEM) for p in paths)

    wrong = [c for c in _calls(recording) if c["tool"] in FILE_TOOLS and on_a_subject_path(c["args"])]
    if not wrong:
        return None
    return (f"{len(wrong)} filesystem call(s) against the matter rather than this agent's own notes: "
            f"{[(c['tool'], c['args']) for c in wrong[:3]]}")


def wrote_a_program_for_a_single_call(recording: dict[str, Any]) -> str | None:
    """An `eval` whose program did the work of one tool call."""
    programs = [c for c in _calls(recording) if c["tool"] == "eval"]
    if not programs:
        return None
    trivial = [p for p in programs
               if str(p["args"].get("code", "")).count("await ") <= 1
               and "task(" not in str(p["args"].get("code", ""))]
    if not trivial:
        return None
    return (f"{len(trivial)} program(s) wrapped a single call and dispatched nobody; the tool could have "
            "been called directly for less")


def guessed_an_identifier(recording: dict[str, Any]) -> str | None:
    """Tried an id that was never supplied."""
    supplied = " ".join(_dispatches(recording)) + " " + str(recording.get("system_prompt", ""))
    invented = [
        c for c in _calls(recording)
        if c["tool"] == "read_compliance_check"
        and str(c["args"].get("check_id", "")) not in supplied
    ]
    if not invented:
        return None
    return (f"{len(invented)} check id(s) were used that nobody supplied: "
            f"{[c['args'].get('check_id') for c in invented[:3]]}")


def _report_of(recording: dict[str, Any]) -> dict[str, Any]:
    """The worker's structured report, wherever this recording carries it.

    **Two places, because grading happens at two moments.** A recording written to disk carries `report`,
    added after the run finished. A recording being graded IN the run has no such key yet -- but the
    structured response arrives as a tool call named after the schema (`ClauseFindings(...)`), which the
    recorder already captures. Reading both is what lets one detector serve the offline scorer and the
    live grader.
    """
    report = recording.get("report")
    if isinstance(report, dict):
        return report
    for call in reversed(_calls(recording)):
        if "status" in call["args"]:
            return call["args"]
    return {}


def blocked_without_saying_what_it_needs(recording: dict[str, Any]) -> str | None:
    """Reported `blocked` with an empty `need`, which tells the caller nothing."""
    report = _report_of(recording)
    if report.get("status") != "blocked":
        return None
    if str(report.get("need") or "").strip():
        return None
    return "returned status `blocked` with an empty `need`, so nobody can know what would unblock it"


# --- the rules ----------------------------------------------------------------------------------------
#
# **Every one is taken from an observed failure.** A rule invented from imagination is a rule that will
# fire on correct behaviour, and this project has already spent a day on fixtures built that way.



def rules_for(tier: Tier, rules: tuple[Rule, ...] = ()) -> tuple[Rule, ...]:
    """The rules that apply to one tier. **`rules` is the PRODUCT's registry** (T-7.58): this read a
    module-level `RULES` naming `clause_worker` and this product's dispatch discipline, so a second
    product's grader was handed our rubric. An empty registry grades nothing, which is honest.
    """
    return tuple(rule for rule in rules if tier in rule.applies_to)


def rubric_for(tier: Tier, rules: tuple[Rule, ...] = ()) -> str:
    """The rubric string passed on invocation state, in the bullet form the grader expects.

    **Each criterion leads with its rule id, and that is not decoration.** A grader names criteria itself:
    left to invent one it returned `worker-dispatched-once-not-per-document` for
    `one-dispatch-per-worker`, which reads fine and cannot be joined to anything. Per-criterion verdicts
    are meant to become scores in Langfuse, and a score whose key changes with the grader's phrasing
    measures nothing over time.
    """
    return "\n".join(f"- [{rule.id}] {rule.criterion}" for rule in rules_for(tier, rules))


def rule_named(criterion: str, rules: tuple[Rule, ...] = ()) -> Rule | None:
    """The rule a grader's criterion name refers to, or `None` if it matches nothing we asked about.

    **`None` is a real answer and must not be discarded.** A grader that returns a criterion we never
    wrote is either hallucinating one or renaming ours past recognition; either way the verdict cannot be
    scored, and silently dropping it would leave a rule looking permanently clean.
    """
    handle = criterion.strip().strip("[]").lower()
    for rule in rules:
        if rule.id in handle:
            return rule
    return None


def broken(recording: dict[str, Any], *, tier: Tier,
           rules: tuple[Rule, ...] = ()) -> dict[str, str]:
    """Which rules this recording breaks, and the gap for each. `{}` means it held to all of them.

    **Reports rather than enforces.** This is the base-rate measurement that has to exist before any
    revision loop is turned on -- otherwise "the rubric helped" is unfalsifiable.
    """
    found: dict[str, str] = {}
    for rule in rules_for(tier, rules):
        if rule.detect is None:
            continue
        gap = rule.detect(recording)
        if gap:
            found[rule.id] = gap
    return found


def instructions_for(tier: Tier, rules: tuple[Rule, ...] = ()) -> Sequence[str]:
    """The sentences a tier is actually told, in the words the model reads them in."""
    return [rule.instruction for rule in rules_for(tier, rules)]

