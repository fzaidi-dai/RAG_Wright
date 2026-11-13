"""The HD-2 deciders read the tiers' real decisions, and Jev's answers, correctly -- hermetic, on scripted models.

A decider that misreads a tier's message would measure the reader, not the tier: so each reading rule is shown on a
message built for it, and the chat-manager is driven through the real `build_chat_agent` to prove the run stops at
its decision rather than executing the tool it chose.
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage

from rag_wright.api import GoldItem, GoldSet
from tests.harness.fabric import _a_context, _resources, _settings, scripted
from tests.harness.gold import deciders
from tests.harness.gold.deciders import Probabilities, d1_choice, d2_workers, decision_of, opening
from tests.harness.gold.loom import WORKERS, loom
from tests.harness.gold.scoring import score_workers


def _calls(*calls: tuple[str, dict]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": f"c{i}"} for i, (n, a) in enumerate(calls)])


def test_D1_IS_THE_FIRST_TOOL_DELEGATING_IS_TASK_AND_NO_TOOL_IS_ANSWERING() -> None:
    assert d1_choice(_calls(("read_swatch", {"swatch": "sw-1"}))) == "read_swatch"
    assert d1_choice(_calls(("task", {"description": "x", "subagent_type": "orchestrator"}))) == "delegate"
    assert d1_choice(_calls(("ask_the_user", {"question": "Which order?"}))) == "ask_the_user"
    assert d1_choice(AIMessage(content="Hello!")) == "answer"


def test_D2_IS_THE_WORKERS_DISPATCHED_BY_TASK_OR_BY_A_PROGRAM_AND_UNDECIDED_WHILE_IT_READS() -> None:
    assert d2_workers(_calls(("task", {"subagent_type": "testing_worker"}),
                             ("task", {"subagent_type": "composition_worker"}))) == ["composition_worker",
                                                                                     "testing_worker"]
    program = "await Promise.all([tools.task({subagent_type: 'sourcing_worker', description: 'x'})])"
    assert d2_workers(_calls(("eval", {"code": program}))) == ["sourcing_worker"]
    assert d2_workers(AIMessage(content="The plan has two collections.")) == ["none"]
    assert d2_workers(_calls(("list_mills", {}))) is None
    assert d2_workers(_calls(("write_todos", {"todos": []}))) is None


def test_THE_RUN_STOPS_AT_THE_CHAT_MANAGERS_DECISION() -> None:
    """Scripted with ONE message: had the run gone on to execute the tool and call the model again, the exhausted
    script would raise."""
    from rag_wright.harness import build_chat_agent

    agent = build_chat_agent(_a_context(), _resources(), _settings(), model=scripted(_calls(("list_mills", {}))),
                             definition=loom(variant="small"))

    assert asyncio.run(decision_of(agent, "Which mills can we order from?", d1_choice)) == "list_mills"


def test_THE_VIEW_IS_PUT_IN_FRONT_OF_THE_WORDS_AS_THE_HARNESS_DOES() -> None:
    assert opening({"surface": "swatch", "id": "sw-1"}, "What is it?") == "<viewing>\nswatch: sw-1\n</viewing>\n\nWhat is it?"
    assert opening({}, "What is it?") == "What is it?"


class FakeJev:
    """Jev at the capability layer: records each request and answers from `answers` in turn."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.answers: list[dict] = []

    async def __call__(self, name: str, inputs: dict, *, resources: object = None) -> dict:
        assert name == "jev_decision"
        self.sent.append(inputs)
        return {"answers": self.answers.pop(0)}


@pytest.fixture
def jev(monkeypatch: pytest.MonkeyPatch) -> FakeJev:
    import rag_wright.api

    fake = FakeJev()
    monkeypatch.setattr(rag_wright.api, "ainvoke_model", fake)
    return fake


def _item(**inputs: object) -> GoldItem:
    return GoldItem(id="i1", input=inputs, expected="")


def test_JEV_D1_SPLIT_DELEGATES_ON_A_PLANNING_YES_AND_OTHERWISE_TAKES_THE_CHOICE(jev: FakeJev) -> None:
    kept = Probabilities()
    decide = deciders.jev_d1("small", kept, split=True)
    jev.answers = [{"plan": {"noul": 0.9}, "act": {"choice": "read_swatch"}},
                   {"plan": {"noul": 0.2}, "act": {"choice": "read_swatch"}}]

    assert asyncio.run(decide(_item(message="m", view={}))) == "delegate"
    assert asyncio.run(decide(_item(message="m", view={}))) == "read_swatch"
    assert "delegate" not in jev.sent[0]["questions"]["act"]["criteria"]
    assert len(kept.rows) == 2


def test_JEV_D1_SINGLE_OFFERS_EVERY_OPTION_OF_THE_VARIANT(jev: FakeJev) -> None:
    decide = deciders.jev_d1("small", Probabilities(), split=False)
    jev.answers = [{"act": {"choice": "delegate"}}]

    assert asyncio.run(decide(_item(message="m", view={}))) == "delegate"
    assert set(jev.sent[0]["questions"]["act"]["criteria"]) == {"list_mills", "read_swatch", "list_swatches",
                                                            "read_order", "delegate", "ask_the_user", "answer"}


def test_JEV_D2_SENDS_THE_WORKERS_OVER_THE_THRESHOLD_OR_NONE(jev: FakeJev) -> None:
    decide = deciders.jev_d2(Probabilities())
    jev.answers = [{"composition_worker": {"noul": 0.8}, "sourcing_worker": {"noul": 0.6},
                    "testing_worker": {"noul": 0.1}},
                   {w: {"noul": 0.1} for w in WORKERS}]

    assert asyncio.run(decide(_item(brief="b", view={}))) == ["composition_worker", "sourcing_worker"]
    assert asyncio.run(decide(_item(brief="b", view={}))) == ["none"]


def test_JEV_B1_READS_A_YES_AS_SELF_CONTAINED(jev: FakeJev) -> None:
    decide = deciders.jev_b1(Probabilities())
    jev.answers = [{"b1": {"noul": 0.7}}, {"b1": {"noul": 0.3}}]
    turns = [{"role": "user", "content": "Which mills?"}, {"role": "assistant", "content": "mill-a, mill-b."}]

    assert asyncio.run(decide(_item(turns=turns, message="Which is fastest?", view={}))) == "self_contained"
    assert asyncio.run(decide(_item(turns=turns, message="Which is fastest?", view={}))) == "refers_back"
    assert "mill-a, mill-b." in jev.sent[0]["state"]


def test_D2_SCORING_IS_PER_WORKER_YES_AND_NO_SO_SENDING_EVERYONE_FAILS() -> None:
    gold = GoldSet(name="planted", version="1", items=[
        GoldItem(id="a", input="", expected=["composition_worker"]),
        GoldItem(id="b", input="", expected=["none"]),
    ])
    workers = tuple(WORKERS)
    everyone = {"a": sorted(WORKERS), "b": sorted(WORKERS)}
    right = {"a": ["composition_worker"], "b": ["none"]}

    assert not score_workers(gold, everyone, workers=workers, floor=0.5).passed
    assert score_workers(gold, everyone, workers=workers, floor=0.5).diagnostics["per_label"]["sourcing_worker:no"] == {
        "n": 2, "accuracy": 0.0}
    assert score_workers(gold, right, workers=workers, floor=1.0).passed
    assert score_workers(gold, right, workers=workers, floor=1.0).diagnostics["exact_sets"] == 1.0
