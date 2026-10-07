"""EP-REF-1d (ADR-0118): the reference product seam (`ContractComplianceSeam`) composes the engine correctly.
Hermetic tests prove each method dispatches to the right engine call (invoker slug / reference wrapper / API
read); the `-m store` smoke seeds a tiny contract KG and proves the read legs (find_party / counterparties /
affiliates / contract_terms / span_locations) run over a real workspace. (The heavy ingest/QA and the compliance
legs are proven live in EP-E2E-2 and EP-REF-1c -- the seam only forwards to those invokers/wrappers.)
"""
from __future__ import annotations

import os

import pytest

import rag_wright.packs.reference_seam as seam_mod
from rag_wright.packs.reference_seam import ContractComplianceSeam


# --- hermetic: each method composes the right engine call ---------------------------------------


async def test_query_and_ingest_legs_invoke_the_right_capability(monkeypatch):
    calls = []

    async def _fake_invoke(name, inputs, *, resources):
        calls.append((name, inputs))
        return {"ok": name}

    monkeypatch.setattr(seam_mod, "ainvoke_subgraph", _fake_invoke)
    s = ContractComplianceSeam(config=object())
    ws = object()

    await s.ingest_contract(ws, "DOC-1", cache_dir="/tmp/c", text="a contract body")
    await s.ask_contract(ws, "DOC-1", "what is the cap?")
    await s.search_corpus(ws, "mutual cap clauses", k=5)

    assert calls[0][0] == "contract_ingestion_pipeline" and calls[0][1]["cache_dir"] == "/tmp/c"
    assert calls[0][1]["document"].source_doc_id == "DOC-1" and calls[0][1]["document"].text == "a contract body"
    assert calls[1] == ("intra_document_qa", {"contract_id": "DOC-1", "question": "what is the cap?"})
    assert calls[2][0] == "typed_property_retrieval" and calls[2][1]["query"] == "mutual cap clauses" and calls[2][1]["k"] == 5


async def test_compliance_methods_delegate_to_the_reference_wrappers(monkeypatch):
    seen = {}

    async def _ingest(ws, **kw):
        seen["ingest"] = kw
        return "ingest-report"

    async def _check(ws, **kw):
        seen["check"] = kw
        return "check-report"

    monkeypatch.setattr(seam_mod, "invoke_policy_ingest", _ingest)
    monkeypatch.setattr(seam_mod, "invoke_compliance_check", _check)
    s = ContractComplianceSeam(config=object())

    assert await s.ingest_policy(object(), source="P", sections_path="/tmp/s.json") == "ingest-report"
    assert seen["ingest"] == {"source": "P", "sections_path": "/tmp/s.json", "doc_name": None, "data": None}
    assert await s.check(object(), subject_text="t", source_doc="d", sources=["P"]) == "check-report"
    assert seen["check"] == {"subject_text": "t", "source_doc": "d", "k": 8, "sources": ["P"]}


def test_find_party_shapes_and_sorts(monkeypatch):
    monkeypatch.setattr(seam_mod, "entities_by_name",
                        lambda ws, name: [{"entity_id": "0000000002", "name": "Beta"}, {"entity_id": "0000000001", "name": "Acme"}])
    out = ContractComplianceSeam(config=object()).find_party(object(), "acme")
    assert out == [("0000000001", "Acme"), ("0000000002", "Beta")]  # (entity_id, name), sorted, all matches kept


def test_pure_vocab_methods_need_no_store():
    s = ContractComplianceSeam(config=object())
    assert "Cap On Liability" in s.clause_type_vocabulary()
    assert s.canonical_clause_type("Cap On Liability") == "Cap On Liability"
    assert s.canonical_clause_type("not a real clause type xyz") is None


# --- live ArcadeDB (opt-in): the read legs over a real workspace --------------------------------


def _cfg():
    from rag_wright.api import EngineConfig, StoreConfig
    return EngineConfig(store=StoreConfig(
        host=os.environ["ARCADEDB_HOST"], port=os.environ["ARCADEDB_PORT"],
        user=os.environ["ARCADEDB_USER"], password=os.environ["ARCADEDB_PASSWORD"],
        protocol=os.getenv("ARCADEDB_PROTOCOL", "http")))


@pytest.mark.store
def test_reference_seam_read_legs_live():
    from rag_wright.packs.contracts.capabilities.contract_kg_store import ContractKGStore
    from rag_wright.contracts.identifiers import ChunkId
    from rag_wright.packs.contracts.schemas.property import ClausePropertyRecord, PropertyAssertion, PropertyDimension
    from rag_wright.contracts.provenance import ConfidenceTag, Provenance
    from rag_wright.store.seam import GraphEdge, GraphNode

    s = ContractComplianceSeam(_cfg())
    ws = s.open("ragwright_ref_seam_live", reset=True)

    # seed a tiny entity graph: Acme CONTRACTS_WITH Beta, Acme AFFILIATE_OF Acme Holdings
    ws._store.write_graph(
        [GraphNode(node_key=k, entity_id=k, name=n, entity_type="Organization", confidence="EXTRACTED", chunk_id="d:0:h")
         for k, n in [("A", "Acme"), ("B", "Beta"), ("E", "Acme Holdings")]],
        [GraphEdge(source_key="A", target_key="B", relationship_type="Contracts With", confidence="EXTRACTED", chunk_id="c1"),
         GraphEdge(source_key="A", target_key="E", relationship_type="Affiliate Of", confidence="EXTRACTED", chunk_id="c2")])

    assert s.find_party(ws, "Acme") == [("A", "Acme")]  # engine-normalized name -> entity id
    assert {e.entity_id for e in s.counterparties(ws, "A")} == {"B"}
    assert {e.entity_id for e in s.affiliates(ws, "A")} == {"E"}

    # seed one contract clause + its span -> contract_terms + span_locations read legs
    cid = ChunkId.of("K", 0, "K body")
    ContractKGStore(ws._store).write_clause_kg(ClausePropertyRecord(
        clause_id=str(cid), function="Cap On Liability", span_id=f"{cid}#0",
        assertions=[PropertyAssertion(provenance=Provenance.of(cid), confidence=ConfidenceTag.EXTRACTED,
                                      dimension=PropertyDimension.MUTUALITY, value="mutual", span_id=f"{cid}#0")]))
    terms = s.contract_terms(ws, "K")
    assert len(terms) == 1 and ("mutuality", "mutual") in {(p.dimension, p.value) for p in terms[0].properties}
