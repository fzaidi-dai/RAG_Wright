"""KG-7 (FR-S.1, ADR-0033/ADR-0036): the Party<->Contract unifying link over the one contract KG.

The two graphs were built separately -- the typed Clause KG (`Clause` + typed property edges) and the party
graph (`Entity` + `CONTRACTS_WITH`/`AFFILIATE_OF`) -- and coexist in one store, but nothing connected a party
to the clauses it is a party to. This capability adds the missing link, WITHOUT re-ingesting either graph: a
pure pass over the already-populated `Contract` and `Entity` nodes that writes `PARTY_TO` edges
(`Entity -> Contract`).

Why an edge (not a `contract_id` field like `Clause` carries): `Clause -> Contract` is many-to-ONE (a clause
belongs to exactly one contract, so its `contract_id` is id-encoded and Contract->Clauses is a free key-range
query). `Party -> Contract` is many-to-MANY, which an id/field cannot represent and an edge can (and the edge
stays extensible for a future per-clause party ROLE attribute).

The join is by PROVENANCE, on the canonical id: each `Entity.chunk_id` (`<source_doc_id>:idx:hash`) carries the
`source_doc_id` of the contract GP-1B extracted the party from, which -- post-HYG-1/HYG-2, now that every graph
shares the one canonical `_` slug -- is an exact match for a `Contract.contract_id`. (The earlier design matched
`Contract.parties_json` names to `Entity.name`, but `parties_json` is empty on the live contracts, so it linked
nothing.) An entity whose contract has no `Contract` node -- the corpus-coverage gap, most CUAD contracts are
party-extracted but not clause-ingested (see CUAD-FULL-COVERAGE) -- is counted, not dropped. Contract->Clause
needs no edge: `Clause.clause_id` already encodes `contract_id`, so a party reaches its clauses via
`Party -PARTY_TO-> Contract` then the clause_id key-range.

LIMITATION (recorded): GP-1B upserts one `Entity` node per resolved party, so a multi-contract party keeps only
its last-written `chunk_id`; the provenance join therefore links it to that one extraction-source contract, not
every contract it signed. Full many-to-many coverage would need the per-(contract, party) mention data (the
GP-1B cache), a later enhancement alongside the party-role layer.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from rag_wright.capabilities.registry import CapabilityRegistry


class PartyContractLink(BaseModel):
    """One `PARTY_TO` edge: the party `Entity` (by `entity_id`) is a party to `contract_id`. `party_name` is
    the `Entity`'s name (provenance for the link)."""

    entity_id: str
    contract_id: str
    party_name: str


class PartyClauseLinkResult(BaseModel):
    """The capability's output: the derived `PARTY_TO` links plus the honest unmatched count."""

    links: list[PartyContractLink]
    unmatched_parties: int  # entity rows whose contract has no Contract node (the corpus-coverage gap)
    contracts_processed: int


@runtime_checkable
class _LinkStore(Protocol):
    def all_contracts(self) -> list[dict]: ...  # rows: {contract_id}
    def all_entities(self) -> list[dict]: ...  # rows: {entity_id, name, chunk_id}
    def write_party_contract_links(self, links: list[PartyContractLink]) -> None: ...


def _source_doc_of(entity: dict) -> str:
    """The contract's source_doc_id from an Entity's chunk_id (`<source_doc_id>:idx:hash`)."""
    return (entity.get("chunk_id") or "").rsplit(":", 2)[0]


def derive_party_contract_links(
    contracts: list[dict], entities: list[dict]
) -> PartyClauseLinkResult:
    """Pure: link each `Entity` to the `Contract` whose id matches its `chunk_id` source_doc (the extraction
    provenance), emitting one `PARTY_TO` link per (entity, contract). A (entity, contract) pair is deduped; an
    entity whose contract has no node (the coverage gap) is counted, never dropped."""
    contract_ids = {contract["contract_id"] for contract in contracts}

    links: list[PartyContractLink] = []
    seen: set[tuple[str, str]] = set()
    unmatched = 0
    for entity in entities:
        contract_id = _source_doc_of(entity)
        if contract_id not in contract_ids:
            unmatched += 1
            continue
        pair = (entity["entity_id"], contract_id)
        if pair in seen:
            continue
        seen.add(pair)
        links.append(PartyContractLink(
            entity_id=entity["entity_id"], contract_id=contract_id, party_name=entity.get("name") or ""))

    return PartyClauseLinkResult(
        links=links, unmatched_parties=unmatched, contracts_processed=len(contracts))


def party_clause_linking(store: Any) -> PartyClauseLinkResult:
    """The registered capability: read the populated `Contract` + `Entity` nodes, derive the `PARTY_TO` links
    (by extraction provenance), write them, and return the result. No re-ingest -- a link pass only."""
    result = derive_party_contract_links(store.all_contracts(), store.all_entities())
    store.write_party_contract_links(result.links)
    return result


def register_party_clause_linking(registry: CapabilityRegistry) -> None:
    """KG-7: register `party_clause_linking` (function; the Party<->Contract unifying link, ADR-0036)."""
    registry.register(
        "party_clause_linking",
        contract=PartyClauseLinkResult,
        kind="function",
        display_name="Party-clause linking (PARTY_TO edges over the unified contract KG)",
    )
