"""ADR-0119: a generic Jev typed-decision capability over the OpenRouter Decisions API.

Jev (TypeSafe's "System-1" decision model) returns CALIBRATED typed answers -- a yes/no (`noul`), a `choice` from
a set, or a `score` -- for a `state` + typed `questions`, with no generated text, in ~70-500 ms. This capability
is DOMAIN-FREE mechanism: it just forwards a Decisions-API request and returns the typed answers. The compliance
reference domain uses it for the operative-rule gate + claim_types/actor (ADR-0119, where it reaches the LLM's
accuracy zero/few-shot, calibrated, at ~$0.00002/call); any domain can use it for routing / tagging / screening.

It is I/O-bound (a network call), so it is an ASYNC `model` capability -- invoke via `api.ainvoke_model` (the sync
`api.invoke_model` refuses an async impl). Laya (`laya` skill) is the open-weight / on-prem fallback.
"""
from __future__ import annotations

import os
import time
from typing import Any


async def jev_decision(resources: Any, inputs: dict) -> dict:  # noqa: ARG001 - API model, store-independent
    """Invoke a typed-decision model (Jev). `inputs`: `state` (the text/object to judge), `questions` (dict keyed
    by question id, each `{type: "noul"|"choice"|"score", instructions, criteria}`), optional `model` (a
    DecisionModelProfile key, default `jev-1.13`). The endpoint, served id, key env and timeout come from the
    decision-model PROFILE (`models.profiles.decision_profile`, ADR-0119) -- not hardcoded here -- so swapping
    Jev versions or pointing at an on-prem Laya decisions server is config. Returns the Decisions-API body
    `{answers: {<id>: {type, noul|choice|score, ...}}, usage: {...}}`. Raises if the key env is unset or the API errors."""
    import httpx

    from rag_wright.models.profiles import decision_profile
    from rag_wright.models.usage import record_usage

    prof = decision_profile(inputs.get("model"))
    api_key = os.environ.get(prof.api_key_env)
    if not api_key:
        raise RuntimeError(f"jev_decision requires {prof.api_key_env}")
    body = {"model": prof.served, "state": inputs["state"], "questions": inputs["questions"]}
    timeout = float(os.environ.get("RAG_JEV_TIMEOUT_S", str(prof.timeout_s)))
    started = time.perf_counter()
    async with httpx.AsyncClient() as client:
        r = await client.post(prof.endpoint, headers={"Authorization": f"Bearer {api_key}"}, json=body, timeout=timeout)
        r.raise_for_status()
        out = r.json()
    # ING-4c: meter the call like any model call (the endpoint reports its own usage + cost), so a paid decision is
    # never invisible to `measure_usage`; a response with no cost counts as uncosted, never as free.
    usage = out.get("usage") or {} if isinstance(out, dict) else {}
    record_usage(prof.model_id, input_tokens=int(usage.get("input_tokens") or 0),
                 output_tokens=int(usage.get("output_tokens") or 0), cost=usage.get("cost"),
                 latency_ms=(time.perf_counter() - started) * 1000)
    return out
