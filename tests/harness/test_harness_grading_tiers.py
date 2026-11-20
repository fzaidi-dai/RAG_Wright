"""Rules and grading reach every tier the engine builds, the chat-manager included (PS-72).

**Until this, `AgentDefinition.rules` was declared and read by nothing.** A product attached the grader itself, through
`build_orchestrator(extra_middleware=)` or a worker spec's middleware, and could not attach it to the chat-manager at
all: `Tier` had no value for it and `build_chat_agent` took no middleware. A product with only a chat-manager (TexWright's
first agent) therefore had no way to grade a run. Now a product passes `grading=Grading(on_evaluation)` and every tier
with a rule of its own is recorded and graded; with no `grading`, nothing is built.

Driven through the real builders with a grader that judges FROM the evidence tool, so a verdict here proves the run's
recording reached the grader -- not just that a middleware was constructed.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from rag_wright.harness import (
    Grading,
    GradeThisTier,
    Rule,
    broken,
    build_chat_agent,
    build_orchestrator,
    instructions_for,
    start_run,
)
from tests.harness.fabric import _a_context, _fabric, _resources, _settings, scripted


def _answered_from_a_tool(recording: dict[str, Any]) -> str | None:
    """A chat-manager rule a product would write: an answer must come from a tool, never from memory."""
    called = [call for turn in recording.get("turns") or [] for message in turn.get("said") or []
              for call in message.get("tool_calls") or []]
    return None if called else "answered without calling any tool"


ANSWERS_FROM_A_TOOL = Rule(id="answers-from-a-tool", applies_to=("chat-manager",),
                           instruction="Answer from the tools, never from memory.",
                           criterion="The answer came from a tool call.", detect=_answered_from_a_tool)
ONE_MILL_PER_ENQUIRY = Rule(id="one-mill-per-enquiry", applies_to=("worker",),
                            instruction="Ask each mill once per enquiry.", criterion="Each mill was asked once.",
                            detect=None)


def _chat_only(*rules: Rule) -> Any:
    """The fabric product cut down to a chat-manager: nothing needs planning and there are no workers."""
    return dataclasses.replace(_fabric(), planning={}, workers=(), rules=rules, routing=None)


class JudgesFromTheEvidence(GenericFakeChatModel):
    """A grader that calls `check_this_run`, then fails exactly the rules the tool reported broken.

    **It reads the evidence, so the verdict depends on the recording.** A scripted verdict would pass whether or not
    the run was recorded at all."""

    asked: Any

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Any:
        from langchain_core.outputs import ChatGeneration, ChatResult

        evidence = next((m for m in messages if isinstance(m, ToolMessage)), None)
        if evidence is None:
            reply = AIMessage(content="", tool_calls=[{"name": "check_this_run", "args": {}, "id": "g1",
                                                       "type": "tool_call"}])
        else:
            question = next(str(m.content) for m in messages if isinstance(m, HumanMessage))
            self.asked.append(question)
            nonce = re.search(r"<verdict-([0-9a-f]+)>", question).group(1)
            found = json.loads(evidence.content)
            ids = re.findall(r"^- \[([^\]]+)\]", question.split(f"<rules-{nonce}>")[-1], re.M)
            lines = [f"- {i} | fail | {found['broken'][i]}" if i in found["broken"] else f"- {i} | pass"
                     for i in ids]
            result = "needs_revision" if found["broken"] else "satisfied"
            reply = AIMessage(content=f"<verdict-{nonce}>\nresult: {result}\nexplanation: judged from the evidence.\n"
                                      + "\n".join(lines) + f"\n</verdict-{nonce}>")
        return ChatResult(generations=[ChatGeneration(message=reply)])


def _grader(asked: list) -> Any:
    return JudgesFromTheEvidence(messages=iter([]), asked=asked)


def _ask(definition: Any, model: Any, grading: Any) -> Any:
    agent = build_chat_agent(_a_context(), _resources(), _settings(), definition=definition, model=model,
                             grading=grading)
    return asyncio.run(start_run(agent, _a_context(), message="Which mills can we order from?"))


# --- the chat-manager is graded through the tier the engine builds ------------------------------------------------


def test_A_CHAT_MANAGER_ONLY_PRODUCT_IS_GRADED_AGAINST_ITS_OWN_RULE() -> None:
    """**TexWright's case.** The violation is planted -- the model answers from memory -- and the grader, reading the
    run's recording through its evidence tool, fails exactly that rule, on the chat-manager, through `build_chat_agent`."""
    verdicts: list = []
    asked: list = []

    _ask(_chat_only(ANSWERS_FROM_A_TOOL), scripted(AIMessage(content="Mill A and mill B.")),
         Grading(on_evaluation=verdicts.append, model=_grader(asked)))

    assert len(verdicts) == 1, f"one tier with a rule, one verdict: {verdicts}"
    (verdict,) = verdicts
    assert verdict["tier"] == "chat-manager"
    assert verdict["result"] == "needs_revision"
    assert verdict["criteria"] == [{"name": "answers-from-a-tool", "passed": False,
                                    "gap": "answered without calling any tool"}]
    assert "The answer came from a tool call." in asked[0], "the chat-manager's rubric did not reach the grader"


def test_THE_SAME_RULE_HOLDS_WHEN_THE_RUN_KEEPS_IT() -> None:
    """**The other direction**: a rule that fails on correct behaviour teaches everyone to ignore it."""
    verdicts: list = []

    _ask(_chat_only(ANSWERS_FROM_A_TOOL),
         scripted(AIMessage(content="", tool_calls=[{"name": "list_mills", "args": {}, "id": "c1",
                                                     "type": "tool_call"}]),
                  AIMessage(content="Mill A and mill B.")),
         Grading(on_evaluation=verdicts.append, model=_grader([])))

    assert [v["result"] for v in verdicts] == ["satisfied"], verdicts


def test_NO_GRADING_BUILDS_NO_GRADER() -> None:
    """Grading is opt-in: without `grading=` no tier records or grades, and the run costs what it did before."""
    asked: list = []
    run = _ask(_chat_only(ANSWERS_FROM_A_TOOL), scripted(AIMessage(content="Mill A and mill B.")), None)

    assert run.state["messages"][-1].content == "Mill A and mill B."
    assert asked == []


def test_A_TIER_WITH_NO_RULE_OF_ITS_OWN_IS_NOT_GRADED() -> None:
    """**An empty rubric is a model call that judges nothing.** A product whose rules are all a worker's pays no grader
    on its chat-manager."""
    verdicts: list = []
    asked: list = []

    _ask(_chat_only(ONE_MILL_PER_ENQUIRY), scripted(AIMessage(content="Mill A and mill B.")),
         Grading(on_evaluation=verdicts.append, model=_grader(asked)))

    assert verdicts == [] and asked == []


# --- the orchestrator and its workers, from the same `grading` ---------------------------------------------------


def _built(monkeypatch: Any, definition: Any, grading: Any) -> dict:
    """What `build_orchestrator` hands the library, read where it is handed (a compiled agent hides its middleware)."""
    import deepagents

    handed: dict = {}
    real = deepagents.create_deep_agent

    def capture(**kwargs: Any) -> Any:
        handed.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(deepagents, "create_deep_agent", capture)
    build_orchestrator(_a_context(), _resources(), _settings(), definition=definition,
                       model=scripted(AIMessage(content="done")), grading=grading)
    return handed


def _graders(middleware: list) -> list[GradeThisTier]:
    return [m for m in middleware if isinstance(m, GradeThisTier)]


def test_THE_ORCHESTRATOR_AND_EACH_WORKER_ARE_GRADED_AS_THEIR_OWN_TIER(monkeypatch: Any) -> None:
    rules = (ONE_MILL_PER_ENQUIRY,
             Rule(id="plans-before-dispatching", applies_to=("orchestrator",), instruction="Plan first.",
                  criterion="The orchestrator planned before dispatching.", detect=None))
    handed = _built(monkeypatch, dataclasses.replace(_fabric(), rules=rules, routing=None),
                    Grading(on_evaluation=lambda verdict: None))

    assert [g.name for g in _graders(handed["middleware"])] == ["GradeThisTier(orchestrator)"]
    (worker,) = handed["subagents"]
    assert [g.name for g in _graders(worker["middleware"])] == ["GradeThisTier(worker)"]
    assert "Each mill was asked once." in _graders(worker["middleware"])[0]._rubric
    assert "Each mill was asked once." not in _graders(handed["middleware"])[0]._rubric


def test_THE_RECORDER_SITS_OUTSIDE_THE_ROUTER(monkeypatch: Any) -> None:
    """**A route settled in code makes no model call**, so a recorder inside the router would miss that turn and the
    grader would judge a run with a hole in it. On both routed tiers it sits outside."""
    import rag_wright.harness.agent as agent_module

    rules = (ANSWERS_FROM_A_TOOL, Rule(id="plans", applies_to=("orchestrator",), instruction="Plan.",
                                       criterion="It planned.", detect=None))
    definition = dataclasses.replace(_fabric(), rules=rules)
    chat: dict = {}
    real = agent_module.create_deep_agent
    monkeypatch.setattr(agent_module, "create_deep_agent", lambda **kwargs: chat.update(kwargs) or real(**kwargs))
    build_chat_agent(_a_context(), _resources(), _settings(), definition=definition,
                     model=scripted(AIMessage(content="done")), grading=Grading(on_evaluation=print))
    orchestrator = _built(monkeypatch, definition, Grading(on_evaluation=print))

    for handed, router in ((chat, "RouteTheTurn"), (orchestrator, "RouteTheBrief")):
        order = [type(m).__name__ for m in handed["middleware"]]
        assert order.index("RecordTheContext") < order.index(router), order


def test_WITHOUT_GRADING_NO_TIER_CARRIES_A_GRADER(monkeypatch: Any) -> None:
    handed = _built(monkeypatch, dataclasses.replace(_fabric(), rules=(ONE_MILL_PER_ENQUIRY,), routing=None), None)

    assert not _graders(handed["middleware"])
    assert not any(_graders(worker["middleware"]) for worker in handed["subagents"])


# --- what a product checks its own rules with ----------------------------------------------------------------------


def test_A_PRODUCT_CAN_CHECK_ITS_CHAT_MANAGER_RULES_FROM_THE_PUBLIC_SURFACE() -> None:
    """`instructions_for` says what a tier is told, so a product can test every instruction is in its prompt; `broken`
    runs the detectors over a recording, offline."""
    rules = (ANSWERS_FROM_A_TOOL, ONE_MILL_PER_ENQUIRY)

    assert instructions_for("chat-manager", rules) == ["Answer from the tools, never from memory."]
    assert broken({"turns": []}, tier="chat-manager", rules=rules) == {
        "answers-from-a-tool": "answered without calling any tool"}
