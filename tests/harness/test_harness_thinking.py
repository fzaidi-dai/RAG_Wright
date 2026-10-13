"""How much a tier thinks, decided per TIER (T-5a.14d, ANFR-3).

**Measured A/B on the real model, same question, same 10 tools, only the `reasoning` flag differing:**

| | duration | output tokens | reasoning tokens | tool call chosen |
|---|---|---|---|---|
| reasoning ON | **34.8s** | 1,812 | **1,905** | `list_matter_documents` |
| reasoning OFF | **4.3s** | 42 | 0 | `list_matter_documents` |

Identical decision, 8x the wall clock, 43x the output. With no `max_tokens` cap the reasoning is
unbounded, so a chat-manager turn can exceed its own 60s deadline -- and the retry reasons just as long,
which is how one eval item burned three consecutive attempts and died. 68% of that run's wall clock was
attempts abandoned this way.

**A profile cannot express this and a middleware can**, which is `withhold.py`'s lesson verbatim:
`register_provider_profile` keys on the MODEL and both tiers run the same one, so turning reasoning off
through the profile would take it from the orchestrator too -- the tier whose job is genuine decomposition
and where thinking may well earn its cost. Nothing here claims it does not; that needs its own measurement.
"""

from __future__ import annotations

from typing import Any

import pytest

from rag_wright.harness.thinking import HowMuchToThink


class _Request:
    """The parts of `ModelRequest` this middleware touches, and `override` as the library defines it."""

    def __init__(self, model: Any, model_settings: dict[str, Any] | None = None) -> None:
        self.model = model
        self.model_settings = model_settings or {}

    def override(self, **overrides: Any) -> _Request:
        settled = _Request(self.model, self.model_settings)
        for key, value in overrides.items():
            setattr(settled, key, value)
        return settled


class _Model:
    def __init__(self, extra_body: dict[str, Any] | None = None) -> None:
        self.extra_body = extra_body


PINNED = {
    "provider": {"order": ["deepinfra/bf16"], "allow_fallbacks": False},
    "reasoning": {"enabled": True},
    "usage": {"include": True},
}


def _settings_of(middleware: HowMuchToThink, extra_body: dict[str, Any] | None = PINNED) -> dict:
    seen: dict = {}

    def handler(request: Any) -> str:
        seen.update(request.model_settings)
        return "ok"

    assert middleware.wrap_model_call(_Request(_Model(extra_body)), handler) == "ok"
    return seen


def test_A_TIER_TOLD_NOT_TO_THINK_SENDS_REASONING_DISABLED() -> None:
    assert _settings_of(HowMuchToThink(reasoning=False, tier="chat-manager"))["extra_body"][
        "reasoning"] == {"enabled": False}


def test_A_TIER_TOLD_TO_THINK_SENDS_REASONING_ENABLED() -> None:
    """**The negative half.** A middleware that only ever disables would make the per-tier split a
    one-way door, and the orchestrator's thinking is not something this task measured."""
    assert _settings_of(HowMuchToThink(reasoning=True, tier="orchestrator"))["extra_body"][
        "reasoning"] == {"enabled": True}


def test_THE_ENDPOINT_PIN_SURVIVES() -> None:
    """**The failure a naive override would cause, silently.** `extra_body` is REPLACED by `bind`, so
    writing `{"reasoning": ...}` into `model_settings` would drop `provider.order` and
    `allow_fallbacks: False` -- unpinning the endpoint that T-5a.12w pinned to kill a measured variance
    (two byte-identical prompts, two different plans). Nothing would fail; the variance would simply
    return wearing the pin's name.
    """
    body = _settings_of(HowMuchToThink(reasoning=False, tier="chat-manager"))["extra_body"]

    assert body["provider"] == {"order": ["deepinfra/bf16"], "allow_fallbacks": False}
    assert body["usage"] == {"include": True}, "the per-call cost would stop being reported"


def test_A_MODEL_WITH_NO_EXTRA_BODY_STILL_WORKS() -> None:
    """A model carrying no `extra_body` at all must still be told, not skipped."""
    settings = _settings_of(HowMuchToThink(reasoning=False, tier="chat-manager"), extra_body=None)

    assert settings["extra_body"]["reasoning"] == {"enabled": False}


def test_A_SELF_HOSTED_ENDPOINT_IS_TOLD_IN_ITS_OWN_DIALECT() -> None:
    """**`_init_kwargs` has two branches and this must honour both.** A vLLM endpoint reads
    `chat_template_kwargs.enable_thinking`; *"OpenRouter's `reasoning` field means nothing to it"*.
    Writing the wrong key would turn thinking off in a way that silently does nothing -- a change that
    reads as "it made no difference" rather than as a bug.
    """
    vllm = {"chat_template_kwargs": {"enable_thinking": True}}
    body = _settings_of(HowMuchToThink(reasoning=False, tier="chat-manager"), extra_body=vllm)["extra_body"]

    assert body["chat_template_kwargs"]["enable_thinking"] is False
    assert "reasoning" not in body, "OpenRouter's spelling means nothing to a vLLM server"


def test_A_CAP_IS_SENT_ONLY_WHEN_ONE_WAS_ASKED_FOR() -> None:
    """**A cap that truncates a worker's structured report is worse than no cap**, so it is applied only
    where it has been measured, never as a blanket default."""
    capped = _settings_of(HowMuchToThink(reasoning=False, max_tokens=2048, tier="chat-manager"))
    uncapped = _settings_of(HowMuchToThink(reasoning=True, tier="orchestrator"))

    assert capped["max_tokens"] == 2048
    assert "max_tokens" not in uncapped


def test_THE_TIERS_MIDDLEWARE_NAMES_DIFFER() -> None:
    """`create_agent` refuses two middlewares sharing a name, and both tiers carry one of these."""
    assert (HowMuchToThink(reasoning=False, tier="chat-manager").name
            != HowMuchToThink(reasoning=True, tier="orchestrator").name)


@pytest.mark.parametrize("tier", ["chat-manager", "orchestrator"])
def test_NOTHING_ELSE_IN_MODEL_SETTINGS_IS_DISTURBED(tier: str) -> None:
    """Another middleware may have set something; this one adds, it does not take over."""
    middleware = HowMuchToThink(reasoning=False, tier=tier)
    seen: dict = {}

    def handler(request: Any) -> str:
        seen.update(request.model_settings)
        return "ok"

    middleware.wrap_model_call(_Request(_Model(PINNED), {"temperature": 0.0}), handler)

    assert seen["temperature"] == 0.0


# --- the wiring, which is where a per-tier setting usually dies -----------------------------------------


def test_THE_CHAT_MANAGER_IS_TOLD_NOT_TO_THINK_AND_IS_CAPPED() -> None:
    """The tier whose 1,905 reasoning tokens bought the same tool call as 42."""
    from rag_wright.harness.agent import CHAT_TURN_TOKEN_CAP, chat_thinking

    thinking = chat_thinking()

    assert thinking._reasoning is False
    assert thinking._max_tokens == CHAT_TURN_TOKEN_CAP, (
        "an uncapped turn can outlive its own deadline, which makes the deadline decorative and the "
        "retry pointless -- the measured failure mode"
    )


def test_THE_ORCHESTRATOR_STILL_THINKS() -> None:
    """**Deliberately unchanged.** This task measured ONE question on ONE tier; the orchestrator's job is
    decomposition, and §2a's hardest decisions live there. Turning its reasoning off on this evidence
    would be exactly the unmeasured change T-5a.12af was reverted for."""
    from rag_wright.harness.tiers import orchestrator_thinking

    assert orchestrator_thinking()._reasoning is True


def test_EACH_TIER_ACTUALLY_CARRIES_ITS_THINKING_SETTING() -> None:
    """**The guard the others do not give.** Everything above proves `chat_thinking()` returns the right
    object; nothing proved the builder *uses* it. Planted, and it was true: deleting the line from
    `build_chat_agent` left 42 tests green while the chat-manager went back to spending 1,905 reasoning
    tokens per turn.

    Read from the builder's source, the same way `test_EVERY_TIER_MIDDLEWARE_IS_ON_WORKERS_OR_EXPLAINED`
    reads it -- the compiled graph does not expose its middleware, and a test that walked it would be
    testing LangGraph.
    """
    import ast
    import inspect
    import textwrap

    from rag_wright.harness import agent as agent_module
    from rag_wright.harness import tiers as tiers_module

    def calls(function) -> set[str]:
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        return {node.func.id for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}

    assert "chat_thinking" in calls(agent_module.build_chat_agent), (
        "the chat-manager is built without its thinking setting, so it reverts to the profile's "
        "reasoning: 1,905 tokens to reach the tool call it reaches in 42"
    )
    assert "orchestrator_thinking" in calls(tiers_module.build_orchestrator), (
        "the orchestrator's thinking is no longer stated at its tier, so nobody can tell a decision "
        "from an oversight"
    )
