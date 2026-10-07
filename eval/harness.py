"""The golden evaluation harness (T9, SPEC section 12).

Measures retrieval recall@k per archetype, with the text and graph legs kept separate (recall is
bounded by their union, so each leg is measured on its own). The capabilities it will measure
(retrieval, T15-T21; graph, T22-T26) do not exist yet, so `evaluate` takes an injected `retrieve`
function; a stub proves the plumbing now, and the real capabilities plug in at Phase 5.

A `GoldenQuestion` carries CUAD's ground-truth answer spans and, once the corpus is chunked, the
`relevant_ids` (the chunk_ids / entity_ids that should be retrieved). recall@k is computed against
`relevant_ids`; resolving answer spans to chunk_ids happens at eval time, after chunking.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from rag_wright.packs.contracts.schemas.ontology import ClauseCategory


class Archetype(str, Enum):
    """The four question archetypes the eval is split by (SPEC section 12)."""

    EXACT_LEXICAL = "exact_lexical"  # a specific term/value, lexically findable (dates, parties, law)
    SEMANTIC = "semantic"  # a conceptual clause, needs meaning (non-compete, exclusivity)
    CLAUSE_FINDING = "clause_finding"  # find a specific clause type and cite it (IP, liability, ...)
    RELATIONAL = "relational"  # multi-hop over the entity graph (built separately, T10)


class GoldenQuestion(BaseModel):
    """One golden question: its archetype, CUAD ground truth, and (resolved later) relevant ids."""

    qid: str
    source_doc_id: str
    archetype: Archetype
    question: str
    category: Optional[ClauseCategory] = None  # None for relational (T10)
    answer_spans: list[str] = Field(default_factory=list)  # CUAD ground-truth answer text
    relevant_ids: set[str] = Field(default_factory=set)  # chunk_ids/entity_ids, resolved at eval time


def recall_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """Fraction of the relevant set found in the top-k retrieved. Vacuously 1.0 if nothing relevant."""
    if not relevant:
        return 1.0
    return len(set(retrieved[:k]) & relevant) / len(relevant)


class RecallReport(BaseModel):
    """Mean recall@k per `archetype/leg`, plus the question count per archetype."""

    k: int
    per_archetype_leg: dict[str, float]  # "archetype/leg" -> mean recall
    counts: dict[str, int]  # archetype -> number of questions


Retrieve = Callable[[GoldenQuestion, str], Sequence[str]]


def evaluate(
    golden: Sequence[GoldenQuestion],
    retrieve: Retrieve,
    *,
    k: int,
    legs: Sequence[str] = ("text", "graph"),
) -> RecallReport:
    """Run recall@k over the golden set, per archetype and per leg (measured separately)."""
    total: dict[str, float] = defaultdict(float)
    n: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    for question in golden:
        counts[question.archetype.value] += 1
        for leg in legs:
            recall = recall_at_k(retrieve(question, leg), question.relevant_ids, k)
            key = f"{question.archetype.value}/{leg}"
            total[key] += recall
            n[key] += 1
    per = {key: total[key] / n[key] for key in total}
    return RecallReport(k=k, per_archetype_leg=per, counts=dict(counts))
