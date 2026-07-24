"""Per-model profiles and role resolution for the model seam (T11).

A profile is keyed by model id and carries only what the seam needs to make a *structured-output*
call safely: the structured-output method (default `function_calling`, more broadly supported across
open models than `json_schema`) and an optional structured-only `extra_body`. The `extra_body` is
applied by the seam only to the forced structured call (for example to disable thinking on the
structured emit), leaving free-text and reasoning calls unaffected. Provider/model flags live here
in config and in a dated ADR, never in a capability or node call site (CLAUDE.md standing rule).

Model priority for the structured-output-under-reasoning call class is a config decision, not
capability code: DeepSeek V4 Pro is the primary, Qwen 3.7 Plus the selectable secondary, and the
Gemma 4 class is the general / local-deployment default (SPEC section 4, tech stack; Phase 2 ledger).
The exact OpenRouter slug and the empirical structured profile (method + `extra_body`) for the
structured-reasoning model are confirmed against a live call at T12 and recorded in its ADR; each id
below is a documented default, overridable by env so the empirical slug needs no code change.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

StructuredMethod = Literal["function_calling", "json_mode", "json_schema"]


class ModelProfile(BaseModel):
    """How a given model id must be driven for a forced structured-output call."""

    # `model_id` sits in Pydantic's protected `model_` namespace; opt out (it is a plain field).
    model_config = ConfigDict(frozen=True, protected_namespaces=())

    model_id: str
    structured_method: StructuredMethod = "function_calling"
    # Applied by the seam only to the forced structured call, never to the base client.
    structured_extra_body: Optional[dict[str, Any]] = Field(default=None)
    # Applied by the seam to the BASE client (every call to this model). Carries request-level provider
    # routing (e.g. OpenRouter `{"provider": {"sort": "throughput"}}`) -- a provider flag, so it lives in
    # config + a dated ADR, never in node/agent code (ADR-0027). Grounded: `extra_body` is a real
    # `BaseChatOpenAI` field for exactly this purpose.
    extra_body: Optional[dict[str, Any]] = Field(default=None)


class ModelRole(str, Enum):
    """Which model does which job. The mapping to ids lives in config, not in capability code."""

    STRUCTURED_REASONING = "structured_reasoning"  # forced schema under reasoning: extraction, grading, synthesis
    STRUCTURED_REASONING_SECONDARY = "structured_reasoning_secondary"  # same call class, selectable fallback
    GENERAL = "general"  # reasoning, generation, vision-to-text, RLM; the local-deployment default
    SUMMARIZATION = "summarization"  # a smaller model for chunking and summarization (FR-I.6 tiering)
    OKF_ENRICHMENT = "okf_enrichment"  # cheap classify + one-line description for OKF signposts (FR-K.2, ADR-0023)


# Default model ids per role, confirmed against the live OpenRouter catalog at T12 (ADR-0006).
# Overridable by env (`_ROLE_ENV`), so a later slug change stays config, not code.
DEFAULT_STRUCTURED_REASONING = "deepseek/deepseek-v4-pro"
DEFAULT_STRUCTURED_REASONING_SECONDARY = "qwen/qwen3.7-plus"
DEFAULT_GENERAL = "google/gemma-4-31b-it"
DEFAULT_SUMMARIZATION = "deepseek/deepseek-v4-flash"  # the smaller/faster DeepSeek (FR-I.6)
# OKF signpost enrichment is a simple classify-and-describe task; a cheap Gemma matched DeepSeek V4 Pro
# on it (100% category agreement, good one-liners, ~4x cheaper/faster) at the 2026-07-22 bench (ADR-0023).
# THIS TASK ONLY; every other call class stays on its DeepSeek/Gemma role above.
DEFAULT_OKF_ENRICHMENT = "google/gemma-4-26b-a4b-it"

_ROLE_ENV: dict[ModelRole, tuple[str, str]] = {
    ModelRole.STRUCTURED_REASONING: ("RAG_MODEL_STRUCTURED_REASONING", DEFAULT_STRUCTURED_REASONING),
    ModelRole.STRUCTURED_REASONING_SECONDARY: (
        "RAG_MODEL_STRUCTURED_REASONING_SECONDARY",
        DEFAULT_STRUCTURED_REASONING_SECONDARY,
    ),
    ModelRole.GENERAL: ("RAG_MODEL_GENERAL", DEFAULT_GENERAL),
    ModelRole.SUMMARIZATION: ("RAG_MODEL_SUMMARIZATION", DEFAULT_SUMMARIZATION),
    ModelRole.OKF_ENRICHMENT: ("RAG_MODEL_OKF_ENRICHMENT", DEFAULT_OKF_ENRICHMENT),
}

# Registered profiles keyed by model id. A model without an entry falls back to the safe default
# (function_calling, no extra_body) via `profile_for`, so no call site special-cases a model.
# Structured-output methods and extra bodies below are empirical, confirmed by a live forced-schema
# call at T12 and recorded in ADR-0006.
PROFILES: dict[str, ModelProfile] = {
    # DeepSeek V4 Pro honors the forced tool call while reasoning; no thinking-disable needed. Route by
    # THROUGHPUT so OpenRouter prefers the fastest provider over the cheapest (which throttled the bulk
    # property extraction, T58); keeps V4 Pro, model rule intact (ADR-0027).
    DEFAULT_STRUCTURED_REASONING: ModelProfile(
        model_id=DEFAULT_STRUCTURED_REASONING,
        extra_body={"provider": {"sort": "throughput"}},
    ),
    # Qwen 3.7 Plus rejects `tool_choice` object/required in thinking mode ("<400> ... does not
    # support being set to required or object in thinking mode"); disabling reasoning on the forced
    # structured call alone fixes it, leaving its free-text/reasoning calls untouched (ADR-0006).
    DEFAULT_STRUCTURED_REASONING_SECONDARY: ModelProfile(
        model_id=DEFAULT_STRUCTURED_REASONING_SECONDARY,
        structured_extra_body={"reasoning": {"enabled": False}},
    ),
    DEFAULT_GENERAL: ModelProfile(model_id=DEFAULT_GENERAL),
    # DeepSeek V4 Flash does reasoning + structured output together, like V4 Pro (ADR-0006); no
    # thinking-disable needed. As the T58 BULK property extractor (Flash->Pro cascade, ADR-0028) it routes
    # by THROUGHPUT too, to dodge the cheapest-provider throttle (ADR-0027).
    DEFAULT_SUMMARIZATION: ModelProfile(
        model_id=DEFAULT_SUMMARIZATION,
        extra_body={"provider": {"sort": "throughput"}},
    ),
    # Gemma 4 26b-a4b takes the forced tool call cleanly (default function_calling, no extra_body);
    # 0 structured-output errors across the 20-clause bench (ADR-0023).
    DEFAULT_OKF_ENRICHMENT: ModelProfile(model_id=DEFAULT_OKF_ENRICHMENT),
}


def model_for(role: ModelRole) -> str:
    """Resolve a role to a model id: the env override if set, else the documented default."""
    env_var, default = _ROLE_ENV[role]
    return os.getenv(env_var, default)


def profile_for(model_id: str) -> ModelProfile:
    """The registered profile for a model id, or a safe default profile for an unregistered one."""
    return PROFILES.get(model_id, ModelProfile(model_id=model_id))
