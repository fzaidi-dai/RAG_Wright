"""LG-3a: the `relational_qa` composite subgraph -- hermetic (stub traverse/rehydrate/generate, no LLM/DB).

Composes graph_query -> chunk_read -> generate_answer as one hardened LangGraph. Query-side posture: a
transient traversal failure degrades to empty evidence (the generator then abstains), never a dropped item;
an orphaned chunk_id (a real pipeline inconsistency) dead-letters rather than fabricating.
"""

from __future__ import annotations

from langgraph.types import RetryPolicy

from rag_wright.capabilities.answer_generator import EvidenceItem, GeneratedAnswer
from rag_wright.capabilities.chunk_read import ChunkReadResult, ChunkText
from rag_wright.capabilities.graph_query import GraphAnswer, GraphEvidence
from rag_wright.contracts.ontology import RelationshipType
from rag_wright.subgraphs.relational_qa import build_relational_qa

_FAST_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.0)


def _graph_answer(evidence):
    return GraphAnswer(
        start_entity_id="acme", relationship_type=RelationshipType.CONTRACTS_WITH.value, evidence=evidence
    )


def _evidence(entity_id, chunk_ids, confidences):
    return GraphEvidence(
        entity_id=entity_id, name=entity_id, hops=len(chunk_ids),
        path_entity_ids=["acme", entity_id], chunk_ids=chunk_ids, confidences=confidences,
    )


class _StubTraverse:
    """(start, rel, hops) -> GraphAnswer; optionally fails `fail_times` before succeeding."""

    def __init__(self, answer, fail_times=0):
        self._answer = answer
        self._fail_times = fail_times
        self.calls = 0

    def __call__(self, start, rel, hops):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("store blip")
        return self._answer


def _rehydrate_ok(text_by_id):
    def rehydrate(chunk_ids):
        return ChunkReadResult(
            chunks=[ChunkText(chunk_id=c, text=text_by_id[c], source_doc_id=c.rsplit(":", 2)[0])
                    for c in chunk_ids]
        )
    return rehydrate


def _rehydrate_orphan(chunk_ids):
    raise KeyError("no persisted text for chunk_id 'doc:9:zz'")


def _capturing_generate():
    """A generator that echoes what evidence it was handed, so the test can assert threading."""
    seen = {}

    def generate(query, evidence):
        seen["evidence"] = evidence
        if not evidence:
            return GeneratedAnswer(answer="abstain", citations=[], abstained=True)
        return GeneratedAnswer(
            answer=f"answer to {query}", citations=[e.chunk_id for e in evidence], abstained=False
        )

    return generate, seen


def test_composes_traverse_rehydrate_generate_into_a_cited_answer():
    answer = _graph_answer([_evidence("beta", ["doc:0:h0"], ["EXTRACTED"])])
    traverse = _StubTraverse(answer)
    generate, _ = _capturing_generate()
    graph = build_relational_qa(traverse, _rehydrate_ok({"doc:0:h0": "Acme contracts with Beta."}), generate,
                                retry_policy=_FAST_RETRY)

    out = graph.invoke({"query": "Who does Acme contract with?", "start_entity_id": "acme"})

    assert out["answer"].abstained is False
    assert out["answer"].citations == ["doc:0:h0"]  # cited the rehydrated chunk (no claim without a citation)
    assert traverse.calls == 1


def test_confidence_tag_is_surfaced_from_the_graph_to_the_generator():
    answer = _graph_answer([_evidence("beta", ["doc:0:h0"], ["INFERRED"])])
    generate, seen = _capturing_generate()
    graph = build_relational_qa(_StubTraverse(answer), _rehydrate_ok({"doc:0:h0": "text"}), generate,
                                retry_policy=_FAST_RETRY)

    graph.invoke({"query": "q", "start_entity_id": "acme"})

    assert [e.confidence for e in seen["evidence"]] == ["INFERRED"]  # FR-S.4: confidence travels into evidence
    assert [e.text for e in seen["evidence"]] == ["text"]


def test_transient_traversal_retries_then_degrades_to_empty_and_abstains():
    answer = _graph_answer([_evidence("beta", ["doc:0:h0"], ["EXTRACTED"])])
    traverse = _StubTraverse(answer, fail_times=99)  # always fails
    generate, seen = _capturing_generate()
    graph = build_relational_qa(traverse, _rehydrate_ok({}), generate, retry_policy=_FAST_RETRY)

    out = graph.invoke({"query": "q", "start_entity_id": "acme"})

    assert traverse.calls == 3  # retried up to max_attempts
    assert out["answer"].abstained is True  # degraded to empty evidence -> abstain (query survives)
    assert seen["evidence"] == []  # never a dropped item, an empty one


def test_orphan_chunk_dead_letters_without_fabricating_or_crashing():
    answer = _graph_answer([_evidence("beta", ["doc:9:zz"], ["EXTRACTED"])])
    generate, seen = _capturing_generate()
    graph = build_relational_qa(_StubTraverse(answer), _rehydrate_orphan, generate, retry_policy=_FAST_RETRY)

    out = graph.invoke({"query": "q", "start_entity_id": "acme"})

    assert out["dead_letter"]["reason"] == "rehydration_orphan_chunk"  # surfaced, not fabricated
    assert "answer" not in out  # no answer produced (generator never ran)
    assert "evidence" not in seen  # generate node skipped


def test_registers_as_a_subgraph():
    from rag_wright.capabilities.registry import CapabilityRegistry
    from rag_wright.subgraphs.relational_qa import register_relational_qa

    reg = CapabilityRegistry()
    register_relational_qa(reg)
    assert reg.get("relational_qa").kind == "subgraph"
    assert reg.get("relational_qa").contract is GeneratedAnswer
