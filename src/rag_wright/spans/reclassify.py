"""INGEST-LLM-CLASSIFIER (ADR-0048) Phase A: the non-destructive reclassify logic.

Pure, testable core (no store, no LLM): given a chunk's spans (full context) + the existing Clause labels + a
batched classifier, compute the NEW primary + multi-label per existing clause, and detect PRIMARY FLIPS (the only
thing that triggers Phase B re-extraction). The store driver (`scripts/reclassify_kg.py`) reads/writes the KG and
runs this concurrently with X/N progress."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from rag_wright.contracts.function import NO_FUNCTION, FunctionScore, primary_function


@dataclass(frozen=True)
class ClauseReclass:
    """The reclassification of one existing Clause: its id/span, old primary, and the new scores + primary."""

    clause_id: str
    span_id: str
    old_function: str
    new_scores: list[FunctionScore]
    new_primary: str  # primary_function(new_scores) or NO_FUNCTION

    @property
    def primary_flipped(self) -> bool:
        return self.old_function != self.new_primary


def reclassify_chunk(
    chunk_text: str,
    ordered_spans: list[tuple[str, str]],       # [(span_id, span_text)] in span order (full chunk = context)
    existing_by_span: dict[str, tuple[str, str]],  # span_id -> (clause_id, old_function) for existing Clauses
    classify_fn: Any,
) -> list[ClauseReclass]:
    """Batched-classify the chunk's spans (one call, chunk as context), then map the result back to the chunk's
    EXISTING Clause nodes only (non-destructive: spans without an existing Clause are left to Phase B)."""
    if not ordered_spans:
        return []
    scores_per_span = classify_fn.classify_spans(chunk_text, [t for _, t in ordered_spans])
    out: list[ClauseReclass] = []
    for (span_id, _), scores in zip(ordered_spans, scores_per_span):
        if span_id in existing_by_span:
            clause_id, old_fn = existing_by_span[span_id]
            out.append(ClauseReclass(
                clause_id=clause_id, span_id=span_id, old_function=old_fn,
                new_scores=list(scores), new_primary=primary_function(scores) or NO_FUNCTION))
    return out


@dataclass
class ReclassDelta:
    """The aggregate reclassification delta (for the dry-run report + gating Phase B)."""

    total: int = 0
    unchanged: int = 0
    flipped: int = 0                       # primary changed -> Phase B re-extraction candidates
    to_none: int = 0                       # primary flipped to NO_FUNCTION (clause should be retired)
    from_none: int = 0                     # was NONE-ish, now a real function (should not happen for EXISTING clauses)
    transitions: Counter = field(default_factory=Counter)  # (old -> new) -> count, flips only

    def add(self, rc: ClauseReclass) -> None:
        self.total += 1
        if rc.primary_flipped:
            self.flipped += 1
            self.transitions[(rc.old_function, rc.new_primary)] += 1
            if rc.new_primary == NO_FUNCTION:
                self.to_none += 1
            if rc.old_function == NO_FUNCTION:
                self.from_none += 1
        else:
            self.unchanged += 1

    def top_transitions(self, n: int = 15) -> list[tuple[tuple[str, str], int]]:
        return self.transitions.most_common(n)
