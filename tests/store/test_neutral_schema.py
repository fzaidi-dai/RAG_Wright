"""ING-8a: the DEFAULT schema is domain-neutral. `ensure_schema()` creates only the engine types; a pack's types
(vertices, structural edges, typed edges, unique indexes) come only from THAT pack's `.ttl`, via
`ensure_pack_schema(ttl)` -- the contract reference pack's schema is created only when asked for. Hermetic: a
recording store, no ArcadeDB."""
from __future__ import annotations

from pathlib import Path

from rag_wright.store.arcadedb import ArcadeDBStore

_INCIDENTS = str(Path(__file__).parents[1] / "journey" / "incidents_pack.ttl")
_ENGINE_TYPES = {"Chunk", "Entity", "Relationship", "Mentions", "Span", "Document", "EmbeddedIn", "AttachedTo"}
_CONTRACT_TYPES = {"Clause", "PropertyValue", "Contract", "HasProperty", "IsExceptionTo", "CAPS", "BOUNDED_BY",
                   "GOVERNED_BY", "HAS_FAVORABILITY"}


def _recording(pack_ttl=None):
    store = ArcadeDBStore.__new__(ArcadeDBStore)
    store._client, store._database, store._pack_ttl = None, "scratch", pack_ttl
    cmds: list[str] = []
    store._command = cmds.append
    store.type_names = lambda: set()
    store.index_names = lambda: set()
    return store, cmds


def _created(cmds):
    return {c.split()[3] for c in cmds if c.startswith(("CREATE VERTEX TYPE", "CREATE EDGE TYPE"))}


def test_the_default_schema_is_only_the_engine_types():
    store, cmds = _recording()
    store.ensure_schema()
    assert _created(cmds) == _ENGINE_TYPES


def test_a_configured_pack_adds_only_its_own_types():
    store, cmds = _recording(_INCIDENTS)
    store.ensure_schema()
    created = _created(cmds)
    assert {"Incident", "System", "AFFECTS"} <= created and not created & _CONTRACT_TYPES
    assert "CREATE INDEX ON Incident (incident_id) UNIQUE" in cmds


def test_the_reference_contract_schema_is_created_on_request():
    from rag_wright.packs.contracts.capabilities.contract_kg_store import ContractKGStore

    store, cmds = _recording()
    ContractKGStore(store)  # the reference pack ensures its declared schema + its own typed property edges
    assert _CONTRACT_TYPES <= _created(cmds) and not _created(cmds) & _ENGINE_TYPES


def test_known_document_ids_are_the_ingested_document_nodes():
    store = ArcadeDBStore.__new__(ArcadeDBStore)
    store.type_names = lambda: {"Document"}
    store._query = lambda sql: [{"doc_id": "a.pdf"}, {"doc_id": "b.xlsx"}, {"doc_id": None}] if "Document" in sql else []
    assert store.known_document_ids() == {"a.pdf", "b.xlsx"}
    store.type_names = lambda: set()
    assert store.known_document_ids() == set()


def test_the_reference_pack_ensures_its_own_schema_once_per_store():
    from rag_wright.packs.contracts.capabilities.contract_kg_store import ContractKGStore

    store, cmds = _recording()
    ContractKGStore(store)
    first = len(cmds)
    ContractKGStore(store)  # a second wrapper over the same store: no repeated DDL
    assert first and len(cmds) == first and "Clause" in _created(cmds)
    ContractKGStore(object())  # a store without packs (a test fake) is left alone


def test_property_encoding_follows_the_packs_the_store_has():
    from rag_wright.packs.contracts.ontology.loader import reference_pack_ttl

    store, _ = _recording()
    assert store._property_types("Clause") == {}  # neutral: no contract types to encode by
    store.ensure_pack_schema(reference_pack_ttl())
    assert store._property_types("Clause")  # the reference pack, once ensured, encodes its own types
