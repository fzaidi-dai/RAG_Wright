"""CAP-REG-3 (FR-Q, ADR-0033): the KG-primary retrieval core, packaged out of `eval/kg_primary.py`.

The eval script ranked ACORD candidates through inline closures (`pool_of`, `_match`, `_tiebreak`) wrapped in
MODE/VARIANT/MATCH ablation scaffolding. This module lifts the ADOPTED operating point out of that scaffolding
as three pure, deterministic `function` capabilities, each registered under its FR-C slug so the query graph
(and the `cross_corpus_retrieval` subgraph, LG-3c) can bind them:

  - **candidate_routing** -- the union combiner: several routing signals (LLM / LegalBERT classifier /
    dimension-prior) each propose ranked functions; union them (first-wins, recall-safe) and fetch the
    candidate clause pool for that function set (KG-5e). The store pool lookup is an injected seam.
  - **typed_constraint_match_rank** -- grade each candidate by how many query (dimension, value) constraints
    its grounded typed props satisfy, under KG-5a canonicalization + subsumption (`constraint_match_count`).
    Recall-safe: a zero-match candidate keeps its place (stable sort), never dropped.
  - **dense_rank_tiebreak** -- order candidates by descending cosine to the query vector: the embedding signal
    that breaks constraint-match ties meaningfully (KG-6 / V4). Pure -- vectors come from `embedding`.

The three carry NO LLM call (the LLM front door is `query_constraint_extraction` + query function
classification, upstream); they are exact, deterministic compute -> `function`, not `subgraph`.
"""

from __future__ import annotations

from typing import Callable, Sequence

from pydantic import BaseModel

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.value_match import constraint_match_count

# The store seam for candidate_routing: given the routed functions, return the candidate clause ids. Bound in
# production (LG-3c) to the store's function->pool query; injected as a fake in tests.
PoolFn = Callable[[list[str]], list[str]]

_NONE_FUNCTION = "NONE"  # the classifier's no-function sentinel; never routes a pool


class CandidatePool(BaseModel):
    """candidate_routing output: the unioned routing functions and the candidate clause ids they select."""

    functions: list[str]
    candidate_ids: list[str]


class RankedClause(BaseModel):
    """One graded candidate: its clause id and how many query constraints its props satisfy (KG-5a)."""

    clause_id: str
    match_score: float


class MatchRanking(BaseModel):
    """typed_constraint_match_rank output: candidates in descending graded (constraint-match) order."""

    ranked: list[RankedClause]


class DenseScoredClause(BaseModel):
    """One candidate scored by cosine similarity to the query vector."""

    clause_id: str
    cosine: float


class DenseRanking(BaseModel):
    """dense_rank_tiebreak output: candidates in descending cosine order."""

    ranked: list[DenseScoredClause]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity; 0.0 when either vector has zero norm (mirrors eval/kg_primary._cosine)."""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def candidate_routing(
    function_predictions: Sequence[Sequence[str]], *, pool_fn: PoolFn
) -> CandidatePool:
    """Union the ranked function predictions from each router (first-wins, order-preserving; empty and the
    `NONE` sentinel dropped), then fetch the candidate clause pool for that function set. No functions -> an
    empty pool with no store lookup. The recall-safe union combiner (KG-5e)."""
    functions: list[str] = []
    for predictions in function_predictions:
        for function in predictions:
            if function and function != _NONE_FUNCTION and function not in functions:
                functions.append(function)
    candidate_ids = pool_fn(functions) if functions else []
    return CandidatePool(functions=functions, candidate_ids=candidate_ids)


def typed_constraint_match_rank(
    query_constraints: set, candidate_props: Sequence[tuple[str, set]]
) -> MatchRanking:
    """Grade each candidate by how many of the query's (dimension, value) constraints its grounded typed props
    satisfy (KG-5a canonicalization + subsumption via `constraint_match_count`), returning descending graded
    order. Recall-safe: the sort is stable, so a zero-match candidate keeps its input position, never dropped."""
    scored = [
        RankedClause(clause_id=clause_id, match_score=float(constraint_match_count(query_constraints, props)))
        for clause_id, props in candidate_props
    ]
    ranked = sorted(scored, key=lambda r: -r.match_score)  # stable -> ties keep input order
    return MatchRanking(ranked=ranked)


def dense_rank_tiebreak(
    query_vector: Sequence[float], candidate_vectors: Sequence[tuple[str, Sequence[float]]]
) -> DenseRanking:
    """Order candidates by descending cosine similarity to the query vector -- the embedding tiebreak signal
    (KG-6 / V4). Pure: the vectors come from the `embedding` capability, not from a store or a model here."""
    scored = [
        DenseScoredClause(clause_id=clause_id, cosine=_cosine(query_vector, vector))
        for clause_id, vector in candidate_vectors
    ]
    ranked = sorted(scored, key=lambda r: -r.cosine)
    return DenseRanking(ranked=ranked)


def register_candidate_routing(registry: CapabilityRegistry) -> None:
    """CAP-REG-3: register `candidate_routing` (function; the union combiner -> candidate pool)."""
    registry.register(
        "candidate_routing",
        contract=CandidatePool,
        kind="function",
        display_name="Candidate routing (union combiner -> candidate pool)",
    )


def register_typed_constraint_match_rank(registry: CapabilityRegistry) -> None:
    """CAP-REG-3: register `typed_constraint_match_rank` (function; KG-5a graded constraint match, recall-safe)."""
    registry.register(
        "typed_constraint_match_rank",
        contract=MatchRanking,
        kind="function",
        display_name="Typed constraint match rank (graded, subsumption-aware)",
    )


def register_dense_rank_tiebreak(registry: CapabilityRegistry) -> None:
    """CAP-REG-3: register `dense_rank_tiebreak` (function; cosine order for meaningful tie-breaking)."""
    registry.register(
        "dense_rank_tiebreak",
        contract=DenseRanking,
        kind="function",
        display_name="Dense rank tiebreak (cosine order)",
    )
