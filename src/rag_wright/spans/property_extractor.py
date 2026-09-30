"""T57b (FR-C.6, ADR-0025/0026): the targeted PROPERTY extractor.

Given an operative span, its FUNCTION (from the T56 classifier), and its clause provenance, extract the
queryable PROPERTIES the span states -> a `ClausePropertyRecord` (T57a). Structured output through the
model-profile seam (DeepSeek V4 Pro by default; quality-sensitive extraction). The extractor is
FUNCTION-AWARE: it only asks for the dimensions that apply to that clause type (`FUNCTION_DIMENSIONS`),
which keeps the prompt tight, the output on-vocabulary, and out-of-scope dimensions from being invented.

The LLM->contract mapping (`build_record`) is a pure, unit-tested function: it scope-filters to the
function's dimensions, and coerces an out-of-vocabulary value to an AMBIGUOUS assertion (the schema's
`other` escape) rather than dropping or crashing. At ingestion (T57c) this runs concurrently via
`util.map_concurrent`.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

from rag_wright.contracts.function import canonical_function
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.property import (
    CLOSED_VOCAB,
    FOLIO_CLAUSE_IRI,
    ClausePropertyRecord,
    PropertyAssertion,
    PropertyDimension,
)
from rag_wright.contracts.provenance import ConfidenceTag, Provenance
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_structured

_D = PropertyDimension

# Which property dimensions apply to which FUNCTION (the two-tier schema, operationalized). A clause type
# not listed falls back to the cross-cutting pair; extraction stays focused on what the ACORD queries
# actually filter that clause type on.
FUNCTION_DIMENSIONS: dict[str, tuple[PropertyDimension, ...]] = {
    "Cap On Liability": (_D.MUTUALITY, _D.FAVORABILITY, _D.CARVE_OUT, _D.CAP_BASIS, _D.CAP_QUANTUM, _D.PARTY_ASYMMETRY),
    "Uncapped Liability": (_D.MUTUALITY, _D.FAVORABILITY, _D.CARVE_OUT, _D.PARTY_ASYMMETRY),
    "Indirect/Consequential Damages Waiver": (_D.MUTUALITY, _D.FAVORABILITY, _D.CARVE_OUT, _D.DAMAGE_TYPE),
    "Warranty Disclaimer": (_D.FAVORABILITY, _D.WARRANTY_SCOPE),
    "Indemnification": (_D.MUTUALITY, _D.FAVORABILITY, _D.CLAIM_SCOPE, _D.COVERED_SUBJECT, _D.COVERED_PARTIES, _D.PROCEDURAL),
    "Governing Law": (_D.JURISDICTION, _D.LAW_MULTIPLICITY),
    "No-Solicit Of Employees": (_D.NONSOLICIT_TARGET, _D.TEMPORAL_BOUND),
    "No-Solicit Of Customers": (_D.NONSOLICIT_TARGET, _D.TEMPORAL_BOUND),
    "Renewal Term": (_D.RENEWAL_MECHANISM, _D.NOTICE_PERIOD),
    "Notice Period To Terminate Renewal": (_D.RENEWAL_MECHANISM, _D.NOTICE_PERIOD),
    "IP Ownership Assignment": (_D.IP_OWNERSHIP, _D.COVERED_PARTIES),
    "Joint IP Ownership": (_D.IP_OWNERSHIP, _D.COVERED_PARTIES),
    "License Grant": (_D.COVERED_PARTIES,),
    "Affiliate License-Licensor": (_D.COVERED_PARTIES,),
    "Affiliate License-Licensee": (_D.COVERED_PARTIES,),
}
_DEFAULT_DIMENSIONS: tuple[PropertyDimension, ...] = (_D.MUTUALITY, _D.FAVORABILITY)


def dimensions_for(function: str) -> tuple[PropertyDimension, ...]:
    return FUNCTION_DIMENSIONS.get(function, _DEFAULT_DIMENSIONS)


class ExtractedProperty(BaseModel):
    """One property as the LLM emits it (pre-provenance): a dimension, a value, and a confidence."""

    dimension: PropertyDimension
    value: str
    confidence: ConfidenceTag


class PropertyExtraction(BaseModel):
    """The LLM's structured output for one span: the properties it states (empty if none)."""

    properties: list[ExtractedProperty] = []


def extraction_prompt(function: str, dimensions: tuple[PropertyDimension, ...], text: str) -> str:
    """The extraction prompt: only the dimensions that apply to this function, with their vocabularies."""
    lines = []
    for d in dimensions:
        vocab = CLOSED_VOCAB.get(d)
        if vocab:
            lines.append(f"- {d.value}: one or more of {sorted(vocab)}; a genuinely novel value must be AMBIGUOUS")
        else:
            lines.append(f"- {d.value}: a short lowercase_snake value (open-valued)")
    dims = "\n".join(lines)
    return (
        f"You extract queryable PROPERTIES from a '{function}' contract clause span for a legal search index.\n"
        "Report ONLY properties the span actually states; omit any dimension the span does not address "
        "(do not guess). Confidence: EXTRACTED (stated), INFERRED (clearly implied), AMBIGUOUS (a novel "
        "value outside the list, or competing readings). Multi-valued dimensions (e.g. carve_out) may have "
        "several entries.\n\n"
        f"Dimensions for a '{function}' clause:\n{dims}\n\n"
        f"Span:\n{text[:2000]}"
    )


def build_record(
    *, chunk_id: ChunkId, function: str, span_id: str, extraction: PropertyExtraction
) -> ClausePropertyRecord:
    """Map an LLM `PropertyExtraction` to a validated `ClausePropertyRecord` (pure; no model call).

    Scope-filters to the function's dimensions (drops any invented out-of-scope dimension), and coerces an
    out-of-vocabulary value to an AMBIGUOUS assertion (the schema's `other` escape) instead of dropping it.
    """
    function = canonical_function(function) or function  # normalize classifier casing (e.g. 'Ip' -> 'IP')
    prov = Provenance.of(chunk_id)
    applicable = set(dimensions_for(function))
    assertions: list[PropertyAssertion] = []
    for p in extraction.properties:
        if p.dimension not in applicable:
            continue  # function-aware: ignore a dimension that does not belong to this clause type
        try:
            assertions.append(
                PropertyAssertion(
                    provenance=prov, confidence=p.confidence, dimension=p.dimension, value=p.value, span_id=span_id
                )
            )
        except ValidationError:
            # out-of-vocabulary value with a non-AMBIGUOUS confidence -> retain it as the AMBIGUOUS escape
            try:
                assertions.append(
                    PropertyAssertion(
                        provenance=prov, confidence=ConfidenceTag.AMBIGUOUS, dimension=p.dimension,
                        value=p.value, span_id=span_id,
                    )
                )
            except ValidationError:
                continue  # empty value or otherwise unsalvageable -> drop
    return ClausePropertyRecord(
        clause_id=str(chunk_id), function=function, folio_iri=FOLIO_CLAUSE_IRI.get(function, ""),
        assertions=assertions,
    )


@runtime_checkable
class PropertyExtractor(Protocol):
    """Span -> `ClausePropertyRecord`. The seam a test stubs and T57c drives via `map_concurrent`."""

    def __call__(self, *, chunk_id: ChunkId, function: str, text: str, span_id: str) -> ClausePropertyRecord: ...


class SeamPropertyExtractor:
    """The real extractor: structured output through the model-profile seam on STRUCTURED_REASONING
    (DeepSeek V4 Pro by default). Retries a bare `None` (a transient miss `with_structured_output`
    returns), returning an empty record only if extraction persistently fails (the span keeps its
    function; it simply carries no properties)."""

    def __init__(self, model_id: Optional[str] = None, *, runnable=None, retries: int = 3) -> None:
        self._runnable = runnable or build_structured(
            model_id or model_for(ModelRole.STRUCTURED_REASONING), PropertyExtraction
        )
        self._retries = retries

    def __call__(self, *, chunk_id: ChunkId, function: str, text: str, span_id: str = "") -> ClausePropertyRecord:
        prompt = extraction_prompt(function, dimensions_for(function), text)
        extraction: Optional[PropertyExtraction] = None
        for _ in range(self._retries):
            try:
                extraction = self._runnable.invoke(prompt)
            except Exception:  # noqa: BLE001 - transient provider/parse error; retry
                continue
            if extraction is not None:
                break
        return build_record(
            chunk_id=chunk_id, function=function, span_id=span_id,
            extraction=extraction or PropertyExtraction(),
        )


class HybridPropertyExtractor:
    """CLS-B (FR-C.6/FR-I.4): the Step-3a extractor that OFFLOADS the classifier-covered dimensions from the LLM.
    For each applicable dim (`dimensions_for(function)`), a trained `DimClassifier` answers the COVERED ones (top-k:
    the primary as EXTRACTED, any lower-ranked soft tags as INFERRED) and the LLM is asked for ONLY the UNCOVERED
    ones — skipped entirely when the clause type is fully covered (the per-span latency win). Same `PropertyExtractor`
    Protocol, so the ADR-0028 grounding + ADR-0040 symbolic gates downstream apply unchanged. The classifiers honor
    the device-agnostic serving seam (GPU-if-available-else-CPU); the LLM stays on the model-profile seam."""

    def __init__(self, registry, *, runnable=None, model_id: Optional[str] = None, retries: int = 3) -> None:
        self._registry = registry
        self._runnable = runnable or build_structured(
            model_id or model_for(ModelRole.STRUCTURED_REASONING), PropertyExtraction)
        self._retries = retries

    def __call__(self, *, chunk_id: ChunkId, function: str, text: str, span_id: str = "") -> ClausePropertyRecord:
        function = canonical_function(function) or function
        prov = Provenance.of(chunk_id)
        assertions: list[PropertyAssertion] = []
        uncovered: list[PropertyDimension] = []
        for d in dimensions_for(function):
            clf = self._registry.get(d)
            if clf is None:
                uncovered.append(d)
                continue
            for rank, (value, _prob) in enumerate(clf.classify(text)):
                conf = ConfidenceTag.EXTRACTED if rank == 0 else ConfidenceTag.INFERRED
                try:
                    assertions.append(PropertyAssertion(provenance=prov, confidence=conf, dimension=d,
                                                        value=value, span_id=span_id))
                except ValidationError:
                    continue  # a value outside the dim vocab (shouldn't happen from a trained head) -> drop
        if uncovered:  # ask the LLM for the uncovered dims only; a fully-covered clause type skips the call
            prompt = extraction_prompt(function, tuple(uncovered), text)
            extraction: Optional[PropertyExtraction] = None
            for _ in range(self._retries):
                try:
                    extraction = self._runnable.invoke(prompt)
                except Exception:  # noqa: BLE001 - transient provider/parse error; retry
                    continue
                if extraction is not None:
                    break
            uncovered_set = set(uncovered)
            rec = build_record(chunk_id=chunk_id, function=function, span_id=span_id,
                               extraction=extraction or PropertyExtraction())
            assertions += [a for a in rec.assertions if a.dimension in uncovered_set]
        return ClausePropertyRecord(clause_id=str(chunk_id), function=function,
                                    folio_iri=FOLIO_CLAUSE_IRI.get(function, ""), assertions=assertions)
