"""DD-1b (ADR-0117): the ContractKGStore extension re-expresses the typed clause KG, the legacy flat graph, and
contract metadata onto the generic `Store.kg_write` -- so the engine store imports no contract contract. These
hermetic tests capture the statements `kg_write` emits (via a connection-less store whose `execute_transaction` is
recorded and whose `kg_read` gate returns empty) and assert the SAME typed-edge / predicate / provenance / FOLIO /
functions-JSON behaviour the store builder had. `_property_types` reads the real pack `.ttl` (no DB)."""
from __future__ import annotations

from rag_wright.capabilities.contract_kg_store import ContractKGStore
from rag_wright.contracts.contract_meta import ContractRecord
from rag_wright.contracts.function import FunctionConfidence, FunctionScore
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.property import ClausePropertyRecord, PropertyAssertion, PropertyDimension
from rag_wright.contracts.provenance import ConfidenceTag, Provenance
from rag_wright.store.arcadedb import ArcadeDBStore, _DIM_EDGE_STR

_D = PropertyDimension


def _record(seed, function, props):
    cid = ChunkId.of(seed, 0, seed + " body")
    prov = Provenance.of(cid)
    assertions = [PropertyAssertion(provenance=prov, confidence=c, dimension=d, value=v, span_id=f"{cid}#0")
                  for d, v, c in props]
    return ClausePropertyRecord(clause_id=str(cid), function=function, assertions=assertions), str(cid)


def _capturing_store():
    """A connection-less ArcadeDBStore: `kg_read` gate -> empty (clause absent), `execute_transaction` recorded,
    `_property_types` reads the real pack."""
    s = object.__new__(ArcadeDBStore)
    s._query = lambda sql: []  # the content-hash gate's kg_read -> not already written
    txns: list[list[str]] = []
    s._db = type("_Rec", (), {"execute_transaction": lambda self, stmts: txns.append(list(stmts))})()
    return s, txns


def _stmts(record):
    s, txns = _capturing_store()
    ContractKGStore(s).write_clause_kg(record)
    return txns[0] if txns else []


def test_every_dimension_maps_to_a_typed_edge():
    """A new PropertyDimension cannot silently break the write path: the dim->edge map must be total."""
    assert set(_DIM_EDGE_STR) == {d.value for d in PropertyDimension}


def test_statements_use_typed_edges_with_predicate_and_provenance():
    rec, _cid = _record("capA", "Cap On Liability", [
        (_D.MUTUALITY, "mutual", ConfidenceTag.EXTRACTED),
        (_D.CARVE_OUT, "fraud", ConfidenceTag.EXTRACTED),
        (_D.DAMAGE_TYPE, "consequential", ConfidenceTag.EXTRACTED)])
    stmts = _stmts(rec)
    sql = "\n".join(stmts)
    assert "CREATE EDGE HAS_MUTUALITY" in sql
    assert "CREATE EDGE EXCEPTS" in sql
    assert "CREATE EDGE PROHIBITS" in sql  # damage_type waiver
    assert "CREATE EDGE HasProperty" not in sql
    assert "http://www.w3.org/ns/odrl/2/prohibition" in sql
    assert sql.count("predicate_iri = ") == 3
    assert sql.count("confidence = ") == 3
    assert sql.count("span_id = ") == 4  # the 3 property edges + the Clause vertex's clause-level span_id
    clause_stmt = next(s for s in stmts if s.startswith("UPDATE Clause SET"))
    assert "span_id = " in clause_stmt


def test_value_nodes_get_folio_grounding():
    rec, _cid = _record("x", "Cap On Liability", [(_D.CARVE_OUT, "fraud", ConfidenceTag.EXTRACTED)])
    assert "https://folio.openlegalstandard.org/RqGxSnAp9vX42GRKHqwvBe" in "\n".join(_stmts(rec))


def test_cuad_extension_dims_route_to_grants_and_has_edges():
    rec, _cid = _record("excl", "Exclusivity", [
        (_D.EXCLUSIVITY_TYPE, "exclusive", ConfidenceTag.EXTRACTED),
        (_D.AUDIT_FREQUENCY, "annual", ConfidenceTag.EXTRACTED)])
    sql = "\n".join(_stmts(rec))
    assert "CREATE EDGE GRANTS" in sql
    assert "http://www.w3.org/ns/odrl/2/permission" in sql
    assert "CREATE EDGE HAS_AUDIT_FREQUENCY" in sql


def test_empty_record_writes_only_the_clause_node():
    rec, _cid = _record("empty", "Cap On Liability", [])
    stmts = _stmts(rec)
    assert len(stmts) == 1 and stmts[0].startswith("UPDATE Clause")


def test_clause_upsert_emits_functions_json():
    import json

    rec, _ = _record("s1", "Cap On Liability", [])
    rec = rec.model_copy(update={"functions": [
        FunctionScore(function="Cap On Liability", confidence=FunctionConfidence.HIGH),
        FunctionScore(function="Indemnification", confidence=FunctionConfidence.MEDIUM)]})
    clause_stmt = _stmts(rec)[0]
    payload = json.dumps([{"function": "Cap On Liability", "confidence": "high"},
                          {"function": "Indemnification", "confidence": "medium"}])
    assert "functions = " in clause_stmt and payload in clause_stmt


def test_clause_upsert_empty_functions_is_empty_json_array():
    rec, _ = _record("s2", "Governing Law", [])
    assert "'[]'" in _stmts(rec)[0]


def test_write_property_graph_uses_the_legacy_flat_edge():
    rec, _cid = _record("flat", "Cap On Liability", [(_D.CARVE_OUT, "fraud", ConfidenceTag.EXTRACTED)])
    s, txns = _capturing_store()
    ContractKGStore(s).write_property_graph(rec)
    sql = "\n".join(txns[0])
    assert "CREATE EDGE HasProperty" in sql and "predicate_iri = " not in sql  # flat edge, no predicate IRI


def test_patch_canonical_jurisdictions_writes_canonical_value_for_resolvable_only():
    """DD-1c: re-expressed onto kg_read (the jurisdiction value nodes) + kg_write (partial upsert of
    `canonical_value`); a non-resolvable surface form is left untouched."""
    s = object.__new__(ArcadeDBStore)
    rows = [{"value_key": "jurisdiction:England and Wales", "value": "England and Wales"},
            {"value_key": "jurisdiction:ref", "value": "a reference to the laws"}]
    s._query = lambda sql: rows  # the kg_read over the jurisdiction PropertyValue nodes
    txns: list[list[str]] = []
    s._db = type("_Rec", (), {"execute_transaction": lambda self, stmts: txns.append(list(stmts))})()
    out = ContractKGStore(s).patch_canonical_jurisdictions()
    assert out == {"seen": 2, "canonicalized": 1}  # only 'England and Wales' resolves
    stmt = txns[0][0]
    assert "canonical_value = 'england'" in stmt  # surface untouched; canonical slug added
    assert "UPSERT WHERE value_key = 'jurisdiction:England and Wales'" in stmt


def test_upsert_contract_encodes_parties_as_json_and_null_page_count():
    s, txns = _capturing_store()
    ContractKGStore(s).upsert_contract(ContractRecord(
        contract_id="C1", name="Distributor Agreement", parties=["Acme", "Beta"],
        source_doc_id="C1", content_hash="h"))
    stmt = txns[0][0]
    assert stmt.startswith("UPDATE Contract SET") and "UPSERT WHERE contract_id = 'C1'" in stmt
    assert 'parties_json = \'["Acme", "Beta"]\'' in stmt  # list under a STRING column -> JSON string
    assert "page_count = null" in stmt  # optional -> null
