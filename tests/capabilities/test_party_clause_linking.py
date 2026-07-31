"""KG-7 (revised): the Party<->Contract link, derived by a PROVENANCE join over already-populated nodes.

The original name-match join was dead on the live data (`Contract.parties_json` is empty on every contract).
The real association lives in `Entity.chunk_id` (`<source_doc_id>:idx:hash`, where `source_doc_id` is the
contract) -- stamped by GP-1B at extraction. Post-HYG-1/2 all graphs share the canonical `_` id, so this is a
clean exact id join. Hermetic: the derivation is pure (plain dicts, no store); the capability runs against a
fake store. No re-ingest -- KG-7 only reads populated Contract + Entity nodes and adds PARTY_TO edges.
"""

from __future__ import annotations

from rag_wright.capabilities.party_clause_linking import (
    PartyClauseLinkResult,
    PartyContractLink,
    derive_party_contract_links,
    party_clause_linking,
    register_party_clause_linking,
)
from rag_wright.capabilities.registry import CapabilityRegistry


def _entity(entity_id, name, contract_id):
    # chunk_id = <source_doc_id>:<index>:<hash>; source_doc_id is the contract
    return {"entity_id": entity_id, "name": name, "chunk_id": f"{contract_id}:0:{'a' * 16}"}


# --- derive_party_contract_links: pure, provenance (chunk_id source_doc == contract_id) join --------


def test_links_entities_to_contracts_by_provenance():
    contracts = [{"contract_id": "ACME_2020-EX-10.1-SUPPLY_AGREEMENT"}]
    entities = [
        _entity("CIK1", "Acme Corporation", "ACME_2020-EX-10.1-SUPPLY_AGREEMENT"),
        _entity("UNLINKED:beta", "Beta LLC", "ACME_2020-EX-10.1-SUPPLY_AGREEMENT"),
    ]

    result = derive_party_contract_links(contracts, entities)

    assert isinstance(result, PartyClauseLinkResult)
    assert {(l.entity_id, l.contract_id) for l in result.links} == {
        ("CIK1", "ACME_2020-EX-10.1-SUPPLY_AGREEMENT"),
        ("UNLINKED:beta", "ACME_2020-EX-10.1-SUPPLY_AGREEMENT"),
    }
    assert result.unmatched_parties == 0
    assert result.contracts_processed == 1


def test_party_name_is_carried_from_the_entity():
    contracts = [{"contract_id": "C1"}]
    result = derive_party_contract_links(contracts, [_entity("CIK1", "Acme Corporation", "C1")])
    assert result.links[0].party_name == "Acme Corporation"


def test_entity_whose_contract_has_no_node_is_counted_not_linked():
    # the coverage gap: an entity from a contract that was never ingested as a Contract node
    contracts = [{"contract_id": "C1"}]
    entities = [_entity("CIK1", "Acme", "C1"), _entity("CIK2", "Zeta", "UNINGESTED_CONTRACT")]

    result = derive_party_contract_links(contracts, entities)

    assert {(l.entity_id, l.contract_id) for l in result.links} == {("CIK1", "C1")}
    assert result.unmatched_parties == 1  # Zeta's contract has no node -> counted, never fabricated


def test_dedups_repeated_entity_contract_pair():
    contracts = [{"contract_id": "C1"}]
    entities = [_entity("CIK1", "Acme", "C1"), _entity("CIK1", "Acme", "C1")]
    result = derive_party_contract_links(contracts, entities)
    assert len(result.links) == 1  # one (entity, contract) edge


def test_entity_with_no_chunk_id_is_unmatched_not_error():
    contracts = [{"contract_id": "C1"}]
    result = derive_party_contract_links(contracts, [{"entity_id": "X", "name": "N", "chunk_id": ""}])
    assert result.links == [] and result.unmatched_parties == 1


# --- party_clause_linking: reads populated nodes, derives, writes PARTY_TO edges -------------------


class _FakeStore:
    def __init__(self, contracts, entities):
        self._contracts = contracts
        self._entities = entities
        self.written = None

    def all_contracts(self):
        return self._contracts

    def all_entities(self):
        return self._entities

    def write_party_contract_links(self, links):
        self.written = list(links)


def test_capability_reads_derives_and_writes_the_links():
    store = _FakeStore(
        contracts=[{"contract_id": "C1"}],
        entities=[_entity("CIK1", "Acme Corporation", "C1")],
    )

    result = party_clause_linking(store)

    assert [(l.entity_id, l.contract_id) for l in result.links] == [("CIK1", "C1")]
    assert store.written == result.links
    assert all(isinstance(l, PartyContractLink) for l in store.written)


def test_registers_as_a_function():
    reg = CapabilityRegistry()
    register_party_clause_linking(reg)
    assert reg.get("party_clause_linking").kind == "function"
    assert reg.get("party_clause_linking").contract is PartyClauseLinkResult
