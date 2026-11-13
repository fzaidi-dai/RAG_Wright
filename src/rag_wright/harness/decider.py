"""The routing decider: a decision model asked the harness's routing questions (ADR-0146, HD-4).

**A routing decision is a classification, so it is asked of a model that classifies.** The decision model (Jev,
through the capability layer) answers a `choice` among options or a yes/no per question, calibrated, in under a
second, and its probability is what `Decision.confidence` has been waiting for. Measured on the routing gold sets
(HD-2): it matches the tiers' own model on D1 and B1 and beats it on D2.

**Unavailable is an answer, not an error.** `jev_decision` not registered (silently: that is configuration), no key,
or a failed call: the decider returns None and the caller decides as it did before there was a decider. After one
failure the run stops asking, so a missing key costs one warning per run rather than one per question.

**One record per run** (`run_key`), as the call record and the decision ledger keep theirs: the decision model flips
about 3% of answers between identical calls, so a question asked twice in one run gets the first answer again.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

logger = logging.getLogger(__name__)

#: The engine capability asked. A product registers it from `engine_capabilities()`, like any engine capability.
CAPABILITY = "jev_decision"
#: The key on a reply's `response_metadata` that carries the route the decider chose and its probability, so the
#: decision ledger can record that probability when the reply settles a decision.
ROUTED = "routed"


@dataclass(frozen=True)
class Routed:
    """A `choice` and the probability the decision model gave it."""

    choice: str
    confidence: float


@dataclass
class _RunRecord:
    answers: dict[str, dict] = field(default_factory=dict)
    unavailable: bool = False


@dataclass
class Decider:
    """Asks the decision model, caching each run's answers. `model` names a decision-model profile (None: the
    capability's default)."""

    model: str | None = None
    runs: dict[str, _RunRecord] = field(default_factory=dict)

    async def choose(self, run: str, state: str, instructions: str, options: Mapping[str, str]) -> Routed | None:
        """One option of `options` (`{option: what choosing it means}`) for `state`, or None when unavailable."""
        answers = await self._ask(run, state, {"choice": {"type": "choice", "instructions": instructions,
                                                          "criteria": dict(options)}})
        return None if answers is None else answers["choice"]

    async def yes_no(self, run: str, state: str, questions: Mapping[str, tuple[str, str]]) -> dict[str, float] | None:
        """The probability of yes for each question (`{id: (question, what a yes means)}`), asked in one call, or
        None when unavailable."""
        return await self._ask(run, state, {
            qid: {"type": "noul", "instructions": question, "criteria": {"true": yes, "false": "no"}}
            for qid, (question, yes) in questions.items()})

    async def _ask(self, run: str, state: str, questions: dict) -> dict | None:
        """Each question's answer, already read into its value: a `choice` as `Routed`, a yes/no as
        its probability. None when unavailable, including an answer that is not one of the questions' own."""
        from rag_wright.api import ainvoke_model, capability_index

        record = self.runs.setdefault(run, _RunRecord())
        if record.unavailable:
            return None
        key = json.dumps([state, questions], sort_keys=True)
        if key in record.answers:
            return record.answers[key]
        if CAPABILITY not in capability_index():  # not configured: silent, unlike a failure
            record.unavailable = True
            return None
        inputs = {"state": state, "questions": questions, **({"model": self.model} if self.model else {})}
        try:
            raw = (await ainvoke_model(CAPABILITY, inputs, resources=None))["answers"]
            # every question answered, in the shape asked, or the whole answer is unusable
            answers = {qid: _read(raw[qid], question) for qid, question in questions.items()}
        except Exception:  # noqa: BLE001 - any failure means "decide as before", never a failed turn
            logger.warning("decision_model_unavailable", exc_info=True)
            record.unavailable = True
            return None
        record.answers[key] = answers
        return answers


def _read(answer: dict, question: dict) -> Any:
    """One answer as its value, or an error when it is not an answer to `question`."""
    if question["type"] == "noul":
        return float(answer["noul"])
    choice = answer["choice"]
    if choice not in question["criteria"]:
        raise ValueError(f"{choice!r} is not one of the options")
    return Routed(choice=choice, confidence=float((answer.get("probabilities") or {}).get(choice, 0.0)))
