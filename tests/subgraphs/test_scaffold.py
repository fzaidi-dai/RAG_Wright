"""LG-0: the hardened-subgraph pattern compiles and works, hermetically (no live LLM/DB).

These three tiny StateGraphs demonstrate + verify the primitives every RAG_Wright subgraph uses:
a RetryPolicy that recovers a transient failure, a conditional escalation edge, and a dead-letter terminal
that drops a bad item without raising. Node functions are injected fakes -- the pattern LG-1 follows.
"""

from __future__ import annotations

from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, dead_letter


class _State(TypedDict, total=False):
    x: int
    escalate: bool
    result: Optional[str]
    dead_letter: Optional[dict]


class _Transient(Exception):
    """A transient error NOT in LangGraph's default no-retry list -> DEFAULT_RETRY retries it."""


def test_retry_policy_recovers_a_transient_failure():
    calls = {"n": 0}

    def flaky(state: _State) -> _State:
        calls["n"] += 1
        if calls["n"] < 2:
            raise _Transient("provider blip")
        return {"result": "ok"}

    g = StateGraph(_State)
    g.add_node("flaky", flaky, retry_policy=DEFAULT_RETRY)
    g.add_edge(START, "flaky")
    g.add_edge("flaky", END)
    out = g.compile().invoke({"x": 1})
    assert out["result"] == "ok"
    assert calls["n"] == 2  # failed once, retried, succeeded


def test_conditional_escalation_edge_routes_on_state():
    def cheap(state: _State) -> _State:
        return {"escalate": state["x"] > 0}

    def strong(state: _State) -> _State:
        return {"result": "escalated"}

    def settle(state: _State) -> _State:
        return {"result": "cheap"}

    def route(state: _State) -> str:
        return "strong" if state.get("escalate") else "settle"

    g = StateGraph(_State)
    g.add_node("cheap", cheap)
    g.add_node("strong", strong)
    g.add_node("settle", settle)
    g.add_edge(START, "cheap")
    g.add_conditional_edges("cheap", route, {"strong": "strong", "settle": "settle"})
    g.add_edge("strong", END)
    g.add_edge("settle", END)
    graph = g.compile()
    assert graph.invoke({"x": 1})["result"] == "escalated"  # escalates
    assert graph.invoke({"x": -1})["result"] == "cheap"  # does not


def test_dead_letter_terminal_drops_a_bad_item_without_raising():
    def failing(state: _State) -> _State:
        return {"dead_letter": dead_letter("no_models", item=state["x"])}

    g = StateGraph(_State)
    g.add_node("failing", failing)
    g.add_edge(START, "failing")
    g.add_edge("failing", END)
    out = g.compile().invoke({"x": 7})
    assert out["dead_letter"] == {"reason": "no_models", "item": 7}
    assert out.get("result") is None  # the item was dropped, not produced -- and nothing raised
