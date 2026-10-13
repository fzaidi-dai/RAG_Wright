"""How much a tier thinks, decided per TIER (T-5a.14d, ANFR-3, ANFR-1).

**The measurement this exists for.** Same question, same 10 tools, same model, only the `reasoning` flag
differing:

| | duration | output tokens | reasoning tokens | tool call chosen |
|---|---|---|---|---|
| reasoning ON | **34.8s** | 1,812 | **1,905** | `list_matter_documents` |
| reasoning OFF | **4.3s** | 42 | 0 | `list_matter_documents` |

**Identical decision, 8x the wall clock, 43x the output.** With no `max_tokens` cap the reasoning is
unbounded, so a chat-manager turn can outlive its own 60s attempt deadline -- and the retry reasons just
as long, so it fails again. That is how one eval item burned three consecutive attempts and died, and why
**68% of a run's wall clock** was attempts abandoned before they returned. The cost was invisible until
T-5a.14b-i made abandoned attempts appear in the trace at all.

**A profile cannot express this and a middleware can**, which is `withhold.py`'s lesson verbatim.
`register_provider_profile` keys on the MODEL, and every tier runs the same one -- so turning reasoning
off there would take it from the orchestrator too. `ModelRequest.model_settings` is bound onto the model
for one request (`factory.py` does `model.bind(**request.model_settings)`), and a middleware is attached
to a tier, so this is the one seam that can say *"this TIER thinks less"*.

**It MERGES `extra_body` rather than replacing it, and that is not tidiness.** `bind` replaces the whole
value, so writing `{"reasoning": ...}` alone would drop `provider.order` and `allow_fallbacks: False` --
unpinning the endpoint T-5a.12w pinned after measuring two different plans from a byte-identical prompt --
and drop `usage.include`, after which no per-call cost is reported at all. Nothing would fail. The
variance would simply come back wearing the pin's name.

**What this does NOT claim.** One question, one sample per arm, one tier. The orchestrator is left
thinking: its job is decomposition, §2a's hardest decisions live there, and turning it off on this
evidence would be the unmeasured change T-5a.12af was reverted for.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware


class HowMuchToThink(AgentMiddleware):
    """Tell one tier whether to reason, and optionally bound what a single turn may produce."""

    def __init__(self, *, reasoning: bool, tier: str, max_tokens: int | None = None) -> None:
        super().__init__()
        self._reasoning = reasoning
        self._tier = tier
        #: **Applied only where it has been measured.** A cap that truncates a worker's structured report
        #: is worse than no cap -- it would turn a slow answer into a wrong one, silently. So this is a
        #: parameter rather than a default, and the tiers that have not been measured carry none.
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        """Distinct per tier: `create_agent` refuses two middlewares sharing a name, and both tiers
        carry one of these."""
        return f"{type(self).__name__}({self._tier})"

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._asking_for(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._asking_for(request))

    def _asking_for(self, request: Any) -> Any:
        """This turn's request, with the tier's thinking settings folded in."""
        body = dict(getattr(request.model, "extra_body", None) or {})
        # **Speak the endpoint's own dialect, read from what it was already sent.** `_init_kwargs` has
        # two branches: OpenRouter is told `reasoning`, and a self-hosted vLLM is told
        # `chat_template_kwargs.enable_thinking` -- *"OpenRouter's `reasoning` field means nothing to
        # it"*. Writing the wrong key would turn thinking off in a way that silently does nothing, which
        # is the failure that reads as "the change had no effect" rather than as a bug.
        #
        # Detected from the body rather than from `base_url`, so there is ONE place that knows which
        # endpoint speaks which dialect and this is not a second copy of it.
        if "chat_template_kwargs" in body:
            body["chat_template_kwargs"] = {**body["chat_template_kwargs"],
                                            "enable_thinking": self._reasoning}
        else:
            body["reasoning"] = {"enabled": self._reasoning}

        settings = {**(request.model_settings or {}), "extra_body": body}
        if self._max_tokens is not None:
            settings["max_tokens"] = self._max_tokens
        return request.override(model_settings=settings)
