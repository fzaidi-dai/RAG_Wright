"""KG-2 (FR-C.6, ADR-0033/0028): per-clause typed property extraction into the unified contract KG.

The extraction MODEL is granite-4.2-8b (the Leg-C winner, GP-1B; no A/B -- DeepSeek is a KG-6 below-par
contingency only). The MECHANISM is the GP-1B recipe (`kg-extraction-recipe` Skill): docling-graph +
`ontology.clause_template.Clause` (the KG-1 bridge template) via the `capabilities.dg_extraction` seam.

This module holds the two pieces that turn a raw extraction into a gated, contract-shaped record:

1. `clause_to_record` -- the PURE, unit-tested adapter from the KG-1 template (`Clause`, typed fields:
   enums + lists + nested odrl:Constraint models) to the existing property contract (`ClausePropertyRecord`
   / `PropertyAssertion`, T57a). Each filled field becomes one assertion (dimension, value) carrying
   provenance (FR-S.4) + the span citation (FR-Q.6). The OTHER escape means "not asserted" -> dropped;
   `CapBasis.cap_other` maps to the canonical `other` (the one documented KG-1 vocab divergence). Every
   emitted closed value is in `property.py::CLOSED_VOCAB` (the KG-1 test pins the two vocabularies equal).

2. `DGClausePropertyExtractor` -- composes an (injectable) Clause-extraction fn with the adapter and the
   deterministic grounding-judge gate (`property_grounding.reground`, ADR-0028): an EXTRACTED value on a
   lexically-anchored dimension whose cue is absent from the text is downgraded to AMBIGUOUS. The extraction
   fn is injected so the mapping + gate are testable with no model call; the live default is granite-4.2-8b.
"""

from __future__ import annotations

import os
import re
from enum import Enum
from typing import Any, Callable, Optional

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
from rag_wright.ontology._generated_vocab import VALUE_SYNONYMS
from rag_wright.ontology.clause_template import DamageType, ExceptionModel, _normalize_enum
from rag_wright.spans.property_grounding import reground
from rag_wright.spans.semantic_judge import asemantic_judge, semantic_judge
from rag_wright.spans.symbolic_validation import symbolic_validate

_D = PropertyDimension
_OTHER = "Other"  # the compiler's auto-added OTHER escape sentinel -> "not asserted"

# scalar enum field on Clause -> the dimension it asserts
_SCALAR_ENUM_DIMS: dict[str, PropertyDimension] = {
    "has_mutuality": _D.MUTUALITY,
    "has_favorability": _D.FAVORABILITY,
    "has_asymmetry": _D.PARTY_ASYMMETRY,
    "has_warranty_scope": _D.WARRANTY_SCOPE,
    "has_claim_scope": _D.CLAIM_SCOPE,
    "has_ip_ownership": _D.IP_OWNERSHIP,
    "has_renewal": _D.RENEWAL_MECHANISM,
    "covers_party_scope": _D.COVERED_PARTIES,
    "prohibits_solicit": _D.NONSOLICIT_TARGET,
    "requires_duty": _D.PROCEDURAL,
    # tier 3 -- CUAD-family extensions (KG-4)
    "has_exclusivity_type": _D.EXCLUSIVITY_TYPE,
    "has_right_of_first_type": _D.RIGHT_OF_FIRST_TYPE,
    "has_restriction_scope": _D.RESTRICTION_SCOPE,
    "has_coc_consent": _D.COC_CONSENT,
    "has_assignment_consent": _D.ASSIGNMENT_CONSENT,
    "has_escrow_release_trigger": _D.ESCROW_RELEASE_TRIGGER,
    "has_mfn_scope": _D.MFN_SCOPE,
    "has_termination_right": _D.TERMINATION_RIGHT,
    "dispute_method": _D.DISPUTE_METHOD,  # ADR-0049 (2): Dispute Resolution method
    "royalty_basis": _D.ROYALTY_BASIS,    # ADR-0049 (2): Royalties basis
    "condition_type": _D.CONDITION_TYPE,  # ADR-0049 (2): Condition Precedent kind
}
# open-valued CUAD dims: direct string fields on Clause -> dimension
_OPEN_STR_DIMS: dict[str, PropertyDimension] = {
    "audit_frequency": _D.AUDIT_FREQUENCY,
    "commitment_quantum": _D.COMMITMENT_QUANTUM,
    "ld_trigger": _D.LD_TRIGGER,
}
# list enum field on Clause -> the (multi-valued) dimension it asserts
_LIST_ENUM_DIMS: dict[str, PropertyDimension] = {
    "covers": _D.COVERED_SUBJECT,  # issue 0040: CLOSED conduct vocab -> out-of-vocab drops (not verbatim-retained)
    "collateral_type": _D.COLLATERAL_TYPE,  # ADR-0049 (2): Security Interest collateral (multi-valued)
    "force_majeure_event": _D.FORCE_MAJEURE_EVENT,          # ADR-0049 (2): Force Majeure events (multi-valued)
    "confidentiality_exception": _D.CONFIDENTIALITY_EXCEPTION,  # ADR-0049 (2): NDA carve-outs (multi-valued)
}
# issue 0037: the OPEN descriptive list-dims -- field -> (dimension, closed-vocab enum). Captured VERBATIM on the
# Clause; canonicalized here (exact/keyword -> canonical value; else skos:broader synonym -> canonical; else the
# verbatim phrase is KEPT, never dropped to OTHER). These dims are unbounded in symbolic_validation + lexically
# grounded (ADR-0028), so a spurious value whose cue is absent from the text is still downgraded by the gate.
_OPEN_LIST_DIMS: dict[str, tuple[PropertyDimension, type[Enum]]] = {
    "excepts": (_D.CARVE_OUT, ExceptionModel),
    "prohibits_damage": (_D.DAMAGE_TYPE, DamageType),
}


def _canonical_value(member: Any) -> str | None:
    """An enum member's canonical value, or None if it is the OTHER escape (not asserted).
    `CapBasis.cap_other` -> canonical `other` (the documented KG-1 divergence)."""
    value = member.value if isinstance(member, Enum) else str(member)
    if value == _OTHER:
        return None
    return "other" if value == "cap_other" else value


def _clean(text: Optional[str]) -> str | None:
    """A non-empty stripped open-valued literal, or None."""
    if text is None:
        return None
    stripped = text.strip()
    return stripped or None


def _norm_key(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", s).lower()


def _open_list_value(dim: PropertyDimension, enum_cls: type[Enum], raw: Any) -> str | None:
    """issue 0037: canonicalize one OPEN-dim item, keeping it VERBATIM when nothing matches (never OTHER-dropped).
    Order: exact/keyword vocab match -> its canonical value; else an ontology skos:broader synonym -> the canonical
    value (e.g. 'loss of profits' -> 'consequential' for damage_type); else the cleaned verbatim phrase."""
    s = _clean(str(raw.value) if isinstance(raw, Enum) else str(raw))
    if s is None:
        return None
    member = _normalize_enum(enum_cls, s, keyword_fallback=True)  # -> a member, or OTHER if no match
    canon = _canonical_value(member)  # None iff OTHER
    if canon is not None:
        return canon
    syn = VALUE_SYNONYMS.get(dim.value, {}).get(_norm_key(s))  # skos:broader synonym -> canonical
    return syn if syn is not None else s  # else keep verbatim


def clause_to_record(
    clause: Any, *, chunk_id: ChunkId, function: str, span_id: str = ""
) -> ClausePropertyRecord:
    """Map an extracted `Clause` (KG-1 template) to a validated `ClausePropertyRecord` (pure; no gate, no
    model). `function` is the KNOWN clause type (from the T56 classifier), not the LLM's `clause_type`.
    Every assertion starts EXTRACTED; the grounding gate (applied by the extractor) downgrades the
    ungrounded ones. Closed-vocab values are guaranteed in-vocabulary by the KG-1 template."""
    prov = Provenance.of(chunk_id)
    assertions: list[PropertyAssertion] = []

    def add(dimension: PropertyDimension, value: str | None,
            confidence: ConfidenceTag = ConfidenceTag.EXTRACTED) -> None:
        if value is None or not str(value).strip():
            return
        assertions.append(
            PropertyAssertion(
                provenance=prov, confidence=confidence,
                dimension=dimension, value=value, span_id=span_id,
            )
        )

    for field, dim in _SCALAR_ENUM_DIMS.items():
        add(dim, _canonical_value(getattr(clause, field, None)))
    for field, dim in _LIST_ENUM_DIMS.items():
        for member in getattr(clause, field, None) or []:
            add(dim, _canonical_value(member))
    for field, (dim, enum_cls) in _OPEN_LIST_DIMS.items():  # issue 0037: verbatim-retaining open descriptive dims
        vocab = CLOSED_VOCAB.get(dim, frozenset())
        for raw in getattr(clause, field, None) or []:
            val = _open_list_value(dim, enum_cls, raw)
            if val is None:
                continue
            # a canonical (in-vocab) value is EXTRACTED; a retained VERBATIM tail value is admissible only as the
            # AMBIGUOUS "other" escape (PropertyAssertion contract) -- kept + flagged, never dropped (issue 0037).
            add(dim, val, ConfidenceTag.EXTRACTED if val in vocab else ConfidenceTag.AMBIGUOUS)
    for field, dim in _OPEN_STR_DIMS.items():  # open-valued CUAD dims (direct string fields)
        add(dim, _clean(getattr(clause, field, None)))

    caps = getattr(clause, "caps", None)
    if caps is not None:
        add(_D.CAP_BASIS, _canonical_value(caps.cap_basis))
        add(_D.CAP_QUANTUM, _clean(caps.cap_quantum))  # open-valued

    bound = getattr(clause, "bounded_by", None)
    if bound is not None:
        duration = _clean(bound.temporal_duration)
        if duration is not None:
            kind = (bound.temporal_kind or "").strip().lower()
            add(_D.NOTICE_PERIOD if kind == "notice_period" else _D.TEMPORAL_BOUND, duration)

    law = getattr(clause, "governed_by", None)
    if law is not None:
        add(_D.JURISDICTION, _clean(law.jurisdiction_name))  # open-valued
        add(_D.LAW_MULTIPLICITY, _canonical_value(law.law_multiplicity))

    return ClausePropertyRecord(
        clause_id=str(chunk_id),
        function=canonical_function(function) or function,
        folio_iri=FOLIO_CLAUSE_IRI.get(function, ""),
        span_id=span_id,  # the operative span (1:1) -- carried even when the clause has no properties
        assertions=assertions,
    )


ClauseExtractFn = Callable[[str], Any]
"""text -> an extracted `Clause` instance (or None). Injected so the adapter + gate test with no model."""


class DGClausePropertyExtractor:
    """Extract a clause's typed properties end to end: run the Clause extraction (granite-4.2-8b via
    docling-graph, injected), adapt to `ClausePropertyRecord`, then apply the deterministic
    grounding-judge gate (ADR-0028). Matches the `PropertyExtractor` call shape (T57b) so it drops into
    the ingestion driver. A None extraction yields an empty (but valid) record for that clause."""

    def __init__(self, extract_fn: ClauseExtractFn, *, semantic_judge_fn: Any = None,
                 aextract_fn: Any = None, asemantic_judge_fn: Any = None) -> None:
        self._extract = extract_fn
        # ADR-0040 Layer 3: an optional LLM semantic judge. Injected (default None -> deterministic-only) so
        # existing callers + hermetic tests are unaffected; the production pipeline wires the real granite judge.
        self._semantic_judge_fn = semantic_judge_fn
        # ASYNC-B2b (ADR-0057): the async twins (extraction on the async docling-graph seam + async judge).
        self._aextract = aextract_fn
        self._asemantic_judge_fn = asemantic_judge_fn

    def _empty(self, chunk_id: ChunkId, function: str) -> ClausePropertyRecord:
        return ClausePropertyRecord(
            clause_id=str(chunk_id), function=canonical_function(function) or function,
            folio_iri=FOLIO_CLAUSE_IRI.get(function, ""), assertions=[])

    def _grounded(self, clause: Any, *, chunk_id: ChunkId, function: str, text: str, span_id: str
                  ) -> ClausePropertyRecord:
        # ADR-0028 lexical grounding gate, then ADR-0040 symbolic (function->dimension applicability) gate.
        record = clause_to_record(clause, chunk_id=chunk_id, function=function, span_id=span_id)
        return symbolic_validate(reground(record, text))

    def __call__(
        self, *, chunk_id: ChunkId, function: str, text: str, span_id: str = ""
    ) -> ClausePropertyRecord:
        clause = self._extract(text)
        if clause is None:
            return self._empty(chunk_id, function)
        record = self._grounded(clause, chunk_id=chunk_id, function=function, text=text, span_id=span_id)
        if self._semantic_judge_fn is not None:  # ADR-0040 Layer 3 LLM semantic gate (production only)
            record = semantic_judge(record, text, self._semantic_judge_fn)
        return record

    async def aextract(
        self, *, chunk_id: ChunkId, function: str, text: str, span_id: str = ""
    ) -> ClausePropertyRecord:
        """ASYNC-B2b (ADR-0057): the async twin of `__call__`. Runs the docling-graph clause extraction on the
        async seam (true wall-clock deadline), the same deterministic grounding/symbolic gates, then the async
        Layer-3 semantic judge. Same contract as `__call__`."""
        clause = await self._aextract(text)
        if clause is None:
            return self._empty(chunk_id, function)
        record = self._grounded(clause, chunk_id=chunk_id, function=function, text=text, span_id=span_id)
        if self._asemantic_judge_fn is not None:
            record = await asemantic_judge(record, text, self._asemantic_judge_fn)
        return record


def granite_clause_extractor(model: Any = None, *, semantic_judge_fn: Any = None,
                             asemantic_judge_fn: Any = None, list_model: str | None = None,
                             samples: int | None = None) -> DGClausePropertyExtractor:
    """The live default: granite-4.2-8b via the SELECTED serving backend (`default_extraction_model` reads
    `RAG_SERVING` -> vLLM-Granite in product, OpenRouter-Granite in dev; MS1-3, ADR-0079). Pass a different
    `ExtractionModel` to override, or a `semantic_judge_fn`/`asemantic_judge_fn` to enable the ADR-0040 Layer-3
    gate (sync/async). ASYNC-B2b wires the async extraction seam (`aextract_clause`) so `aextract` gets the true
    wall-clock deadline.

    TAGPARSE-INGEST-1b: `RAG_INGEST_CLAUSE_EXTRACTOR` selects the Clause-producing step -- `tagparse` (DEFAULT
    since ADR-0081: function-independent thematic tag-parse groups) or `docling` (the legacy docling-graph
    server-side-JSON path, kept for rollback). BOTH feed the SAME downstream (adapt to ClausePropertyRecord +
    ADR-0028 grounding + ADR-0040 symbolic gate). tagparse is the default because docling hard-crashes ~89% of
    real CUAD clauses (grounded A/B, 45 clauses: docling success 0.11 vs tagparse 1.00). NOTE: tagparse issues one
    LLM call per thematic GROUP (~8/clause) vs docling's ~1; cost is reduced via `is_extractable_span` (fewer spans),
    NOT by pruning groups or batching clauses -- both were measured in issue 0036 at a ~15-18% property-recall loss.

    `list_model` (ARGUMENT; else env `RAG_INGEST_LIST_MODEL`; else the profile general model) is the SECOND model
    for the cross-model list union on list-bearing groups -- exposed here (like `model`) so the caller configures
    it explicitly; pass `"off"` to disable. `samples` (else env) sets same-model multi-sample union."""
    from rag_wright.capabilities.dg_extraction import aextract_clause, default_extraction_model, extract_clause

    chosen = model or default_extraction_model("clause-extract")
    if os.getenv("RAG_INGEST_CLAUSE_EXTRACTOR", "tagparse").strip().lower() == "tagparse":
        from rag_wright.spans.tag_clause_extractor import atag_extract_clause, tag_extract_clause
        model_id = chosen.model
        return DGClausePropertyExtractor(
            lambda text: tag_extract_clause(text, model_id, list_model=list_model, samples=samples),
            aextract_fn=lambda text: atag_extract_clause(text, model_id, list_model=list_model, samples=samples),
            semantic_judge_fn=semantic_judge_fn, asemantic_judge_fn=asemantic_judge_fn,
        )
    return DGClausePropertyExtractor(
        lambda text: extract_clause(text, chosen),
        aextract_fn=lambda text: aextract_clause(text, chosen),
        semantic_judge_fn=semantic_judge_fn, asemantic_judge_fn=asemantic_judge_fn,
    )


class ClassifierPropertyExtractor:
    """CLS-C (ADR-0115): the classifier-first Step-3a property extractor. Runs the FUNCTION-INDEPENDENT
    `HybridPropertyExtractor` (classifiers for the 21 covered dims + ONE residual LLM call for the 7 numeric/open
    dims) and then the SAME record-level gates as `DGClausePropertyExtractor`: ADR-0028 `reground` -> ADR-0040
    `symbolic_validate` -> optional Layer-3 `semantic_judge` (sync) / `asemantic_judge` (async). This REPLACES the
    full-LLM tag-parse extraction for these dims -- it is the decided path, not a toggle over an LLM fallback.
    Same `PropertyExtractor` Protocol (`__call__` + `aextract`), so nothing downstream changes."""

    def __init__(self, hybrid: Any, *, semantic_judge_fn: Any = None, asemantic_judge_fn: Any = None) -> None:
        self._hybrid = hybrid
        self._semantic_judge_fn = semantic_judge_fn
        self._asemantic_judge_fn = asemantic_judge_fn

    def _gate(self, record: ClausePropertyRecord, text: str) -> ClausePropertyRecord:
        return symbolic_validate(reground(record, text))  # ADR-0028 lexical, then ADR-0040 symbolic

    def __call__(self, *, chunk_id: ChunkId, function: str, text: str, span_id: str = "",
                 functions: tuple[str, ...] = ()) -> ClausePropertyRecord:
        record = self._gate(self._hybrid(chunk_id=chunk_id, function=function, text=text, span_id=span_id,
                                         functions=functions), text)
        if self._semantic_judge_fn is not None:
            record = semantic_judge(record, text, self._semantic_judge_fn)
        return record

    async def aextract(self, *, chunk_id: ChunkId, function: str, text: str,
                       span_id: str = "", functions: tuple[str, ...] = ()) -> ClausePropertyRecord:
        raw = await self._hybrid.aextract(chunk_id=chunk_id, function=function, text=text, span_id=span_id,
                                          functions=functions)
        record = self._gate(raw, text)
        if self._asemantic_judge_fn is not None:
            record = await asemantic_judge(record, text, self._asemantic_judge_fn)
        return record


def classifier_property_extractor(*, registry: Any = None, runnable: Any = None, model_id: Optional[str] = None,
                                  semantic_judge_fn: Any = None, asemantic_judge_fn: Any = None,
                                  classifier_fn: Any = None) -> ClassifierPropertyExtractor:
    """Build the CLS-C classifier-first Step-3a extractor. The classifier LANE comes from `classifier_fn` when given
    (EP-RT-7: the ingestion pipeline passes the `clause_property_classification` capability dispatch, so the fleet is
    invoked through the capability -- the single production path), else from the local `registry` fleet. `runnable`
    defaults to the structured seam for the residual numeric call; the ADR-0028/0040 gates + the judge apply unchanged."""
    from rag_wright.spans.property_extractor import HybridPropertyExtractor

    hybrid = HybridPropertyExtractor(registry, runnable=runnable, model_id=model_id, classifier_fn=classifier_fn)
    return ClassifierPropertyExtractor(hybrid, semantic_judge_fn=semantic_judge_fn,
                                       asemantic_judge_fn=asemantic_judge_fn)
