"""KG-7 (FR-S.1, ADR-0033/ADR-0036): the Party<->Contract unifying link over the one contract KG.

The two graphs were built separately -- the typed Clause KG (`Clause` + typed property edges) and the party
graph (`Entity` + `CONTRACTS_WITH`/`AFFILIATE_OF`) -- and coexist in one store, but nothing connected a party
to the clauses it is a party to. This capability adds the missing link, WITHOUT re-ingesting either graph: a
pure pass over the already-populated `Contract` and `Entity` nodes that writes `PARTY_TO` edges
(`Entity -> Contract`).

Why an edge (not a `contract_id` field like `Clause` carries): `Clause -> Contract` is many-to-ONE (a clause
belongs to exactly one contract, so its `contract_id` is id-encoded and Contract->Clauses is a free key-range
query). `Party -> Contract` is many-to-MANY -- a deduped `Entity` (one node per CIK) is a party to many
contracts -- which an id/field cannot represent and an edge can (and the edge stays extensible for a future
per-clause party ROLE attribute).

The join is by canonical name: `Contract.parties_json` (the authoritative party names) and `Entity.name` both
pass through `normalize_entity_name`, so surface variants ("Bank of America" / "Bank of America, N.A.") match
the one CIK node. A party with no matching `Entity` (private / unlinked, resolved to no node) is counted, not
dropped -- the same honest gap as the GP-1B relational graph. Contract->Clause needs no edge: `Clause.clause_id`
already encodes `contract_id`, so a party reaches its clauses via `Party -PARTY_TO-> Contract` then the
clause_id key-range.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.corpus.canonicalize import normalize_entity_name


class PartyContractLink(BaseModel):
    """One `PARTY_TO` edge: the party `Entity` (by `entity_id`) is a party to `contract_id`. `party_name` is
    the surface form from `parties_json` that matched (provenance for the link)."""

    entity_id: str
    contract_id: str
    party_name: str


class PartyClauseLinkResult(BaseModel):
    """The capability's output: the derived `PARTY_TO` links plus the honest unmatched-party count."""

    links: list[PartyContractLink]
    unmatched_parties: int  # parties in parties_json with no matching Entity node (private/unlinked gap)
    contracts_processed: int


@runtime_checkable
class _LinkStore(Protocol):
    def all_contracts(self) -> list[dict]: ...  # rows: {contract_id, parties_json}
    def all_entities(self) -> list[dict]: ...  # rows: {entity_id, name}
    def write_party_contract_links(self, links: list[PartyContractLink]) -> None: ...


def _parties_of(contract: dict) -> list[str]:
    raw = contract.get("parties_json") or "[]"
    parties = json.loads(raw) if isinstance(raw, str) else (raw or [])
    return [p for p in parties if isinstance(p, str)]


def derive_party_contract_links(
    contracts: list[dict], entities: list[dict]
) -> PartyClauseLinkResult:
    """Pure: match each contract's `parties_json` names to `Entity` nodes by `normalize_entity_name`, emitting
    one `PARTY_TO` link per (entity, contract). First-wins on a normalized-name collision; a (entity, contract)
    pair is deduped; an unmatched party is counted, never dropped."""
    entity_by_norm: dict[str, str] = {}
    for entity in entities:
        key = normalize_entity_name(entity.get("name") or "")
        if key and key not in entity_by_norm:
            entity_by_norm[key] = entity["entity_id"]

    links: list[PartyContractLink] = []
    seen: set[tuple[str, str]] = set()
    unmatched = 0
    for contract in contracts:
        contract_id = contract["contract_id"]
        for name in _parties_of(contract):
            key = normalize_entity_name(name)
            entity_id = entity_by_norm.get(key) if key else None
            if entity_id is None:
                unmatched += 1
                continue
            pair = (entity_id, contract_id)
            if pair in seen:
                continue
            seen.add(pair)
            links.append(PartyContractLink(entity_id=entity_id, contract_id=contract_id, party_name=name))

    return PartyClauseLinkResult(
        links=links, unmatched_parties=unmatched, contracts_processed=len(contracts))


def party_clause_linking(store: Any) -> PartyClauseLinkResult:
    """The registered capability: read the populated `Contract` + `Entity` nodes, derive the `PARTY_TO` links,
    write them, and return the result (incl. the unmatched-party count). No re-ingest -- a link pass only."""
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
