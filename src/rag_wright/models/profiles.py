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
    # Some serving stacks cannot do SERVER-SIDE grammar-constrained structured output at all (self-hosted
    # Gemma 4 on vLLM 0.26: json_schema/json_mode run away to max_model_len; function_calling needs a buggy
    # tool-parser -- while FREE-TEXT is perfect). Flag those models so generation uses the CLIENT-SIDE
    # free-text + tag-parse path (answer_generator.answer_model_for) instead of the guided-decoding seam.
    client_side_structured: bool = False
    # Applied by the seam only to the forced structured call, never to the base client.
    structured_extra_body: Optional[dict[str, Any]] = Field(default=None)
    # The free-text counterpart to `structured_extra_body`: applied by the seam to the FREE-TEXT generation path
    # (`astream_text` / the client-side tag-parse path), never to a forced structured call. A reasoning model must
    # have its reasoning EXPLICITLY set on this path too -- leaving it unset falls to the provider default, which
    # for qwen3.8 on the streaming path is inconsistent (intermittently returns reasoning-only / empty content).
    # `structured_extra_body` does not reach here (it binds only to `with_structured_output`), so this is a separate
    # slot; it also lets the free-text extraction use a DIFFERENT reasoning setting than the forced-structured judge.
    text_extra_body: Optional[dict[str, Any]] = Field(default=None)
    # Applied by the seam to the BASE client (every call to this model). Carries request-level provider
    # routing (e.g. OpenRouter `{"provider": {"sort": "throughput"}}`) -- a provider flag, so it lives in
    # config + a dated ADR, never in node/agent code (ADR-0027). Grounded: `extra_body` is a real
    # `BaseChatOpenAI` field for exactly this purpose.
    extra_body: Optional[dict[str, Any]] = Field(default=None)

    # ADR-0100: the profile also carries HOW to REACH the model, so a model STRING fully describes both the
    # model and its access -- the engine builds the client from this, and callers only need the string. A string
    # can pin a backend (mix OpenRouter + self-hosted vLLM/Modal by using different strings), or leave `backend`
    # None to fall back to the global `RAG_SERVING` default (back-compat).
    backend: Optional[Literal["openrouter", "vllm", "ollama"]] = None  # None -> RAG_SERVING default
    # the id the backend actually expects (OpenRouter slug / vLLM `--served-model-name`), often != our string.
    served_model_id: Optional[str] = None  # default = model_id
    base_url_env: Optional[str] = None  # env var holding the base_url; default per backend (VLLM_BASE_URL, ...)
    api_key_env: Optional[str] = None   # env var holding the api key; default per backend (VLLM_API_KEY, ...)


class ModelRole(str, Enum):
    """Which model does which job. The mapping to ids lives in config, not in capability code."""

    STRUCTURED_REASONING = "structured_reasoning"  # forced schema under reasoning: extraction, grading, synthesis
    STRUCTURED_REASONING_SECONDARY = "structured_reasoning_secondary"  # same call class, selectable fallback
    GENERAL = "general"  # reasoning, generation, vision-to-text, RLM; the local-deployment default
    SUMMARIZATION = "summarization"  # a smaller model for chunking and summarization (FR-I.6 tiering)
    OKF_ENRICHMENT = "okf_enrichment"  # cheap classify + one-line description for OKF signposts (FR-K.2, ADR-0023)
    # issue 0005: the ingest clause-function classifier as its OWN role, so it can run on a different model than
    # GENERAL (e.g. Gemma-4) without moving the other stages. Route (b) is CLIENT-SIDE tag-parse -> works on any
    # model. Defaults to the product LLM (unchanged behavior); set RAG_MODEL_FUNCTION_CLASSIFY to override.
    FUNCTION_CLASSIFY = "function_classify"
    # 0009-VLM: VLM-based OCR escalation for degraded scans (via docling ApiVlmOptions -> OpenRouter). Defaults to
    # Gemma-4 (a vision model, unlike the Granite product LLM); set RAG_MODEL_VISION_OCR to swap the model.
    VISION_OCR = "vision_ocr"


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

# MS1-2 (ADR-0039): the product substrate is a SINGLE self-hosted model on the A100. EVERY role defaults to
# Granite -- Gemma/DeepSeek are DROPPED from the product default but stay REGISTERED in `PROFILES` below, so a
# dev run can still select any of them via the `RAG_MODEL_*` / `RAG_MODEL_ALL` env overrides. The DEFAULT_*
# constants above are kept as those foundation-model profile keys + documented dev-override values. (OKF
# signpost enrichment -- the one-time ADR-0023 Gemma exception -- is NOT wired into the ingestion/query
# pipeline (only `okf/enrich.py`), so it too defaults to Granite; ADR-0023's Gemma choice is now vestigial.)
_PRODUCT_LLM = "ibm-granite/granite-4.2-8b"

_ROLE_ENV: dict[ModelRole, tuple[str, str]] = {
    ModelRole.STRUCTURED_REASONING: ("RAG_MODEL_STRUCTURED_REASONING", _PRODUCT_LLM),
    ModelRole.STRUCTURED_REASONING_SECONDARY: ("RAG_MODEL_STRUCTURED_REASONING_SECONDARY", _PRODUCT_LLM),
    ModelRole.GENERAL: ("RAG_MODEL_GENERAL", _PRODUCT_LLM),
    ModelRole.SUMMARIZATION: ("RAG_MODEL_SUMMARIZATION", _PRODUCT_LLM),
    ModelRole.OKF_ENRICHMENT: ("RAG_MODEL_OKF_ENRICHMENT", _PRODUCT_LLM),
    ModelRole.FUNCTION_CLASSIFY: ("RAG_MODEL_FUNCTION_CLASSIFY", _PRODUCT_LLM),
    # 0009-VLM: defaults to Gemma-4 (a vision model), NOT the Granite product LLM -- OCR needs vision.
    ModelRole.VISION_OCR: ("RAG_MODEL_VISION_OCR", DEFAULT_GENERAL),
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
    # Gemma 4, like Qwen, rejects/returns-None on forced structured output when its reasoning mode is on for
    # richer schemas (nullable fields): the CU-C1 NL->type emit returned None on every call until reasoning was
    # disabled on the forced structured call. Same fix as the secondary (structured-only, so free-text/reasoning
    # calls -- and the two-step reason node -- are untouched); simpler schemas (chunking _BoundaryList/_Summary)
    # verified still valid with it. Empirical, dated: CU-D2 / ADR-0032.
    DEFAULT_GENERAL: ModelProfile(
        model_id=DEFAULT_GENERAL,
        structured_extra_body={"reasoning": {"enabled": False}},
    ),
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
    # The ADOPTED default (2026-09-04): DeepSeek V4 Flash via OpenRouter's auto-updating `~...-latest` alias.
    # It does reasoning + structured output together (function_calling, no thinking-disable needed) and returns
    # content directly. The `~...-latest` alias otherwise routes to a SLOW/flaky provider (measured 6.9s vs 1.0s),
    # so pin `provider.sort=latency` -- OpenRouter routes to the lowest-latency provider (applied to every seam
    # call; the docling-graph extraction path injects the same routing separately in `dg_extraction`). Replaces the
    # de-listed `ibm-granite/granite-4.1-8b` (OpenRouter 404: no endpoints); granite-4.2-8b needed reasoning
    # disabled to avoid empty content, so DeepSeek Flash is the lower-friction default.
    # Product default (ADR-0079). Granite-4.1-8b was de-listed on OpenRouter (404); granite-4.2-8b is its direct
    # successor and the replacement default -- same family, same reasoning-off handling, and RELIABLE on the
    # docling-graph extraction path (measured 5/5 clean clause extractions vs deepseek-v4-flash's flaky ~2/5,
    # which produced "no models" a large fraction of the time regardless of provider). `reasoning:{enabled:false}`
    # is LOAD-BEARING: granite-4.2 is a reasoning model and returns empty `content` on a forced structured call
    # unless reasoning is disabled. `provider:{sort:latency}` picks the fastest of its (few) providers.
    "ibm-granite/granite-4.2-8b": ModelProfile(
        model_id="ibm-granite/granite-4.2-8b",
        extra_body={"provider": {"sort": "latency"}, "reasoning": {"enabled": False}},
    ),
    # Qwen 3.8 27b: UNLIKE qwen3.7-plus, it ACCEPTS a forced `tool_choice` object/required WHILE reasoning is ON
    # (measured 2026-09-08), so we keep its deep reasoning on the single-shot forced-structured path (the compliance
    # judge) -- that reasoning is the point of picking Qwen. `structured_extra_body={"reasoning":{"enabled":True}}`
    # EXPLICITLY forces reasoning on the forced structured call: leaving it UNSET falls to the provider default,
    # which for a forced tool call does little/no reasoning (measured ~5s + shallow vs ~32s deep) -- the opposite of
    # the intended "reasoning-on, accept the latency" choice. Measured trade-offs on the judge: reasoning-ON ~32s
    # (deep, chosen), reasoning-OFF ~5s (shallow).
    # `text_extra_body={"reasoning":{"enabled":False}}` (issue 0020): the FREE-TEXT/tag-parse path (query constraint
    # extraction) needs reasoning set EXPLICITLY too -- unset, qwen3.8 on the streaming path intermittently returns
    # empty content (~1/3 of runs, measured), dropping the query's constraints. OFF (not ON) because constraint
    # extraction is mechanical: OFF is deterministic + cheaper and drops the redundant raw-phrase `cap_quantum` that
    # reasoning-ON adds. So the judge reasons deeply while query extraction does not -- two settings, two slots.
    # `provider:{sort:throughput}` dodges the cheapest-provider throttle (ADR-0027).
    "qwen/qwen3.8-27b": ModelProfile(
        model_id="qwen/qwen3.8-27b",
        structured_extra_body={"reasoning": {"enabled": True}},
        text_extra_body={"reasoning": {"enabled": False}},
        extra_body={"provider": {"sort": "throughput"}},
    ),
    # ADR-0100: backend-PINNED strings for the same Qwen3.8-27B -- pick the string, get the backend. `-or` routes
    # to OpenRouter (slug qwen/qwen3.8-27b, its provider-routing + reasoning-field flags); `-modal` routes to the
    # self-hosted vLLM server (served-name Qwen/Qwen3.8-27B, reasoning via vLLM's `chat_template_kwargs`, and NO
    # OpenRouter `provider` routing). The product uses one string; mixing backends per stage is just two strings.
    "qwen3.8-27b-or": ModelProfile(
        model_id="qwen3.8-27b-or", backend="openrouter", served_model_id="qwen/qwen3.8-27b",
        structured_extra_body={"reasoning": {"enabled": True}},
        text_extra_body={"reasoning": {"enabled": False}},
        extra_body={"provider": {"sort": "throughput"}},
    ),
    "qwen3.8-27b-modal": ModelProfile(
        model_id="qwen3.8-27b-modal", backend="vllm", served_model_id="Qwen/Qwen3.8-27B",
        # vLLM controls Qwen3 reasoning via chat_template_kwargs (enable_thinking), not OpenRouter's reasoning
        # field; base_url/key come from VLLM_BASE_URL/VLLM_API_KEY (override with base_url_env for a 2nd server).
        structured_extra_body={"chat_template_kwargs": {"enable_thinking": True}},
        text_extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    ),
    # Kimi-k3 (Moonshot) shows the same `function_calling` degeneracy as granite (empty structured result on
    # some queries); `json_schema` fixes it. Registered only for the KG-6 query-side model comparison (not
    # adopted). Empirical, KG-6 / ADR-0034.
    "moonshotai/kimi-k3": ModelProfile(
        model_id="moonshotai/kimi-k3",
        structured_method="json_schema",
    ),
    # Self-hosted Gemma 4 QAT on vLLM: forced `function_calling` returns HTTP 400 ("tool_choice=function ...
    # requires --tool-call-parser to be set") -- vLLM won't honor a forced named tool without a tool-call
    # parser. `json_schema` routes through vLLM's NATIVE guided decoding (xgrammar), which needs no parser.
    # Note this is a DIFFERENT id from the OpenRouter `google/gemma-4-31b-it` profile above (which uses the
    # OpenRouter-only `{"reasoning": {"enabled": False}}` extra_body -- inapplicable to vLLM). Empirical, dated
    # 2026-08-08: GATE-2 400 on function_calling, clean on json_schema. Candidate self-hosted GENERAL model.
    # NOTE (2026-08-08, dated finding): self-hosted Gemma 4 on vLLM 0.26 could NOT do reliable GRAMMAR-
    # CONSTRAINED structured output -- BOTH `json_schema` and `json_mode` (xgrammar guided decoding) RUN AWAY
    # to max_model_len instead of emitting EOS, while FREE-TEXT generation terminates perfectly (1s, coherent,
    # correct, finish=stop) and even emits inline [chunk_id] citations. function_calling needs
    # --tool-call-parser gemma4 (concurrency <pad> bug, vllm#39392). So structured output on this stack is the
    # open problem, NOT model quality/VRAM. Path forward: generate the answer as FREE-TEXT and extract the
    # GeneratedAnswer (citations, abstained) DETERMINISTICALLY from it, bypassing guided decoding. `json_schema`
    # kept here to match the Granite precedent; it does NOT yet work for this model on vLLM.
    "google/gemma-4-31B-it-qat-w4a16-ct": ModelProfile(
        model_id="google/gemma-4-31B-it-qat-w4a16-ct",
        structured_method="json_schema",  # unused: server-side guided decoding runs away on this stack
        client_side_structured=True,      # -> free-text + client-side tag parse instead
    ),
    "google/gemma-4-26B-A4B-it": ModelProfile(
        model_id="google/gemma-4-26B-A4B-it",
        structured_method="json_schema",
        client_side_structured=True,
    ),
}


def model_for(role: ModelRole) -> str:
    """Resolve a role to a model id. Precedence (all config, MS1-2): the ROLE-SPECIFIC override
    (`RAG_MODEL_<ROLE>`) > the ALL-ROLES override (`RAG_MODEL_ALL`, to point every role at one model for a
    quick cross-model test) > the documented default. So a run can swap one role, or every role, purely by env.
    """
    env_var, default = _ROLE_ENV[role]
    return os.getenv(env_var) or os.getenv("RAG_MODEL_ALL") or default


def profile_for(model_id: str) -> ModelProfile:
    """The registered profile for a model id, or a safe default profile for an unregistered one."""
    return PROFILES.get(model_id, ModelProfile(model_id=model_id))
