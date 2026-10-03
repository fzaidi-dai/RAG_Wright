"""AC-journey-2: the `incidents` smoke domain -- a NON-CONTRACT domain built entirely on the engine API as
*config + .ttl + capabilities + seam*, proving a new domain composes the generic engine with NO engine edit and
NO contract assumptions. Test-only (the contract pack stays the engine's reference); this module is a new domain's
own code -- its ingestion/query CAPABILITIES (registered + invoked by name) and a thin product SEAM.

It touches NO `ArcadeDBStore`, embedder, model id, id-string parsing, or contract type -- only `rag_wright.api`
(`kg_write`/`kg_read`/`kg_edges`, `open_workspace`, the invokers) and the seam `KgNode`/`KgEdge` value types.
"""
from __future__ import annotations

from typing import Any

from rag_wright.api import ainvoke_subgraph, kg_edges, kg_write, open_workspace
from rag_wright.capabilities.manifests import CapabilityManifest
from rag_wright.store.seam import KgEdge, KgNode

# --- Step 3: the domain INGESTION capability (a `subgraph`), composing the engine's generic kg_write ------------


async def ingest(resources: Any, inputs: dict) -> dict:
    """Write incident reports into the domain's typed KG: one `Incident` node + one `System` node + an `AFFECTS`
    edge per report, in one transaction via the engine's generic `kg_write`. `inputs["reports"]` =
    [{incident_id, title, severity, system_id, system_name}]."""
    reports = inputs["reports"]
    nodes: list[KgNode] = []
    edges: list[KgEdge] = []
    for r in reports:
        nodes.append(KgNode("Incident", "incident_id", {
            "incident_id": r["incident_id"], "title": r["title"], "severity": r["severity"]}))
        nodes.append(KgNode("System", "system_id", {"system_id": r["system_id"], "name": r["system_name"]}))
        edges.append(KgEdge("AFFECTS", "Incident", "incident_id", r["incident_id"],
                            "System", "system_id", r["system_id"], {}))
    kg_write(resources, nodes, edges)
    return {"written": len(reports)}


# --- Step 4: a domain QUERY capability (a `subgraph`): which incidents affect a system (AFFECTS traversal) ------


async def incidents_for_system(resources: Any, inputs: dict) -> dict:
    """The incidents that AFFECT a given system -- an in-traversal (incoming `AFFECTS` edges -> the source
    `Incident`) over the domain's own types, via the engine's generic `kg_edges`. No contract vocabulary."""
    rows = kg_edges(resources, "System", where={"system_id": inputs["system_id"]}, direction="in",
                    edge_type="AFFECTS",
                    select={"incident_id": "v.incident_id", "title": "v.title", "severity": "v.severity"})
    return {"incidents": rows}


# --- the capability manifests the domain registers (impl_ref -> this module) -----------------------------------

INCIDENT_CAPS = (
    CapabilityManifest(
        slug="incident_ingestion", kind="subgraph", display_name="Incident ingestion",
        description="Write incident reports into the incidents KG (Incident/System/AFFECTS).",
        representative_queries=("ingest these incident reports", "load incidents into the graph"),
        impl_ref="tests.journey.incidents_domain:ingest"),
    CapabilityManifest(
        slug="incident_query", kind="subgraph", display_name="Incidents affecting a system",
        description="The incidents that affect a given system (AFFECTS traversal).",
        representative_queries=("which incidents affect the billing system?", "incidents on system X"),
        impl_ref="tests.journey.incidents_domain:incidents_for_system"),
)


# --- Step 5: the thin product SEAM -----------------------------------------------------------------------------


class IncidentSeam:
    """A new domain's product seam: tenancy (a workspace per corpus) + invoking the domain capabilities BY NAME
    through the engine API. It holds no store/embedder/model-id/contract knowledge -- that is all engine-side."""

    def __init__(self, config) -> None:
        self._config = config

    def open(self, corpus: str, *, reset: bool = False):
        return open_workspace(self._config, corpus=corpus, reset=reset)

    async def ingest_reports(self, ws, reports: list[dict]) -> int:
        out = await ainvoke_subgraph("incident_ingestion", {"reports": reports}, resources=ws)
        return out["written"]

    async def incidents_affecting(self, ws, system_id: str) -> list[dict]:
        out = await ainvoke_subgraph("incident_query", {"system_id": system_id}, resources=ws)
        return out["incidents"]
