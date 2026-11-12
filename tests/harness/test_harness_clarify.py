"""Asking the user mid-run.

Ported from RuleWright under ADR-0139's handover (PS-48a); the substrate is `rag_wright.harness.clarify`
and `runs.start_run` / `resume_run`. RuleWright's test of where the tool sits in its own manifest stays in
RuleWright.

**The rule comes from a verified fact, not a principle.** Resuming an interrupted run **replays the node
from the start**, so everything before `interrupt()` runs twice.

A tool that spends money, writes a row or sends anything before its question therefore does it **once on
the way in and again on the way back** -- silently, because the second pass is indistinguishable from the
first. So the guard below reads the function body: a statement before the interrupt is the defect, and no
behavioural test would show it without actually paying for the double.
"""

from __future__ import annotations

import ast
import inspect

import pytest
from langgraph.prebuilt.tool_node import ToolRuntime

from rag_wright.harness.clarify import clarification_tool
from tests.harness.fabric import FabricContext, scripted


def _body_of(source: str, name: str = "ask_the_user") -> list[ast.stmt]:
    """The tool function's statements, docstring excluded."""
    tree = ast.parse(inspect.cleandoc(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                body = body[1:]
            return body
    raise AssertionError(f"{name} not found; this guard is reading nothing")


def _statements_before_interrupt(body: list[ast.stmt]) -> list[str]:
    """Everything that would run twice on resume."""
    before: list[str] = []
    for statement in body:
        if any(isinstance(node, ast.Call) and getattr(node.func, "id", None) == "interrupt"
               for node in ast.walk(statement)):
            return before
        before.append(ast.unparse(statement)[:80])
    raise AssertionError("no call to `interrupt` at all -- the tool cannot ask anybody anything")


def test_NOTHING_HAPPENS_BEFORE_THE_INTERRUPT() -> None:
    """**The whole tool, in one assertion.**

    Resume replays the node, so a statement here runs twice. The tool spends nothing, writes nothing and
    sends nothing -- there is no "afterwards" to get wrong.
    """
    import rag_wright.harness.clarify as module

    before = _statements_before_interrupt(_body_of(inspect.getsource(module)))

    assert before == [], (
        f"these statements run BEFORE the interrupt and therefore twice on resume: {before}. Gather the "
        "arguments, call `interrupt()`, act only afterwards -- and here, do not act at all."
    )


def test_the_guard_CATCHES_a_side_effect_before_the_interrupt() -> None:
    """Planted, because the real tool complies and would prove nothing about the detector."""
    offending = '''
        def ask_the_user(question: str):
            """Ask."""
            store.write({"asked": question})
            spend_money()
            return interrupt({"question": question})
    '''

    before = _statements_before_interrupt(_body_of(offending))

    assert len(before) == 2
    assert "store.write" in before[0]


def test_the_guard_NOTICES_a_tool_that_never_interrupts() -> None:
    """A clarification tool that returns a string instead of interrupting would answer its own question --
    and the run would carry on with the model's guess as though a person had spoken."""
    with pytest.raises(AssertionError, match="no call to `interrupt`"):
        _statements_before_interrupt(_body_of('''
            def ask_the_user(question: str):
                """Ask."""
                return "probably the first one"
        '''))


# --- the tool on the surface --------------------------------------------------------------------------


def test_the_tool_asks_for_a_QUESTION_and_nothing_else() -> None:
    """A clarification takes the question and no context: it touches no run's data, so there is no scope
    to bind and nothing for a schema to leak."""
    assert set(clarification_tool().tool_call_schema.model_fields) == {"question"}


def test_the_DESCRIPTION_tells_the_model_when_NOT_to_ask() -> None:
    """**Each use stops the run and waits for a person.** A model that checks in politely turns an agent
    into a form -- and the only place to say so is the description, because there is no tool call to
    forbid."""
    described = " ".join((clarification_tool().description or "").lower().split())

    assert "cannot proceed without" in described
    assert "do not use it to check in" in described


# --- the run actually suspends and resumes ------------------------------------------------------------


def _context() -> FabricContext:
    return FabricContext(account="acme-mills", season="ss26", collection="resort")


async def test_a_question_SUSPENDS_the_run_and_the_answer_RESUMES_it() -> None:
    """**End to end through the door**, with a scripted model so the assertion is about the mechanism.

    The run stops at the question, the answer goes back through `resume_run`, and the tool returns what
    the person said.
    """
    from deepagents import create_deep_agent
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver

    from rag_wright.harness.runs import resume_run, start_run

    agent = create_deep_agent(
        model=scripted(
            AIMessage(content="", tool_calls=[{"name": "ask_the_user",
                                               "args": {"question": "Which collection did you mean?"},
                                               "id": "1", "type": "tool_call"}]),
            AIMessage(content="Thanks -- using the resort collection."),
        ),
        tools=[clarification_tool()],
        context_schema=FabricContext,
        checkpointer=InMemorySaver(),
    )

    started = await start_run(agent, _context(), message="Ask me something.")

    assert started.state.get("__interrupt__"), (
        "the run did not suspend: the tool returned instead of interrupting, so the model would carry on "
        "with its own guess as though a person had answered"
    )

    resumed = await resume_run(agent, _context(), answer="the resort collection", run_id=started.run_id)

    answered = [m for m in resumed.state.get("messages", []) if getattr(m, "name", "") == "ask_the_user"]
    assert answered, "the tool never produced a result, so the answer did not reach it"
    assert "resort collection" in str(answered[-1].content)


async def test_the_RESUMED_run_still_knows_its_SCOPE() -> None:
    """**The exact path the central hazard lives on.**

    Runtime context is per-invocation: a resume that omits it wakes every tool with
    `runtime.context is None`, and a store namespace built from it then falls back to one global
    namespace. A clarification is the commonest reason a run is ever resumed, so this is where that would
    bite -- at the moment a person has just answered.
    """
    from deepagents import create_deep_agent
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool as make_tool
    from langgraph.checkpoint.memory import InMemorySaver

    from rag_wright.harness.clarify import interrupt
    from rag_wright.harness.runs import resume_run, start_run

    seen: list = []

    @make_tool
    def ask_with_context(question: str, runtime: ToolRuntime[FabricContext]) -> str:
        """Ask, and record what scope the resumed pass sees."""
        answer = interrupt({"question": question})
        seen.append(getattr(runtime.context, "scope", None))
        return f"ok {answer}"

    agent = create_deep_agent(
        model=scripted(
            AIMessage(content="", tool_calls=[{"name": "ask_with_context", "args": {"question": "q"},
                                               "id": "1", "type": "tool_call"}]),
            AIMessage(content="done"),
        ),
        tools=[ask_with_context], context_schema=FabricContext, checkpointer=InMemorySaver(),
    )

    started = await start_run(agent, _context(), message="go")
    await resume_run(agent, _context(), answer="A", run_id=started.run_id)

    assert seen and seen[-1] is not None, (
        "the resumed tool woke with no scope -- the cross-scope memory read the harness exists to prevent"
    )
    assert seen[-1] == _context().scope
