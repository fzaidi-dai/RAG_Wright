"""CUAD subset selection (T7, docs/archive/plans/Corpus_Acquisition.md).

The subset is a **deliberate, recorded filter over the full CUAD pull, chosen for archetype
coverage, never a first-N slice**. Given per-contract metadata, `select_subset` picks ~100-150
contracts that: include some scanned PDFs (so the Docling OCR / vision-to-text path is exercised);
spread across the agreement types rather than clustering one; and deliberately include multi-party
contracts and contracts that share a party (the raw material the relational and multi-hop eval
questions are built from, T10). The `SubsetManifest` records the criteria and coverage stats so the
subset is reproducible.

This is pure logic (no network, no PDF parsing). The full pull, scanned detection (via the system
`pdftotext`), and the manifest write live in the CLI (`scripts/acquire_cuad.py`).
"""

from __future__ import annotations

from collections import Counter, defaultdict

from pydantic import BaseModel, Field


class ContractMeta(BaseModel):
    """Metadata for one pulled CUAD contract, the input to selection."""

    contract_id: str
    agreement_type: str
    parties: list[str]  # normalized party surface forms
    is_scanned: bool
    size_bytes: int


class SelectionCriteria(BaseModel):
    """The recorded selection criteria (part of the manifest, so the subset is reproducible)."""

    target_min: int = 100
    target_max: int = 150
    min_scanned: int = 10  # some scanned PDFs for the vision-to-text path
    min_shared_party_contracts: int = 20  # raw material for multi-hop questions (T10)
    min_multi_party: int = 20  # contracts with >= 2 parties


class SubsetManifest(BaseModel):
    """The selected subset plus the criteria and coverage stats. Reproducible from the same input.

    `source_snapshot` pins the exact CUAD source (a Zenodo record id or a GitHub release/commit) so
    "reproducible from the manifest" is real: the same source + the same criteria reproduce the same
    subset.
    """

    source_snapshot: str
    criteria: SelectionCriteria
    selected_ids: list[str]
    agreement_type_counts: dict[str, int]
    scanned_count: int
    multi_party_count: int
    shared_party_groups: list[list[str]] = Field(default_factory=list)
    total_size_bytes: int


def _shared_party_index(contracts: list[ContractMeta]) -> dict[str, list[str]]:
    """party -> sorted contract_ids that name it, for parties shared by >= 2 contracts."""
    by_party: dict[str, list[str]] = defaultdict(list)
    for contract in contracts:
        for party in contract.parties:
            by_party[party].append(contract.contract_id)
    return {
        party: sorted(ids) for party, ids in by_party.items() if len(set(ids)) >= 2
    }


def select_subset(
    contracts: list[ContractMeta], criteria: SelectionCriteria, *, source_snapshot: str
) -> SubsetManifest:
    """Select a coverage-driven subset. Deterministic: same input yields the same manifest.

    `source_snapshot` is the pinned CUAD source (Zenodo record id / GitHub release) recorded in the
    manifest so the subset is reproducible.
    """
    by_id = {c.contract_id: c for c in contracts}
    ordered = sorted(contracts, key=lambda c: c.contract_id)  # deterministic traversal
    shared = _shared_party_index(contracts)
    selected: dict[str, None] = {}  # insertion-ordered set

    def take(contract_id: str) -> None:
        if contract_id in by_id and contract_id not in selected:
            if len(selected) < criteria.target_max:
                selected[contract_id] = None

    # 1. Scanned coverage (take all available if fewer than requested).
    for c in ordered:
        if len([i for i in selected if by_id[i].is_scanned]) >= criteria.min_scanned:
            break
        if c.is_scanned:
            take(c.contract_id)

    # 2. Shared-party coverage: pull whole groups so a shared party links >= 2 selected contracts.
    for _party, ids in sorted(shared.items()):
        if sum(1 for i in selected if len(shared_groups_of(by_id[i], shared)) > 0) >= (
            criteria.min_shared_party_contracts
        ):
            break
        for contract_id in ids:
            take(contract_id)

    # 3. Multi-party coverage.
    for c in ordered:
        if sum(1 for i in selected if len(by_id[i].parties) >= 2) >= criteria.min_multi_party:
            break
        if len(c.parties) >= 2:
            take(c.contract_id)

    # 4. Agreement-type spread: round-robin across types (sorted) until the target is reached.
    by_type: dict[str, list[str]] = defaultdict(list)
    for c in ordered:
        by_type[c.agreement_type].append(c.contract_id)
    made_progress = True
    while len(selected) < criteria.target_max and made_progress:
        made_progress = False
        for _type, ids in sorted(by_type.items()):
            for contract_id in ids:
                if contract_id not in selected:
                    before = len(selected)
                    take(contract_id)
                    if len(selected) > before:
                        made_progress = True
                    break  # one per type per round -> spread, not cluster

    selected_ids = list(selected)
    chosen = [by_id[i] for i in selected_ids]
    selected_set = set(selected_ids)
    shared_groups = [
        sorted(i for i in ids if i in selected_set)
        for ids in shared.values()
    ]
    shared_groups = [g for g in shared_groups if len(g) >= 2]
    # dedupe identical groups deterministically
    unique_groups = sorted({tuple(g) for g in shared_groups})
    return SubsetManifest(
        source_snapshot=source_snapshot,
        criteria=criteria,
        selected_ids=selected_ids,
        agreement_type_counts=dict(Counter(c.agreement_type for c in chosen)),
        scanned_count=sum(1 for c in chosen if c.is_scanned),
        multi_party_count=sum(1 for c in chosen if len(c.parties) >= 2),
        shared_party_groups=[list(g) for g in unique_groups],
        total_size_bytes=sum(c.size_bytes for c in chosen),
    )


def shared_groups_of(contract: ContractMeta, shared: dict[str, list[str]]) -> list[str]:
    """The shared parties this contract participates in (non-empty means it links other contracts)."""
    return [party for party in contract.parties if party in shared]
