"""AC-journey-2 (ADR-0117): the capstone -- a NON-CONTRACT domain runs end-to-end through `rag_wright.api` as
*config + .ttl + capabilities + seam*, with no engine edit and no contract assumptions.

The `incidents` domain (tests/journey/incidents_domain.py) registers its OWN ingestion + query capabilities
(impl_ref -> its own code) and drives them through a thin seam. The `-m store` test runs the real journey over a
live workspace: open (its pack) -> ingest reports -> graph-query which incidents affect a system -> attribute
read -- proving the generic engine serves a brand-new domain. A hermetic test proves the caps register + resolve.
"""
from __future__ import annotations

import os

import pytest

from rag_wright.api import EngineConfig, StoreConfig, kg_read
from rag_wright.capabilities import manifests as m
from rag_wright.capabilities.invoke import capability_impl
from rag_wright.capabilities.manifests import register_capability

from tests.journey.incidents_domain import INCIDENT_CAPS, IncidentSeam

_PACK = os.path.join(os.path.dirname(__file__), "incidents_pack.ttl")
_REPORTS = [
    {"incident_id": "INC-1", "title": "disk full on billing", "severity": "high",
     "system_id": "SYS-BILL", "system_name": "billing"},
    {"incident_id": "INC-2", "title": "latency spike on billing", "severity": "low",
     "system_id": "SYS-BILL", "system_name": "billing"},
    {"incident_id": "INC-3", "title": "auth outage", "severity": "high",
     "system_id": "SYS-AUTH", "system_name": "auth"},
]


@pytest.fixture
def register_incident_caps(monkeypatch):
    # isolate: register the domain caps into a COPY of the runtime catalog so the global stays the reference pack
    # (the authoring-contract guard asserts MANIFEST_SPECS <= the engine's canonical slugs).
    monkeypatch.setattr(m, "MANIFEST_SPECS", dict(m.MANIFEST_SPECS))
    for cap in INCIDENT_CAPS:
        register_capability(cap)
    return None


def test_a_new_domains_caps_register_and_resolve_by_name(register_incident_caps):
    # the capability-runtime step: a new domain's caps are in the catalog and resolve to their impl via impl_ref
    assert "incident_ingestion" in m.MANIFEST_SPECS and "incident_query" in m.MANIFEST_SPECS
    from tests.journey.incidents_domain import incidents_for_system, ingest
    assert capability_impl("incident_ingestion") is ingest
    assert capability_impl("incident_query") is incidents_for_system


def _cfg():
    return EngineConfig(
        store=StoreConfig(
            host=os.environ["ARCADEDB_HOST"], port=os.environ["ARCADEDB_PORT"],
            user=os.environ["ARCADEDB_USER"], password=os.environ["ARCADEDB_PASSWORD"],
            protocol=os.getenv("ARCADEDB_PROTOCOL", "http")),
        pack=_PACK)


@pytest.mark.store
async def test_incidents_domain_journey_end_to_end(register_incident_caps):
    seam = IncidentSeam(_cfg())
    ws = seam.open("ragwright_journey_incidents", reset=True)  # Step 0+1: config + its own .ttl pack schema

    # Step 3: ingest via the domain ingestion capability (composes kg_write) -- through the seam, by name
    written = await seam.ingest_reports(ws, _REPORTS)
    assert written == 3

    # Step 4: a graph query -- which incidents affect the billing system (AFFECTS traversal)
    billing = await seam.incidents_affecting(ws, "SYS-BILL")
    assert {r["incident_id"] for r in billing} == {"INC-1", "INC-2"}  # not INC-3 (auth)
    assert {r["incident_id"] for r in await seam.incidents_affecting(ws, "SYS-AUTH")} == {"INC-3"}

    # Step 4 (attribute read via the API): high-severity incidents, straight kg_read on the domain's own type
    high = kg_read(ws, "Incident", where={"severity": "high"})
    assert {r["incident_id"] for r in high} == {"INC-1", "INC-3"}

    # the journey used only the engine API + the domain's own types -- the contract pack's types were never created
    types = ws._store.type_names()
    assert {"Incident", "System", "AFFECTS"} <= types
    assert "Clause" not in types and "Contract" not in types
