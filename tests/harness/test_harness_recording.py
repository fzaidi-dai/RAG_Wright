"""What the recorder captures, and whether it could tell us anything.

Ported from RuleWright's eval tooling under ADR-0139's handover (PS-48e), with the recorder itself, which now lives in
the engine as `rag_wright.harness.recording`: the rule detectors and the grader read its shape, so writer and readers
belong together.

**The recorder is the instrument, so it is the thing most worth doubting.** Its first version captured the opening turn
in full and only sizes thereafter -- which read as working, and was why a worker that made 23 calls under an
orchestrator against 3 alone could not be explained. These drive a REAL agent with a scripted model, so the
middleware runs on the path a product uses rather than against a hand-built request.
"""

from __future__ import annotations

import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from rag_wright.harness.recording import RecordTheContext, compare, every_call, spend, transcript
from tests.harness.fabric import scripted


@tool
def ask_a_mill(question: str) -> str:
    """Ask the mill feed a question."""
    return "Nothing on the feed answers that."


def _run(*questions: str) -> dict:
    """An agent that asks the same tool once per scripted question, then answers."""
    sink: dict = {}
    calls = [AIMessage(content="", tool_calls=[{"name": "ask_a_mill", "args": {"question": q}, "id": f"c{i}",
                                                "type": "tool_call"}])
             for i, q in enumerate(questions)]
    agent = create_agent(scripted(*calls, AIMessage(content="No stock found.")), tools=[ask_a_mill],
                         system_prompt="You answer questions about one collection.",
                         middleware=[RecordTheContext(sink, "test")])
    agent.invoke({"messages": [{"role": "user", "content": "Who stocks cotton poplin?"}]})
    return sink


def test_EVERY_TOOL_CALL_IS_RECORDED_WITH_ITS_ARGUMENTS() -> None:
    """**The assertion the first recorder could not have made.** Three calls to one tool are only interpretable once
    you can see whether the three questions differed."""
    calls = every_call(_run("poplin", "cotton poplin", "poplin 120gsm"))

    assert [c["tool"] for c in calls] == ["ask_a_mill"] * 3
    assert [c["args"]["question"] for c in calls] == ["poplin", "cotton poplin", "poplin 120gsm"], (
        "the arguments are missing or wrong -- a count of calls cannot tell rephrasing from asking different things"
    )
    assert [c["turn"] for c in calls] == [1, 2, 3]


def test_EACH_TURN_RECORDS_ONLY_WHAT_ARRIVED_SINCE_THE_LAST_ONE() -> None:
    """**Otherwise turn 23 is twenty-three turns of history and the reader gives up.** The whole history is kept too;
    `new` is what makes it readable."""
    turns = _run("first", "second")["turns"]

    assert len(turns) == 3, "one turn per model call: two tool calls plus the final answer"
    assert [m["role"] for m in turns[0]["new"]] == ["human"]
    # The call is recorded on the turn it was MADE, and its result arrives on the next one.
    assert turns[0]["said"][0]["tool_calls"][0]["args"] == {"question": "first"}
    assert [m["role"] for m in turns[1]["new"]] == ["ai", "tool"]
    assert turns[1]["new"][1]["text"].startswith("Nothing on the feed")
    assert len(turns[2]["messages"]) > len(turns[1]["messages"]), "history must still accumulate"


def test_THE_OPENING_CONTEXT_IS_WHAT_THE_AGENT_WAS_HANDED() -> None:
    """The half an isolated-versus-orchestrated diff compares."""
    sink = _run("only")

    assert sink["system_prompt"].startswith("You answer questions about one collection")
    assert sink["tools_offered"] == ["ask_a_mill"]
    assert [m["role"] for m in sink["opening_messages"]] == ["human"]
    assert sink["opening_messages"][0]["text"] == "Who stocks cotton poplin?"


def test_THE_TRANSCRIPT_READS_AS_A_RUN() -> None:
    """It is read by a person at the moment something has gone wrong, so it has to be legible."""
    rendered = transcript(_run("poplin", "cotton poplin"))

    assert "--- turn 1" in rendered and "--- turn 3" in rendered
    assert "ai -> ask_a_mill({'question': 'poplin'})" in rendered
    assert "ai -> ask_a_mill({'question': 'cotton poplin'})" in rendered


def test_A_LONG_MESSAGE_IS_CLIPPED_AND_SAYS_SO() -> None:
    """A clipped message that does not admit it is a lie about what the model saw."""
    from langchain_core.messages import ToolMessage

    from rag_wright.harness.recording import KEEP_CHARS, _message

    recorded = _message(ToolMessage(content="x" * (KEEP_CHARS + 500), tool_call_id="c", name="match_compositions"))

    assert len(recorded["text"]) == KEEP_CHARS
    assert recorded["clipped_from"] == KEEP_CHARS + 500


def test_THE_RECORDER_CHANGES_NOTHING() -> None:
    """**A recorder that altered a prompt would be measuring itself.** Same agent, same script, with and without it."""
    script = [AIMessage(content="", tool_calls=[{"name": "ask_a_mill", "args": {"question": "poplin"}, "id": "c0",
                                                 "type": "tool_call"}]),
              AIMessage(content="No stock found.")]

    def answer(middleware: list) -> str:
        agent = create_agent(scripted(*script), tools=[ask_a_mill],
                             system_prompt="You answer questions about one collection.", middleware=middleware)
        return str(agent.invoke({"messages": [{"role": "user", "content": "q"}]})["messages"][-1].content)

    assert answer([]) == answer([RecordTheContext({}, "test")]) == "No stock found."


@pytest.mark.parametrize("questions", [("a",), ("a", "b", "c", "d")])
def test_THE_CALL_COUNT_MATCHES_WHAT_THE_AGENT_ACTUALLY_DID(questions: tuple[str, ...]) -> None:
    """**Anti-vacuity.** A recorder that always reported one call would pass every test above that used one call."""
    assert len(every_call(_run(*questions))) == len(questions)


# --- what it cost, on both sides of the comparison ----------------------------------------------------


def _sink_for(messages: list[AIMessage]) -> dict:
    sink: dict = {}
    agent = create_agent(scripted(*messages), tools=[ask_a_mill], system_prompt="You answer questions.",
                         middleware=[RecordTheContext(sink, "cost")])
    agent.invoke({"messages": [{"role": "user", "content": "q"}]})
    return sink


def test_TOKENS_ARE_RECORDED_PER_TURN_AND_TOTALLED() -> None:
    """**A tier run alone attaches no tracing**, so a token figure that lives only in a trace cannot be compared
    against the isolated half at all. It has to be local on both sides."""
    sink = _sink_for([AIMessage(content="first", usage_metadata={"input_tokens": 100, "output_tokens": 10,
                                                                  "total_tokens": 110})])

    assert [t["tokens"] for t in sink["turns"]] == [{"input": 100, "output": 10}]
    totals = spend(sink)
    assert totals["model_calls"] == 1
    assert totals["input_tokens"] == 100
    assert totals["output_tokens"] == 10


def test_A_MODEL_THAT_REPORTS_NO_USAGE_COUNTS_AS_ZERO_NOT_AS_A_CRASH() -> None:
    """Telemetry degrades to silence; it never fails the run it is measuring."""
    assert spend(_sink_for([AIMessage(content="done")]))["input_tokens"] == 0


def test_THE_COMPARISON_REPORTS_RATIOS_NOT_ONLY_TOTALS() -> None:
    """"23 calls" only means something beside the 3 the same tier took alone."""
    alone = {"turns": [{"context_chars": 100, "tokens": {"input": 100, "output": 10}, "said": []}]}
    under = {"turns": [{"context_chars": 400, "tokens": {"input": 400, "output": 20}, "said": []},
                       {"context_chars": 500, "tokens": {"input": 500, "output": 20}, "said": []}]}

    rendered = compare(alone, under)

    assert "model_calls" in rendered and "2.00x" in rendered
    assert "9.00x" in rendered, "input tokens went 100 -> 900 and the ratio must say so"


def test_THE_COMPARISON_SURVIVES_AN_EMPTY_SIDE() -> None:
    """A tier that was never woken must not blow up the report that says so."""
    assert "--" in compare({"turns": []}, {"turns": []})


# --- counting dispatches in an arm that emits no tool calls -------------------------------------------


def test_EVERY_DISPATCH_IS_COUNTED_NOT_ONLY_THE_FIRST() -> None:
    """**A coded arm has no `task` tool calls to count**: they happen inside the sandbox. A dispatch starts the
    subagent afresh, so its opening turn is a single human message; that definition works in both arms."""
    sink: dict = {}
    recorder = RecordTheContext(sink, "dispatches")

    for brief in ("Read ONLY swatch 1", "Read ONLY swatch 2", "Read ONLY swatch 3"):
        agent = create_agent(scripted(AIMessage(content="done")), tools=[ask_a_mill],
                             system_prompt="You read one swatch.", middleware=[recorder])
        agent.invoke({"messages": [{"role": "user", "content": brief}]})

    assert sink["dispatches"] == ["Read ONLY swatch 1", "Read ONLY swatch 2", "Read ONLY swatch 3"], (
        "a per-swatch fan-out must be visible as three dispatches, or the score that exists to catch one cannot"
    )


def test_A_CONTINUING_CONVERSATION_IS_NOT_A_NEW_DISPATCH() -> None:
    """**The other direction, and what makes the count mean anything.** A tier's later turns reach the recorder too;
    if those counted, one dispatch would look like five."""
    sink = _run("first", "second")

    assert len(sink["turns"]) == 3
    assert sink.get("dispatches") == ["Who stocks cotton poplin?"], "later turns were counted as dispatches"
