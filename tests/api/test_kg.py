"""EP-API-3 (ADR-0117): scoped KG access + id/format accessors on the engine API. Hermetic tests cover delegation to
the handle's store + the pure id/format accessors; the live test round-trips kg_write/kg_read + span_positions over a
real workspace."""
from __future__ import annotations

import os

import pytest

from rag_wright.api import decode_bbox, document_of, kg_read, kg_write, span_positions
from rag_wright.packs.compliance.capabilities.compliance_store import ComplianceStore


class _FakeStore:
    def __init__(self):
        self.calls = []

    def kg_read(self, node_type, **kw):
        self.calls.append(("read", node_type, kw))
        return [{"span_id": "doc:0:h#0", "bbox": "[0.1,0.2,0.3,0.4]", "doc_start": 1}]

    def kg_write(self, nodes, edges=()):
        self.calls.append(("write", list(nodes), list(edges)))


class _FakeWS:
    def __init__(self, store):
        self._store = store


# --- KG access delegates to the handle's store ---

def test_kg_read_delegates_to_the_handle_store():
    s = _FakeStore()
    out = kg_read(_FakeWS(s), "Requirement", where={"source": "S"}, fields=["requirement_id"])
    assert out and s.calls[0] == ("read", "Requirement", {"where": {"source": "S"}, "fields": ["requirement_id"],
                                                           "distinct": None, "order_by": None, "limit": None,
                                                           "key_range": None})


def test_kg_write_delegates_to_the_handle_store():
    s = _FakeStore()
    kg_write(_FakeWS(s), ["n1"], ["e1"])
    assert s.calls[0] == ("write", ["n1"], ["e1"])


def test_span_positions_reads_spans_and_decodes_bbox():
    s = _FakeStore()
    rows = span_positions(_FakeWS(s), "doc")
    assert s.calls[0][1] == "Span" and s.calls[0][2]["where"] == {"document_id": "doc"}
    assert rows[0]["bbox"] == (0.1, 0.2, 0.3, 0.4)  # JSON string decoded to a tuple


# --- id/format accessors (pure) ---

def test_document_of_is_the_first_id_segment():
    assert document_of("ACME_MSA:3:deadbeef") == "ACME_MSA"
    assert document_of("") == ""


def test_decode_bbox_handles_json_and_none():
    assert decode_bbox("[0.1, 0.2, 0.3, 0.4]") == (0.1, 0.2, 0.3, 0.4)
    assert decode_bbox(None) is None
    assert decode_bbox("not json") is None


# --- live: KG access + span_positions over a real workspace ---

@pytest.mark.store
def test_kg_access_and_span_positions_live():
    from rag_wright.api import EngineConfig, StoreConfig, open_workspace
    from rag_wright.api import KgNode

    cfg = EngineConfig(store=StoreConfig(
        host=os.environ["ARCADEDB_HOST"], port=os.environ["ARCADEDB_PORT"],
        user=os.environ["ARCADEDB_USER"], password=os.environ["ARCADEDB_PASSWORD"],
        protocol=os.getenv("ARCADEDB_PROTOCOL", "http")))
    ws = open_workspace(cfg, corpus="ragwright_epapi3_live", reset=True)
    ComplianceStore(ws._store).ensure_compliance_schema()

    kg_write(ws, [KgNode("Requirement", "requirement_id", {
        "requirement_id": "FTC:255.1:h", "source": "FTC", "citation": "c", "deontic_type": "obligation",
        "actor": "a", "requirement_text": "t", "evidence_standard": "", "severity": "", "applicability_json": [],
        "confidence": "EXTRACTED", "pages": [1], "bbox": None})])
    rows = kg_read(ws, "Requirement", fields=["requirement_id", "source"], where={"source": "FTC"})
    assert rows and rows[0]["requirement_id"] == "FTC:255.1:h"
    assert ComplianceStore.policy_of_requirement(rows[0]["requirement_id"]) == "FTC"  # the pack's id parser
    assert span_positions(ws, "no-such-doc") == []  # no spans ingested -> empty, but the path ran end-to-end
