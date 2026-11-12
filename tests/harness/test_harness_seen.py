"""What the run has already asked, and whether remembering it is safe.

Ported from RuleWright under ADR-0139's handover (PS-48f); the substrate is `rag_wright.harness.seen`
(`RememberWhatWasCalled`, `WhatTheRunHasSeen`, `RunRecord`, `answered_nothing`, `run_key`, `OneAnswerPerTurn`), driven
with the fabric product's own declarations of which tools are repeatable and which argument carries the question.
RuleWright's tests of its own declarations (which of its tools are cacheable, which carry a question) stay in
RuleWright.

**A cache that serves a stale answer is worse than the repetition it replaces**, so these press on the boundary rather
than the happy path: which tools are eligible, what counts as empty, and whether one agent's record can ever reach
another. Driven through a REAL agent wherever the answer depends on the middleware chain.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AIMessage
from langchain_core.tools import tool as make_tool

from rag_wright.harness.seen import RememberWhatWasCalled, RunRecord, WhatTheRunHasSeen, answered_nothing
from tests.harness.fabric import scripted

#: **The fabric product's declarations.** Reading a swatch and matching compositions answer the same within a run;
#: a mill's order status changes while the run watches it, so it is not repeatable.
REPEATABLE = frozenset({"read_swatch", "match_compositions", "list_mills"})
#: The argument that carries the question, for the tools whose question may be phrased differently.
QUESTION = {"read_swatch": "question", "match_compositions": "requirements"}


def _seen() -> WhatTheRunHasSeen:
    return WhatTheRunHasSeen(repeatable=REPEATABLE, question_argument=QUESTION)


def _call(name: str, args: dict, ident: str) -> dict:
    return {"name": name, "args": args, "id": ident, "type": "tool_call"}


def _run(tool: Any, calls: list[dict], seen: WhatTheRunHasSeen, *, encoder: Any = None,
         carry_forward: Any = None) -> Any:
    replies = [AIMessage(content="", tool_calls=[c]) for c in calls] + [AIMessage(content="done")]
    agent = create_agent(scripted(*replies), tools=[tool], system_prompt="s",
                         middleware=[RememberWhatWasCalled(seen, encoder=encoder, carry_forward=carry_forward)])
    return agent.invoke({"messages": [{"role": "user", "content": "go"}]})


def _answers(state: Any, tool: str) -> list[Any]:
    return [m for m in state["messages"] if getattr(m, "name", "") == tool]


# --- the saving -----------------------------------------------------------------------------------


def test_AN_IDENTICAL_CALL_NEVER_REACHES_THE_TOOL() -> None:
    """**25 of 194 recorded tool calls were byte-identical repeats**, each a real retrieval for an answer already in
    the transcript. The point is that the operation does not run -- and the repeat still carries the answer, for a
    model whose earlier result was summarised away."""
    ran: list[str] = []

    @make_tool
    def read_swatch(swatch: str, question: str) -> dict:
        """Read one swatch."""
        ran.append(swatch)
        return {"found": True, "answer": "120 gsm"}

    state = _run(read_swatch, [_call("read_swatch", {"swatch": "s1", "question": "weight?"}, "a"),
                               _call("read_swatch", {"swatch": "s1", "question": "weight?"}, "b")], _seen())

    assert ran == ["s1"], f"the tool ran {len(ran)} times for one distinct question"
    said = _answers(state, "read_swatch")
    assert "already made this exact call" in str(said[-1].content)
    assert "120 gsm" in str(said[-1].content), "the repeat carries no answer"


def test_A_DIFFERENT_QUESTION_ABOUT_THE_SAME_SUBJECT_STILL_RUNS() -> None:
    """**The failure that would matter most.** Reading one swatch twice for two different questions is normal work."""
    ran: list[str] = []

    @make_tool
    def read_swatch(swatch: str, question: str) -> dict:
        """Read one swatch."""
        ran.append(question)
        return {"found": True, "answer": "..."}

    _run(read_swatch, [_call("read_swatch", {"swatch": "s1", "question": "weight?"}, "a"),
                       _call("read_swatch", {"swatch": "s1", "question": "shrinkage?"}, "b")], _seen())

    assert ran == ["weight?", "shrinkage?"]


def test_A_TOOL_WHOSE_ANSWER_CHANGES_IS_NEVER_SERVED_FROM_MEMORY() -> None:
    """**Opt-in, and this is why.** An order's status changes while the run watches it; answering the second look
    from the first would report a shipped order as pending, forever."""
    ran: list[int] = []

    @make_tool
    def read_order() -> dict:
        """How far an order has got."""
        ran.append(1)
        return {"found": True, "status": "pending"}

    _run(read_order, [_call("read_order", {}, "a"), _call("read_order", {}, "b")], _seen())

    assert len(ran) == 2, "an order status was served from a cache"
    assert "read_order" not in REPEATABLE


# --- what counts as nothing -----------------------------------------------------------------------


@pytest.mark.parametrize("result", [
    None, "", [], {},
    {"resolved": False, "mills": []},
    {"found": False, "detail": "No such swatch in this collection."},
    {"matched_count": 0, "possible_count": 0},
    {"swatch_count": 0},
    {"swatches": []},
    {"citations": []},
])
def test_THESE_ARE_ALL_WAYS_OF_SAYING_NOTHING(result) -> None:
    """**Declared once.** Each product tool says "nothing" in its own shape, and a second opinion about emptiness
    scattered across callers is how two of them come to disagree."""
    assert answered_nothing(result) is True


@pytest.mark.parametrize("result", [
    {"resolved": True, "mills": [{"mill": "mill-a"}]},
    {"found": True, "swatch": {"defects": []}},
    {"matched_count": 0, "possible_count": 5},
    {"swatch_count": 3, "swatches": []},
    {"swatches": [{"swatch": "s1"}]},
    "the fabric weighs 120 gsm",
])
def test_THESE_ARE_ANSWERS_AND_MUST_NOT_READ_AS_NOTHING(result) -> None:
    """**A match with 0 matched and 5 possible FOUND something**; calling it empty would make every such search look
    like a dead end."""
    assert answered_nothing(result) is False


def test_A_PRODUCTS_OWN_COUNTS_DECIDE_EMPTINESS_NOT_ONE_PRODUCTS_FIELD_NAMES() -> None:
    """**Found by this port** (PS-48f). Emptiness by count recognised exactly one product's fields,
    `matched_count`/`possible_count`; a fabric result `{"swatch_count": 0}` read as an answer, so an empty search was
    never noticed as one. A result carrying counts is empty when every count is zero, whatever the counts are named."""
    assert answered_nothing({"swatch_count": 0, "query": "outerwear"}) is True
    assert answered_nothing({"swatch_count": 0, "mill_count": 2}) is False
    assert answered_nothing({"matched_count": 0, "possible_count": 0}) is True, "the original shape still reads empty"


def test_THE_LAST_EMPTY_ANSWER_IS_FINDABLE() -> None:
    """The cause a gate could not see before: a filesystem search came straight after a lookup reported nothing."""
    here = RunRecord(repeatable=REPEATABLE, question_argument=QUESTION)
    here.remember("match_compositions", {"requirements": "outerwear"}, {"matched_count": 1, "possible_count": 0})
    here.remember("list_mills", {"region": "eu"}, {"resolved": False, "mills": []})

    empty = here.last_answered_nothing()

    assert empty is not None and empty.tool == "list_mills"
    assert here.by_tool("match_compositions")[0].result["matched_count"] == 1


def test_A_RUN_THAT_FOUND_EVERYTHING_HAS_NO_LAST_EMPTY() -> None:
    here = RunRecord(repeatable=REPEATABLE, question_argument=QUESTION)
    here.remember("match_compositions", {"requirements": "outerwear"}, {"matched_count": 1, "possible_count": 0})

    assert here.last_answered_nothing() is None


# --- whose record it is ---------------------------------------------------------------------------


def test_ONE_AGENTS_RECORD_CANNOT_REACH_ANOTHER() -> None:
    """**Scoped to one agent.** A shared record would serve one worker's answer to another -- a cross-run claim, worse
    than the repetition it prevents."""
    ran: list[str] = []

    @make_tool
    def list_mills(region: str) -> dict:
        """The mills in a region."""
        ran.append(region)
        return {"mills": ["mill-a"]}

    _run(list_mills, [_call("list_mills", {"region": "eu"}, "a")], _seen())
    _run(list_mills, [_call("list_mills", {"region": "eu"}, "b")], _seen())

    assert ran == ["eu", "eu"], "a second agent was served the first agent's answer"


def test_THE_KEY_IS_THE_RUN_AND_SURVIVES_A_RE_DISPATCH() -> None:
    """**Keying by DISPATCH was measurably too narrow**: a re-dispatched worker asked the same thing in new words and
    nothing matched, because the halves sat either side of a dispatch boundary. One run is one record, and no run can
    reach another, or another scope."""
    from types import SimpleNamespace as NS

    from rag_wright.harness.seen import run_key

    def asked(**configurable: str) -> str:
        return run_key(NS(runtime=NS(config={"configurable": configurable})))

    first = asked(thread_id="acme:ss26:resort:run-7", checkpoint_ns="tools:PARENT-A|tools:call-1")
    second = asked(thread_id="acme:ss26:resort:run-7", checkpoint_ns="tools:PARENT-B|tools:call-9")
    assert first == second == "acme:ss26:resort:run-7", "a re-dispatch started a fresh record"

    assert asked(thread_id="acme:ss26:resort:run-8") != first
    assert asked(thread_id="acme:ss26:outerwear:run-7") != first


def test_WITHOUT_A_THREAD_THE_NAMESPACE_PREFIX_IS_USED() -> None:
    """**And its obvious reading is wrong.** `checkpoint_ns`'s LAST segment is the individual tool call, so keyed on
    the raw value nothing would ever match."""
    from types import SimpleNamespace as NS

    from rag_wright.harness.seen import run_key

    def asked(namespace: str) -> str:
        return run_key(NS(runtime=NS(config={"configurable": {"checkpoint_ns": namespace}})))

    assert asked("tools:PARENT-A|tools:call-1") == asked("tools:PARENT-A|tools:call-2")
    assert asked("tools:PARENT-A|tools:call-1") != asked("tools:PARENT-B|tools:call-3")
    assert asked("tools:call-1") == asked("tools:call-2") == ""


def test_A_MISSING_NAMESPACE_FALLS_BACK_RATHER_THAN_CRASHING() -> None:
    """Bookkeeping degrades to silence; it never fails the run it is measuring."""
    from types import SimpleNamespace as NS

    from rag_wright.harness.seen import run_key

    assert run_key(NS(runtime=None)) == ""
    assert run_key(NS(runtime=NS(config=None))) == ""
    assert run_key(NS(runtime=NS(config={"configurable": {"thread_id": "t9"}}))) == "t9"


def test_ONE_RUN_IS_ONE_KEY_AT_BOTH_SEAMS() -> None:
    """**`runtime.config` exists on a `ToolCallRequest` and NOT on a `ModelRequest`**, so every model-seam caller once
    got `""` -- one record for every run in the process, reported as an answer. This asks a real agent, at both
    seams, and asserts they agree."""
    from rag_wright.harness.seen import run_key

    keys: dict[str, str] = {}

    class Watch(AgentMiddleware):
        def wrap_model_call(self, request, handler):
            keys["model"] = run_key(request)
            return handler(request)

        def wrap_tool_call(self, request, handler):
            keys["tool"] = run_key(request)
            return handler(request)

    @make_tool
    def ping() -> dict:
        """Anything at all."""
        return {"found": True}

    agent = create_agent(scripted(AIMessage(content="", tool_calls=[_call("ping", {}, "a")]), AIMessage(content="done")),
                         tools=[ping], system_prompt="s", middleware=[Watch()])
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config={"configurable": {"thread_id": "w1:run-7"}})

    assert keys["tool"] == "w1:run-7"
    assert keys["model"] == "w1:run-7", f"the model seam keyed this run as {keys['model']!r}"


# --- a product's chance to amend a result in the light of the run -------------------------------------


def _matches_stay_matched(record: RunRecord, tool: str, content: Any) -> Any:
    """The fabric's carry-forward: a later, broader match that found nothing does not unfind an earlier match."""
    if tool != "match_compositions" or not isinstance(content, dict) or content.get("matched"):
        return None
    earlier = [s for call in record.by_tool(tool) for s in (call.result or {}).get("matched", [])]
    if not earlier:
        return None
    return {**content, "result": f"{content['result']} Earlier in this run {earlier} matched; a later match does "
                                 "not unmatch them."}


def _matcher(*results: dict) -> Any:
    replies = iter(results)

    @make_tool
    def match_compositions(requirements: str) -> dict:
        """Match a requirement set."""
        return next(replies)

    return match_compositions


def test_A_PRODUCTS_CARRY_FORWARD_AMENDS_A_LATER_RESULT_FROM_THE_RECORD() -> None:
    """**Live, this cost a whole extra dispatch**: a worker matched a document, swept again more broadly, got nothing,
    and reported nothing found. The hook is handed the run's record, so the product can say what was already found;
    its amendment replaces the result, and the result's own finding is kept."""
    tool = _matcher({"matched": ["cotton-poplin-120gsm"], "result": "1 matched"}, {"matched": [], "result": "0 matched"})

    state = _run(tool, [_call("match_compositions", {"requirements": "120 gsm cotton"}, "a"),
                        _call("match_compositions", {"requirements": "any outerwear"}, "b")],
                 _seen(), carry_forward=_matches_stay_matched)

    said = json.loads(_answers(state, "match_compositions")[-1].content)["result"]
    assert "cotton-poplin-120gsm" in said, f"the second match unfinds the first: {said!r}"
    assert "does not unmatch them" in said
    assert said.startswith("0 matched"), "the second result was replaced, not amended"


def test_A_RESULT_THE_HOOK_DECLINES_IS_LEFT_ALONE() -> None:
    """**Nothing is appended when there is nothing to carry**; a note on every call is noise, and noise is what stops
    a signpost being read."""
    tool = _matcher({"matched": ["cotton-poplin-120gsm"], "result": "1 matched"},
                    {"matched": ["linen-slub-180gsm"], "result": "1 matched"})

    state = _run(tool, [_call("match_compositions", {"requirements": "one"}, "a"),
                        _call("match_compositions", {"requirements": "two"}, "b")],
                 _seen(), carry_forward=_matches_stay_matched)

    assert "does not unmatch" not in json.loads(_answers(state, "match_compositions")[-1].content)["result"]


# --- the same question, asked in different words ------------------------------------------------------


class Alike:
    """A stand-in encoder with a stated notion of sameness, so the test asserts the WIRING; the threshold was measured
    against the real encoder on questions from a live run, which belongs in the constant's docstring."""

    def __init__(self, groups: list[set[str]]) -> None:
        self._groups = groups

    def encode_dense(self, text: str) -> list[float]:
        return [1.0 if text in group else 0.0 for group in self._groups] or [0.0]


def _reader(answers: list[str]) -> Any:
    seen_questions: list[str] = []

    @make_tool
    def read_swatch(swatch: str, question: str) -> dict:
        """Read one swatch."""
        seen_questions.append(question)
        return {"found": True, "answer": answers[len(seen_questions) - 1]}

    return read_swatch, seen_questions


def _twice(tool: Any, one: str, two: str, *, encoder: Any, swatches: tuple[str, str] = ("s1", "s1")) -> Any:
    return _run(tool, [_call("read_swatch", {"swatch": swatches[0], "question": one}, "a"),
                       _call("read_swatch", {"swatch": swatches[1], "question": two}, "b")], _seen(), encoder=encoder)


def test_THE_SAME_QUESTION_IN_DIFFERENT_WORDS_IS_NOT_ASKED_TWICE() -> None:
    """**Three reads of one document, phrased three ways, in a single live dispatch**, each a real retrieval and a real
    model call."""
    tool, asked = _reader(["it weighs 120 gsm", "SHOULD NOT BE REACHED"])
    one, two = "What does this swatch weigh?", "Give the weight of the swatch."

    state = _twice(tool, one, two, encoder=Alike([{one, two}]))

    assert asked == [one], f"the swatch was read twice for one question: {asked}"
    last = _answers(state, "read_swatch")[-1]
    assert "asked this in different words" in str(last.content)
    assert one in str(last.content), "the earlier QUESTION is not shown, so the model cannot tell if it fits"
    assert "it weighs 120 gsm" in str(last.content)


def test_A_GENUINELY_DIFFERENT_QUESTION_IS_STILL_ASKED() -> None:
    """**The failure that would matter more than the saving**: serving the weight to a question about shrinkage is a
    wrong answer, and a repeated call is only a slow one."""
    tool, asked = _reader(["it weighs 120 gsm", "3% shrinkage"])
    one, two = "What does this swatch weigh?", "How much does it shrink?"

    _twice(tool, one, two, encoder=Alike([{one}, {two}]))

    assert asked == [one, two]


def test_THE_SAME_QUESTION_ABOUT_A_DIFFERENT_SUBJECT_IS_STILL_ASKED() -> None:
    """**Subject first, similarity second.** Every argument but the question must match, or one swatch's answer would
    be served for another -- the worst outcome available here."""
    tool, asked = _reader(["s1 weighs 120 gsm", "s2 weighs 180 gsm"])
    question = "What does this swatch weigh?"

    _twice(tool, question, question, encoder=Alike([{question}]), swatches=("s1", "s2"))

    assert asked == [question, question], "a second swatch was answered from the first's read"


def test_WITHOUT_AN_ENCODER_EVERY_CALL_STILL_RUNS() -> None:
    """**Similarity is an optimisation, never a dependency.**"""
    tool, asked = _reader(["a", "b"])
    one, two = "What does this swatch weigh?", "Give the weight of the swatch."

    _twice(tool, one, two, encoder=None)

    assert asked == [one, two]


def test_AN_ENCODER_THAT_RAISES_COSTS_A_CALL_AND_NOTHING_ELSE() -> None:
    """A failing optimisation degrades to the behaviour that existed before it."""
    class Broken:
        def encode_dense(self, text: str):
            raise RuntimeError("the encoder is down")

    tool, asked = _reader(["a", "b"])
    one, two = "Weight?", "What does it weigh?"

    _twice(tool, one, two, encoder=Broken())

    assert asked == [one, two]


def test_A_RELATED_QUESTION_IS_TOLD_WHAT_HAS_ALREADY_BEEN_ESTABLISHED() -> None:
    """**Between "the same question" and "unrelated" is where a spiral lives.** Substituting an answer in that band
    would risk answering the wrong question, so the call runs, and its result says what has already been asked."""
    class SomewhatAlike:
        def __init__(self, pair: tuple[str, str]) -> None:
            self._pair = pair

        def encode_dense(self, text: str) -> list[float]:
            if text == self._pair[0]:
                return [1.0, 0.0]
            if text == self._pair[1]:
                return [0.7, 0.714]  # ~0.7 cosine with the first: mention, do not substitute
            return [0.0, 1.0]

    tool, asked = _reader(["it weighs 120 gsm", "the weave is plain"])
    one, two = "Is this a heavy fabric?", "Describe the weave."

    state = _twice(tool, one, two, encoder=SomewhatAlike((one, two)))

    assert asked == [one, two], "a related but different question was substituted rather than asked"
    last = json.loads(_answers(state, "read_swatch")[-1].content)
    assert "already_asked_here" in last, "the second read says nothing about the first"
    assert one in last["already_asked_here"]
    assert "it weighs 120 gsm" in last["already_asked_here"]
    assert last["answer"] == "the weave is plain", "its own answer was replaced, not amended"


def test_AN_UNRELATED_QUESTION_IS_NOT_CLUTTERED() -> None:
    """**A note on every call is noise, and noise is what stops a signpost being read.**"""
    tool, asked = _reader(["it weighs 120 gsm", "3% shrinkage"])
    one, two = "What does it weigh?", "How much does it shrink?"

    state = _twice(tool, one, two, encoder=Alike([{one}, {two}]))

    assert asked == [one, two]
    assert "already_asked_here" not in json.loads(_answers(state, "read_swatch")[-1].content)


async def test_ENCODING_DOES_NOT_BLOCK_THE_EVENT_LOOP() -> None:
    """**A local encode measures 0.24-1.04 s**, and run inline it blocks every other coroutine in the process --
    including the model calls it exists to save. The async path warms the vector in a worker thread first."""
    import asyncio
    import time

    ticks: list[int] = []

    class Slow:
        def encode_dense(self, text: str):
            time.sleep(0.25)
            return [1.0, 0.0]

    async def heartbeat() -> None:
        for i in range(12):
            await asyncio.sleep(0.02)
            ticks.append(i)

    middleware = RememberWhatWasCalled(_seen(), encoder=Slow())
    request = type("Request", (), {"tool_call": {"name": "read_swatch", "id": "a",
                                                 "args": {"swatch": "s1", "question": "what does it weigh?"}},
                                   "runtime": None})()

    beating = asyncio.create_task(heartbeat())
    await middleware._warm(request)
    await beating

    assert len(ticks) == 12, "the loop was starved while the encoder ran"
    assert "what does it weigh?" in middleware._vectors, "the warm-up did not cache anything"


# --- a model that repeats itself in one breath ------------------------------------------------------------


def test_A_RESPONSE_THAT_REPEATS_ONE_CALL_IS_COLLAPSED() -> None:
    """**Live, one turn emitted the structured response 726 times**, each rejected separately into the context: 25,393
    output tokens in one turn. Identity is `(name, args)`, so this is repetition rather than breadth."""
    from rag_wright.harness.seen import OneAnswerPerTurn

    said = AIMessage(content="", tool_calls=[{"name": "FabricFindings", "args": {"status": "done"}, "id": f"c{i}",
                                              "type": "tool_call"} for i in range(726)])

    kept = OneAnswerPerTurn()._settled(said)

    assert len(kept.tool_calls) == 1
    assert kept.tool_calls[0]["args"] == {"status": "done"}


def test_PARALLEL_CALLS_TO_ONE_TOOL_ARE_NOT_REPETITION() -> None:
    """**The failure that would matter more than the saving.** Reading five swatches in one turn is wanted behaviour --
    same tool, different arguments. Collapsing on NAME would destroy it."""
    from rag_wright.harness.seen import OneAnswerPerTurn

    said = AIMessage(content="", tool_calls=[{"name": "read_swatch", "args": {"swatch": s, "question": "weight?"},
                                              "id": s, "type": "tool_call"} for s in ("s1", "s2", "s3", "s4", "s5")])

    assert len(OneAnswerPerTurn()._settled(said).tool_calls) == 5


def test_A_HEALTHY_RESPONSE_IS_RETURNED_UNTOUCHED() -> None:
    """A guard that rebuilds every message is a guard that can corrupt one."""
    from rag_wright.harness.seen import OneAnswerPerTurn

    said = AIMessage(content="here you go", tool_calls=[{"name": "match_compositions",
                                                         "args": {"requirements": "outerwear"}, "id": "a",
                                                         "type": "tool_call"}])

    assert OneAnswerPerTurn()._settled(said) is said


def test_THE_COLLAPSE_HAPPENS_BEFORE_THE_FRAMEWORK_SEES_IT() -> None:
    """**End to end**, because the value is entirely in where it sits: the framework emits one error per call, so a
    response caught after it has already cost the context."""
    from rag_wright.harness.seen import OneAnswerPerTurn

    ran: list[str] = []

    @make_tool
    def ask_a_mill(question: str) -> str:
        """Ask the mill feed."""
        ran.append(question)
        return "an answer"

    repeated = AIMessage(content="", tool_calls=[{"name": "ask_a_mill", "args": {"question": "q"}, "id": f"c{i}",
                                                  "type": "tool_call"} for i in range(50)])
    agent = create_agent(scripted(repeated, AIMessage(content="done")), tools=[ask_a_mill], system_prompt="s",
                         middleware=[OneAnswerPerTurn()])
    state = agent.invoke({"messages": [{"role": "user", "content": "go"}]})

    assert ran == ["q"], f"the tool ran {len(ran)} times for one repeated call"
    assert len(_answers(state, "ask_a_mill")) == 1, "more than one tool message entered the context"
