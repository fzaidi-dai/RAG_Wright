"""A worker reasons with its own model (PS-78).

**Every tier and worker ran `AgentDefinition.model_id_for(settings)`**, so trying another model on one worker -- DeepSeek
on a worker that runs on Qwen -- changed the whole product. `worker_spec(..., model_id=)` names an engine model id for
that worker alone, and the builders make it through `agent_chat_model`, the profile seam, never a bare string.

**Accounting follows the model by construction**: the engine meter records each call under the id its model was built
for, so a worker's calls land under the worker's model. **Grading names the model a verdict is about** and keeps one
judge, so an A/B of a worker's model is judged by the same grader on both arms.

Driven through the real builders with scripted models standing in for the ones `agent_chat_model` would build.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from langchain_core.messages import AIMessage

import rag_wright.harness.workers as workers_module
from rag_wright.harness import Grading, Rule, build_orchestrator, build_worker, start_run, worker_spec
from rag_wright.models.seam import _AgentCallMeter
from rag_wright.models.usage import usage_scope
from tests.harness.fabric import (
    FabricFindings,
    FabricToolSurface,
    _a_context,
    _fabric,
    _resources,
    _settings,
    scripted,
)
from tests.harness.test_harness_grading_tiers import _grader

THE_WORKERS_MODEL = "deepseek/deepseek-v4-flash"
THE_PRODUCTS_MODEL = "fabric-mill-model"

DISPATCH = AIMessage(content="", tool_calls=[{"name": "task", "id": "t1", "type": "tool_call", "args": {
    "description": "Match the poplin requirement.", "subagent_type": "composition_worker"}}])
REPORT = AIMessage(content="", tool_calls=[{"name": "FabricFindings", "id": "r1", "type": "tool_call", "args": {
    "status": "done", "swatches": ["cotton-poplin-120gsm"]}}])


def _metered(model_id: str, *replies: Any) -> Any:
    """A scripted model metered the way `build_agent_model` meters a real one: under the id it was built for."""
    model = scripted(*replies)
    model.callbacks = [_AgentCallMeter(model_id, trace=False)]
    return model


def _building(monkeypatch: Any, *replies: Any) -> list[str]:
    """Stand in for `agent_chat_model` where workers are built: each id asked for gets a metered scripted model."""
    asked: list[str] = []

    def built(settings: Any, model_id: str) -> Any:
        asked.append(model_id)
        return _metered(model_id, *replies)

    monkeypatch.setattr(workers_module, "agent_chat_model", built)
    return asked


def _worker(model_id: str | None = THE_WORKERS_MODEL):
    """The fabric product's worker, on a model of its own unless `model_id` is None."""
    def worker(context: Any, resources: Any, ledger: Any = None) -> dict:
        return worker_spec("composition_worker", "Finds fabric compositions matching a requirement set.",
                           "You match requirement sets to fabric compositions.",
                           FabricToolSurface().build(context, resources, only=["fabric.match_compositions"]),
                           FabricFindings, ledger=ledger, definition=_fabric(), model_id=model_id)
    return worker


def _definition(*rules: Rule, model_id: str | None = THE_WORKERS_MODEL) -> Any:
    return dataclasses.replace(_fabric(), workers=(_worker(model_id),), routing=None, rules=rules)


def _handed(monkeypatch: Any, definition: Any, grading: Any = None) -> dict:
    """What `build_orchestrator` hands the library."""
    import deepagents

    handed: dict = {}
    real = deepagents.create_deep_agent
    monkeypatch.setattr(deepagents, "create_deep_agent", lambda **kwargs: handed.update(kwargs) or real(**kwargs))
    build_orchestrator(_a_context(), _resources(), _settings(), definition=definition,
                       model=scripted(AIMessage(content="done")), grading=grading)
    return handed


def _dispatched(monkeypatch: Any, definition: Any, grading: Any = None) -> Any:
    """One orchestrated run: the orchestrator dispatches the worker once and answers; the usage it recorded."""
    _building(monkeypatch, REPORT)
    orchestrator = _metered(THE_PRODUCTS_MODEL, DISPATCH, AIMessage(content="Poplin is matched."))
    agent = build_orchestrator(_a_context(), _resources(), _settings(), definition=definition, model=orchestrator,
                               grading=grading)
    with usage_scope() as usage:
        run = asyncio.run(start_run(agent, _a_context(), message="Which compositions match the poplin?"))
    return run, usage


def test_A_WORKER_WITH_A_MODEL_ID_IS_COMPILED_WITH_THE_MODEL_BUILT_FOR_IT(monkeypatch: Any) -> None:
    asked = _building(monkeypatch)

    handed = _handed(monkeypatch, _definition())

    (worker,) = handed["subagents"]
    assert asked == [THE_WORKERS_MODEL], asked
    assert [meter.model_id for meter in worker["model"].callbacks] == [THE_WORKERS_MODEL]
    assert "model_id" not in worker, "the library was handed a key it does not read"


def test_WITHOUT_A_MODEL_ID_A_WORKER_RUNS_THE_PRODUCTS_MODEL_AS_BEFORE(monkeypatch: Any) -> None:
    asked = _building(monkeypatch)

    spec = _worker(None)(_a_context(), _resources())
    handed = _handed(monkeypatch, _definition(model_id=None))

    assert set(spec) == {"name", "description", "system_prompt", "tools", "response_format", "middleware"}
    assert "model" not in handed["subagents"][0]
    assert asked == []


def test_A_DISPATCHED_WORKER_IS_ANSWERED_BY_ITS_OWN_MODEL_AND_METERED_UNDER_IT(monkeypatch: Any) -> None:
    """**The accounting half.** The orchestrator's two calls land under the product's model, the worker's report under
    the worker's -- the same `by_model` a product's ledger reads."""
    run, usage = _dispatched(monkeypatch, _definition())

    assert run.state["messages"][-1].content == "Poplin is matched."
    assert "cotton-poplin-120gsm" in str(run.state["messages"]), "the worker's own model did not report"
    assert {model: per.calls for model, per in usage.by_model.items()} == {THE_PRODUCTS_MODEL: 2,
                                                                         THE_WORKERS_MODEL: 1}


def test_BUILD_WORKER_COMPILES_THE_WORKER_ON_ITS_OWN_MODEL(monkeypatch: Any) -> None:
    """A worker tested alone runs the model it runs orchestrated; a model passed in still wins, for a scripted test."""
    asked = _building(monkeypatch)
    spec = _worker()(_a_context(), _resources())

    build_worker(spec, _settings())
    assert asked and set(asked) == {THE_WORKERS_MODEL}, asked

    asked.clear()
    build_worker(spec, _settings(), model=scripted())
    assert asked == []


def test_THE_ENGINE_BUILDS_A_WORKERS_MODEL_METERED_UNDER_ITS_ID(monkeypatch: Any) -> None:
    """**The meter keys on the id the model was built for**, which is why a worker's calls need no accounting of their
    own. Construction only: nothing is called."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    model = workers_module.agent_chat_model(_settings(), THE_WORKERS_MODEL)

    assert [c.model_id for c in model.callbacks if isinstance(c, _AgentCallMeter)] == [THE_WORKERS_MODEL]


WORKER_RULE = Rule(id="reports-a-swatch", applies_to=("worker",), instruction="Report what matched.",
                   criterion="A swatch was reported.", detect=None)
ORCHESTRATOR_RULE = Rule(id="dispatches-once", applies_to=("orchestrator",), instruction="Dispatch once.",
                         criterion="The worker was dispatched once.", detect=None)


def test_A_VERDICT_NAMES_THE_MODEL_ITS_TIER_RAN_ON(monkeypatch: Any) -> None:
    verdicts: list = []

    _dispatched(monkeypatch, _definition(WORKER_RULE, ORCHESTRATOR_RULE),
                Grading(on_evaluation=verdicts.append, model=_grader([])))

    assert {v["tier"]: v["model"] for v in verdicts} == {"composition_worker": THE_WORKERS_MODEL,
                                                         "orchestrator": THE_PRODUCTS_MODEL}, verdicts


def test_ONE_JUDGE_GRADES_EVERY_ARM(monkeypatch: Any) -> None:
    """**The grader is the product's model, not the graded worker's**: a DeepSeek arm graded by DeepSeek and a Qwen arm
    by Qwen would compare two judges, not two workers."""
    _building(monkeypatch)
    handed = _handed(monkeypatch, _definition(WORKER_RULE), Grading(on_evaluation=print))

    (worker,) = handed["subagents"]
    (grader,) = [m for m in worker["middleware"] if type(m).__name__ == "GradeThisTier"]
    assert grader._model_id == THE_PRODUCTS_MODEL
