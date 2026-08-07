"""ADR-0044: the `clause_exception_linking` capability -- the cap<->uncapped carve-out relationship.

Hermetic: the pure proximity derivation (no store) + the capability over a fake store (no DB). Proximity ~ same
liability section: a nearby Uncapped clause is the cap's carve-out; a distant one is not linked (no false
carve-out). The link is INFERRED.
"""

from __future__ import annotations

from rag_wright.capabilities.clause_exception_linking import (
    ClauseExceptionLinkResult,
    clause_exception_linking,
    derive_exception_links,
    register_clause_exception_linking,
)
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.provenance import ConfidenceTag


def _pos(clause_id, function, contract_id, doc_start, doc_end):
    return {"clause_id": clause_id, "function": function, "contract_id": contract_id,
            "doc_start": doc_start, "doc_end": doc_end}


# --- the pure proximity derivation -------------------------------------------------------------------------


def test_links_a_nearby_uncapped_clause_to_its_cap_as_an_inferred_exception():
    positions = [
        _pos("C:5:h", "Cap On Liability", "C", 1000, 1200),
        _pos("C:6:h", "Uncapped Liability", "C", 1300, 1400),  # gap 100 -> same section
    ]
    result = derive_exception_links(positions, window=3000)
    assert len(result.links) == 1 and result.unlinked_exceptions == 0
    link = result.links[0]
    assert link.exception_clause_id == "C:6:h" and link.cap_clause_id == "C:5:h"
    assert link.confidence is ConfidenceTag.INFERRED  # a derived, reasoned link (FR-S.4)


def test_a_distant_uncapped_clause_is_not_linked_no_false_carveout():
    positions = [
        _pos("C:1:h", "Cap On Liability", "C", 0, 100),
        _pos("C:9:h", "Uncapped Liability", "C", 5000, 5100),  # gap ~4900 > window -> unrelated, skip
    ]
    result = derive_exception_links(positions, window=3000)
    assert result.links == [] and result.unlinked_exceptions == 1


def test_an_uncapped_clause_with_no_cap_in_the_contract_is_unlinked():
    result = derive_exception_links([_pos("C:2:h", "Uncapped Liability", "C", 100, 200)])
    assert result.links == [] and result.unlinked_exceptions == 1


def test_links_to_the_nearest_cap_when_several():
    positions = [
        _pos("C:1:h", "Cap On Liability", "C", 0, 100),
        _pos("C:8:h", "Cap On Liability", "C", 2000, 2100),
        _pos("C:9:h", "Uncapped Liability", "C", 2200, 2300),  # nearest to C:8:h (gap 100) not C:1:h
    ]
    result = derive_exception_links(positions, window=3000)
    assert result.links[0].cap_clause_id == "C:8:h"


def test_contracts_are_scoped_no_cross_contract_links():
    positions = [
        _pos("A:1:h", "Cap On Liability", "A", 0, 100),
        _pos("B:1:h", "Uncapped Liability", "B", 50, 150),  # different contract -> not linked to A's cap
    ]
    result = derive_exception_links(positions)
    assert result.links == [] and result.unlinked_exceptions == 1


# --- the capability over a fake store ---------------------------------------------------------------------


class _FakeStore:
    def __init__(self, positions):
        self._positions = positions
        self.written = None

    def clause_positions(self, functions):
        return [p for p in self._positions if p["function"] in functions]

    def write_clause_exception_links(self, links):
        self.written = links


def test_capability_reads_positions_derives_and_writes():
    store = _FakeStore([
        _pos("C:5:h", "Cap On Liability", "C", 1000, 1200),
        _pos("C:6:h", "Uncapped Liability", "C", 1300, 1400),
        _pos("C:7:h", "Governing Law", "C", 1500, 1600),  # a non-liability clause -> never returned by the read
    ])
    result = clause_exception_linking(store)
    assert isinstance(result, ClauseExceptionLinkResult)
    assert len(result.links) == 1 and store.written == result.links  # derived links were written


def test_registers_as_a_function():
    reg = CapabilityRegistry()
    register_clause_exception_linking(reg)
    entry = reg.get("clause_exception_linking")
    assert entry.kind == "function" and entry.contract is ClauseExceptionLinkResult
