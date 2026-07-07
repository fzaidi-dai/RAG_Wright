"""The single construction point for the model client (T11).

Every model is built here, and `with_structured_output` is reached only here. A capability names a
model id (via `profiles.model_for(role)`) and asks the seam for a plain client or a
structured-output runnable; it never constructs `ChatOpenAI` or passes a provider/model flag itself.
The structured-only `extra_body` from the model's profile is applied to the forced structured call
alone, so free-text and reasoning calls on the same model are unaffected (CLAUDE.md standing rule).

Grounded against `langchain_openai.chat_models.base` (ADR-0001): `ChatOpenAI` takes `model`,
`temperature`, `api_key`, `base_url`; `with_structured_output(schema, method=..., **kwargs)` forwards
kwargs into the tool binding, so `extra_body` passed here binds to the structured runnable only.
"""

from __future__ import annotations

import os
from typing import Any

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from rag_wright.models.profiles import profile_for


def _openrouter_config() -> dict[str, Any]:
    """OpenRouter connection config from env (default serving path; secrets only in `.env`)."""
    return {
        "api_key": os.environ["OPENROUTER_API_KEY"],
        "base_url": os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    }


def build_model(model_id: str, *, temperature: float = 0.0, **overrides: Any) -> ChatOpenAI:
    """Construct the base client for `model_id`. Carries no structured-output flag or `extra_body`."""
    return ChatOpenAI(
        model=model_id,
        temperature=temperature,
        **_openrouter_config(),
        **overrides,
    )


def build_structured(
    model_id: str, schema: Any, *, include_raw: bool = False
) -> Runnable:
    """A structured-output runnable for `model_id`, driven by its profile.

    The profile supplies the method and the optional structured-only `extra_body`; the `extra_body`
    is bound to this forced structured call only. This is the sole path to `with_structured_output`.
    """
    profile = profile_for(model_id)
    kwargs: dict[str, Any] = {"method": profile.structured_method, "include_raw": include_raw}
    if profile.structured_extra_body is not None:
        kwargs["extra_body"] = profile.structured_extra_body
    return build_model(model_id).with_structured_output(schema, **kwargs)
