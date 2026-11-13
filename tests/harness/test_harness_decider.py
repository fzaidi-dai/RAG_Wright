"""The routing decider asks the decision model, remembers the run's answers, and steps aside when it cannot ask (HD-4).

Hermetic: the capability layer is patched, so each test can count the calls the decider makes and choose what the
decision model answers. The live test at the end makes one real call per question shape.
"""

from __future__ import annotations

import asyncio

import pytest

from rag_wright.harness.decider import Decider, Routed
from rag_wright.harness.decisions import DecisionLedger


class FakeDecisionModel:
    """`ainvoke_model` for `jev_decision`: records each request and answers with `answer(request)`."""

    def __init__(self, answer=None, *, fails: bool = False) -> None:
        self.requests: list[dict] = []
        self._answer = answer
        self._fails = fails

    async def __call__(self, name: str, inputs: dict, *, resources: object = None) -> dict:
        assert name == "jev_decision"
        self.requests.append(inputs)
        if self._fails:
            raise RuntimeError("jev_decision requires OPENROUTER_API_KEY")
        return {"answers": self._answer(inputs)}


def _routes_to(choice: str, p: float):
    return lambda inputs: {"choice": {"type": "choice", "choice": choice, "probabilities": {choice: p}}}


@pytest.fixture
def registered(monkeypatch: pytest.MonkeyPatch):
    """Patch the capability layer; `jev_decision` registered unless a test says otherwise."""
    import rag_wright.api

    def install(fake: FakeDecisionModel, *, catalog: tuple[str, ...] = ("jev_decision",)) -> FakeDecisionModel:
        monkeypatch.setattr(rag_wright.api, "ainvoke_model", fake)
        monkeypatch.setattr(rag_wright.api, "capability_index", lambda: {slug: {} for slug in catalog})
        return fake
    return install


OPTIONS = {"list_mills": "List the mills.", "delegate": "Plan multi-step work.", "answer": "Reply directly."}


def test_A_CHOICE_COMES_BACK_WITH_ITS_PROBABILITY(registered) -> None:
    fake = registered(FakeDecisionModel(_routes_to("list_mills", 0.83)))

    routed = asyncio.run(Decider().choose("run-1", "Which mills?", "What should the assistant do?", OPTIONS))

    assert routed == Routed(choice="list_mills", confidence=0.83)
    assert fake.requests[0]["questions"]["choice"] == {"type": "choice", "instructions": "What should the assistant do?",
                                                       "criteria": OPTIONS}
    assert fake.requests[0]["state"] == "Which mills?"


def test_SEVERAL_YES_NO_QUESTIONS_ARE_ONE_CALL(registered) -> None:
    fake = registered(FakeDecisionModel(lambda inputs: {q: {"type": "noul", "noul": 0.9 if q == "a" else 0.2}
                                                        for q in inputs["questions"]}))

    yes = asyncio.run(Decider().yes_no("run-1", "brief", {"a": ("Needs a?", "it needs a"),
                                                          "b": ("Needs b?", "it needs b")}))

    assert yes == {"a": 0.9, "b": 0.2}
    assert len(fake.requests) == 1
    assert fake.requests[0]["questions"]["a"] == {"type": "noul", "instructions": "Needs a?",
                                                  "criteria": {"true": "it needs a", "false": "no"}}


def test_A_QUESTION_ASKED_AGAIN_IN_A_RUN_IS_ANSWERED_FROM_THE_RUN_AND_NOT_ACROSS_RUNS(registered) -> None:
    """Jev flips about 3% of answers between identical calls; within a run the first answer stands."""
    fake = registered(FakeDecisionModel(_routes_to("delegate", 0.7)))
    decider = Decider()

    first = asyncio.run(decider.choose("run-1", "Compare mills.", "Do what?", OPTIONS))
    again = asyncio.run(decider.choose("run-1", "Compare mills.", "Do what?", OPTIONS))
    other_run = asyncio.run(decider.choose("run-2", "Compare mills.", "Do what?", OPTIONS))

    assert first == again == other_run
    assert len(fake.requests) == 2


def test_UNREGISTERED_MEANS_UNAVAILABLE_AND_NOTHING_IS_CALLED(registered) -> None:
    fake = registered(FakeDecisionModel(_routes_to("delegate", 0.7)), catalog=())

    assert asyncio.run(Decider().choose("run-1", "x", "Do what?", OPTIONS)) is None
    assert fake.requests == []


def test_A_FAILED_CALL_IS_UNAVAILABLE_AND_THE_RUN_STOPS_ASKING(registered, caplog) -> None:
    fake = registered(FakeDecisionModel(fails=True))
    decider = Decider()

    assert asyncio.run(decider.choose("run-1", "x", "Do what?", OPTIONS)) is None
    assert asyncio.run(decider.yes_no("run-1", "y", {"a": ("Needs a?", "yes")})) is None
    assert len(fake.requests) == 1
    assert sum("decision_model_unavailable" in r.message for r in caplog.records) == 1
    assert asyncio.run(decider.choose("run-2", "x", "Do what?", OPTIONS)) is None
    assert len(fake.requests) == 2  # a new run asks again


def test_AN_ANSWER_MISSING_A_QUESTION_IS_UNAVAILABLE_NOT_A_GUESS(registered) -> None:
    registered(FakeDecisionModel(lambda inputs: {"a": {"type": "noul", "noul": 0.9}}))

    assert asyncio.run(Decider().yes_no("run-1", "brief", {"a": ("A?", "yes"), "b": ("B?", "yes")})) is None


def test_A_YES_NO_WITHOUT_A_NUMBER_IS_UNAVAILABLE_NOT_A_FAILED_TURN(registered) -> None:
    registered(FakeDecisionModel(lambda inputs: {"a": {"type": "noul", "noul": None}}))

    assert asyncio.run(Decider().yes_no("run-1", "brief", {"a": ("A?", "yes")})) is None


def test_A_CHOICE_OUTSIDE_THE_OPTIONS_IS_UNAVAILABLE_NOT_A_ROUTE(registered) -> None:
    registered(FakeDecisionModel(_routes_to("read_file", 0.9)))

    assert asyncio.run(Decider().choose("run-1", "x", "Do what?", OPTIONS)) is None


def test_THE_PROBABILITY_IS_THE_DECISIONS_CONFIDENCE_ON_THE_LEDGER(registered) -> None:
    registered(FakeDecisionModel(_routes_to("list_mills", 0.83)))
    routed = asyncio.run(Decider().choose("run-1", "Which mills?", "Do what?", OPTIONS))

    made = DecisionLedger().record("D1", tier="chat-manager", chose=routed.choice, confidence=routed.confidence)

    assert made.confidence == 0.83


@pytest.mark.model
def test_LIVE_ONE_CALL_PER_QUESTION_SHAPE() -> None:
    """Two real decision-model calls (about $0.00004)."""
    from dotenv import load_dotenv

    from rag_wright.api import engine_capabilities, register_capability

    load_dotenv()
    register_capability(next(m for m in engine_capabilities() if m.slug == "jev_decision"))
    decider = Decider()

    routed = asyncio.run(decider.choose("live", "Which mills can we order from?",
                                        "What should the assistant do with the person's latest message?", OPTIONS))
    yes = asyncio.run(decider.yes_no("live", "Find the cheapest mill for 10,000 m of poplin.",
                                     {"sourcing": ("Does this request need the sourcing worker?",
                                                   "Compares mills on capacity, lead time, minimum order and price."),
                                      "testing": ("Does this request need the testing worker?",
                                                  "Checks a swatch's lab reports against a buyer's standard.")}))

    assert routed is not None and routed.choice == "list_mills" and 0.0 < routed.confidence <= 1.0
    assert yes is not None and yes["sourcing"] > 0.5 > yes["testing"]
