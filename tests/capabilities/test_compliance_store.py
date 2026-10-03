"""EP-REF-1b (R3b, ADR-0117): the compliance READ facade on `ComplianceStore` -- the reference versions the
product seam lifts (EP-SEAM-3). Hermetic tests drive the shaping/parsing over a fake store + canned rows and the
two pure helpers; the `-m store` test writes requirements (with and without page/bbox provenance) into a live
ArcadeDB compliance schema and proves the reads round-trip.
"""
from __future__ import annotations

import json

import pytest

from rag_wright.capabilities.compliance_store import ComplianceStore
from rag_wright.contracts.compliance import DeonticType, Requirement


# --- pure helpers (no store) --------------------------------------------------------------------


def test_policy_of_requirement_splits_on_the_first_colon():
    # a source name may contain dots/hyphens -> split on the FIRST colon, never the last
    assert ComplianceStore.policy_of_requirement("ISO.27001-2022:§5.1:abc123") == "ISO.27001-2022"
    assert ComplianceStore.policy_of_requirement("") == ""


def test_gated_pairs_copies_the_report_field_and_tolerates_absence():
    class _Report:
        gated_pairs = [{"requirement_id": "p:§1:h", "actor": "seller", "scope": "document"}]

    out = ComplianceStore.gated_pairs(_Report())
    assert out == [{"requirement_id": "p:§1:h", "actor": "seller", "scope": "document"}]
    assert out[0] is not _Report.gated_pairs[0]  # a shallow copy, not the report's own dict
    assert ComplianceStore.gated_pairs(object()) == []  # an older report with no field -> [] (never an error)


# --- store reads over a fake store ---------------------------------------------------------------


class _FakeStore:
    """Canned `all_requirements` honoring the DB-side `sources` scope (scalar None = store-wide, [] = none)."""

    def __init__(self, rows):
        self._rows = rows

    def all_requirements(self, sources=None):
        if sources is None:
            return list(self._rows)
        wanted = set(sources)
        return [r for r in self._rows if r.get("source") in wanted]


_ROWS = [
    {"requirement_id": "p1:§1:h", "source": "p1", "citation": "§ 1", "requirement_text": "must do X",
     "pages": [3], "bbox": json.dumps([1.0, 2.0, 3.0, 4.0])},
    {"requirement_id": "p1:§2:h", "source": "p1", "citation": "§ 2", "requirement_text": "must do Y",
     "pages": [], "bbox": None},  # curated before provenance -> no page/bbox
    {"requirement_id": "p2:§1:h", "source": "p2", "citation": "§ 1", "requirement_text": "must do Z",
     "pages": [7], "bbox": "not-json"},  # malformed bbox -> None, page still known
]


def test_requirements_for_is_scoped_to_one_policy():
    store = ComplianceStore(_FakeStore(_ROWS))
    out = store.requirements_for("p1")
    assert {r["requirement_id"] for r in out} == {"p1:§1:h", "p1:§2:h"}


def test_requirement_locations_decode_pages_and_bbox_defensively():
    locs = {loc.requirement_id: loc for loc in ComplianceStore(_FakeStore(_ROWS)).requirement_locations("p1")}
    assert locs["p1:§1:h"].pages == [3] and locs["p1:§1:h"].bbox == (1.0, 2.0, 3.0, 4.0)
    assert locs["p1:§1:h"].citation == "§ 1" and locs["p1:§1:h"].text == "must do X"
    assert locs["p1:§2:h"].pages == [] and locs["p1:§2:h"].bbox is None  # absent provenance -> empty / None

    p2 = ComplianceStore(_FakeStore(_ROWS)).requirement_locations("p2")[0]
    assert p2.pages == [7] and p2.bbox is None  # malformed bbox degrades to no rectangle, page kept


def test_curated_requirement_count_scopes_or_counts_store_wide():
    store = ComplianceStore(_FakeStore(_ROWS))
    assert store.curated_requirement_count() == 3  # store-wide
    assert store.curated_requirement_count(["p1"]) == 2
    assert store.curated_requirement_count([]) == 0  # scope-to-nothing


# --- live ArcadeDB (opt-in) ----------------------------------------------------------------------


def _req(source: str, i: int, *, pages=None, bbox=None) -> Requirement:
    text = f"{source} rule {i}: a party must do the thing."
    return Requirement(
        requirement_id=Requirement.make_id(source, f"s{i}", text), source=source,
        citation=f"§ {i}", deontic_type=DeonticType.OBLIGATION, actor="party",
        applicability_scope=[], requirement_text=text, pages=pages or [], bbox=bbox)


@pytest.fixture
def store():
    from rag_wright.store.arcadedb import ArcadeDBStore

    s = ArcadeDBStore.from_env(database="ragwright_test_compliance_store", reset=True)
    s.ensure_compliance_schema()
    yield s
    s.close()


@pytest.mark.store
def test_compliance_read_facade_roundtrip_live(store):
    cs = ComplianceStore(store)
    cs.write_requirements([
        _req("p1", 0, pages=[3], bbox=(1.0, 2.0, 3.0, 4.0)),
        _req("p1", 1),  # no provenance
        _req("p2", 0, pages=[7]),
    ])

    assert cs.curated_requirement_count() == 3
    assert cs.curated_requirement_count(["p1"]) == 2
    assert {r["source"] for r in cs.requirements_for("p1")} == {"p1"} and len(cs.requirements_for("p1")) == 2

    by_id = {loc.requirement_id: loc for loc in cs.requirement_locations("p1")}
    withprov = _req("p1", 0).requirement_id
    assert by_id[withprov].pages == [3] and by_id[withprov].bbox == (1.0, 2.0, 3.0, 4.0)
    noprov = _req("p1", 1).requirement_id
    assert by_id[noprov].pages == [] and by_id[noprov].bbox is None
    assert ComplianceStore.policy_of_requirement(withprov) == "p1"
