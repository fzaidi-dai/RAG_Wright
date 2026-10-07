"""LG-2: query_constraint_extraction subgraph -- hermetic (fake record_fn, no LLM)."""

from __future__ import annotations

from rag_wright.contracts.identifiers import ChunkId
from rag_wright.packs.contracts.schemas.property import ClausePropertyRecord, PropertyAssertion, PropertyDimension
from rag_wright.contracts.provenance import ConfidenceTag, Provenance
from rag_wright.packs.contracts.subgraphs.query_constraint_extraction import build_query_constraint_extraction
from rag_wright.packs.contracts.subgraphs.typed_clause_extraction import TransientExtraction


def _record_with_one_constraint() -> ClausePropertyRecord:
    prov = Provenance.of(ChunkId.of("q", 0, "a mutual cap"))
    assertion = PropertyAssertion(
        provenance=prov, confidence=ConfidenceTag.EXTRACTED, dimension=PropertyDimension.MUTUALITY, value="mutual"
    )
    return ClausePropertyRecord(clause_id=str(prov.chunk_id), function="Cap On Liability", assertions=[assertion])


def test_extracts_constraints_from_the_query():
    graph = build_query_constraint_extraction(lambda text, model: _record_with_one_constraint(), model_id="granite")
    out = graph.invoke({"query_text": "a mutual cap"})
    assert out["constraints"] == [("mutuality", "mutual")]


def test_failed_extraction_degrades_to_empty_without_dropping_the_query():
    def failing(text: str, model: str):
        raise TransientExtraction("no models")

    graph = build_query_constraint_extraction(failing, model_id="granite")
    out = graph.invoke({"query_text": "x"})
    assert out["constraints"] == []  # empty, not raised -> query survives (embedding-only fallback)


def test_none_result_degrades_to_empty():
    graph = build_query_constraint_extraction(lambda text, model: None, model_id="granite")
    out = graph.invoke({"query_text": "x"})
    assert out["constraints"] == []


def test_registers_as_a_subgraph():
    from rag_wright.capabilities.registry import CapabilityRegistry
    from rag_wright.packs.contracts.subgraphs.query_constraint_extraction import register_query_constraint_extraction

    reg = CapabilityRegistry()
    register_query_constraint_extraction(reg)
    assert reg.get("query_constraint_extraction").kind == "subgraph"
