"""Provision-boundary decision: deterministic-first, with a decision-model (Jev) fallback for the UNCERTAIN residue.

`provision_boundary_verdict` (``spans.segment``) classifies each span deterministically into ``start`` / ``continue``
/ ``uncertain``. The ``uncertain`` residue -- short, plausibly-heading lines in styles the deterministic rules do not
confidently classify (a new document convention, a colon/odd heading) -- is adjudicated by a pluggable async decider,
the ``jev_decision`` capability by default (invoked THROUGH the engine invoker, one batched call for the whole
residue). The decider is OPTIONAL: with no decision model configured, or on any error, an uncertain span GRACEFULLY
DEGRADES to "not a new provision" (the prior deterministic behavior) -- so ingestion never requires a decision model
and the hermetic tests stay offline. This is the neuro-symbolic shape (ADR-0040): a cheap, exact symbolic layer for
the clear majority + a model only for the ambiguous part -- flexible where regex is rigid, bounded in cost.
"""
from __future__ import annotations

import os
from typing import Any, Awaitable, Callable, Optional

from rag_wright.spans.segment import provision_boundary_verdict

# A residue decider: candidate texts (the UNCERTAIN spans) -> a start flag each (True = begins a new provision).
BoundaryDecider = Callable[[list[str]], Awaitable[list[bool]]]

_THRESHOLD = 0.5


async def adecide_provision_starts(
    texts: list[str], *, decider: Optional[BoundaryDecider] = None
) -> list[bool]:
    """Per-span "starts a new provision?" flags, aligned to ``texts``. Deterministic ``start``/``continue`` are
    decided for free; only the ``uncertain`` residue is passed to ``decider`` (one batched call). No decider, or a
    decider error, degrades the residue to False (fold in) -- never an exception, never a lost span."""
    verdicts = [provision_boundary_verdict(t) for t in texts]
    starts = [v == "start" for v in verdicts]
    residue = [i for i, v in enumerate(verdicts) if v == "uncertain"]
    if residue and decider is not None:
        try:
            decided = await decider([texts[i] for i in residue])
        except Exception:  # noqa: BLE001 - a decision-model blip must not fail ingestion; degrade to deterministic
            decided = [False] * len(residue)
        for i, flag in zip(residue, decided):
            starts[i] = bool(flag)
    return starts


def jev_boundary_decider(resources: Any = None) -> Optional[BoundaryDecider]:
    """Build the Jev-backed residue decider, or ``None`` when no decision model is configured/registered (then the
    boundary stays purely deterministic). It invokes the ``jev_decision`` capability THROUGH the engine invoker --
    one batched call with all candidate lines in the ``state`` and one ``noul`` question per line."""
    from rag_wright.api import ainvoke_model, capability_index
    from rag_wright.models.profiles import decision_profile

    model = os.environ.get("RAG_DECISION_MODEL")
    prof = decision_profile(model)
    if not os.environ.get(prof.api_key_env) or "jev_decision" not in capability_index():
        return None  # no decision model available -> deterministic-only

    async def _decide(texts: list[str]) -> list[bool]:
        state = "\n".join(f"[{i}] {t.strip()}" for i, t in enumerate(texts))
        questions = {
            f"c{i}": {
                "type": "noul",
                "instructions": (
                    f"In the numbered lines above, does line [{i}] BEGIN a new numbered section or provision of a "
                    "contract (a section heading / start), rather than continue the previous provision's text?"
                ),
                "criteria": {
                    "true": f"line [{i}] begins a new section or provision",
                    "false": f"line [{i}] continues the current provision",
                },
            }
            for i in range(len(texts))
        }
        out = await ainvoke_model(
            "jev_decision", {"state": state, "questions": questions, "model": model}, resources=resources
        )
        answers = out.get("answers", {}) if isinstance(out, dict) else {}
        return [float((answers.get(f"c{i}") or {}).get("noul", 0.0)) >= _THRESHOLD for i in range(len(texts))]

    return _decide
