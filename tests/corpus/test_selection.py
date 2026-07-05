"""Tests for CUAD subset selection (T7, RAC-7, docs/Corpus_Acquisition.md).

The subset is a deliberate recorded filter over the full pull, chosen for archetype coverage, NOT a
first-N slice. These tests pin the coverage guarantees: scanned PDFs are included (vision-to-text
path), the agreement types are spread rather than clustered, multi-party and shared-party contracts
are deliberately included (the raw material for relational/multi-hop eval, T10), the selection is
reproducible, and the manifest records the criteria and coverage stats.
"""

from rag_wright.corpus.selection import (
    ContractMeta,
    SelectionCriteria,
    select_subset,
)

SNAP = "zenodo:4595826"  # pinned CUAD source snapshot


def _contracts():
    """A synthetic full pull: 40 contracts clustered so a first-N slice would be lopsided.

    The first 20 (sorted) are all "License" and single-party, so a first-N selection would pick
    only License contracts and no scanned/shared-party coverage. The coverage-driven selector must
    instead spread across types and pull in the scanned/shared-party/multi-party contracts.
    """
    contracts = []
    # 20 License, single-party, not scanned, ids sort first (aaa*)
    for i in range(20):
        contracts.append(
            ContractMeta(
                contract_id=f"aaa_license_{i:02d}",
                agreement_type="License Agreement",
                parties=[f"Solo Corp {i}"],
                is_scanned=False,
                size_bytes=500_000,
            )
        )
    # 8 scanned across two other types
    for i in range(8):
        contracts.append(
            ContractMeta(
                contract_id=f"mmm_scanned_{i:02d}",
                agreement_type="Supply Agreement" if i % 2 else "Distribution Agreement",
                parties=[f"Scan Co {i}", "Acme Corp"],  # share "Acme Corp"
                is_scanned=True,
                size_bytes=800_000,
            )
        )
    # 12 multi-party / shared-party across more types
    types = ["Hosting Agreement", "Reseller Agreement", "Services Agreement"]
    for i in range(12):
        contracts.append(
            ContractMeta(
                contract_id=f"zzz_multi_{i:02d}",
                agreement_type=types[i % len(types)],
                parties=["Acme Corp", f"Beta LLC {i}"],  # all share "Acme Corp"
                is_scanned=False,
                size_bytes=700_000,
            )
        )
    return contracts


def test_selection_is_not_a_first_n_slice():
    contracts = _contracts()
    criteria = SelectionCriteria(target_max=15, min_scanned=4)
    manifest = select_subset(contracts, criteria, source_snapshot=SNAP)
    # A first-N slice of the sorted contracts would be all "License Agreement"; coverage-driven
    # selection must span multiple agreement types.
    assert len(manifest.agreement_type_counts) >= 3


def test_selection_includes_scanned_for_vision_path():
    contracts = _contracts()
    criteria = SelectionCriteria(target_max=20, min_scanned=4)
    manifest = select_subset(contracts, criteria, source_snapshot=SNAP)
    assert manifest.scanned_count >= 4


def test_selection_includes_shared_party_material_for_multihop():
    contracts = _contracts()
    criteria = SelectionCriteria(target_max=20, min_scanned=2)
    manifest = select_subset(contracts, criteria, source_snapshot=SNAP)
    # At least one group of >=2 selected contracts sharing a party (the multi-hop raw material).
    assert any(len(group) >= 2 for group in manifest.shared_party_groups)


def test_selection_includes_multi_party_contracts():
    contracts = _contracts()
    manifest = select_subset(contracts, SelectionCriteria(target_max=20, min_scanned=2), source_snapshot=SNAP)
    assert manifest.multi_party_count >= 1


def test_selection_respects_target_max():
    contracts = _contracts()
    manifest = select_subset(contracts, SelectionCriteria(target_max=12, min_scanned=2), source_snapshot=SNAP)
    assert len(manifest.selected_ids) <= 12


def test_selection_is_reproducible():
    contracts = _contracts()
    criteria = SelectionCriteria(target_max=15, min_scanned=3)
    a = select_subset(contracts, criteria, source_snapshot=SNAP)
    b = select_subset(contracts, criteria, source_snapshot=SNAP)
    assert a.selected_ids == b.selected_ids


def test_manifest_records_criteria_and_coverage():
    contracts = _contracts()
    criteria = SelectionCriteria(target_max=15, min_scanned=3)
    manifest = select_subset(contracts, criteria, source_snapshot=SNAP)
    assert manifest.criteria == criteria
    assert manifest.source_snapshot == SNAP
    assert sum(manifest.agreement_type_counts.values()) == len(manifest.selected_ids)
    assert manifest.total_size_bytes == sum(
        c.size_bytes for c in contracts if c.contract_id in set(manifest.selected_ids)
    )


def test_selection_covers_min_scanned_when_available_else_all():
    # If fewer scanned exist than requested, take all available rather than fail.
    contracts = [
        ContractMeta(
            contract_id=f"c_{i}",
            agreement_type="License Agreement",
            parties=["X"],
            is_scanned=(i == 0),
            size_bytes=1000,
        )
        for i in range(5)
    ]
    manifest = select_subset(contracts, SelectionCriteria(target_max=5, min_scanned=3), source_snapshot=SNAP)
    assert manifest.scanned_count == 1  # only one scanned available
