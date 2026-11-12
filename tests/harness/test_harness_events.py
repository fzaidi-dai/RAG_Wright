"""What a run tells the client while it runs, and what a cut-off run tells it afterwards.

Ported from RuleWright under ADR-0139's handover (PS-48a); the substrate is `rag_wright.harness.events`.

**The contract is the harness's, and that is the decision these tests hold.** Forwarding langgraph's chunks
unchanged would make the surface depend on node names -- `PatchToolCallsMiddleware.before_agent` is real
output from the bare stack -- so every middleware change would become a front-end change. A small named set
of events is the seam instead.

**The tuple arity was verified, not assumed**: `astream(subgraphs=True, ...)` yields a 2-tuple with one
stream mode and a **3-tuple** with several. Reading the wrong one produces events that look plausible and
carry the wrong field, which is the worst kind of wrong for a stream nobody inspects byte by byte.
"""

from __future__ import annotations

from types import SimpleNamespace

from rag_wright.harness.events import MAIN, RunEvent, translate
from tests.harness.fabric import FabricContext


def a_message(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=text)


def test_a_token_chunk_becomes_a_token_event() -> None:
    [event] = translate((), "messages", (a_message("the blend is"), {}))

    assert event == RunEvent("token", {"text": "the blend is"}, MAIN)


def test_an_EMPTY_chunk_produces_nothing() -> None:
    """A tool-call chunk carries no text. Forwarding an empty token would make the surface render a
    flicker for every tool the model decides to call."""
    assert translate((), "messages", (a_message(""), {})) == []
    assert translate((), "messages", (SimpleNamespace(content=None), {})) == []


def test_a_NON_STRING_content_block_is_not_forwarded() -> None:
    """Content can be a list of blocks. Rendering that as text puts `[{'type': 'text'...}]` on screen."""
    assert translate((), "messages", (SimpleNamespace(content=[{"type": "text"}]), {})) == []


def test_a_completed_TOOL_becomes_a_tool_event() -> None:
    """A tool call is the unit a client draws a card for."""
    chunk = {"tools": {"messages": [SimpleNamespace(name="list_mills")]}}

    [event] = translate((), "updates", chunk)

    assert event == RunEvent("tool", {"tool": "list_mills"}, MAIN)


def test_MIDDLEWARE_bookkeeping_is_not_forwarded() -> None:
    """**The reason the contract exists.** These are real chunks from the bare DeepAgents stack; a client
    written against them breaks the first time the middleware order changes."""
    for node in ("PatchToolCallsMiddleware.before_agent", "TodoListMiddleware.after_model", "model"):
        assert translate((), "updates", {node: {"messages": []}}) == []


def test_a_SUBAGENT_event_carries_its_namespace() -> None:
    """**Two subagents interleave**, so the namespace has to travel with the event rather than be inferred
    from arrival order -- which is exactly what a client's cards are indexed by."""
    [event] = translate(("tools:call_abc",), "updates",
                        {"tools": {"messages": [SimpleNamespace(name="match_compositions")]}})

    assert event.namespace == "tools:call_abc"
    assert event.name == "tool"


def test_a_NESTED_namespace_is_joined_rather_than_flattened() -> None:
    """A subagent inside a subagent is a real shape once the tiers exist; collapsing it would make two
    different agents look like one."""
    [event] = translate(("tools:a", "tools:b"), "messages", (a_message("hi"), {}))

    assert event.namespace == "tools:a|tools:b"


def test_the_MAIN_agent_has_an_empty_namespace() -> None:
    """`()` is what langgraph sends for the top-level graph, and the client routes on the difference."""
    [event] = translate((), "messages", (a_message("hi"), {}))

    assert event.namespace == MAIN == ""


def test_an_UNKNOWN_stream_mode_produces_nothing() -> None:
    """A mode we did not ask for arriving is a framework change, not an event. Silence is the safe
    response; guessing at its shape is not."""
    assert translate((), "values", {"messages": []}) == []
    assert translate((), "debug", {"anything": 1}) == []


def test_NO_EVENT_CARRIES_TENANCY() -> None:
    """**Scope reaches the wire too.** An SSE frame is more exposed than a prompt: it crosses the network
    and lands in a browser's memory, where anything on the page can read it."""
    events = [
        *translate((), "messages", (a_message("hi"), {})),
        *translate(("tools:x",), "updates",
                   {"tools": {"messages": [SimpleNamespace(name="match_compositions")]}}),
    ]

    assert events
    for event in events:
        for key in ("scope", "shared_scope", "account", "season", "collection", "tenant"):
            assert key not in event.data


# --- what a cut-off run tells the user -----------------------------------------------------------------


def _context() -> FabricContext:
    return FabricContext(account="acme-mills", season="ss26", collection="resort")


def _result(outcome: object, messages: list, context: FabricContext | None = None) -> object:
    from rag_wright.harness.runs import RunResult

    return RunResult(run_id="r1", context=context or _context(), state={"messages": messages},
                     outcome=outcome)


def test_A_CUT_OFF_RUN_TELLS_THE_USER_WHAT_FINISHED() -> None:
    """**The half the person actually experiences.** A stream that stops is not a stream that finished --
    and on a failure it used to stop anyway: no partial, no explanation, not even the terminating frame a
    client waits for. So a cut-off run looked exactly like a dropped connection.

    The work is already there: the finished results sit in the checkpoint. This is what says so out loud.
    """
    from langchain_core.messages import AIMessage, ToolMessage

    from rag_wright.harness.events import what_was_finished
    from rag_wright.harness.runs import RunOutcome

    events = what_was_finished(_result(RunOutcome.PARTIAL, [
        AIMessage(content="", tool_calls=[{"name": "match_compositions", "args": {}, "id": "a",
                                           "type": "tool_call"}]),
        ToolMessage(content="cotton-poplin-120gsm, 3 of 3 mills", tool_call_id="a", name="match_compositions"),
    ]))

    assert events, "a cut-off run produced no events at all, so the client is told nothing"
    assert any(e.name == "incomplete" for e in events), [e.name for e in events]

    said = " ".join(str(e.data) for e in events)
    assert "cotton-poplin-120gsm" in said, f"the finished work is not in what the client is told: {said[:200]}"
    assert "match_compositions" in said, "the client cannot tell WHICH work finished"


def test_IT_SAYS_PLAINLY_THAT_SOMETHING_IS_MISSING() -> None:
    """**A partial answer presented as an answer is worse than a failure.** The frame has to be legible as
    incomplete without the client inferring it from an absence."""
    from langchain_core.messages import ToolMessage

    from rag_wright.harness.events import what_was_finished
    from rag_wright.harness.runs import RunOutcome

    events = what_was_finished(_result(RunOutcome.PARTIAL, [
        ToolMessage(content="cotton-poplin-120gsm", tool_call_id="a", name="match_compositions")]))
    data = next(e.data for e in events if e.name == "incomplete")

    assert data.get("outcome") == RunOutcome.PARTIAL
    assert data.get("missing"), "nothing says what is missing, so the user cannot tell what to ask again"


def test_A_RUN_THAT_PRODUCED_NOTHING_SAYS_SO_RATHER_THAN_SHOWING_AN_EMPTY_ANSWER() -> None:
    """When the retries are exhausted the model never answered; the checkpoint holds only the user's
    question. Dressing that up as a partial result would report an empty gap as work."""
    from langchain_core.messages import HumanMessage

    from rag_wright.harness.events import what_was_finished
    from rag_wright.harness.runs import RunOutcome

    events = what_was_finished(_result(RunOutcome.NOTHING_CAME_BACK, [HumanMessage(content="blends?")]))
    data = next(e.data for e in events if e.name == "incomplete")

    assert data.get("outcome") == RunOutcome.NOTHING_CAME_BACK
    assert not data.get("finished"), f"an empty run reported finished work: {data}"


def test_A_COMPLETE_RUN_IS_TOLD_NOTHING_EXTRA() -> None:
    """**The negative half, and the damaging one.** A caveat emitted on every run would put "incomplete"
    on every correct answer the product gives."""
    from langchain_core.messages import AIMessage

    from rag_wright.harness.events import what_was_finished
    from rag_wright.harness.runs import RunOutcome

    assert what_was_finished(_result(RunOutcome.COMPLETE, [AIMessage(content="Two mills stock it.")])) == []


def test_A_RUN_WAITING_ON_A_PERSON_IS_NOT_REPORTED_AS_INCOMPLETE() -> None:
    """It has not failed -- it is holding a question. Telling the user work was lost while their answer is
    outstanding would be false."""
    from langchain_core.messages import AIMessage

    from rag_wright.harness.events import what_was_finished
    from rag_wright.harness.runs import RunOutcome

    assert what_was_finished(
        _result(RunOutcome.WAITING_ON_A_PERSON, [AIMessage(content="which collection?")])) == []


def test_NO_TENANCY_REACHES_THE_CLIENT() -> None:
    """Scope is kept out of everything the model can reach, and SSE frames are more exposed than a prompt.
    Asserted on a scope whose parts could not occur in a tool result by accident."""
    from langchain_core.messages import ToolMessage

    from rag_wright.harness.events import what_was_finished
    from rag_wright.harness.runs import RunOutcome

    context = FabricContext(account="acct-7f3a91", season="season-c42e", collection="coll-5b8d07")
    said = " ".join(str(e.data) for e in what_was_finished(_result(
        RunOutcome.PARTIAL, [ToolMessage(content="x", tool_call_id="a", name="t")], context)))

    for secret in context.scope:
        assert secret not in said, "a scope id reached an SSE frame"
