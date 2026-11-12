"""The agent's model: our clocks on the engine's client, and what a call really cost.

Ported from RuleWright under ADR-0139's handover (PS-48b); the substrate is `rag_wright.harness.models`. RuleWright's
tests of its own model setting and its worker ceiling stay in RuleWright, and its source-reading check that a prompt
is sent only at the `verbose` trace level is covered here by `tests/models/test_tracing.py`, on behaviour.

**The engine builds the model** (`api.agent_chat_model`); the harness passes only its clocks -- the timeout and
the retry count, measured against this harness's latency distribution. `real_cost_of` and `GenerationRecorder`
read the figure off the response and write nothing when nothing is reported: **a wrong cost is worse than no
cost**.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from rag_wright.harness.models import GenerationRecorder, real_cost_of
from tests.harness.fabric import _settings

#: A real engine profile, built with a dummy key: constructing the client reaches no network.
PROFILE = "qwen3.8-27b-modal-or"


def _overrides_handed_to_the_engine(monkeypatch) -> dict:
    """What the harness passes the ENGINE when it asks for a model.

    **The clocks are the only part of the model that is the harness's**, so this is the contract worth
    guarding: the engine owns the backend, endpoint, key, served id and reasoning dialect, and the harness owns
    the timeout and the retry count. `test_THE_TIMEOUT_REACHES_THE_CLIENT_AND_NOT_ONLY_THE_KWARGS` does the
    end-to-end half against a real build.
    """
    from rag_wright.harness import models as harness_models

    seen: dict = {}

    def spy(model_id: str, **overrides: Any) -> Any:
        seen.update(overrides, model_id=model_id)
        return object()

    monkeypatch.setattr(harness_models.api, "agent_chat_model", spy)
    monkeypatch.setattr(harness_models, "_disable_the_catch_all_subagent", lambda _m: None)
    harness_models.agent_chat_model(_settings(), PROFILE)
    return seen


def result(*, llm_output=None, generation_info=None) -> LLMResult:
    return LLMResult(
        generations=[[ChatGeneration(message=AIMessage(content="hi"), generation_info=generation_info)]],
        llm_output=llm_output,
    )


def _recorder(monkeypatch) -> tuple[GenerationRecorder, dict]:
    emitted: dict = {}
    monkeypatch.setattr("rag_wright.harness.models.record_generation", lambda s, **kwargs: emitted.update(kwargs))
    return GenerationRecorder(_settings()), emitted


def test_the_cost_is_found_where_a_NON_STREAMED_call_puts_it() -> None:
    assert real_cost_of(result(llm_output={"token_usage": {"cost": 0.00042}})) == 0.00042


def test_the_cost_is_found_where_a_STREAMED_call_puts_it() -> None:
    """**Engine issue 0021.** `ainvoke` preserves raw usage in `response_metadata`; streaming does not, and
    the figure survives only on the final generation's info."""
    assert real_cost_of(result(generation_info={"usage": {"cost": 0.00017}})) == 0.00017


def test_NO_reported_cost_means_NO_cost_written() -> None:
    """A wrong cost is worse than no cost. Langfuse prices from its table, and a total built without it is a
    floor."""
    assert real_cost_of(result()) is None
    assert real_cost_of(result(llm_output={"token_usage": {"input_tokens": 10}})) is None


def test_the_recorder_emits_a_generation_with_the_real_cost(monkeypatch) -> None:
    """One generation per completed call, carrying usage, the provider's own cost, and the basis that cost was
    measured on."""
    recorder, emitted = _recorder(monkeypatch)
    recorder.on_llm_start({}, [], run_id="r1")
    recorder.on_llm_end(result(llm_output={"token_usage": {"cost": 0.0009, "prompt_tokens": 97,
                                                           "completion_tokens": 18}}), run_id="r1")

    assert emitted["cost"] == 0.0009
    assert emitted["usage"] == {"input": 97, "output": 18}
    assert emitted["metadata"]["cost_basis"] == "per-call"
    assert emitted["latency_ms"] is not None, "a per-second deployment needs the latency"
    assert recorder.costs == [0.0009]


def test_a_SELF_HOSTED_call_is_tagged_PER_SECOND_with_no_cost(monkeypatch) -> None:
    """**Without the tag, an unpriced run is indistinguishable from a free one.** A self-hosted deployment
    reports no per-call cost because it bills by the second; the basis is read off the observation (no cost
    reported), never off the endpoint's URL."""
    recorder, emitted = _recorder(monkeypatch)
    recorder.on_llm_start({}, [], run_id="r1")
    recorder.on_llm_end(result(llm_output={"token_usage": {"prompt_tokens": 5, "completion_tokens": 5}}),
                        run_id="r1")

    assert emitted["cost"] is None
    assert emitted["metadata"]["cost_basis"] == "per-second"
    assert emitted["latency_ms"] >= 0.0
    assert emitted["usage"] == {"input": 5, "output": 5}


def test_tokens_are_found_when_only_USAGE_METADATA_carries_them() -> None:
    """Streaming puts them on the message rather than in `llm_output`, and a generation with no tokens is a run
    that looks free."""
    from rag_wright.harness.models import token_usage_of

    message = AIMessage(content="hi", usage_metadata={"input_tokens": 11, "output_tokens": 4, "total_tokens": 15})

    assert token_usage_of(LLMResult(generations=[[ChatGeneration(message=message)]])) == {"input": 11, "output": 4}


def test_a_BROKEN_emit_never_breaks_the_model_call(monkeypatch) -> None:
    """Planted. Telemetry degrades to silence, never to a failed answer."""
    def explode(*a, **k):
        raise RuntimeError("langfuse is down")

    monkeypatch.setattr("rag_wright.harness.models.record_generation", explode)

    recorder = GenerationRecorder(_settings())
    recorder.on_llm_start({}, [], run_id="r1")
    recorder.on_llm_end(result(llm_output={"token_usage": {"cost": 0.5}}), run_id="r1")


def test_the_PRIVATE_resolver_we_depend_on_still_exists() -> None:
    """**A guarded private import.** `create_deep_agent` resolves its model with
    `deepagents._models.resolve_model`, which passes a built model through unchanged; private means it can
    move, so this fails loudly on a version bump rather than letting a model be configured another way."""
    from deepagents._models import resolve_model

    assert callable(resolve_model)


def _messages() -> list:
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    return [[
        SystemMessage(content="You match compositions."),
        HumanMessage(content="Which blends suit outerwear?"),
        AIMessage(content="", tool_calls=[{"name": "match_compositions", "args": {"requirements": "outerwear"},
                                           "id": "c1", "type": "tool_call"}]),
        ToolMessage(content='{"matched": ["cotton-poplin-120gsm"]}', tool_call_id="c1", name="match_compositions"),
    ]]


def test_THE_PROMPT_THE_MODEL_WAS_GIVEN_IS_RECORDED(monkeypatch) -> None:
    """**The whole point.** `input` must carry the turn's messages, with each tool call's ARGUMENTS -- "it
    called a tool 23 times" is a symptom; the 23 argument values are the diagnosis."""
    recorder, emitted = _recorder(monkeypatch)
    recorder.on_chat_model_start({}, _messages(), run_id="r1", metadata={})
    recorder.on_llm_end(result(llm_output={"token_usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
                        run_id="r1")

    sent = emitted["input"]
    assert sent is not None, "the prompt was not recorded, so the trace cannot say what was asked"
    assert [m["role"] for m in sent] == ["system", "human", "ai", "tool"]
    assert sent[0]["text"].startswith("You match compositions")
    assert sent[2]["tool_calls"] == [{"name": "match_compositions", "args": {"requirements": "outerwear"}}], (
        "the tool call's arguments are missing -- a count of calls without their arguments is not evidence"
    )
    assert sent[3]["name"] == "match_compositions" and "matched" in sent[3]["text"]


def test_A_SUBAGENTS_TURN_IS_ATTRIBUTED_TO_THE_SUBAGENT(monkeypatch) -> None:
    """Without this, an orchestrator turn and a worker turn are the same row in the trace."""
    recorder, emitted = _recorder(monkeypatch)
    recorder.on_chat_model_start({}, _messages(), run_id="r1", metadata={"lc_agent_name": "composition_worker"})
    recorder.on_llm_end(result(llm_output={"token_usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
                        run_id="r1")

    assert emitted["metadata"]["agent"] == "composition_worker"


def test_THE_TIER_AT_THE_TOP_IS_NAMED_TOO(monkeypatch) -> None:
    """`lc_agent_name` is `None` for the agent that was invoked directly. Left as `None` it would read as
    missing data rather than as "this was the parent"."""
    recorder, emitted = _recorder(monkeypatch)
    recorder.on_chat_model_start({}, _messages(), run_id="r1", metadata={"lc_agent_name": None})
    recorder.on_llm_end(result(llm_output={"token_usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
                        run_id="r1")

    assert emitted["metadata"]["agent"] == "the tier invoked directly"


def test_A_CALL_WITH_NO_RECORDED_START_STILL_EMITS(monkeypatch) -> None:
    """A generation whose start was missed must not vanish: the cost is the thing that cannot be lost."""
    recorder, emitted = _recorder(monkeypatch)
    recorder.on_llm_end(result(llm_output={"token_usage": {"cost": 0.002, "prompt_tokens": 1,
                                                           "completion_tokens": 1}}), run_id="never")

    assert emitted["cost"] == 0.002
    assert emitted["input"] is None


def test_THE_MODEL_HAS_A_REQUEST_TIMEOUT(monkeypatch) -> None:
    """**Without this, nothing above can time a run out.** A per-worker ceiling is a promise the layer below
    has to be able to keep."""
    from rag_wright.harness.models import MODEL_REQUEST_TIMEOUT_S

    assert _overrides_handed_to_the_engine(monkeypatch)["timeout"] == MODEL_REQUEST_TIMEOUT_S, (
        "the harness did not pass its timeout to the engine, so the model is built with the engine's own default "
        "instead of the one measured against this harness's latency distribution"
    )
    assert 0 < MODEL_REQUEST_TIMEOUT_S <= 120, (
        "a healthy agent turn has been measured at 1-25 seconds; a ceiling far above that stops being a stall "
        "detector"
    )


def test_A_STALLED_CALL_IS_RETRIED_IN_EXACTLY_ONE_PLACE(monkeypatch) -> None:
    """**Once, in a place a reader can find.** Retries used to happen inside the SDK AND in
    `ModelRetryMiddleware`; the two multiply, so three middleware attempts were six socket attempts. The SDK's
    count is zero and the policy lives in the middleware, where it has a named `retry_on`."""
    from rag_wright.harness.errors import MODEL_RETRY_LIMIT
    from rag_wright.harness.models import MODEL_RETRIES

    assert _overrides_handed_to_the_engine(monkeypatch)["max_retries"] == MODEL_RETRIES
    assert MODEL_RETRIES == 0, "the SDK is retrying behind the retry middleware's back again"
    assert 1 <= MODEL_RETRY_LIMIT <= 3, "the middleware is now the only retry policy; it must have one"


def test_THE_TIMEOUT_REACHES_THE_CLIENT_AND_NOT_ONLY_THE_KWARGS(monkeypatch) -> None:
    """**Anti-vacuity, and it is the whole point.** Asserting the kwarg is not asserting the HTTP client got
    it -- and the client is where a socket read is actually abandoned. This builds the real model the way the
    agent builds it; the credentials reach the engine through its environment, as a product sets them."""
    from rag_wright.harness.models import MODEL_REQUEST_TIMEOUT_S, agent_chat_model

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    model = agent_chat_model(_settings(), PROFILE)

    assert model.request_timeout == MODEL_REQUEST_TIMEOUT_S, (
        "the timeout never reached ChatOpenAI, so the socket read is still uncancellable"
    )
    assert model.max_retries is not None
    assert model.root_async_client.timeout is not None, (
        "the ASYNC client has no timeout -- every agent run is async, so this is the one that matters"
    )


def test_A_CALL_CAN_BE_TRACED_BACK_TO_ITS_ENDPOINT() -> None:
    """**A pin nobody can verify is a preference.** OpenRouter returns the serving provider at the top level of
    the body and the OpenAI-compatible client drops it; the generation id survives, and
    `/api/v1/generation?id=...` reports the endpoint after the fact."""
    from rag_wright.harness.models import generation_id_of

    answered = LLMResult(generations=[[ChatGeneration(
        message=AIMessage(content="x", response_metadata={"id": "gen-1789871513-XmoopUQKL2"}))]])
    assert generation_id_of(answered) == "gen-1789871513-XmoopUQKL2"

    silent = LLMResult(generations=[[ChatGeneration(message=AIMessage(content="x"))]], llm_output={})
    assert generation_id_of(silent) is None, (
        "an absent id must read as unknown, never as a default -- a failure that renders as a plausible value"
    )
