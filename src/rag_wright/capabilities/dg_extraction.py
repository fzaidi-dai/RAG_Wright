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

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rag_wright.capabilities.disambiguation import disambiguate
from rag_wright.capabilities.entity_resolution import ResolutionResult, resolve_entities
from rag_wright.capabilities.graph_extraction import parties_to_extraction
from rag_wright.contracts.identifiers import ChunkId, canonical_source_doc_id
from rag_wright.corpus.edgar import normalize_cik, normalize_name
from rag_wright.ontology.registry import EntityRegistry, RegistryRecord

_PRIVATE_RESOLUTIONS = {"PRIVATE", "SKIP"}


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


def build_verified_registry(vset: dict) -> EntityRegistry:
    """An `EntityRegistry` from the human-verified set: each CIK-resolved entity becomes a
    `RegistryRecord(CIK, representative, aliases=variants)`, so an extracted party surface form that matches
    a verified variant resolves to the CIK. PRIVATE/SKIP entities are not in the closed CIK registry
    (`resolve -> None -> unlinked`). This keeps resolution recall high for verified filers, so in the A/B a
    miss reflects EXTRACTION quality (the model didn't produce a matching name), not resolution weakness."""
    registry = EntityRegistry()
    for entity in vset["entities"]:
        resolution = entity["resolution"]
        if resolution in _PRIVATE_RESOLUTIONS:
            continue
        registry.add(RegistryRecord(
            entity_id=normalize_cik(resolution), canonical_name=entity["representative"],
            aliases=list(entity.get("variants", [])),
        ))
    return registry


def resolve_extracted(
    items: list[tuple[str, ContractParties]], *, registry: EntityRegistry,
    private_map: dict[str, str] | None = None,
) -> ResolutionResult:
    """Bridge docling-graph extractions into our resolution pipeline: each contract's extracted parties ->
    `parties_to_extraction` (CONTRACTS_WITH between them, no LLM) -> `disambiguate` -> `resolve_entities`
    (-> EDGAR CIK). `items` = (contract_id, extracted `ContractParties`). The `ResolutionResult` feeds
    `to_graph` -> `store.write_graph` (GP-1B.5). This is the only new glue vs the gold-anchored GP-1(A):
    the parties now come from docling-graph's LLM extraction instead of the verified `coparty_keys`.

    `private_map` (GP-1B.5a): assign verified-PRIVATE parties their golden `PRIVATE:<key>` id (which the CIK
    registry can't produce) so private anchors/answers are recoverable in the relational eval."""
    results = []
    for contract_id, cp in items:
        names = [p.name for p in cp.parties]
        chunk_id = ChunkId.of(canonical_source_doc_id(contract_id), 0, "|".join(names) or contract_id)
        results.append(parties_to_extraction(chunk_id, names))
    resolution = resolve_entities(disambiguate(results), results, registry=registry)
    if private_map:
        resolution = _apply_private_identities(resolution, private_map)
    return resolution


def build_private_map(vset: dict) -> dict[str, str]:
    """`{normalize_name(surface) -> 'PRIVATE:<entity_key>'}` for verified-PRIVATE entities. Lets an extracted
    private party (a non-filer the CIK registry can't resolve) take the golden `PRIVATE:<key>` node id
    (matching `eval.multihop._identity`), instead of an `UNLINKED:<surface>` that misses the golden answer.
    SKIP entities are excluded (they are excluded from the golden set)."""
    private_map: dict[str, str] = {}
    for entity in vset["entities"]:
        if entity["resolution"] != "PRIVATE":
            continue
        pid = f"PRIVATE:{entity['entity_key']}"
        for surface in (entity["representative"], entity["entity_key"], *entity.get("variants", [])):
            key = normalize_name(surface)
            if key:
                private_map.setdefault(key, pid)
    return private_map


def _apply_private_identities(resolution: ResolutionResult, private_map: dict[str, str]) -> ResolutionResult:
    """Fill in `PRIVATE:<key>` ids for still-unlinked (entity_id None) entities/relationship endpoints whose
    surface matches a verified-private entity; drop any edge that becomes a self-loop after the remap."""
    entities = [
        e.model_copy(update={"entity_id": e.entity_id or private_map.get(normalize_name(e.representative))})
        for e in resolution.entities
    ]
    relationships = []
    for rel in resolution.relationships:
        source_id = rel.source_id or private_map.get(normalize_name(rel.source_ref))
        target_id = rel.target_id or private_map.get(normalize_name(rel.target_ref))
        if source_id is not None and source_id == target_id:
            continue
        relationships.append(rel.model_copy(update={"source_id": source_id, "target_id": target_id}))
    return resolution.model_copy(update={"entities": entities, "relationships": relationships})


# --- GP-1B.3: the extraction-model seam (Granite / Gemma / DeepSeek via docling-graph's LiteLLM config) ---
#
# docling-graph's model routing is its own config seam (provider/model/connection), which we drive from our
# env -- compatible with the "no provider flag in node code" rule (the flags live in config + a dated ADR).
# Two reliability fixes are baked in (GP-1B.1/.2 findings): structured_output=False (json_object -- the strict
# nested json_schema trips DeepSeek and mis-formats others; json_object is reliable for all) and a max_tokens
# cap (the unknown-provider generic 8192-token context window else makes docling-graph SKIP the LLM).

_DEFAULT_MAX_TOKENS = 1500
_DEFAULT_PREAMBLE_CHARS = 8000  # parties are named in the preamble; keeps `direct` within the context window


@dataclass(frozen=True)
class ExtractionModel:
    """One extraction-model choice for the A/B: a label + docling-graph provider + model id + connection.
    `provider` is 'openrouter' (Gemma/DeepSeek) or 'ollama' (Granite, local or Modal-hosted)."""

    label: str
    provider: str
    model: str
    base_url: str
    api_key: str | None = None
    inference: str = "remote"


def openrouter_model(label: str, model: str) -> ExtractionModel:
    """A Gemma/DeepSeek model via OpenRouter (our seam's provider), keyed from the OPENROUTER_* env."""
    return ExtractionModel(
        label=label, provider="openrouter", model=model,
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=os.environ.get("OPENROUTER_API_KEY"), inference="remote",
    )


def ollama_model(label: str, model: str, base_url: str | None = None) -> ExtractionModel:
    """A local (or Modal-hosted) Granite model via Ollama -- no API key. `base_url` overrides OLLAMA_BASE_URL
    (used for the Modal-hosted fallback when local memory exceeds the threshold)."""
    return ExtractionModel(
        label=label, provider="ollama", model=model,
        base_url=base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        api_key=None, inference="local",
    )


def build_pipeline_config(source_path: str, model: ExtractionModel, *, template: type = ContractParties,
                          max_tokens: int = _DEFAULT_MAX_TOKENS) -> Any:
    """The docling-graph `PipelineConfig` for a model choice, with the reliability fixes baked in
    (structured_output=False + max_tokens cap). Kept import-light so hermetic tests need no LLM."""
    from docling_graph import PipelineConfig
    from docling_graph.llm_clients.config import (
        ConnectionOverrides,
        GenerationOverrides,
        LlmRuntimeOverrides,
    )
    from pydantic import SecretStr

    connection = ConnectionOverrides(
        base_url=model.base_url,
        api_key=SecretStr(model.api_key) if model.api_key else None,
    )
    return PipelineConfig(
        source=source_path, template=template, backend="llm", inference=model.inference,
        extraction_contract="direct", processing_mode="many-to-one",
        structured_output=False,  # reliability fix: json_object, not the strict nested json_schema
        provider_override=model.provider, model_override=model.model,
        llm_overrides=LlmRuntimeOverrides(
            generation=GenerationOverrides(max_tokens=max_tokens), connection=connection,
        ),
    )


def extract_parties(text: str, model: ExtractionModel, *, template: type = ContractParties,
                    max_tokens: int = _DEFAULT_MAX_TOKENS,
                    preamble_chars: int = _DEFAULT_PREAMBLE_CHARS) -> Any | None:
    """Extract parties from contract `text` with `model` via docling-graph (API mode). Writes the preamble to
    a temp .md (docling-graph needs a path, not a raw string), runs `run_pipeline`, returns the first
    extracted model (a `ContractParties`) or None if extraction yielded nothing."""
    from docling_graph import run_pipeline

    md = Path(tempfile.mkdtemp(prefix="dg_extract_")) / "contract.md"
    md.write_text(text[:preamble_chars], encoding="utf-8")
    ctx = run_pipeline(build_pipeline_config(str(md), model, template=template, max_tokens=max_tokens),
                       mode="api")
    return ctx.extracted_models[0] if ctx.extracted_models else None


# --- KG-2: per-clause typed property extraction (the same seam, the KG-1 clause template) ---

_CLAUSE_MAX_TOKENS = 2000  # a Clause has ~30 typed dims; a well-constrained extraction fits well under this.
# (INGEST-REFACTOR: truncation was NOT a size problem -- unconstrained free-text fields like `document_reference`
# were dumping verbatim clause prose and ballooning the JSON; the fix is field constraints, not a higher cap.)
_CLAUSE_TEXT_CHARS = 12000  # one operative span is short; a generous cap that never truncates a real clause


def extract_clause(text: str, model: ExtractionModel, *, max_tokens: int = _CLAUSE_MAX_TOKENS) -> Any | None:
    """Extract one clause's typed properties from span `text` with `model`, using the KG-1 bridge template
    (`ontology.clause_template.Clause`). Same docling-graph API-mode seam + reliability fixes as
    `extract_parties`; returns the extracted `Clause` (typed properties) or None. The Clause -> our
    `ClausePropertyRecord` contract mapping + the grounding-judge gate live in `spans.clause_kg_extractor`."""
    from rag_wright.ontology.clause_template import Clause

    return extract_parties(
        text, model, template=Clause, max_tokens=max_tokens, preamble_chars=_CLAUSE_TEXT_CHARS
    )
