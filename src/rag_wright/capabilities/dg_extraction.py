"""GP-1(B): docling-graph-based entity extraction from contract text.

docling-graph (IBM/docling-project) is a schema-driven LLM knowledge-graph extractor: you pass a Pydantic
*template* (entities via `model_config=ConfigDict(graph_id_fields=[...])`, relationships via the `edge()`
helper), and `run_pipeline(config, mode="api")` returns `context.extracted_models` (your Pydantic instances)
+ `context.knowledge_graph` (a networkx.DiGraph). This module holds our contract template; the extracted
parties feed our existing `parties_to_extraction -> disambiguate -> resolve(EDGAR CIK) -> write_graph`
pipeline (GP-1B.2), so docling-graph replaces only the LLM extraction step. Model routing (Granite / Gemma /
DeepSeek) is docling-graph's own LiteLLM config seam, driven from our env (GP-1B.3).

`edge()` is intentionally defined here, not imported: the shipped docling-graph example templates each define
this ~15-line helper locally (it is not exported from the package); it only writes `json_schema_extra` keys the
GraphConverter reads (`edge_label`, `graph_reference`, `reference_closed_catalog`).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def edge(
    label: str,
    default: Any = None,
    *,
    reference: bool = False,
    closed_catalog: bool = False,
    default_factory: Any = None,
    **kwargs: Any,
) -> Any:
    """Declare a field as a docling-graph edge via `json_schema_extra` (local helper, mirrors the shipped
    example templates). `label` -> `edge_label`; `reference` -> id-only link; `closed_catalog` -> closed
    reference catalog. A `list[Entity]` edge should pass `default_factory=list`."""
    json_schema_extra: dict[str, Any] = dict(kwargs.pop("json_schema_extra", {}) or {})
    json_schema_extra["edge_label"] = label
    if reference:
        json_schema_extra["graph_reference"] = True
    if closed_catalog:
        json_schema_extra["reference_closed_catalog"] = True
    if default_factory is not None:
        return Field(default_factory=default_factory, json_schema_extra=json_schema_extra, **kwargs)
    return Field(default, json_schema_extra=json_schema_extra, **kwargs)


class Party(BaseModel):
    """A signing party (organization) to the agreement. Its `name` is the stable, document-derived identity
    (docling-graph hashes `graph_id_fields` into the node id; we resolve `name` -> EDGAR CIK downstream)."""

    model_config = ConfigDict(graph_id_fields=["name"], extra="ignore", populate_by_name=True)

    name: str = Field(description="Exact legal name of an organization that is a signing party to the agreement")


class ContractParties(BaseModel):
    """The contract, identified by its title, with the organizations that are its signing parties. Minimal
    schema targeting 1-hop CONTRACTS_WITH (the parties per contract); richer clause/relationship edges can be
    added later. Designed for docling-graph's `direct`/`dense` extraction contracts."""

    model_config = ConfigDict(graph_id_fields=["title"], extra="ignore", populate_by_name=True)

    title: str = Field(description="The contract or agreement title / document name")
    parties: list[Party] = edge(
        "PARTY_TO", default_factory=list,
        description="The organizations that are the signing parties to this agreement (usually two)",
    )
