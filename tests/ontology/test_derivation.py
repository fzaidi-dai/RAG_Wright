"""Tests for ontology + registry derivation (T8, FR-C.8 / FR-C.7, RAC-8).

Two parts: reconcile the T4 ClauseCategory ontology against the real CUAD master_clauses.csv columns
(tolerating the CSV's casing/spacing artifacts), and build the EDGAR entity registry with CIKs as
canonical entity_ids. The load-bearing test is the registry one: it proves messy EDGAR CIK forms
all normalize to the canonical EntityId (via the single normalize_cik point) and that lookup is
closed-world (unknown surface forms resolve to None, never fabricated) -- this is what prevents the
graph from fragmenting.
"""


from rag_wright.contracts.identifiers import EntityId
from rag_wright.contracts.ontology import ClauseCategory
from rag_wright.ontology.derive import (
    clause_category_columns,
    reconcile_clause_categories,
)
from rag_wright.corpus.edgar import build_edgar_registry
from rag_wright.ontology.registry import EntityRegistry, RegistryRecord


# --- ontology reconciliation (RAC-8, FR-C.8) ------------------------------------------------


def test_clause_category_columns_drops_filename_and_answer_columns():
    header = [
        "Filename",
        "Document Name",
        "Document Name-Answer",
        "Parties",
        "Parties-Answer",
        "Notice Period To Terminate Renewal",
        "Notice Period To Terminate Renewal- Answer",  # CSV spacing artifact
    ]
    assert clause_category_columns(header) == [
        "Document Name",
        "Parties",
        "Notice Period To Terminate Renewal",
    ]


def test_reconcile_matches_all_41_case_insensitively():
    cols = [c.value for c in ClauseCategory]
    rec = reconcile_clause_categories(cols)
    assert rec.ok
    assert len(rec.matched) == 41
    assert not rec.missing and not rec.extra


def test_reconcile_absorbs_csv_casing_variants():
    # The CSV headers use "Ip Ownership Assignment"; the ontology keeps canonical "IP ...".
    cols = [c.value for c in ClauseCategory]
    cols = ["Ip Ownership Assignment" if c == "IP Ownership Assignment" else c for c in cols]
    rec = reconcile_clause_categories(cols)
    assert rec.ok
    assert rec.matched["Ip Ownership Assignment"] is ClauseCategory.IP_OWNERSHIP_ASSIGNMENT


def test_reconcile_reports_missing_and_extra():
    cols = [c.value for c in ClauseCategory if c is not ClauseCategory.INSURANCE]
    cols.append("Force Majeure")  # a category CUAD/ontology does not have
    rec = reconcile_clause_categories(cols)
    assert not rec.ok
    assert ClauseCategory.INSURANCE in rec.missing
    assert "Force Majeure" in rec.extra


# --- entity registry: canonical CIK + closed-world lookup (RAC-8, FR-C.7) -------------------


_TICKERS = [
    {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},  # int CIK
    {"cik_str": "789019", "ticker": "MSFT", "title": "Microsoft Corporation"},  # str CIK
    {"cik_str": "CIK0000078003", "ticker": "PFE", "title": "PFIZER INC"},  # prefixed CIK
]


def _registry():
    return build_edgar_registry(
        _TICKERS, aliases_by_cik={"0000320193": ["Apple Computer, Inc."]}
    )


def test_registry_normalizes_messy_cik_forms_to_canonical_entity_id():
    reg = _registry()
    # int, string, and CIK-prefixed all land on the canonical 10-digit EntityId.
    assert reg.resolve("Apple Inc.") == EntityId.of("0000320193")
    assert reg.resolve("Microsoft Corporation") == EntityId.of("0000789019")
    assert reg.resolve("PFIZER INC") == EntityId.of("0000078003")


def test_registry_resolves_by_ticker_and_alias():
    reg = _registry()
    assert reg.resolve("AAPL") == EntityId.of("0000320193")
    assert reg.resolve("Apple Computer, Inc.") == EntityId.of("0000320193")  # former-name alias


def test_registry_lookup_is_case_and_punctuation_insensitive():
    reg = _registry()
    assert reg.resolve("apple inc") == EntityId.of("0000320193")


def test_registry_is_closed_world_unknown_resolves_to_none():
    reg = _registry()
    assert reg.resolve("Nonpublic Family Holdings LLC") is None  # not fabricated


def test_registry_get_by_canonical_id():
    reg = _registry()
    record = reg.get(EntityId.of("0000320193"))
    assert isinstance(record, RegistryRecord)
    assert record.canonical_name == "Apple Inc."
    assert record.ticker == "AAPL"


def test_registry_skips_invalid_cik_rows_without_fabricating():
    rows = _TICKERS + [{"cik_str": "not-a-cik", "ticker": "BAD", "title": "Broken Row Inc."}]
    reg = build_edgar_registry(rows)
    assert len(reg) == 3  # the broken row is skipped, not invented
    assert reg.resolve("Broken Row Inc.") is None
