"""KG-7: the Party<->Contract unifying link (`party_clause_linking`), derived over already-populated nodes.

Hermetic: the derivation is pure (plain contract/entity dicts, no store), and the capability runs against a
fake store. No re-ingest -- KG-7 only reads the populated Contract + Entity nodes and adds PARTY_TO edges,
joining the party graph to the typed Clause KG (a clause's contract is implicit in its clause_id).
"""

from __future__ import annotations

import json

from rag_wright.capabilities.party_clause_linking import (
    PartyClauseLinkResult,
    PartyContractLink,
    derive_party_contract_links,
    party_clause_linking,
    register_party_clause_linking,
)
from rag_wright.capabilities.registry import CapabilityRegistry


def _entities(*pairs):
    return [{"entity_id": eid, "name": name} for eid, name in pairs]


def _contract(contract_id, parties):
    return {"contract_id": contract_id, "parties_json": json.dumps(parties)}


# --- derive_party_contract_links: pure, normalized-name join --------------------------------------


def test_links_parties_to_contracts_by_normalized_name():
    entities = _entities(("CIK1", "Bank of America, N.A."), ("CIK2", "Acme Corporation"))
    contracts = [_contract("C1", ["Bank of America", "Acme Corp"])]  # surface variants of the same entities

    result = derive_party_contract_links(contracts, entities)

    assert isinstance(result, PartyClauseLinkResult)
    assert {(l.entity_id, l.contract_id) for l in result.links} == {("CIK1", "C1"), ("CIK2", "C1")}
    assert result.unmatched_parties == 0
    assert result.contracts_processed == 1


def test_unmatched_party_is_counted_not_linked():
    entities = _entities(("CIK1", "Acme Corporation"))
    contracts = [_contract("C1", ["Acme Corp", "Private Family Trust"])]  # the trust has no Entity node

    result = derive_party_contract_links(contracts, entities)

    assert {(l.entity_id, l.contract_id) for l in result.links} == {("CIK1", "C1")}
    assert result.unmatched_parties == 1  # the honest gap (private/unlinked party), surfaced not dropped


def test_dedups_repeated_party_within_a_contract():
    entities = _entities(("CIK1", "Acme Corporation"))
    contracts = [_contract("C1", ["Acme Corp", "Acme Corporation", "  acme  corp "])]  # 3 surfaces, 1 entity

    result = derive_party_contract_links(contracts, entities)

    assert len(result.links) == 1  # one (entity, contract) edge, not three


def test_party_across_multiple_contracts_gets_multiple_links():
    entities = _entities(("CIK1", "Acme Corporation"))
    contracts = [_contract("C1", ["Acme Corp"]), _contract("C2", ["Acme Corporation"])]  # many-to-many

    result = derive_party_contract_links(contracts, entities)

    assert {(l.entity_id, l.contract_id) for l in result.links} == {("CIK1", "C1"), ("CIK1", "C2")}


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
        contracts=[_contract("C1", ["Acme Corp"])],
        entities=_entities(("CIK1", "Acme Corporation")),
    )

    result = party_clause_linking(store)

    assert [(l.entity_id, l.contract_id) for l in result.links] == [("CIK1", "C1")]
    assert store.written == result.links  # the derived links were written to the store
    assert all(isinstance(l, PartyContractLink) for l in store.written)


def test_registers_as_a_function():
    reg = CapabilityRegistry()
    register_party_clause_linking(reg)
    assert reg.get("party_clause_linking").kind == "function"
    assert reg.get("party_clause_linking").contract is PartyClauseLinkResult
