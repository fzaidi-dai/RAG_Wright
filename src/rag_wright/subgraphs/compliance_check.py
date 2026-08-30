"""CC-6 (compliance §13.3): the `compliance_check` subgraph -- the headline composite of the compliance module.

A hardened, query-side LangGraph subgraph on `scaffold.py`: for a subject ad, extract its claims (CC-3),
retrieve the APPLICABLE requirements (claim scope <-> requirement applicability), judge each `(claim,
requirement)` pair (CC-4, concurrent), and assemble cited findings + a per-requirement gap matrix + a verdict
summary. Two design requirements from earlier findings are built in here:

- **Section->claim_type applicability map** (the ontology enrichment): a requirement whose extracted
  `applicability_scope` is empty is applied by its FTC section (255.5 material-connections -> endorsement, 255.1
  general -> all, 255.0 definitions -> none). Authored in code referencing `ClaimType` (typos are test failures,
  no drifting .ttl) -- the JUDGE-ONTOLOGY-1 pattern. See [[ontology-lever-vs-extraction-lever]].
- **Ad-level disclosure aggregation** (the CC-4 residual fix): disclosures are ad-level but claims are per-span,
  so before judging, each claim's disclosures are enriched with the union across the whole ad -- a per-span
  fragment with disc=none is not over-flagged when the ad as a whole discloses.

Query-side posture: every node degrades to empty on failure (never crash); the judge's conservative default
(needs_review) plus per-finding human-gating carry the trust guarantees.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from rag_wright.capabilities.compliance_judgment import AJudgeFn, ajudge_pairs
from rag_wright.capabilities.retrieval_core import _cosine
from rag_wright.contracts.compliance import (
    CheckableFact,
    Claim,
    ClaimType,
    ComplianceFinding,
    ComplianceReport,
    Requirement,
    RuleScope,
    Verdict,
)
from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, business_span

_ALL_CLAIM_TYPES = {c.value for c in ClaimType}

# The authored section -> applicable claim_types map (the ontology enrichment; backfills empty-scope requirements).
# KEY FINDING (CC-6 live smoke): the FTC endorsement guides apply by CONTEXT (is the ad an endorsement?), NOT by
# claim_type -- an efficacy/health claim inside an influencer post IS subject to §255.5 disclosure. So claim_type
# is the wrong narrowing axis for THIS corpus: every operative section applies broadly; only the definitions
# section (255.0) is excluded. The map still narrows for a genuinely type-scoped corpus (its real value); here it
# mostly just drops definitions. The real per-claim narrowing (which specific rule is most relevant) is SEMANTIC
# retrieval (Leg-B), the CC-7 refinement. See [[ontology-lever-vs-extraction-lever]].
SECTION_CLAIM_TYPES: dict[str, set[str]] = {
    "255.0": set(),  # Purpose and definitions -- not an operative rule; applies to nothing
    "255.1": _ALL_CLAIM_TYPES,  # General considerations
    "255.2": _ALL_CLAIM_TYPES,  # Consumer endorsements -- any claim made via a testimonial
    "255.3": _ALL_CLAIM_TYPES,  # Expert endorsements -- any claim made by/through an expert
    "255.4": _ALL_CLAIM_TYPES,  # Endorsements by organizations
    "255.5": _ALL_CLAIM_TYPES,  # Disclosure of material connections -- any claim in an endorsement needs disclosure
    "255.6": _ALL_CLAIM_TYPES,  # Endorsements directed to children
}


def _section_of(citation: str) -> str:
    """'§ 255.5' -> '255.5' (the section key for the applicability map)."""
    return (citation or "").replace("§", "").strip()


def applicable_claim_types(requirement: Requirement) -> set[str]:
    """The claim types a requirement applies to: the section's applicable set (context-based, the authoritative
    axis for these context-scoped guides) BROADENED by any extracted claim_type scope. Extracted scope can only
    ADD (granite's per-rule claim_type is noisy and must not wrongly EXCLUDE a claim the section covers); an
    unknown section defaults to all (recall-first). So for the FTC guides every operative section applies broadly
    and only definitions (255.0, empty set + no scope) are excluded -- the honest applicability for this corpus.
    The real per-claim narrowing (most-relevant rule) is semantic retrieval (Leg-B), the CC-7 refinement."""
    section = SECTION_CLAIM_TYPES.get(_section_of(requirement.citation), _ALL_CLAIM_TYPES)
    scope = {c.value for c in requirement.applicability_scope if c.dimension == "claim_type"}
    return section | scope


def _constraints_by_dimension(constraints: list) -> dict:
    """`[Constraint(dimension, value), ...]` -> {dimension: {values}}."""
    out: dict = {}
    for c in constraints:
        out.setdefault(c.dimension, set()).add(c.value)
    return out


def constraint_applies(requirement_scope: list, subject_scope: list) -> bool:
    """COMP-APPLIC-1 Increment 0: the DIMENSION-AGNOSTIC applicability matcher. A requirement applies to a subject
    iff, for EVERY dimension the requirement constrains, the subject's value(s) on that dimension INTERSECT the
    requirement's allowed values. A dimension the requirement does NOT constrain, or one the subject does NOT
    carry, never excludes (recall-first). Both scopes are `(dimension, value)` Constraint lists, so ANY domain
    routes with NO new compliance_check code -- the ontology's dimensions are DATA, not per-domain matcher logic.
    (The advertising `applies_to` keeps its own path: its "definitions section applies to nothing" is authored
    doctrine that pure constraint matching does not express -- a domain with such authored routing adds a thin
    wrapper; a domain with pure scope matching adds none.)"""
    req = _constraints_by_dimension(requirement_scope)
    subj = _constraints_by_dimension(subject_scope)
    for dim, allowed in req.items():
        vals = subj.get(dim)
        if vals is not None and not (vals & allowed):
            return False  # subject HAS this dimension but with a non-matching value -> excluded
    return True


def applies_to(requirement: Requirement, claim: Claim) -> bool:
    """Does `requirement` apply to `claim`? (the claim's type is in the requirement's applicable claim types)."""
    return claim.claim_type.value in applicable_claim_types(requirement)


# CC-8a: the ontology content/context tag (the narrowing routing lever). A CONTEXT section applies regardless of
# claim content (disclosure -> always included); everything else is CONTENT (narrowed by semantic similarity).
SECTION_RULE_SCOPE: dict[str, RuleScope] = {
    "255.5": RuleScope.CONTEXT,  # Disclosure of material connections -- applies to any claim in an endorsement
    "255.4": RuleScope.CONTEXT,  # Endorsements by organizations -- the disclosure/relationship angle
}


def rule_scope_of(requirement: Requirement) -> RuleScope:
    """CONTENT (narrow by similarity) unless the section is a CONTEXT section (disclosure; always-include)."""
    return SECTION_RULE_SCOPE.get(_section_of(requirement.citation), RuleScope.CONTENT)


SelectFn = Callable[[Claim, list], list]  # (claim, requirements) -> the narrowed requirements to judge


def _dedup(requirements: list, vectors: dict, threshold: float) -> list:
    """Greedy near-duplicate collapse: keep a requirement unless it is >= `threshold` cosine-similar to one
    already kept (the 3 near-identical §255.5 disclosure rules -> one). Order-preserving."""
    kept: list = []
    for req in requirements:
        vec = vectors.get(req.requirement_id)
        if vec is not None and any(_cosine(vec, vectors[k.requirement_id]) >= threshold for k in kept
                                   if vectors.get(k.requirement_id) is not None):
            continue
        kept.append(req)
    return kept


def build_select_fn(
    embedder, requirements: list, *, k: int = 5, context_k: int = 3, dedup_threshold: float = 0.92,
    filter_applicability: bool = True, constraint_scope_fn: Any = None
) -> SelectFn:
    """CC-8b: build the semantic-narrowing selector. Precomputes each requirement's BGE vector ONCE. Per claim it
    returns the top-`context_k` CONTEXT rules (disclosure -- kept regardless of content so similarity can't miss
    them, Example B) + the top-`k` CONTENT rules (substantiation etc., ranked by cosine to the claim), deduped.
    Both classes are CAPPED so an over-extracted section (§255.5 -> 32 near-identical disclosure rules) collapses
    to a few representatives rather than re-exploding the cross-product. A precision/cost win that keeps the
    context rules (the recall guarantee) while cutting the redundant-rule noise.

    Three applicability-routing modes (in precedence): `constraint_scope_fn` (COMP-APPLIC-1 Increment 0: generic
    DIMENSION-AGNOSTIC structured routing -- `subject -> [Constraint]`, matched against each requirement's scope by
    `constraint_applies`; ANY domain, no per-domain matcher code) > `filter_applicability=True` (advertising
    claim_type routing via `applies_to`) > `filter_applicability=False` (semantic-only, COMP-VERDICT-GENERIC)."""
    vectors = {r.requirement_id: embedder.encode_dense(r.requirement_text) for r in requirements}

    def _ranked(reqs: list, claim_vec: list) -> list:
        return sorted(reqs, key=lambda r: _cosine(claim_vec, vectors.get(r.requirement_id, [])), reverse=True)

    def select(claim: Any, reqs: list) -> list:
        if constraint_scope_fn is not None:  # generic structured routing (any domain, ontology-driven, DATA)
            subject_scope = constraint_scope_fn(claim)
            applicable = [r for r in reqs if constraint_applies(r.applicability_scope, subject_scope)]
        elif filter_applicability:  # advertising claim_type routing
            applicable = [r for r in reqs if applies_to(r, claim)]
        else:  # semantic-only (generic verdict)
            applicable = list(reqs)
        claim_vec = embedder.encode_dense(claim.assertion_text)
        context = _dedup(_ranked([r for r in applicable if rule_scope_of(r) is RuleScope.CONTEXT], claim_vec),
                         vectors, dedup_threshold)[:context_k]
        content = _dedup(_ranked([r for r in applicable if rule_scope_of(r) is RuleScope.CONTENT], claim_vec),
                         vectors, dedup_threshold)[:k]
        return _dedup(context + content, vectors, dedup_threshold)

    return select


# ASYNC-C1 (ADR-0057): claims_fn is async (its extraction model call gets a true wall-clock deadline).
ClaimsFn = Callable[[str, str], Awaitable[list]]  # (subject_text, source_doc) -> list[Claim]
RequirementsFn = Callable[[], list]  # () -> list[Requirement]


class CheckState(TypedDict, total=False):
    subject_text: str
    source_doc: str
    claims: list
    ad_disclosures: list
    pairs: list
    findings: list
    report: ComplianceReport


def _enrich(claim: Claim, ad_disclosures: set[str]) -> Claim:
    """Enrich a claim's disclosures with the ad-level union (the CC-4 fix). claim_id is content-hashed on the
    assertion, not the disclosures, so it is unchanged -- the finding still cites the original claim."""
    if not ad_disclosures:
        return claim
    merged = sorted(set(claim.disclosures_present) | ad_disclosures)
    return claim.model_copy(update={"disclosures_present": merged})


def build_compliance_check(
    *, claims_fn: ClaimsFn, requirements_fn: RequirementsFn, judge_fn: AJudgeFn,
    select_fn: SelectFn | None = None, retry_policy: Any = DEFAULT_RETRY
):
    """Compile the compliance-check subgraph. All seams are injected for hermetic testing. `select_fn` (CC-8b) is
    the per-claim requirement narrower; when None, every APPLICABLE requirement is judged (the broad default).
    Query-side: each node degrades to empty on failure (never crashes) -- an empty report is a safe answer."""

    async def extract_claims(state: CheckState) -> CheckState:
        with business_span("compliance_check.extract_claims"):
            try:
                claims = await claims_fn(state["subject_text"], state["source_doc"])
            except Exception:  # noqa: BLE001 - degrade-to-empty (query-side never crashes)
                return {"claims": [], "ad_disclosures": []}
        # ad-level disclosure union (CC-4 fix). getattr-tolerant: a generic CheckableFact has no disclosures ->
        # empty union -> _enrich is a no-op, so the same pipeline serves both advertising Claims and bare facts.
        ad = sorted({d for c in claims for d in getattr(c, "disclosures_present", [])})
        return {"claims": claims, "ad_disclosures": ad}

    def retrieve_applicable(state: CheckState) -> CheckState:
        claims = state.get("claims", [])
        ad = set(state.get("ad_disclosures", []))
        with business_span("compliance_check.retrieve_applicable"):
            try:
                requirements = requirements_fn()
            except Exception:  # noqa: BLE001 - degrade-to-empty
                return {"pairs": []}
        if select_fn is not None:  # CC-8b: semantic narrowing (top-k content + always-include context + dedup)
            pairs = [(_enrich(claim, ad), req) for claim in claims for req in select_fn(claim, requirements)]
        else:  # broad default: every applicable requirement
            pairs = [(_enrich(claim, ad), req)
                     for claim in claims for req in requirements if applies_to(req, claim)]
        return {"pairs": pairs}

    async def judge(state: CheckState) -> CheckState:
        pairs = state.get("pairs", [])
        if not pairs:
            return {"findings": []}
        with business_span("compliance_check.judge"):
            # concurrent (gather + semaphore); conservative default inside (a timed-out pair -> needs_review)
            return {"findings": await ajudge_pairs(pairs, ajudge_fn=judge_fn)}

    def assemble(state: CheckState) -> CheckState:
        findings: list[ComplianceFinding] = state.get("findings", [])
        summary: dict[str, int] = {}
        for f in findings:
            summary[f.verdict.value] = summary.get(f.verdict.value, 0) + 1
        # gap matrix: one row per requirement that was checked, rolled up to its worst verdict
        rank = {Verdict.VIOLATION: 3, Verdict.NEEDS_REVIEW: 2, Verdict.COMPLIANT: 1}
        by_req: dict[str, dict] = {}
        for f in findings:
            row = by_req.setdefault(f.requirement_id, {
                "requirement_id": f.requirement_id, "citation": f.citation_requirement.split(" (")[0],
                "verdict": f.verdict, "claims_checked": 0})
            row["claims_checked"] += 1
            if rank[f.verdict] > rank[row["verdict"]]:
                row["verdict"] = f.verdict
        gap_matrix = [{**r, "verdict": r["verdict"].value} for r in by_req.values()]
        report = ComplianceReport(
            source_doc=state["source_doc"], findings=findings, summary=summary, gap_matrix=gap_matrix)
        return {"report": report}

    g = StateGraph(CheckState)
    g.add_node("extract_claims", extract_claims, retry_policy=retry_policy)
    g.add_node("retrieve_applicable", retrieve_applicable, retry_policy=retry_policy)
    g.add_node("judge", judge, retry_policy=retry_policy)
    g.add_node("assemble", assemble)
    g.add_edge(START, "extract_claims")
    g.add_edge("extract_claims", "retrieve_applicable")
    g.add_edge("retrieve_applicable", "judge")
    g.add_edge("judge", "assemble")
    g.add_edge("assemble", END)
    return g.compile()


def _requirement_from_row(row: dict) -> Requirement:
    """Reconstruct a `Requirement` from a stored row (all_requirements), parsing applicability_json back to
    constraints. Lenient: a bad row would raise, but the store wrote validated contracts."""
    import json

    from rag_wright.contracts.compliance import Constraint, DeonticType, Severity
    from rag_wright.contracts.provenance import ConfidenceTag

    scope = [Constraint(dimension=d, value=v) for d, v in json.loads(row.get("applicability_json") or "[]")]
    sev = row.get("severity") or None
    return Requirement(
        requirement_id=row["requirement_id"], source=row["source"], citation=row["citation"],
        deontic_type=DeonticType(row["deontic_type"]), actor=row["actor"],
        applicability_scope=scope, requirement_text=row["requirement_text"],
        evidence_standard=row.get("evidence_standard") or None,
        severity=Severity(sev) if sev else None,
        confidence=ConfidenceTag(row.get("confidence") or "EXTRACTED"))


class UnknownComplianceSourceError(ValueError):
    """Issue 0007: a `sources` filter named a policy `source` that has no requirements in the store. Distinct from
    a zero-requirement check (which a caller may treat as `not_checked`): naming a policy that does not exist is a
    caller error, surfaced explicitly rather than silently matching nothing. Carries `.unknown` and `.present`."""

    def __init__(self, unknown: list[str], present: list[str]) -> None:
        self.unknown = unknown
        self.present = present
        super().__init__(f"unknown compliance source(s): {unknown}; present in store: {present}")


def _validate_sources(store: Any, sources: Optional[list[str]]) -> None:
    """SEG-7a: validate named policy `sources` against the store EARLY -- so an unknown source raises
    `UnknownComplianceSourceError` BEFORE the expensive parse + assertion extraction, never wasting that work.
    `None` (whole store) is not validated (a store without `requirement_sources()` still works)."""
    if sources is None:
        return
    present = store.requirement_sources()
    unknown = sorted(set(sources) - present)
    if unknown:
        raise UnknownComplianceSourceError(unknown, sorted(present))


def _load_requirements(store: Any, sources: Optional[list[str]] = None) -> list[Requirement]:
    """Issue 0007: load the Requirement rows the check runs against, optionally scoped to named policy `source`s.

    `sources=None` -> the whole store (unchanged; `requirement_sources()` is NOT consulted, so a store without it
    still works). A list -> validate the names against `store.requirement_sources()` (an unknown one raises
    `UnknownComplianceSourceError`, not a silent empty match), then load ONLY those via the DB-side filter
    (`store.all_requirements(sources=...)`). An empty list is a valid scope-to-nothing -> zero requirements."""
    if sources is None:
        rows = store.all_requirements()
    else:
        unknown = sorted(set(sources) - store.requirement_sources())
        if unknown:
            raise UnknownComplianceSourceError(unknown, sorted(store.requirement_sources()))
        rows = store.all_requirements(sources=list(sources))
    return [_requirement_from_row(r) for r in rows]


def production_compliance_check(
    store: Any, *, extract_model: Any, judge_model_id: str, embedder: Any = None, k: int = 5,
    sources: Optional[list[str]] = None, claims_fn: Any = None,
):
    """Wire the real capabilities: claims = claim_extraction (CC-3), requirements = the store's Requirement KG
    (CC-5), judge = the Granite compliance judge (CC-4). Requirements are loaded ONCE here; when an `embedder` is
    given, CC-8b semantic narrowing is enabled (top-k content + always-include context + dedup), else broad.

    UNIFY-F: `claims_fn` (async `(text, source) -> [Claim]`) overrides the default whole-text extractor -- the ad
    entrypoint injects PRECOMPUTED per-section claims through it (parsed once via the shared front-end)."""
    from rag_wright.capabilities.claim_extraction import aclaim_extraction
    from rag_wright.capabilities.compliance_judgment import build_acompliance_judge_fn

    requirements = _load_requirements(store, sources)
    select_fn = build_select_fn(embedder, requirements, k=k) if embedder is not None else None

    async def _default_claims_fn(text: str, source: str) -> list:
        return await aclaim_extraction(text, model=extract_model, source_doc=source)

    return build_compliance_check(
        claims_fn=claims_fn or _default_claims_fn,
        requirements_fn=lambda: requirements,
        judge_fn=build_acompliance_judge_fn(judge_model_id),
        select_fn=select_fn,
    )


async def _aextract_ad_claims(chunks: list[str], source_doc: str, extract_model: Any,
                              *, aclaim_fn: Any = None, max_concurrency: int = 4) -> list:
    """SEG-7b: the advertising claim extractor over the SAME semantic CHUNKS as the generic path -- extract typed
    `Claim`s PER CHUNK (concurrently, per the parallel-LLM rule), re-indexed globally for unique ids. The
    typed-Claim tail (claim_type / disclosures / routing) is UNTOUCHED. The structural locator (§/¶/bullet) is
    attached AFTER, by `attach_structural_locators` (SEG-4, verbatim match), same as the generic path. `aclaim_fn`
    is injected for hermetic tests."""
    from rag_wright.capabilities.claim_extraction import aclaim_extraction
    from rag_wright.contracts.compliance import Claim

    fn = aclaim_fn or aclaim_extraction
    sem = asyncio.Semaphore(max_concurrency)

    async def _one(chunk: str) -> list:
        text = (chunk or "").strip()
        if not text:
            return []
        async with sem:
            return await fn(text, model=extract_model, source_doc=source_doc)

    per_chunk = await asyncio.gather(*(_one(c) for c in chunks))
    claims = [c for group in per_chunk for c in group]
    for i, c in enumerate(claims):  # global re-index -> unique claim ids across chunks
        c.fact_id = Claim.make_id(source_doc, i, c.assertion_text)
    return claims


async def run_compliance_check(
    subject_text: Optional[str] = None, source_doc: str = "", *, store: Any, extract_model: Any,
    judge_model_id: str, embedder: Any = None, k: int = 5, sources: Optional[list[str]] = None,
    name: Optional[str] = None, data: Optional[bytes] = None, discoverer: Any = None, aclaim_fn: Any = None,
    doc: Any = None,
) -> ComplianceReport:
    """The ADVERTISING compliance path -> a cited `ComplianceReport`. SEG-7b: accepts EITHER a pasted
    `subject_text` OR an uploaded ad (`name` + raw `data` bytes), and runs the SAME semantic front-end as the
    generic path -- parse -> semantic chunk -> per-chunk typed-`Claim` extraction -> attach structural locators
    (§/¶/bullet) -> judge. The typed-Claim tail (claim_type / disclosure routing) is UNCHANGED; a scanned ad's
    unreadable pages surface on `report.ocr_unreadable_pages` (SEG-6). `sources` (0007) scopes to named policies;
    `discoverer`/`aclaim_fn`/`doc` inject for tests."""
    _validate_sources(store, sources)  # reject an unknown policy BEFORE the expensive parse + extraction
    parsed_doc, unreadable = await _aparse_subject_any(text=subject_text, name=name, data=data, doc=doc)
    chunks = await subject_chunks(parsed_doc, discoverer=discoverer)
    claims = await _aextract_ad_claims(chunks, source_doc, extract_model, aclaim_fn=aclaim_fn)
    attach_structural_locators(claims, parsed_doc)  # SEG-4: verbatim-match each claim to its docling element

    async def _precomputed_claims_fn(_text: str, _source: str) -> list:
        return claims  # extracted once, per-chunk, above

    graph = production_compliance_check(
        store, extract_model=extract_model, judge_model_id=judge_model_id, embedder=embedder, k=k, sources=sources,
        claims_fn=_precomputed_claims_fn)
    subject_text_joined = "\n\n".join(c.assertion_text for c in claims)
    out = await graph.ainvoke({"subject_text": subject_text_joined, "source_doc": source_doc})
    report = out["report"]
    report.ocr_unreadable_pages = unreadable  # SEG-6: the ad path surfaces the OCR PARTIAL too
    return report


def generic_facts_fn(subject_text: str, source_doc: str) -> list:
    """COMP-VERDICT-GENERIC: the domain-agnostic subject producer -- the subject as ONE `CheckableFact` (no
    advertising claim structure). MVP granularity: the whole subject is one fact judged against the semantically
    relevant requirements. (Sentence/paragraph segmentation for finer citations is a later refinement.)"""
    text = (subject_text or "").strip()
    if not text:
        return []
    return [CheckableFact(fact_id=CheckableFact.make_id(source_doc, 0, text), source_doc=source_doc,
                          assertion_text=text)]


def sentence_facts_fn(subject_text: str, source_doc: str) -> list:
    """Issue 0010: the per-SENTENCE subject producer -- one `CheckableFact` per sentence, so each finding cites
    the sentence it is actually about rather than the whole document. Splits via `segment_clause` (sentence
    terminators, abbreviation- and decimal-safe: "Dr. Miller" / "$99" do not split). This is the DEFAULT for
    `run_generic_compliance_verdict` (better citation precision out of the box); pass `generic_facts_fn` for the
    old whole-subject behavior. Falls back to the whole subject if segmentation yields nothing."""
    from rag_wright.spans.segment import segment_clause

    text = (subject_text or "").strip()
    if not text:
        return []
    facts = [
        CheckableFact(fact_id=CheckableFact.make_id(source_doc, i, s), source_doc=source_doc, assertion_text=s)
        for i, sp in enumerate(segment_clause(source_doc, text))
        if (s := sp.text.strip())
    ]
    return facts or generic_facts_fn(subject_text, source_doc)


def production_generic_compliance_check(store: Any, *, judge_model_id: str, embedder: Any, k: int = 8,
                                        sources: Optional[list[str]] = None, facts_fn: Any = None):
    """COMP-VERDICT-GENERIC: wire the DOMAIN-AGNOSTIC verdict path -- generic subject facts (no claim_type),
    SEMANTIC-ONLY requirement narrowing (`filter_applicability=False`, no domain applicability ontology needed),
    and the GENERIC judge (text-only). Gives a cited LLM verdict in ANY compliance domain; enrichment
    (COMP-APPLIC-1) only ADDS structured precision on top. `embedder` is required (semantic retrieval is the
    narrowing here). `sources` (issue 0007) optionally scopes the check to named policy `source`s (None = the
    whole store) -- the bring-your-own-policy case where the store holds more than the one policy being checked.
    `facts_fn` (issue 0008) is the subject producer `(subject_text, source) -> [CheckableFact]`; defaults to
    `generic_facts_fn` (whole subject as ONE fact), and the document path injects a per-section producer."""
    from rag_wright.capabilities.compliance_judgment import build_ageneric_judge_fn

    requirements = _load_requirements(store, sources)
    select_fn = build_select_fn(embedder, requirements, k=k, filter_applicability=False)
    _facts = facts_fn or generic_facts_fn

    async def _claims_fn(text: str, source: str) -> list:
        return _facts(text, source)  # deterministic (no model), adapted to the async claims seam

    return build_compliance_check(
        claims_fn=_claims_fn,
        requirements_fn=lambda: requirements,
        judge_fn=build_ageneric_judge_fn(judge_model_id),
        select_fn=select_fn,
    )


async def run_generic_compliance_verdict(
    subject_text: str, source_doc: str, *, store: Any, judge_model_id: str, embedder: Any, k: int = 8,
    sources: Optional[list[str]] = None, extract_model: Any = None, discoverer: Any = None,
    aextract_fn: Any = None,
) -> ComplianceReport:
    """COMP-VERDICT-GENERIC: a domain-agnostic compliance verdict for a free-text subject against the Requirement
    KG -> a cited `ComplianceReport`. Works with NO domain applicability enrichment (the always-answer guarantee).

    `sources` (issue 0007) optionally scopes the check to named policy `source`s -- None checks against the whole
    store; a list checks against ONLY those policies; `[]` scopes to nothing; an unknown name raises
    `UnknownComplianceSourceError`.

    SEG-7a: a thin shim over `run_subject_compliance_verdict` (text mode). The paste is parsed through docling and
    run through the SAME semantic pipeline as an upload (chunk -> verbatim assertion extraction -> locator ->
    judge); a structureless paste yields per-assertion facts with NO `§` locator. `extract_model`/`discoverer`/
    `aextract_fn` are passed through (the latter two inject for tests)."""
    return await run_subject_compliance_verdict(
        source_doc, store=store, judge_model_id=judge_model_id, embedder=embedder, k=k, sources=sources,
        text=subject_text, extract_model=extract_model, discoverer=discoverer, aextract_fn=aextract_fn)


_HEADING_KINDS = frozenset({"section_header", "title", "field_heading"})


def _merge_wrapped_items(raw: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """SEG-4: coalesce docling's line-split of a wrapped paragraph back into ONE logical element. A line-based
    backend (markdown, a hard-wrapped .txt) emits each physical line as a separate `text` item; a mid-sentence
    line break is a soft-wrap, NOT a paragraph boundary. Signal: the previous same-kind body item does NOT end
    with sentence-terminal punctuation (`.`/`!`/`?`) -> the current item continues it, so merge. Headings never
    merge; a line ending in terminal punctuation starts a new element (a genuine paragraph break)."""
    merged: list[list[str]] = []
    for kind, text in raw:
        if (kind not in _HEADING_KINDS and text and merged
                and merged[-1][0] == kind and merged[-1][1] and merged[-1][1][-1] not in ".!?"):
            merged[-1][1] = f"{merged[-1][1]} {text}"  # soft-wrap continuation of the same logical element
        else:
            merged.append([kind, text])
    return [(k, t) for k, t in merged]


def _item_provenance(parsed_doc: Any) -> list[dict]:
    """SEG-4: docling items -> per-LOGICAL-ELEMENT structural provenance `{text, section, element_kind,
    element_ordinal}`. Line-wrapped paragraphs are merged first (`_merge_wrapped_items`) so ordinals count real
    paragraphs, not physical lines. `section` = the enclosing section number (shared `_section_number`; None
    before the first heading -> a flat doc stays section-less). Ordinals count WITHIN a section, PER KIND (¶ for
    body text, bullet for list items), reset at each heading."""
    from rag_wright.corpus.document_parser import _section_number

    raw: list[tuple[str, str]] = []
    for item in getattr(parsed_doc, "texts", []) or []:
        lab = getattr(item, "label", "")
        kind = str(getattr(lab, "value", lab) or "")  # DocItemLabel enum -> its value; a plain string stays as-is
        text = (getattr(item, "text", "") or "").strip()
        if not text and kind not in _HEADING_KINDS:
            continue  # empty body item: nothing to locate or count
        raw.append((kind, text))

    out: list[dict] = []
    section: Optional[str] = None
    n_sections = 0
    para_ord = 0
    bullet_ord = 0
    for kind, text in _merge_wrapped_items(raw):
        if kind in _HEADING_KINDS:  # a heading opens a new section and resets the within-section ordinals
            n_sections += 1
            section = _section_number(text, n_sections)
            para_ord = bullet_ord = 0
            out.append({"text": text, "section": section, "element_kind": kind, "element_ordinal": None})
            continue
        if kind == "list_item":
            bullet_ord += 1
            ordinal: Optional[int] = bullet_ord
        else:
            para_ord += 1
            ordinal = para_ord
        out.append({"text": text, "section": section, "element_kind": kind, "element_ordinal": ordinal})
    return out


def _norm_ws(s: str) -> str:
    return " ".join((s or "").split())


def attach_structural_locators(facts: list, parsed_doc: Any) -> list:
    """SEG-4: stamp each `CheckableFact` with the structural locator of the docling element its VERBATIM assertion
    came from (`section`, `element_kind`, `element_ordinal`) -> `locator()` renders `§ N ¶M` / `§ N · bullet M`.

    Robust to real-world parsing: docling can split a soft-wrapped paragraph (or a hard-wrapped .txt) into several
    consecutive `text` items, so an assertion may SPAN items. We therefore match against the whitespace-normalized
    CONCATENATION of the body items (each item's char range recorded), find the assertion, and attribute it to the
    item where it STARTS -- so a cross-item assertion is located, never dropped. Unmatched (genuinely absent /
    heavily paraphrased) stays unlocated, still citable by its text. Line-wrapped paragraphs are merged in
    `_item_provenance` so ordinals count real paragraphs, not physical lines. Mutates + returns `facts`."""
    body = [p for p in _item_provenance(parsed_doc) if _norm_ws(p["text"])]
    concat = ""
    ranges: list[tuple[int, int, dict]] = []  # (start, end, provenance) in the normalized concatenation
    for p in body:
        t = _norm_ws(p["text"])
        start = len(concat)
        concat += t + " "
        ranges.append((start, start + len(t), p))
    for f in facts:
        needle = _norm_ws(f.assertion_text)
        if not needle:
            continue
        pos = concat.find(needle)
        if pos < 0:
            continue
        for start, end, p in ranges:  # attribute to the item where the assertion STARTS
            if start <= pos < end:
                f.section = p["section"]
                f.element_kind = p["element_kind"]
                f.element_ordinal = p["element_ordinal"]
                break
    return facts


async def aextract_subject_facts(chunks: list[str], *, source_doc: str, model: Any, aextract_fn: Any = None,
                                 max_concurrency: int = 4) -> list:
    """SEG-3: extract the checkable assertions (verbatim) from each subject CHUNK CONCURRENTLY (semaphore, per the
    parallel-LLM rule) -> `CheckableFact`s, re-indexed globally so `fact_id`s are unique across chunks.
    Domain-neutral (`CheckableFact`, no `claim_type` -- that is the ad path). The structural locator
    (section / ¶ / bullet) is attached later, in SEG-4. `aextract_fn` is injected for hermetic tests."""
    from rag_wright.capabilities.assertion_extraction import aassertion_extraction
    from rag_wright.capabilities.dg_extraction import aextract_parties

    fn = aextract_fn or aextract_parties
    sem = asyncio.Semaphore(max_concurrency)

    async def _one(chunk: str) -> list:
        async with sem:
            return await aassertion_extraction(chunk, model=model, source_doc=source_doc, aextract_fn=fn)

    per_chunk = await asyncio.gather(*(_one(c) for c in chunks))
    facts = [f for group in per_chunk for f in group]
    for i, f in enumerate(facts):  # global re-index -> unique fact_ids across chunks
        f.fact_id = CheckableFact.make_id(source_doc, i, f.assertion_text)
    return facts


async def subject_chunks(parsed_doc: Any, *, discoverer: Any = None) -> list[str]:
    """SEG-2/SEG-5: semantically chunk a parsed subject document into coherent chunk texts, via the SAME shared
    chunker as ingestion (`achunk_texts`; no cache/summarize -- the subject is transient). The default discoverer
    is `StructuralModelFallbackDiscoverer` -- the exact discoverer PRODUCTION INGESTION uses: structural boundaries
    first (no model for a structured doc), a BOUNDED per-section model refinement only for an over-cap section, so
    cost never scales with document length (no size bottleneck) and there is NO subject-specific large-doc code.
    `parsed_doc` is the docling document (exposes `.texts`). RLM is a future escalation, as on ingestion. SEG-3
    extracts the checkable assertions from each returned chunk."""
    from rag_wright.capabilities.rlm_chunking import achunk_texts

    return await achunk_texts(parsed_doc, discoverer=discoverer)


async def semantic_subject_facts(parsed_doc: Any, *, source_doc: str, model: Any = None, discoverer: Any = None,
                                 aextract_fn: Any = None) -> list:
    """SEG-7a: the SUBJECT fact producer -- the composed semantic pipeline that supersedes the regex
    `subject_facts_fn`. Chunk the parsed doc (SEG-2/SEG-5, the production ingestion discoverer) -> extract the
    checkable assertions VERBATIM per chunk (SEG-3) -> attach each to its docling element for the structural
    locator (SEG-4). Returns `CheckableFact`s cited "doc § {section} ¶{n}: {verbatim}" (or just the span for a
    flat doc). `model` (assertion extractor) defaults to the production extraction model; `discoverer`/`aextract_fn`
    are injected for hermetic tests (no model/parse)."""
    m = model
    if m is None and aextract_fn is None:
        from rag_wright.capabilities.dg_extraction import default_extraction_model

        m = default_extraction_model("subject-assert", "ibm-granite/granite-4.1-8b")
    chunks = await subject_chunks(parsed_doc, discoverer=discoverer)
    facts = await aextract_subject_facts(chunks, source_doc=source_doc, model=m, aextract_fn=aextract_fn)
    attach_structural_locators(facts, parsed_doc)
    return facts


async def _aparse_subject_any(*, text: Optional[str], name: Optional[str], data: Optional[bytes],
                              doc: Any = None) -> tuple[Any, list[int]]:
    """SEG-7a: parse ANY subject input to a docling document + OCR unreadable pages, for the uniform semantic
    pipeline. `doc` (a test injection) is returned as-is. Upload (`data`): `aparse_subject` (tiered OCR, captures
    unreadable pages). Paste (`text`): parsed through docling as `.txt` bytes (decision A -- uniform semantic
    handling, no OCR pages), NOT the old short-circuit."""
    if doc is not None:
        return doc, []
    if data is not None:
        return await aparse_subject(name, data)
    if text is not None:
        from rag_wright.corpus.document_parser import aparse_document_bytes

        return await aparse_document_bytes("subject.txt", text.encode("utf-8")), []
    raise ValueError("run_subject_compliance_verdict needs either text= or (name=, data=)")


def subject_facts_fn(sections: list[dict], source_doc: str) -> list:
    """UNIFY-B: the unified section->sentence subject producer. For each parsed section (`{section, heading, text}`)
    split its body into sentences (`segment_clause`, abbreviation/decimal-safe: "Dr. Miller"/"$99" don't split) and
    emit one `CheckableFact` per sentence carrying BOTH the SECTION locator (UNIFY-A) and the CLEAN sentence as
    `assertion_text`. So each finding cites "doc § {section}: sentence" -- the section from document structure, the
    sentence from segmentation, neither polluting the other.

    Contrast with the producers it unifies: `document_facts_fn` (0008) is per-SECTION and folds the heading INTO
    `assertion_text` (coarse citation); `sentence_facts_fn` (0010) is per-SENTENCE but has NO section locator. This
    is per-(section, sentence) WITH the locator as a field. Empty sections and blank sentences are skipped; a
    running index keeps every fact_id unique across sections."""
    from rag_wright.spans.segment import segment_clause

    facts: list = []
    for sec in sections:
        text = (sec.get("text") or "").strip()
        if not text:
            continue
        locator = sec.get("section") or None
        for sp in segment_clause(source_doc, text):
            s = sp.text.strip()
            if not s:
                continue
            facts.append(CheckableFact(fact_id=CheckableFact.make_id(source_doc, len(facts), s),
                                       source_doc=source_doc, assertion_text=s, section=locator))
    return facts


def document_facts_fn(sections: list[dict], source_doc: str) -> list:
    """Issue 0008: turn a parsed subject document's sections (`[{section, heading, text}]`) into PER-SECTION
    `CheckableFact`s, so each section gets its own cited finding -- vs `generic_facts_fn`'s whole-subject single
    fact. Empty sections are skipped; a section's heading is prepended to its body for judge context."""
    facts: list = []
    for i, sec in enumerate(sections):
        text = (sec.get("text") or "").strip()
        if not text:
            continue
        heading = (sec.get("heading") or "").strip()
        assertion = f"{heading}\n{text}" if heading else text
        facts.append(CheckableFact(fact_id=CheckableFact.make_id(source_doc, i, assertion),
                                   source_doc=source_doc, assertion_text=assertion))
    return facts


async def aparse_subject(name: str, data: bytes, *, parser: Any = None) -> tuple[Any, list[int]]:
    """SEG-6: parse an uploaded subject ONCE through the tiered OCR chokepoint (`TieredOCRParser`, the SAME OCR as
    ingestion), returning `(docling_document, ocr_unreadable_pages)`. The unreadable pages (a degraded scan the
    VLM still could not read) are captured from the tiered parser's report so they can surface on the
    `ComplianceReport` -- a verdict is never silently based on half-read text. `parser` injected for tests."""
    from rag_wright.capabilities.parsing import TieredOCRParser
    from rag_wright.corpus.document_parser import aparse_document_bytes

    tiered = parser if parser is not None else TieredOCRParser()
    document = await aparse_document_bytes(name, data, parser=tiered)
    unreadable = list(getattr(getattr(tiered, "report", None), "unreadable_pages", []) or [])
    return document, unreadable


async def run_subject_compliance_verdict(
    source_doc: str, *, store: Any, judge_model_id: str, embedder: Any, k: int = 8,
    sources: Optional[list[str]] = None, text: Optional[str] = None, name: Optional[str] = None,
    data: Optional[bytes] = None, extract_model: Any = None, discoverer: Any = None, aextract_fn: Any = None,
    doc: Any = None,
) -> ComplianceReport:
    """SEG-7a: the ONE subject-compliance front-end. Accepts EITHER a pasted `text` OR an uploaded document
    (`name` + raw `data` bytes: PDF/DOCX/HTML/TXT), and runs the SEMANTIC pipeline uniformly: parse -> semantic
    chunk (the production ingestion discoverer) -> extract the checkable assertions VERBATIM per chunk -> attach
    each to its docling element -> judge. Findings cite "doc § {section} ¶{n}: {verbatim}" (or just the span for a
    structureless subject). The subject is TRANSIENT (parsed/checked, never written to the store); a scanned
    subject's unreadable pages surface on `report.ocr_unreadable_pages` (SEG-6).

    `extract_model` (assertion extractor) defaults to the production extraction model. `sources` (0007) scopes to
    named policies (unknown -> `UnknownComplianceSourceError`). `discoverer`/`aextract_fn`/`doc` are injected for
    hermetic tests. (SEG-7a replaced the old sections/`subject_facts_fn` regex dial with the semantic producer.)"""
    _validate_sources(store, sources)  # SEG-7a: reject an unknown policy BEFORE the expensive parse + extraction
    parsed_doc, unreadable = await _aparse_subject_any(text=text, name=name, data=data, doc=doc)
    facts = await semantic_subject_facts(
        parsed_doc, source_doc=source_doc, model=extract_model, discoverer=discoverer, aextract_fn=aextract_fn)
    graph = production_generic_compliance_check(
        store, judge_model_id=judge_model_id, embedder=embedder, k=k, sources=sources,
        facts_fn=lambda _text, _source: facts)  # precomputed semantic subject facts
    subject_text = "\n\n".join(f.assertion_text for f in facts)
    out = await graph.ainvoke({"subject_text": subject_text, "source_doc": source_doc})
    report = out["report"]
    report.ocr_unreadable_pages = unreadable  # SEG-6: surface the OCR PARTIAL so a verdict is never silently partial
    return report


async def run_compliance_document_verdict(
    doc_name: str, data: bytes, *, store: Any, judge_model_id: str, embedder: Any, k: int = 8,
    sources: Optional[list[str]] = None, extract_model: Any = None, discoverer: Any = None,
    aextract_fn: Any = None, doc: Any = None,
) -> ComplianceReport:
    """Issue 0008 / SEG-7a: check an uploaded subject DOCUMENT (raw bytes: PDF/DOCX/HTML/TXT) for compliance --
    a thin shim over `run_subject_compliance_verdict` (the shared SEMANTIC front-end). Findings cite each verbatim
    assertion's "§ {section} ¶{n}" locator; a scanned subject's unreadable pages surface on the report (SEG-6).
    `sources` (0007) scopes to named policies; `discoverer`/`aextract_fn`/`doc` inject for tests."""
    return await run_subject_compliance_verdict(
        doc_name, store=store, judge_model_id=judge_model_id, embedder=embedder, k=k, sources=sources,
        name=doc_name, data=data, extract_model=extract_model, discoverer=discoverer, aextract_fn=aextract_fn,
        doc=doc)


def register_compliance_check(registry) -> None:
    """Register `compliance_check` (subgraph; CC-6). Contract = `ComplianceReport`."""
    registry.register(
        "compliance_check",
        contract=ComplianceReport,
        kind="subgraph",
        display_name="Compliance check (subject doc x requirements -> cited findings + gap matrix)",
    )
