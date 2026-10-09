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
from typing import Any, Optional

from rag_wright.api import BoundaryDecider  # the shared residue-decider contract (ING-1, ADR-0124)
from rag_wright.packs.contracts.spans.segment import provision_boundary_verdict

_THRESHOLD = 0.5

# ING-4d: the residue rubric, stated ONCE in the state (each per-line question is then minimal). A start is defined by
# STRUCTURE, not topic: the earlier topical wording ("does the line continue the previous text?") made Jev call
# same-topic numbered siblings "continues" and left furniture / lead-ins / table-of-contents lines near 0.5, where
# they flipped between calls. Measured on 10 hand-labelled documents (9 contracts + a lab report, 461 lines): 99.8%
# with zero flips across 3 calls (was 94-96%, flipping) -- `eval/boundary_residue_gold.py`, ADR-0122 ING-4d.
# Domain-neutral by design; the examples are invented, not taken from the measured documents.
_RUBRIC = (
    "Each numbered item below is one line from a long document (a contract, policy, specification or report). For "
    "each item, decide whether that line STARTS A NEW SECTION.\n\n"
    "A line starts a new section only when it opens its own numbered section, subsection, article, schedule, "
    "exhibit, annex or appendix: it begins with a section number such as '7.2', '12.4.1', '3.10', 'Schedule 4.1' or "
    "'Exhibit B' (a stray page number in front, as in '31 7.2 Delivery.', does not change this), followed by a "
    "heading or the section's own text. This holds EVEN WHEN the new section is on the same topic as the text before "
    "it: every numbered sibling (7.1, 7.2, 7.3) and every numbered definition ('3.4 \"Territory\" means ...') is its "
    "own section.\n\n"
    "A line does NOT start a new section when it is any of these:\n"
    "- page furniture: a running header or footer, a page number, a filing or source stamp, a note that text was "
    "omitted or redacted;\n"
    "- a table-of-contents line: section numbers and titles each followed by a page number, often several packed on "
    "one line, such as '1 Scope 2 2 Terms 3' or '4.1 Labels 5 4.2 Packing 6';\n"
    "- a revision-history or change-log entry describing what changed in a section, such as 'Chapter 5.2 updated';\n"
    "- a reference to another section, such as '9.1 (Payment Terms) and' or 'Section 4.2 (Warranty);';\n"
    "- a lettered, roman or bracketed list item, such as '(b) ...' or '(iii) ...';\n"
    "- a lead-in sentence that introduces a list, such as '... the following:' or 'Upon expiry of this policy:';\n"
    "- a signature, name, title, date or address line.")
_CRITERIA = {
    "true": "the line opens its own numbered section, subsection, article, schedule, exhibit, annex or appendix",
    "false": "anything else: page furniture, a table-of-contents line, a cross-reference, a list item, a lead-in "
             "sentence, or a signature/name/date/address line",
}


def residue_request(texts: list[str]) -> tuple[str, dict]:
    """ING-4d: the decision-model request for the uncertain residue -- `(state, questions)`. The state is the rubric
    followed by the numbered lines (whitespace flattened); one minimal `noul` question per line, all sharing the same
    criteria. The lines are shown WITHOUT their surrounding text: measured, context made the model judge topical
    continuity again (82% vs 100%)."""
    body = "\n".join(f"[{i}] {' '.join(t.split())}" for i, t in enumerate(texts))
    questions = {f"c{i}": {"type": "noul", "instructions": f"Item [{i}]", "criteria": dict(_CRITERIA)}
                 for i in range(len(texts))}
    return f"{_RUBRIC}\n\n{body}", questions


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
    one batched call: the structural rubric + all candidate lines in the ``state``, one ``noul`` question per line
    (`residue_request`)."""
    from rag_wright.api import ainvoke_model, capability_index
    from rag_wright.pack_sdk import decision_profile

    model = os.environ.get("RAG_DECISION_MODEL")
    prof = decision_profile(model)
    if not os.environ.get(prof.api_key_env) or "jev_decision" not in capability_index():
        return None  # no decision model available -> deterministic-only

    async def _decide(texts: list[str]) -> list[bool]:
        state, questions = residue_request(texts)
        out = await ainvoke_model(
            "jev_decision", {"state": state, "questions": questions, "model": model}, resources=resources
        )
        answers = out.get("answers", {}) if isinstance(out, dict) else {}
        return [float((answers.get(f"c{i}") or {}).get("noul", 0.0)) >= _THRESHOLD for i in range(len(texts))]

    return _decide


def cached_decider(decider: Optional[BoundaryDecider], cache_dir: Any) -> Optional[BoundaryDecider]:
    """ING-4c (FR-I.5): a residue decider whose answers are content-hash cached -- the same uncertain lines get the
    same decisions on a re-ingest (repeatable boundaries, so downstream caches hit) and are never paid for twice.
    Keyed by the decision model + the residue prompt + the exact batch of candidate texts (the model reads them
    together). A failed call
    is not cached (the caller degrades it as before). `decider=None` stays None."""
    if decider is None:
        return None
    import hashlib
    import json
    from pathlib import Path

    from rag_wright.pack_sdk import decision_profile

    store = Path(cache_dir)

    async def _cached(texts: list[str]) -> list[bool]:
        key = hashlib.sha256(json.dumps([decision_profile().model_id, _RUBRIC, _CRITERIA, texts]).encode("utf-8")
                             ).hexdigest()[:32]  # ING-4d: a changed prompt never reuses old decisions
        path = store / f"{key}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        answers = await decider(texts)
        store.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([bool(a) for a in answers]), encoding="utf-8")
        return answers

    return _cached
