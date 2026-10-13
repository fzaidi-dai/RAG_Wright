"""PS-32: the tracing helpers a caller that emits its own generations needs, on `rag_wright.api` (the agent harness
may import only `api`): the switch, the level, open/close a generation, and a flush."""
from rag_wright import api
from rag_wright.models import tracing


def test_the_helpers_are_the_engine_tracing_functions():
    for name in ("tracing_on", "trace_level", "start_generation", "finish_generation", "flush_tracing"):
        assert name in api.__all__
    assert api.tracing_on is tracing.tracing_on and api.trace_level is tracing.trace_level
    assert api.start_generation is tracing.start_generation and api.finish_generation is tracing.finish_generation


def test_flush_is_a_no_op_when_tracing_is_off(monkeypatch):
    monkeypatch.setenv("RAG_TRACE_LEVEL", "off")
    monkeypatch.setattr(tracing, "_client", None)
    monkeypatch.setattr(tracing, "_client_tried", False)
    api.flush_tracing()  # never raises, builds no client


def test_flush_flushes_the_engine_client(monkeypatch):
    flushed = []

    class Client:
        def flush(self):
            flushed.append(1)

    monkeypatch.setattr(tracing, "_get_client", lambda: Client())
    api.flush_tracing()
    assert flushed == [1]
