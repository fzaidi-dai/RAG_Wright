"""LG-3a / A2: the `relational_qa` composite subgraph -- hermetic (stub traverse/generate, no LLM/DB).

Composes graph_query -> GRAPH-STRUCTURAL evidence -> generate_answer. The evidence is the relationship itself
(cited by the SOURCE CONTRACT parsed from the edge's provenance chunk_id), not rehydrated chunk text -- the
graph is the relationship layer, and there is nothing to rehydrate (A2, MCP-PROTO Phase A). A transient
traversal failure degrades to empty evidence (the generator abstains), never a dropped item.
"""

from __future__ import annotations

from langgraph.types import RetryPolicy

from rag_wright.capabilities.answer_generator import GeneratedAnswer
from rag_wright.capabilities.graph_query import GraphAnswer, GraphEvidence
from rag_wright.subgraphs.relational_qa import build_relational_qa, graph_structural_evidence

_FAST_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.0)


def _graph_answer(evidence):
    return GraphAnswer(
        start_entity_id="acme", relationship_type="Contracts With", evidence=evidence
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


def _capturing_generate():
    seen = {}

    async def generate(query, evidence):
        seen["evidence"] = evidence
        if not evidence:
            return GeneratedAnswer(answer="abstain", citations=[], abstained=True)
        return GeneratedAnswer(
            answer=f"answer to {query}", citations=[e.chunk_id for e in evidence], abstained=False)

    return generate, seen


# --- graph_structural_evidence: the relationship IS the evidence, cited by the source contract ---------------


def test_evidence_cites_the_source_contract_parsed_from_the_edge_chunk_id():
    ev = graph_structural_evidence(_graph_answer([_evidence("beta", ["limeenergy_distributor:0:hash"], ["EXTRACTED"])]))
    assert len(ev) == 1
    assert ev[0].chunk_id == "limeenergy_distributor"  # <source_doc_id>:<index>:<hash> -> the contract id
    assert "beta" in ev[0].text and "acme" in ev[0].text  # the relationship statement is the evidence text
    assert ev[0].confidence == "EXTRACTED"  # FR-S.4 tag surfaced, not rehydrated


def test_evidence_is_one_cited_fact_per_source_contract():
    ev = graph_structural_evidence(_graph_answer([_evidence("beta", ["c1:0:h", "c2:0:h"], ["EXTRACTED", "EXTRACTED"])]))
    assert sorted(e.chunk_id for e in ev) == ["c1", "c2"]  # one fact per contract the co-party edge came from


def test_evidence_falls_back_to_entity_id_without_chunk_provenance():
    ev = graph_structural_evidence(_graph_answer([_evidence("beta", [], [])]))
    assert ev[0].chunk_id == "beta"  # no contract provenance -> cite the reached entity itself


# --- the subgraph: traverse -> assemble -> generate ---------------------------------------------------------


async def test_composes_traverse_assemble_generate_into_a_cited_answer():
    answer = _graph_answer([_evidence("beta", ["limeenergy:0:h0"], ["EXTRACTED"])])
    traverse = _StubTraverse(answer)
    generate, _ = _capturing_generate()
    graph = build_relational_qa(traverse, generate, retry_policy=_FAST_RETRY)

    out = await graph.ainvoke({"query": "Who does Acme contract with?", "start_entity_id": "acme"})

    assert out["answer"].abstained is False
    assert out["answer"].citations == ["limeenergy"]  # cited the SOURCE CONTRACT (no chunk text needed)
    assert traverse.calls == 1


async def test_confidence_tag_is_surfaced_from_the_graph_to_the_generator():
    generate, seen = _capturing_generate()
    graph = build_relational_qa(
        _StubTraverse(_graph_answer([_evidence("beta", ["doc:0:h0"], ["INFERRED"])])), generate,
        retry_policy=_FAST_RETRY)

    await graph.ainvoke({"query": "q", "start_entity_id": "acme"})

    assert [e.confidence for e in seen["evidence"]] == ["INFERRED"]  # FR-S.4: confidence travels into evidence


async def test_transient_traversal_retries_then_degrades_to_empty_and_abstains():
    traverse = _StubTraverse(_graph_answer([_evidence("beta", ["doc:0:h0"], ["EXTRACTED"])]), fail_times=99)
    generate, seen = _capturing_generate()
    graph = build_relational_qa(traverse, generate, retry_policy=_FAST_RETRY)

    out = await graph.ainvoke({"query": "q", "start_entity_id": "acme"})

    assert traverse.calls == 3  # retried up to max_attempts
    assert out["answer"].abstained is True  # degraded to empty evidence -> abstain (query survives)
    assert seen["evidence"] == []  # never a dropped item, an empty one


def test_registers_as_a_subgraph():
    from rag_wright.capabilities.registry import CapabilityRegistry
    from rag_wright.subgraphs.relational_qa import register_relational_qa

    reg = CapabilityRegistry()
    register_relational_qa(reg)
    assert reg.get("relational_qa").kind == "subgraph"
    assert reg.get("relational_qa").contract is GeneratedAnswer
