"""AC-journey-1 (ADR-0117/0067): a NON-CONTRACT domain's KG schema is created from ITS OWN `.ttl` via
`EngineConfig(pack=...)` -- "config + .ttl", no engine edit. Proves the pack gap is closed: `open_workspace`
reads the configured pack (not the hardcoded contract one), `ensure_schema` creates the domain's vertex/edge
types on top of the always-on engine types, and the contract pack's types are NOT created for a non-contract
domain. Hermetic test pins the loader reads an arbitrary pack; the `-m store` test proves it live end-to-end.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from rag_wright.api import EngineConfig, StoreConfig, kg_read, kg_write, open_workspace
from rag_wright.ontology.loader import load_kg_schema
from rag_wright.store.seam import KgEdge, KgNode

_PACK = str(Path(__file__).with_name("incidents_pack.ttl"))


# --- hermetic: the loader reads an arbitrary (non-contract) pack ttl ----------------------------


def test_load_kg_schema_reads_an_arbitrary_pack_ttl():
    vertices, edges = load_kg_schema(_PACK)
    by_name = {v.name: v for v in vertices}
    assert set(by_name) == {"Incident", "System"}  # the domain's own vertex types, not the contract pack's
    assert dict(by_name["Incident"].properties)["severity"] == "STRING"
    assert by_name["Incident"].unique_index == "incident_id"
    assert "AFFECTS" in edges
    # the default (contract) pack is a DIFFERENT, disjoint set -- the loader is not hardcoded to it
    contract_vertices = {v.name for v in load_kg_schema()[0]}
    assert "Clause" in contract_vertices and "Incident" not in contract_vertices


# --- live ArcadeDB (opt-in): the domain schema is created from config + .ttl, no engine edit ----


def _cfg(pack=None):
    return EngineConfig(
        store=StoreConfig(
            host=os.environ["ARCADEDB_HOST"], port=os.environ["ARCADEDB_PORT"],
            user=os.environ["ARCADEDB_USER"], password=os.environ["ARCADEDB_PASSWORD"],
            protocol=os.getenv("ARCADEDB_PROTOCOL", "http")),
        pack=pack)


@pytest.mark.store
def test_open_workspace_creates_the_domain_schema_from_its_pack(tmp_path):
    ws = open_workspace(_cfg(pack=_PACK), corpus="ragwright_journey_incidents", reset=True)
    types = ws._store.type_names()

    # the domain's own types exist...
    assert {"Incident", "System", "AFFECTS"} <= types
    # ...the always-on engine infra exists (a domain still gets chunks/spans/entities)...
    assert {"Chunk", "Span", "Entity"} <= types
    # ...and the CONTRACT pack's types were NOT created for this non-contract domain.
    assert "Clause" not in types and "Contract" not in types and "PropertyValue" not in types


@pytest.mark.store
def test_domain_kg_write_and_read_roundtrip(tmp_path):
    ws = open_workspace(_cfg(pack=_PACK), corpus="ragwright_journey_incidents", reset=True)

    kg_write(
        ws,
        [KgNode("Incident", "incident_id", {"incident_id": "INC-1", "title": "disk full", "severity": "high"}),
         KgNode("System", "system_id", {"system_id": "SYS-A", "name": "billing"})],
        [KgEdge("AFFECTS", "Incident", "incident_id", "INC-1", "System", "system_id", "SYS-A", {})],
    )

    rows = kg_read(ws, "Incident", where={"severity": "high"})
    assert len(rows) == 1 and rows[0]["incident_id"] == "INC-1" and rows[0]["title"] == "disk full"
