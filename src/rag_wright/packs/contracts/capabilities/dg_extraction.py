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

import asyncio
import contextvars
import logging
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache, partial
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rag_wright.capabilities.disambiguation import disambiguate
from rag_wright.capabilities.entity_resolution import ResolutionResult, resolve_entities
from rag_wright.packs.contracts.capabilities.graph_extraction import parties_to_extraction
from rag_wright.contracts.identifiers import ChunkId, canonical_source_doc_id
from rag_wright.packs.contracts.corpus.edgar import normalize_cik, normalize_name
from rag_wright.ontology.registry import EntityRegistry, RegistryRecord

_DOCLING_LOGGER = "docling_graph"  # the package-root logger; children propagate their ERROR records up to it


class ExtractionFailed(Exception):
    """PROD-3 lossless invariant (ADR-0050): a docling-graph extraction call ERRORED (e.g. a truncated / invalid
    LLM JSON response) rather than returning a clean result. docling-graph LOGS such a failure and then SWALLOWS it,
    returning an empty result -- indistinguishable from a genuine no-content extraction unless we watch the log. We
    RAISE this so the ingest graph can retry and, on exhaustion, dead-letter / flag the document, instead of
    silently writing empty extractions. `stage` = 'party' | 'clause'; `reason` = the captured error message."""

    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(f"{stage} extraction failed: {reason}")
        self.stage = stage
        self.reason = reason


@contextmanager
def capture_docling_errors():
    """Capture ERROR-level log records emitted by docling-graph (the package logger `docling_graph`; children
    propagate up) during an extraction call. The captured messages are appended to the yielded list -- a non-empty
    list after the call means the extraction FAILED (vs a genuine clean-empty result)."""
    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno >= logging.ERROR:
                captured.append(record.getMessage())

    logger = logging.getLogger(_DOCLING_LOGGER)
    handler = _Capture()
    handler.setLevel(logging.ERROR)
    logger.addHandler(handler)
    try:
        yield captured
    finally:
        logger.removeHandler(handler)

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
    resolution = resolve_entities(disambiguate(results), results, resolver=registry)
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
    # OpenRouter provider routing from the model's PROFILE (ADR-0100), e.g. a hard pin
    # {"only": ["deepinfra/bf16"], "allow_fallbacks": False}. Threaded into the litellm extraction call so the
    # extraction surface honors the same provider pin as the seam. None -> the env/sort default in `_call_api`.
    provider_routing: dict[str, Any] | None = None


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


def vllm_model(label: str, model: str) -> ExtractionModel:
    """A self-hosted Granite model via the vLLM OpenAI-compatible server (litellm `hosted_vllm` provider);
    base_url/key from the `VLLM_*` env (MS1-3, ADR-0039). This is the product-substrate extraction path."""
    return ExtractionModel(
        label=label, provider="hosted_vllm", model=model,
        base_url=os.environ["VLLM_BASE_URL"], api_key=os.getenv("VLLM_API_KEY", "rw-vllm-dev-key"),
        inference="remote",
    )


_PRODUCT_EXTRACT_DEFAULT = "qwen3.8-27b-modal-or"  # the built-in extraction default (matches _PRODUCT_LLM)


def default_extraction_model(label: str = "clause-extract", model: str | None = None) -> ExtractionModel:
    """The clause/party/claim extraction model for the SELECTED serving backend (MS1-3, ADR-0039). The
    docling-graph extraction is a SEPARATE model surface from the profile seam, so it reads the same `RAG_SERVING`
    switch here (vLLM when `RAG_SERVING=vllm`, else OpenRouter). One env flips chunk + extract + judge together.

    DEFAULT resolution (parallels `models.profiles.model_for`): an EXPLICIT `model` (the caller's `extract_model`,
    passed through) always wins; otherwise `RAG_MODEL_ALL` (the point-every-role-at-one-model knob) is honored,
    then the built-in default (`_PRODUCT_EXTRACT_DEFAULT`). So `RAG_MODEL_ALL=<id>` now genuinely covers the two
    extraction surfaces too (clause + claim), not just the `model_for` roles -- and a caller-supplied model
    argument is unaffected.

    ADR-0100: the backend/base_url/served-id come from the model string's PROFILE (`resolve_connection`), so a
    string can pin OpenRouter or a self-hosted vLLM/Modal server -- mix per stage. An un-pinned string falls back
    to `RAG_SERVING`, unchanged from before."""
    from rag_wright.models.profiles import profile_for
    from rag_wright.models.seam import resolve_connection

    model = model or os.getenv("RAG_MODEL_ALL") or _PRODUCT_EXTRACT_DEFAULT
    conn = resolve_connection(model)
    # ADR-0100: carry the model's PROFILE provider routing (e.g. the deepinfra/bf16 pin) onto the extraction
    # surface too, so a pin set once in the profile holds engine-wide (seam AND extraction), not just the seam.
    routing = (profile_for(model).extra_body or {}).get("provider")
    return ExtractionModel(label=label, provider=conn.provider, model=conn.served_model_id,
                           base_url=conn.base_url, api_key=conn.api_key,
                           inference="local" if conn.backend == "ollama" else "remote",
                           provider_routing=routing)


# INGEST-GRAPH-LATENCY: docling-graph's default per-call timeout is 300s (ReliabilityDefaults.timeout_s), which
# let one stuck extract_parties call block a document for ~5 min. A single granite call is ~10s, so cap it far
# lower and bound the retry exposure -- a hang now fails fast and the caller's per-item tolerance skips it.
_DEFAULT_TIMEOUT_S = 90
_DEFAULT_MAX_RETRIES = 1


def build_pipeline_config(source_path: str, model: ExtractionModel, *, template: type = ContractParties,
                          max_tokens: int = _DEFAULT_MAX_TOKENS,
                          timeout_s: int = _DEFAULT_TIMEOUT_S,
                          max_retries: int = _DEFAULT_MAX_RETRIES,
                          temperature: float | None = None,
                          structured_output: bool = False,
                          extraction_contract: str = "direct", stage_label: str | None = None,
                          gleaning: bool = True) -> Any:
    """The docling-graph `PipelineConfig` for a model choice, with the reliability fixes baked in
    (structured_output=False + max_tokens cap + a sane per-call `timeout_s`/`max_retries`, NOT docling-graph's
    300s default). Kept import-light so hermetic tests need no LLM.

    `extraction_contract` defaults to "direct" (one full-document call -- right for CONTRACTS: the parties
    live in the 8k preamble). LONG documents (regulations) must pass "auto"/"dense": on a doc that dwarfs the
    output budget, "direct" SILENTLY self-rations (measured: FTC §255.5 -> 6 rules direct vs 31 dense), whereas
    "dense" is skeleton-then-fill over chunks and auto-retries truncation by splitting. See
    [[docling-graph-extraction-contract]].

    `gleaning` (issue 0019): docling-graph's `gleaning_enabled` defaults to True, adding a SECOND full-document
    LLM call after the extraction -- "extract any ADDITIONAL information not already extracted" -- a completeness
    pass. It is right where there is more to find (ingestion of a full clause) but pure waste on the QUERY leg,
    where a short question has nothing to glean (measured: the second call returned empty and doubled query cost +
    latency). Default True preserves ingestion behavior; the query-leg constraint extraction passes gleaning=False."""
    from docling_graph import PipelineConfig
    from docling_graph.llm_clients.config import (
        ConnectionOverrides,
        GenerationOverrides,
        LlmRuntimeOverrides,
        ReliabilityOverrides,
        resolve_effective_model_config,
    )
    from pydantic import SecretStr

    connection = ConnectionOverrides(
        base_url=model.base_url,
        api_key=SecretStr(model.api_key) if model.api_key else None,
    )
    overrides = LlmRuntimeOverrides(
        generation=GenerationOverrides(max_tokens=max_tokens, temperature=temperature),
        reliability=ReliabilityOverrides(timeout_s=timeout_s, max_retries=max_retries),
        connection=connection,
    )
    # ASYNC-A4 (ADR-0057): inject OUR deadline-bounded client via PipelineConfig.llm_client (a seam docling-graph
    # honors at pipeline/stages.py:559). Its LLM call runs `litellm.acompletion` under a TRUE asyncio.timeout, so
    # a slow-drip extraction is cancelled (socket torn down) at the deadline instead of running for minutes --
    # docling-graph's own request-building and response-parsing are reused unchanged; NO fork.
    effective = resolve_effective_model_config(model.provider, model.model, overrides=overrides)
    llm_client = _deadline_bounded_client_class()(model_config=effective)
    llm_client._stage_label = stage_label  # ADR-0058/issue 0005: name the stage in the deadline timeout message
    llm_client._base_url = getattr(model, "base_url", "") or ""  # for OpenRouter provider routing (sort=latency)
    llm_client._provider_routing = getattr(model, "provider_routing", None)  # profile pin, e.g. deepinfra/bf16
    return PipelineConfig(
        source=source_path, template=template, backend="llm", inference=model.inference,
        extraction_contract=extraction_contract, processing_mode="many-to-one",
        # default json_object (OpenRouter's strict json_schema returns nothing, GP-1B.2); but vLLM's guided
        # decoding (xgrammar) CONSTRAINS the decoder to the schema, so structured_output=True works + is stricter.
        structured_output=structured_output,
        provider_override=model.provider, model_override=model.model,
        llm_overrides=overrides,
        llm_client=llm_client,
        gleaning_enabled=gleaning,  # issue 0019: off on the query leg (nothing to glean from a short question)
    )


@lru_cache(maxsize=1)
def _deadline_bounded_client_class() -> type:
    """The docling-graph LLM client that runs its call ASYNC under our true wall-clock deadline (ADR-0057,
    ASYNC-A4). Defined lazily (docling-graph imported only on first real extraction) so the module stays
    import-light. Subclasses `LiteLLMClient` and overrides ONLY `_call_api` -- the single point that calls
    litellm -- so all of docling-graph's message building, request building, response parsing, and diagnostics
    are reused unchanged. `litellm.acompletion` is truly cancellable, and `run_pipeline` is synchronous with no
    running event loop (it is called directly, or off the loop via `asyncio.to_thread` in `aextract_*`), so
    `asyncio.run` creates a fresh loop and the socket is torn down at the deadline."""
    from docling_graph.exceptions import ClientError
    from docling_graph.llm_clients.litellm import LiteLLMClient

    from rag_wright.models import seam

    class _DeadlineBoundedLiteLLMClient(LiteLLMClient):
        def _call_api(self, messages: list[dict[str, str]], **params: Any) -> tuple[str, dict[str, Any]]:
            import litellm

            request = self._build_request(messages, **params)
            # For an OpenRouter extraction call: (1) route the provider -- pin an explicit provider order
            # (env OPENROUTER_PROVIDER_ORDER, comma-separated, no fallbacks) for determinism, else prefer the
            # lowest-latency provider (env OPENROUTER_SORT, default 'latency'); (2) DISABLE reasoning. The product
            # default granite-4.2-8b is a reasoning model: on a forced structured call it returns empty `content`
            # unless reasoning is disabled (ADR-0079). The seam applies both via the model profile; the
            # docling-graph path uses litellm, so it is added here. Skipped for a non-OpenRouter (vLLM / local) base.
            from rag_wright.models import tracing
            from rag_wright.models import usage as usage_acct
            traced = tracing.tracing_on()
            capture = traced or usage_acct.usage_capturing()  # issue 0042: also capture into an active usage scope
            if "openrouter" in (getattr(self, "_base_url", "") or "").lower():
                # provider routing precedence: OPENROUTER_PROVIDER_ORDER env (measurement override) > the model's
                # PROFILE pin (ADR-0100, e.g. deepinfra/bf16 for Qwen3.8-27b) > the lowest-latency sort default.
                _order = os.getenv("OPENROUTER_PROVIDER_ORDER", "").strip()
                _profile_prov = getattr(self, "_provider_routing", None)
                if _order:
                    _prov = {"order": [p.strip() for p in _order.split(",") if p.strip()], "allow_fallbacks": False}
                elif _profile_prov:
                    _prov = _profile_prov
                else:
                    _prov = {"sort": os.getenv("OPENROUTER_SORT", "latency")}
                _reason_on = os.getenv("RAG_EXTRACT_REASONING", "0") == "1"  # A/B toggle (default OFF)
                _eb = {**(request.get("extra_body") or {}), "provider": _prov, "reasoning": {"enabled": _reason_on}}
                if capture:
                    _eb["usage"] = {"include": True}  # ask OpenRouter to return the actual per-call cost (issue 0017)
                request["extra_body"] = _eb

            async def _go() -> Any:
                async with asyncio.timeout(seam._MODEL_DEADLINE_S):
                    return await litellm.acompletion(**request)

            import time as _time
            # 0048: open the generation BEFORE the call so Langfuse's own latency is the real duration (not ~0);
            # ended on success AND on the error paths below so no span is left dangling.
            _gen = tracing.start_generation(
                model=self.model, input=messages,
                label=getattr(self, "_stage_label", None) or "docling-graph-extract", stage="litellm") \
                if traced else None
            _t0 = _time.monotonic()
            try:
                response = asyncio.run(_go())
            except TimeoutError as exc:
                tracing.finish_generation(_gen, latency_ms=(_time.monotonic() - _t0) * 1000.0,
                                          metadata={"error": "timeout"})
                raise seam.ModelCallTimeout(
                    f"docling-graph extraction on {seam._call_desc(self.model, getattr(self, '_stage_label', None))} "
                    f"exceeded the {seam._MODEL_DEADLINE_S}s deadline") from exc
            except Exception as exc:  # noqa: BLE001 - wrap like the base's _call_api (docling-graph ClientError)
                tracing.finish_generation(_gen, latency_ms=(_time.monotonic() - _t0) * 1000.0,
                                          metadata={"error": type(exc).__name__})
                raise ClientError(f"LiteLLM async call failed: {type(exc).__name__}",
                                  details={"model": self.model, "error": str(exc)}, cause=exc) from exc

            choices = response.get("choices", [])
            if not choices:
                tracing.finish_generation(_gen, latency_ms=(_time.monotonic() - _t0) * 1000.0,
                                          metadata={"error": "no_choices"})
                raise ClientError("LiteLLM returned no choices", details={"model": self.model})
            content = choices[0].get("message", {}).get("content")
            if not content:
                tracing.finish_generation(_gen, latency_ms=(_time.monotonic() - _t0) * 1000.0,
                                          metadata={"error": "empty_content"})
                raise ClientError("LiteLLM returned empty content", details={"model": self.model})
            _usage_obj = response.get("usage")
            _latency_ms = (_time.monotonic() - _t0) * 1000.0
            if capture:
                # docling-graph party/clause extraction -- the litellm path (invisible to the seam). Read token
                # usage AND OpenRouter's ACTUAL cost (usage.cost / litellm response_cost); record into any active
                # usage scope (issue 0042) and, when tracing, end the generation (issue 0017/0048).
                _in = int(getattr(_usage_obj, "prompt_tokens", 0) or 0) if _usage_obj else 0
                _out = int(getattr(_usage_obj, "completion_tokens", 0) or 0) if _usage_obj else 0
                cost = getattr(_usage_obj, "cost", None) if _usage_obj else None
                if cost is None:
                    cost = (getattr(response, "_hidden_params", {}) or {}).get("response_cost")
                usage_acct.record_usage(self.model, input_tokens=_in, output_tokens=_out, cost=cost,
                                        latency_ms=_latency_ms)
                _gid = response.get("id")  # 0048: OpenRouter generation id for queue-vs-gen attribution
                tracing.finish_generation(
                    _gen, output=str(content),
                    usage=({"input": _in, "output": _out} if _usage_obj else None), cost=cost,
                    latency_ms=_latency_ms,
                    metadata=({"openrouter_generation_id": _gid} if _gid else None))
            else:
                tracing.finish_generation(_gen, latency_ms=_latency_ms)  # end the span even when not capturing usage
            metadata = {"finish_reason": choices[0].get("finish_reason"),
                        "model": response.get("model", self.model), "usage": _usage_obj}
            return str(content), metadata

    return _DeadlineBoundedLiteLLMClient


def extract_parties(text: str, model: ExtractionModel, *, template: type = ContractParties,
                    max_tokens: int = _DEFAULT_MAX_TOKENS,
                    preamble_chars: int = _DEFAULT_PREAMBLE_CHARS,
                    timeout_s: int = _DEFAULT_TIMEOUT_S,
                    temperature: float | None = None,
                    structured_output: bool = False,
                    extraction_contract: str = "direct", stage: str = "party",
                    gleaning: bool = True) -> Any | None:
    """Extract from `text` with `model` via docling-graph (API mode). Writes the preamble to a temp .md
    (docling-graph needs a path, not a raw string), runs `run_pipeline`, returns the first extracted model or
    None. `extraction_contract` defaults to "direct" (contracts); pass "auto"/"dense" for long docs
    (regulations) so a single call does not silently self-ration -- see build_pipeline_config.

    PROD-3 lossless invariant (ADR-0050): docling-graph LOGS an LLM/parse failure and then SWALLOWS it, returning
    an empty result. We capture the ERROR log during the call and RAISE `ExtractionFailed(stage, reason)` on a
    failure, so the ingest graph retries and (on exhaustion) dead-letters / flags the document instead of silently
    writing empty extractions. A genuine clean-empty result (no error logged) still returns None."""
    from docling_graph import run_pipeline

    md = Path(tempfile.mkdtemp(prefix="dg_extract_")) / "contract.md"
    md.write_text(text[:preamble_chars], encoding="utf-8")
    with capture_docling_errors() as errors:
        ctx = run_pipeline(build_pipeline_config(str(md), model, template=template, max_tokens=max_tokens,
                                                 timeout_s=timeout_s, temperature=temperature,
                                                 structured_output=structured_output,
                                                 extraction_contract=extraction_contract,
                                                 stage_label=f"dg_extraction.{stage}",  # issue 0005
                                                 gleaning=gleaning),
                           mode="api")
    if errors:  # docling logged an error then swallowed it -> a failure, NOT a clean-empty result -> raise
        raise ExtractionFailed(stage, errors[-1][:300])
    return ctx.extracted_models[0] if ctx.extracted_models else None


_EXTRACTION_EXECUTOR: ThreadPoolExecutor | None = None
_EXTRACTION_EXECUTOR_LOCK = threading.Lock()


def extraction_executor() -> ThreadPoolExecutor:
    """EXEC-1: the DEDICATED thread pool for the (network-bound) docling-graph extraction offload
    (clause / party / claim / requirement all funnel through `aextract_parties`). Sized by `RAG_EXTRACT_WORKERS`
    (default 32), so extraction concurrency is bounded by OUR semaphores (`CLAUSE_CONCURRENCY`, the cross-doc
    `max_concurrency`) + the deployment -- NOT asyncio's default `min(32, cpu+4)` executor, which is CPU-derived,
    machine-dependent, and would conflate extraction with the genuine CPU work (parse / embed / resolve / DB
    writes) that stays on the default pool. Lazy singleton, reused for the process lifetime."""
    global _EXTRACTION_EXECUTOR
    if _EXTRACTION_EXECUTOR is None:
        with _EXTRACTION_EXECUTOR_LOCK:
            if _EXTRACTION_EXECUTOR is None:
                workers = max(1, int(os.environ.get("RAG_EXTRACT_WORKERS", "32")))
                _EXTRACTION_EXECUTOR = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rag-extract")
    return _EXTRACTION_EXECUTOR


async def aextract_parties(text: str, model: ExtractionModel, **kwargs: Any) -> Any | None:
    """ASYNC-A4 (ADR-0057): `extract_parties` off the event loop. `run_pipeline` is synchronous (docling-graph
    has no async variant), so it runs in a worker thread -- keeping the loop non-blocking -- while the injected
    deadline-bounded client makes the docling-graph LLM socket truly cancellable at `_MODEL_DEADLINE_S` inside
    that worker thread. Same contract as `extract_parties`.

    EXEC-1: it runs on the DEDICATED extraction executor (`extraction_executor()`), NOT the default `to_thread`
    pool -- this is network-bound work, so its concurrency should be bounded by our semaphores + the deployment,
    not the CPU-derived default that also serves parse/embed/resolve/writes.

    ISSUE-0018: a ThreadPoolExecutor worker starts with an EMPTY context, so the OTel ambient context that
    `traced_run` sets (langfuse correlation, stored in contextvars) would NOT reach the docling-graph LLM call --
    its generation would land in a root trace with `sessionId: null`. Capture the CURRENT context at submit time
    (`copy_context()`, per-call so concurrent extractions each carry their own session) and run the worker inside
    it (`ctx.run`), so every generation the extraction emits stays attributed to its document/query."""
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    call = partial(extract_parties, text, model, **kwargs)
    return await loop.run_in_executor(extraction_executor(), lambda: ctx.run(call))


# --- KG-2: per-clause typed property extraction (the same seam, the KG-1 clause template) ---

# PROD-1 finding: a rich clause (esp. after ONT-2 grew the Clause template to ~36 typed dims) can exceed 2000 and
# truncate its structured JSON (a real NDA clause hit max_tokens=2000 -> unterminated string -> that clause's
# properties lost). This is a MAX for INGESTION extraction only (extract_clause), not a per-call cost -- a
# well-constrained extraction still terminates well under it -- so 4000 is a safe headroom bump, not a spend.
_CLAUSE_MAX_TOKENS = 4000
# (INGEST-REFACTOR: truncation was NOT a size problem -- unconstrained free-text fields like `document_reference`
# were dumping verbatim clause prose and ballooning the JSON; the fix is field constraints, not a higher cap.)
_CLAUSE_TEXT_CHARS = 12000  # one operative span is short; a generous cap that never truncates a real clause


def extract_clause(text: str, model: ExtractionModel, *, max_tokens: int = _CLAUSE_MAX_TOKENS,
                   temperature: float | None = None, structured_output: bool = False,
                   gleaning: bool = True) -> Any | None:
    """Extract one clause's typed properties from span `text` with `model`, using the KG-1 bridge template
    (`ontology.clause_template.Clause`). Same docling-graph API-mode seam + reliability fixes as
    `extract_parties`; returns the extracted `Clause` (typed properties) or None. The Clause -> our
    `ClausePropertyRecord` contract mapping + the grounding-judge gate live in `spans.clause_kg_extractor`.
    `gleaning` (issue 0019): pass False on the QUERY leg (constraint extraction from a short question), where the
    completeness pass has nothing to find; leave True for ingestion of a full clause."""
    from rag_wright.packs.contracts.ontology.clause_template import Clause

    return extract_parties(
        text, model, template=Clause, max_tokens=max_tokens, preamble_chars=_CLAUSE_TEXT_CHARS,
        temperature=temperature, structured_output=structured_output, stage="clause", gleaning=gleaning,
    )


async def aextract_clause(text: str, model: ExtractionModel, *, max_tokens: int = _CLAUSE_MAX_TOKENS,
                          temperature: float | None = None, structured_output: bool = False,
                          gleaning: bool = True) -> Any | None:
    """ASYNC-A4 (ADR-0057): `extract_clause` off the event loop (via `asyncio.to_thread`), the injected
    deadline-bounded client truly cancelling the docling-graph LLM socket at the deadline. Same contract.
    `gleaning` (issue 0019): False on the query leg -- no second completeness call for a short question."""
    from rag_wright.packs.contracts.ontology.clause_template import Clause

    return await aextract_parties(
        text, model, template=Clause, max_tokens=max_tokens, preamble_chars=_CLAUSE_TEXT_CHARS,
        temperature=temperature, structured_output=structured_output, stage="clause", gleaning=gleaning)
