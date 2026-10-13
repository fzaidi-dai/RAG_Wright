"""PS-32: `GenerationRecorder` labels each model call by what really happened to it, with concurrent workers.

One recorder serves a whole run, and the run's callbacks reach every worker, so calls from parallel workers are open
at the same time. Only a RETRY of the same model turn (same parent run) proves an earlier attempt was abandoned; a
concurrent call from another worker (another parent) proves nothing about it. A call that fails is closed as an
error, not as abandoned; the end-of-run sweep closes whatever is still open.
"""
from __future__ import annotations

import uuid

import pytest
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.messages import AIMessage

from rag_wright.harness import models


class Span:
    def __init__(self, name):
        self.name, self.updates, self.ended = name, [], False

    def update(self, **kw):
        self.updates.append(kw)

    def end(self):
        self.ended = True

    @property
    def abandoned(self):
        return any((u.get("metadata") or {}).get("abandoned") for u in self.updates)


@pytest.fixture
def recorder(monkeypatch):
    opened, recorded = [], []

    def open_generation(settings, *, model, input=None):
        span = Span(f"span{len(opened)}")
        opened.append(span)
        return span

    def record_generation(settings, **kw):
        recorded.append(kw)
        if kw.get("generation") is not None:
            kw["generation"].end()

    monkeypatch.setattr(models, "open_generation", open_generation)
    monkeypatch.setattr(models, "record_generation", record_generation)
    rec = models.GenerationRecorder(settings=object(), model_id="m")
    rec.opened, rec.recorded = opened, recorded
    return rec


def _start(rec, parent):
    run_id = uuid.uuid4()
    rec.on_chat_model_start({}, [[]], run_id=run_id, parent_run_id=parent, metadata={})
    return run_id


def _result():
    return LLMResult(generations=[[ChatGeneration(message=AIMessage(content="ok"))]], llm_output={})


def test_a_concurrent_call_from_another_worker_is_not_abandoned(recorder):
    worker_a, worker_b = uuid.uuid4(), uuid.uuid4()
    a = _start(recorder, worker_a)
    b = _start(recorder, worker_b)       # another worker's call starts while A is in flight
    recorder.on_llm_end(_result(), run_id=b, parent_run_id=worker_b)
    recorder.on_llm_end(_result(), run_id=a, parent_run_id=worker_a)
    span_a, span_b = recorder.opened
    assert not span_a.abandoned and not span_b.abandoned
    # each call recorded once, against its own span (no second, zero-duration generation)
    assert [r["generation"] for r in recorder.recorded] == [span_b, span_a]


def test_a_retry_of_the_same_turn_closes_the_attempt_it_replaced(recorder):
    turn = uuid.uuid4()
    _start(recorder, turn)                # cancelled by the per-attempt deadline: no end, no error callback
    retry = _start(recorder, turn)
    first, second = recorder.opened
    assert first.abandoned and first.ended
    recorder.on_llm_end(_result(), run_id=retry, parent_run_id=turn)
    assert not second.abandoned and recorder.recorded[0]["generation"] is second


def test_a_failed_call_is_closed_as_an_error_not_as_abandoned(recorder):
    turn = uuid.uuid4()
    failed = _start(recorder, turn)
    recorder.on_llm_error(RuntimeError("provider 500"), run_id=failed, parent_run_id=turn)
    (span,) = recorder.opened
    assert span.ended and not span.abandoned
    assert any((u.get("metadata") or {}).get("error") == "RuntimeError" for u in span.updates)
    _start(recorder, turn)                # the retry finds nothing of the failed call left to mislabel
    assert not span.abandoned


def test_the_end_of_run_sweep_closes_everything_still_open(recorder):
    _start(recorder, uuid.uuid4())
    _start(recorder, uuid.uuid4())
    recorder.close_abandoned()
    assert all(s.ended and s.abandoned for s in recorder.opened)


async def test_through_the_real_middleware_a_retried_turn_beside_a_parallel_agent(recorder):
    """The case the unit tests stand for, end to end: two agents share the run's recorder; agent A's first attempt
    overruns the per-attempt deadline and is retried while agent B's call is in flight."""
    import asyncio

    from langchain.agents import create_agent
    from langchain.agents.middleware import ModelRetryMiddleware
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    from rag_wright.harness.errors import ADeadlineForOneModelTurn, ModelAttemptTooSlow

    class Model(GenericFakeChatModel):
        slow_first: bool = False
        delay_s: float = 0.0
        calls: int = 0

        async def _agenerate(self, *args, **kwargs):
            self.calls += 1
            await asyncio.sleep(5 if (self.slow_first and self.calls == 1) else self.delay_s)
            return await super()._agenerate(*args, **kwargs)

    def agent(model):
        return create_agent(model, tools=[], middleware=[
            ModelRetryMiddleware(retry_on=(ModelAttemptTooSlow,), max_retries=2, initial_delay=0.0,
                                 on_failure="error"),
            ADeadlineForOneModelTurn(0.5, bounds="one attempt", raises=ModelAttemptTooSlow)])

    a = agent(Model(messages=iter([AIMessage(content="a")] * 3), slow_first=True))
    b = agent(Model(messages=iter([AIMessage(content="b")] * 3), delay_s=0.3))  # in flight across A's retry
    config = {"callbacks": [recorder]}
    await asyncio.gather(a.ainvoke({"messages": [("user", "x")]}, config),
                         b.ainvoke({"messages": [("user", "y")]}, config))
    recorder.close_abandoned()

    abandoned = [s for s in recorder.opened if s.abandoned]
    completed = [r["generation"] for r in recorder.recorded]
    assert len(abandoned) == 1                       # only A's cut-off attempt
    assert len(completed) == 2 and None not in completed and not set(completed) & set(abandoned)
