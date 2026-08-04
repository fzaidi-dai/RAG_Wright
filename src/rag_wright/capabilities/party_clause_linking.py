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
from rag_wright.contracts.identifiers import canonical_source_doc_id
from rag_wright.corpus.canonicalize import normalize_entity_name


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


def _name_to_entity(entities: list[dict]) -> dict[str, tuple[str, str]]:
    """Canonical party-name -> (entity_id, name), first-wins -- the name-resolution map for the mention join."""
    by_name: dict[str, tuple[str, str]] = {}
    for entity in entities:
        key = normalize_entity_name(entity.get("name") or "")
        if key and key not in by_name:
            by_name[key] = (entity["entity_id"], entity.get("name") or "")
    return by_name


def derive_party_contract_links_from_mentions(
    contracts: list[dict], entities: list[dict], mentions: dict[str, list[str]]
) -> PartyClauseLinkResult:
    """Pure, TRUE MANY-TO-MANY (PARTY-TO-MANY-TO-MANY): link EVERY party mentioned in a contract to that
    contract, from the GP-1B per-(contract, party) mention data (`mentions` = {contract_key: [party_names]}).
    This fixes the KG-7 last-write-only provenance join (ADR-0036): a party that signed N contracts now gets
    N `PARTY_TO` links, not one. Contract keys are canonicalized (HYG-1, `canonical_source_doc_id`) to match
    `contract_id`s; party names resolve to `entity_id`s by canonical name (`normalize_entity_name`, first-wins).
    One link per (entity, contract), deduped. A name with no resolved entity is counted `unmatched`; a contract
    key with no `Contract` node (e.g. a dead-lettered doc) is skipped."""
    contract_ids = {contract["contract_id"] for contract in contracts}
    by_name = _name_to_entity(entities)

    links: list[PartyContractLink] = []
    seen: set[tuple[str, str]] = set()
    unmatched = 0
    processed: set[str] = set()
    for contract_key, names in mentions.items():
        contract_id = canonical_source_doc_id(contract_key)
        if contract_id not in contract_ids:
            continue
        processed.add(contract_id)
        for name in names:
            hit = by_name.get(normalize_entity_name(name))
            if hit is None:
                unmatched += 1
                continue
            entity_id, _ = hit
            pair = (entity_id, contract_id)
            if pair in seen:
                continue
            seen.add(pair)
            links.append(PartyContractLink(entity_id=entity_id, contract_id=contract_id, party_name=name))

    return PartyClauseLinkResult(
        links=links, unmatched_parties=unmatched, contracts_processed=len(processed))


def party_clause_linking(store: Any, *, mentions: dict[str, list[str]] | None = None) -> PartyClauseLinkResult:
    """The registered capability: read the populated `Contract` + `Entity` nodes, derive the `PARTY_TO` links,
    write them, and return the result. No re-ingest -- a link pass only. When `mentions` (the GP-1B per-contract
    party cache) is supplied, derives TRUE MANY-TO-MANY links (PARTY-TO-MANY-TO-MANY: a party -> every contract
    it signed); otherwise the single-provenance join (KG-7, one contract per party)."""
    if mentions is not None:
        result = derive_party_contract_links_from_mentions(store.all_contracts(), store.all_entities(), mentions)
    else:
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
