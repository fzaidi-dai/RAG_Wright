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

from rag_wright.packs.compliance.capabilities.compliance_judgment import AJudgeFn, ajudge_pairs
from rag_wright.capabilities.retrieval_core import _cosine
from enum import Enum

from rag_wright.packs.compliance.schemas.compliance import (
    CheckableFact,
    Claim,
    ClaimType,
    ComplianceFinding,
    ComplianceReport,
    Constraint,
    DeonticType,
    Requirement,
    RuleScope,
    Verdict,
)
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.packs.compliance.ontology.loader import load_actor_synonyms, load_role_domains, load_section_overrides
from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, business_span
from rag_wright.packs.compliance.capabilities.compliance_store import ComplianceStore

_ALL_CLAIM_TYPES = {c.value for c in ClaimType}

# ADR-0066 P4b: the per-section overrides (DEON-8 applicable claim types + DEON-1 rule scope) are AUTHORITATIVE in
# a DOMAIN PACK ttl (packs/ftc_16cfr255.ttl), not Python literals. To retarget a domain, ship its own pack; nothing
# FTC-specific is hardcoded here. (FTC finding CC-6: the endorsement guides apply by CONTEXT, not claim_type, so
# every operative section applies to ALL claim types and only definitions (255.0) is excluded -- now in the pack.)
_SECTION_RULE_SCOPE_RAW, SECTION_CLAIM_TYPES = load_section_overrides()
SECTION_RULE_SCOPE: dict[str, RuleScope] = {sec: RuleScope(v) for sec, v in _SECTION_RULE_SCOPE_RAW.items()}


def _section_of(citation: str) -> str:
    """'§ 255.5' -> '255.5' (the section key for the applicability map)."""
    return (citation or "").replace("§", "").strip()


def applicable_claim_types(requirement: Requirement) -> set[str]:
    """DEON-8 (issue 0012): the claim types a requirement applies to. A CURATED override (`SECTION_CLAIM_TYPES`,
    the FTC reference pack) WINS when the requirement's section is pinned there -- FTC behavior is byte-identical
    (context sections apply to ALL claim types; §255.0 to none), and a noisy extracted claim_type can neither
    narrow a context section nor rescue definitions. For ANY OTHER (customer) section the extracted `claim_type`
    scope is LOAD-BEARING: it NARROWS (a pricing-scoped rule does not apply to a health claim); an empty scope is
    recall-first (applies to all). So the KG's claim_type field routes for ANY policy, not just FTC -- the ad-path
    analog of the DEON-6/7 actor gate (a curated override on top of a load-bearing typed field). The real per-
    claim narrowing (most-relevant rule) is still semantic retrieval (Leg-B), the CC-7 refinement."""
    section = _section_of(requirement.citation)
    if section in SECTION_CLAIM_TYPES:  # curated FTC override wins (context = all types, definitions = none)
        return SECTION_CLAIM_TYPES[section]
    scope = {c.value for c in requirement.applicability_scope if c.dimension == "claim_type"}
    return scope or _ALL_CLAIM_TYPES  # customer policy: extracted scope narrows; empty -> recall-first


def _constraints_by_dimension(constraints: list) -> dict:
    """`[Constraint(dimension, value), ...]` -> {dimension: {values}}."""
    out: dict = {}
    for c in constraints:
        out.setdefault(c.dimension, set()).add(c.value)
    return out


_ROLE_GENERIC = frozenset({"", "party", "anyone", "any", "all", "everyone", "subject", "person", "other"})

# DEON-6/7: generic, DOMAIN-agnostic role-synonym normalization -- collapse common variants to one canonical role
# so the rule side and the subject side align (BGE cosine on bare role words does NOT encode role equivalence:
# employer~manufacturer 0.68 > advertiser~manufacturer 0.63, so a similarity threshold cannot separate them).
# An unknown role is KEPT as-is (both sides normalize identically, so an exotic domain still matches on its own
# term); this is role knowledge, NOT an FTC/corpus hardcode.
# ADR-0066 P4a: AUTHORITATIVE in compliance_bridge.ttl (cmp:ActorRole skos:altLabel) -- loaded, not a Python
# literal. To add a role synonym, edit the ttl (a new customer domain extends the role pack, not this code).
_ACTOR_SYNONYMS: dict[str, str] = load_actor_synonyms()

# ADR-0068 (engine issue 0013): the DISJOINTNESS knowledge for the recall-first actor gate -- `{canonical role ->
# domain}` from the ontology (cmp:roleDomain). AUTHORITATIVE in compliance_bridge.ttl; a customer domain adds its
# roles' domains in its own pack. Two roles are disjoint iff BOTH are here with DIFFERENT domains.
_ROLE_DOMAINS: dict[str, str] = load_role_domains()


def canonical_actor(raw: str) -> str:
    """DEON-6/7: normalize an actor ROLE to its canonical form -- collapse a known synonym (manufacturer ->
    advertiser), else keep the role as-is (lower-cased). Applied identically to the rule and subject side, so the
    KG actor gate matches on aligned roles."""
    a = (raw or "").strip().lower()
    return _ACTOR_SYNONYMS.get(a, a)


def _actor_set(scope: list) -> set:
    """The canonical `actor` roles in a scope (a claim's, or the document's aggregated)."""
    return {canonical_actor(c.value) for c in (scope or []) if c.dimension == "actor" and c.value}


def roles_disjoint(a: str, b: str) -> bool:
    """ADR-0068: are two CANONICAL actor roles ontology-DISJOINT? True ONLY when both carry a `cmp:roleDomain` and
    the domains DIFFER (e.g. an advertising role vs a labor role). An unmodelled role (no domain), or two roles in
    the same domain, are NOT disjoint -- the recall-first default is compatible."""
    da, db = _ROLE_DOMAINS.get(a), _ROLE_DOMAINS.get(b)
    return da is not None and db is not None and da != db


def roles_compatible(a: str, b: str) -> bool:
    """ADR-0068: two CANONICAL actor roles are COMPATIBLE (a pair worth judging) unless the ontology makes them
    disjoint -- recall-first. A generic/absent role on either side is always compatible. Replaces exact role
    equality: two different-but-overlapping roles (advertiser vs seller) now match, so a real violation is never
    silently dropped because two independent extractions chose different words for the same party."""
    return not a or not b or a in _ROLE_GENERIC or b in _ROLE_GENERIC or not roles_disjoint(a, b)


def actor_matches(rule_actor: str, subject_actors: set) -> bool:
    """DEON-6/7 + ADR-0068: is the requirement's actor ROLE COMPATIBLE with the subject's (already-canonical)
    actors? RECALL-FIRST on two axes: (1) a generic/absent rule actor or a subject with no actor info never gates
    (True); (2) a specific rule actor matches unless it is ontology-DISJOINT from EVERY subject actor -- so an
    unmodelled or merely-different-but-overlapping role is judged, not dropped (issue 0013)."""
    ra = canonical_actor(rule_actor)
    if not ra or ra in _ROLE_GENERIC or not subject_actors:
        return True  # recall-first: nothing to gate on
    return any(roles_compatible(ra, sa) for sa in subject_actors)


def _actor_compatible(a: str, b: str) -> bool:
    """DEON-9 + ADR-0068: the symbolic gate for which permissions can defend which O/F rule -- two CANONICAL actor
    roles are compatible unless ontology-disjoint (recall-first), same predicate as the actor gate."""
    return roles_compatible(a, b)


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


class DeonticRoute(str, Enum):
    """DEON-1 (issue 0012): the JUDGE route a rule takes, derived from its DEONTIC TYPE (a KG-typed field), not
    its FTC section number -- so it works for ANY customer policy."""

    OBLIGATION = "obligation"    # breach = ABSENCE -> document-scoped, judged ONCE (a per-sentence judge cannot
    #                              answer "is it present anywhere?"); always-included (CONTEXT).
    PROHIBITION = "prohibition"  # breach = PRESENCE -> per-assertion, where the subject asserts something related.
    PERMISSION = "permission"    # cannot be violated standalone -> EXCLUDED from violation-judging (an exception /
    #                              defense that modifies an O/F rule; linked in DEON-9).
    AMBIGUOUS = "ambiguous"      # deontic force unreadable (off-vocab, coerced) -> recall-first: per-assertion + flag.


def deontic_route(requirement: Requirement) -> DeonticRoute:
    """DEON-1: the judge route for a rule, from its deontic type. Precedence: a curated `SECTION_RULE_SCOPE`
    override (a hand-tuned domain pack may still pin scope by section) > an AMBIGUOUS deontic (off-vocab, coerced
    to OBLIGATION but flagged -> recall-first, never trusted as an obligation) > the deontic type itself. FTC-
    agnostic: a customer policy routes by what its rules ARE, not by matching FTC 16 CFR 255 section numbers."""
    override = SECTION_RULE_SCOPE.get(_section_of(requirement.citation))
    if override is RuleScope.CONTEXT:
        return DeonticRoute.OBLIGATION
    if override is RuleScope.CONTENT:
        return DeonticRoute.PROHIBITION
    if requirement.confidence is ConfidenceTag.AMBIGUOUS:  # the extractor could not read the deontic force
        return DeonticRoute.AMBIGUOUS
    if requirement.deontic_type is DeonticType.OBLIGATION:
        return DeonticRoute.OBLIGATION
    if requirement.deontic_type is DeonticType.PERMISSION:
        return DeonticRoute.PERMISSION
    return DeonticRoute.PROHIBITION


def rule_scope_of(requirement: Requirement) -> RuleScope:
    """DEON-1: CONTEXT (always-include) for an obligation, else CONTENT (narrow by similarity) -- derived from
    `deontic_route`, so it is DEONTIC-driven, not FTC-section-driven."""
    return RuleScope.CONTEXT if deontic_route(requirement) is DeonticRoute.OBLIGATION else RuleScope.CONTENT


# DEON-2: how much of the subject the obligation judge sees -- the top-N most-relevant passages up to a char
# budget, NOT the whole document (an obligation is judged once over BOUNDED retrieved evidence, not per-sentence
# and not by dumping a contract-length document into one prompt).
OBLIGATION_TOP_N = 5
OBLIGATION_CHAR_BUDGET = 4000


def _within_budget(claims: list, *, top_n: int, char_budget: int) -> list:
    """Take up to `top_n` claims (already ranked) but stop once the cumulative assertion text exceeds
    `char_budget` -- the bounded evidence window for one obligation judgment. Always keeps at least the first."""
    out: list = []
    used = 0
    for c in claims[:top_n]:
        text = c.assertion_text or ""
        if out and used + len(text) > char_budget:
            break
        out.append(c)
        used += len(text)
    return out


def subject_scope(facts: list) -> list:
    """DEON-5: the document-level SubjectScope -- the deduped union of every fact's per-assertion `scope`
    `Constraint`s (actor + any inferred dimension). Feeds the obligation actor-gate (DEON-7: is the rule's actor
    present in the document at all?) and, per-assertion, the prohibition constraint router (DEON-6). Returns a
    `list[Constraint]`; the actor-set is the values on dimension 'actor'."""
    seen: set = set()
    out: list = []
    for f in facts:
        for c in getattr(f, "scope", None) or []:
            key = (c.dimension, c.value)
            if key not in seen:
                seen.add(key)
                out.append(c)
    return out


def _document_signal_line(claims: list) -> str:
    """DEON-8 (Option 1): the ad-level structured SIGNALS rendered as document content for the obligation judge --
    the disclosure union + whether evidence is referenced, aggregated across ALL claims (a disclosure made
    ANYWHERE in the ad satisfies a disclosure obligation, so the whole-document union matters, not just the top-N
    evidence window). getattr-tolerant so a generic (non-ad) fact contributes nothing -> '' (domain-neutral: the
    engine's obligation retriever stays free of ad concepts, the signals only appear when the facts carry them)."""
    disclosures = sorted({d for c in claims for d in (getattr(c, "disclosures_present", None) or [])})
    evidence = any(getattr(c, "evidence_referenced", False) for c in claims)
    parts: list[str] = []
    if disclosures:
        parts.append("disclosures present in the document: " + "; ".join(disclosures))
    if evidence:
        parts.append("the document references supporting evidence")
    return ("\n\n[DOCUMENT SIGNALS] " + "; ".join(parts) + ".") if parts else ""


def build_obligation_pairs_fn(embedder: Any, *, top_n: int = OBLIGATION_TOP_N,
                              char_budget: int = OBLIGATION_CHAR_BUDGET) -> Any:
    """DEON-2: the obligation evidence retriever. For each obligation, embed it and RANK the subject assertions,
    take the top-N most-relevant up to a char budget, and build one bounded evidence `CheckableFact` -> one
    `(evidence_fact, obligation)` pair. Symbolic/vector narrowing (KG deontic route + embeddings) selects the
    small evidence set; the LLM then judges once over it (breach = absence). Claims are embedded ONCE.

    DEON-8 (Option 1): the ad-level structured SIGNALS (the disclosure union / evidence-referenced) are appended
    to each bundle as document content, so an obligation judged ONCE on the ad path still sees a disclosure made
    anywhere in the ad. getattr-tolerant, so the generic path is unaffected."""
    def obligation_pairs(obligations: list, claims: list, source_doc: str) -> list:
        if not (obligations and claims):
            return []
        # DEON-7: the ACTOR GATE (symbolic, zero LLM) -- an obligation whose bound actor is NOT present in the
        # document's actor-set is out of scope, so it is SKIPPED entirely (no retrieval, no judge call). Robust to
        # role synonyms via `actor_matches`; recall-first (a generic/absent actor never gates).
        doc_actors = _actor_set(subject_scope(claims))
        obligations = [ob for ob in obligations if actor_matches(ob.actor, doc_actors)]
        if not obligations:
            return []
        signal_line = _document_signal_line(claims)  # DEON-8: carry the ad-level disclosure/evidence signals
        claim_vecs = [(c, embedder.encode_dense(c.assertion_text)) for c in claims]  # embed the subject once
        pairs: list = []
        for ob in obligations:
            ob_vec = embedder.encode_dense(ob.requirement_text)
            ranked = [c for c, _ in sorted(claim_vecs, key=lambda cv: _cosine(ob_vec, cv[1]), reverse=True)]
            evidence = _within_budget(ranked, top_n=top_n, char_budget=char_budget)
            # issue 0044: `assertion_text` is document text ONLY (so the citation stays a quote from the user's
            # doc); the DEON-8 signals ride in `document_signals`, seen by the judge but never cited. The bundle is
            # the top-N spans joined -> ASSEMBLED evidence, flagged so a consumer never renders it as one verbatim.
            text = "\n\n".join(c.assertion_text for c in evidence) or "(empty subject)"
            fact = CheckableFact(fact_id=CheckableFact.make_id(source_doc, ob.requirement_id, text),
                                 source_doc=source_doc, assertion_text=text,
                                 document_signals=signal_line, citation_kind="assembled")
            pairs.append((fact, ob))
        return pairs

    return obligation_pairs


DEFENSE_TOP_N = 3


def build_defense_linker(embedder: Any, requirements: list, *, top_n: int = DEFENSE_TOP_N) -> Any:
    """DEON-9 (issue 0012): the PERMISSION-AS-DEFENSE linker (ADR-0044 pattern, requirement side). For an
    obligation/prohibition rule, return the same-`source` PERMISSIONS that may EXCUSE it (a carve-out/safe-harbor),
    so the judge can rule a legitimate exception COMPLIANT instead of a false violation. Symbolic candidacy: same
    policy source + actor-compatible (canonical, recall-first); ranked by semantic proximity and capped at `top_n`
    -- rank+cap, NOT a fragile similarity threshold. Zero extra LLM: the ONE judge call now reasons over the rule
    plus its linked defenses. Vectors are precomputed ONCE (like `build_select_fn`)."""
    vectors = {r.requirement_id: embedder.encode_dense(r.requirement_text) for r in requirements}
    perms_by_source: dict[str, list] = {}
    for r in requirements:
        if deontic_route(r) is DeonticRoute.PERMISSION:
            perms_by_source.setdefault(r.source, []).append(r)

    def defenses_for(rule: Any) -> list:
        if deontic_route(rule) is DeonticRoute.PERMISSION:
            return []  # a permission is not judged for violation, so it carries no defenses of its own
        perms = perms_by_source.get(rule.source, [])
        if not perms:
            return []
        ra = canonical_actor(rule.actor)
        cands = [p for p in perms if _actor_compatible(ra, canonical_actor(p.actor))]
        rule_vec = vectors.get(rule.requirement_id, [])
        ranked = sorted(cands, key=lambda p: _cosine(rule_vec, vectors.get(p.requirement_id, [])), reverse=True)
        return ranked[:top_n]

    return defenses_for


def _with_defenses(requirement: Any, defense_linker: Any) -> Any:
    """DEON-9: attach the linked permissions (rendered `citation: text`) to a rule as query-time `defenses`, so
    the judge renders them as structured exception context. A no-op (returns the rule unchanged) when nothing is
    linked, so an unrelated rule is untouched."""
    if defense_linker is None:
        return requirement
    linked = defense_linker(requirement)
    if not linked:
        return requirement
    return requirement.model_copy(
        update={"defenses": [f"{p.citation}: {p.requirement_text}" for p in linked]})


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
            actors = _actor_set(subject_scope)  # DEON-6: prohibition gated by (non-actor constraints) AND actor role
            applicable = [r for r in reqs if constraint_applies(r.applicability_scope, subject_scope)
                          and actor_matches(r.actor, actors)]
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


def _actor_gated_pairs(claims: list, prohibitions: list, obligations: list, constraint_scope_fn: Any) -> list[dict]:
    """ADR-0068 (issue 0013): the (assertion|document, rule) pairs the symbolic ACTOR gate SKIPPED before any judge
    call -- recomputed from the SAME module gate (`actor_matches`) the router applies, so the report can state
    honest coverage and a gated pair is never silent. OBLIGATIONS: a rule whose bound actor is ontology-disjoint
    from every document actor (DEON-7, document scope). PROHIBITIONS: per assertion, a rule that PASSES the
    constraint router but is actor-disjoint from the assertion's actors (DEON-6) -- computed only when constraint
    routing is active (the ad path narrows prohibitions by claim_type, not the actor gate, so it reports none).
    Empty unless a disjoint role actually blocked a pair (the recall-first norm)."""
    gated: list[dict] = []
    doc_actors = _actor_set(subject_scope(claims)) if claims else set()
    for ob in obligations:
        if not actor_matches(ob.actor, doc_actors):
            gated.append({"requirement_id": ob.requirement_id, "citation": ob.citation,
                          "actor": canonical_actor(ob.actor), "subject_actors": sorted(doc_actors),
                          "scope": "document", "claim_id": None})
    if constraint_scope_fn is not None:
        for claim in claims:
            subj = constraint_scope_fn(claim)
            actors = _actor_set(subj)
            for p in prohibitions:
                if constraint_applies(p.applicability_scope, subj) and not actor_matches(p.actor, actors):
                    gated.append({"requirement_id": p.requirement_id, "citation": p.citation,
                                  "actor": canonical_actor(p.actor), "subject_actors": sorted(actors),
                                  "scope": "assertion", "claim_id": getattr(claim, "fact_id", None)})
    return gated


# ASYNC-C1 (ADR-0057): claims_fn is async (its extraction model call gets a true wall-clock deadline).
ClaimsFn = Callable[[str, str], Awaitable[list]]  # (subject_text, source_doc) -> list[Claim]
RequirementsFn = Callable[[], list]  # () -> list[Requirement]


class CheckState(TypedDict, total=False):
    subject_text: str
    source_doc: str
    claims: list
    ad_disclosures: list
    pairs: list
    gated: list  # ADR-0068 (issue 0013): (assertion|document, rule) pairs the actor gate skipped -> the report
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
    select_fn: SelectFn | None = None, obligation_pairs_fn: Any = None, defense_linker: Any = None,
    constraint_scope_fn: Any = None, retry_policy: Any = DEFAULT_RETRY
):
    """Compile the compliance-check subgraph. All seams are injected for hermetic testing. `select_fn` (CC-8b) is
    the per-claim requirement narrower; when None, every APPLICABLE requirement is judged (the broad default).

    DEON-1/DEON-2 (issue 0012): when `obligation_pairs_fn` (`(obligations, claims, source) -> [(evidence_fact,
    obligation)]`) is provided, the requirements are split by `deontic_route`: PROHIBITION/AMBIGUOUS rules are
    judged PER-ASSERTION (narrowed by `select_fn`), OBLIGATION rules are judged ONCE each over a BOUNDED retrieved
    evidence bundle (breach = absence, unanswerable per-sentence), and PERMISSION rules are excluded from
    violation-judging. When None (the ad path, until DEON-8), the prior per-assertion pairing is kept.

    DEON-9: when `defense_linker` (`rule -> [permission]`) is provided, each O/F rule is enriched with the same-
    source PERMISSIONS that may EXCUSE it (attached as query-time `defenses`), passed to that rule's judge as
    structured exception context so a legitimate carve-out is not a false violation. Query-side: each node degrades
    to empty on failure (never crashes)."""

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
        if defense_linker is not None:  # DEON-9: enrich each O/F rule with its same-source permission carve-outs
            requirements = [_with_defenses(r, defense_linker) for r in requirements]
        if obligation_pairs_fn is None:  # ad path (until DEON-8): the prior per-assertion pairing
            if select_fn is not None:  # CC-8b: semantic narrowing (top-k content + always-include context + dedup)
                pairs = [(_enrich(claim, ad), req) for claim in claims for req in select_fn(claim, requirements)]
            else:  # broad default: every applicable requirement
                pairs = [(_enrich(claim, ad), req)
                         for claim in claims for req in requirements if applies_to(req, claim)]
            return {"pairs": pairs}
        # DEON-1/2: deontic split -- prohibitions per-assertion, obligations judged ONCE over bounded retrieved
        # evidence, permissions excluded.
        prohibitions = [r for r in requirements
                        if deontic_route(r) in (DeonticRoute.PROHIBITION, DeonticRoute.AMBIGUOUS)]
        obligations = [r for r in requirements if deontic_route(r) is DeonticRoute.OBLIGATION]
        pairs = []
        for claim in claims:  # prohibitions/ambiguous: per-assertion, where the subject asserts something related
            selected = select_fn(claim, prohibitions) if select_fn is not None else prohibitions
            pairs.extend((_enrich(claim, ad), req) for req in selected)
        if obligations and claims:  # obligations: one bounded (evidence, obligation) pair each
            pairs.extend(obligation_pairs_fn(obligations, claims, state["source_doc"]))
        # ADR-0068 (issue 0013): surface what the ACTOR gate skipped, so a symbolic drop is never a silent recall
        # loss (empty is the recall-first norm; a disjoint role that blocked a pair shows up here).
        gated = _actor_gated_pairs(claims, prohibitions, obligations, constraint_scope_fn)
        return {"pairs": pairs, "gated": gated}

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
            source_doc=state["source_doc"], findings=findings, summary=summary, gap_matrix=gap_matrix,
            gated_pairs=state.get("gated", []))  # ADR-0068: honest coverage -- the actor gate is never silent
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

    from rag_wright.packs.compliance.schemas.compliance import DeonticType, Severity
    from rag_wright.contracts.provenance import ConfidenceTag

    scope = [Constraint(dimension=d, value=v) for d, v in json.loads(row.get("applicability_json") or "[]")]
    sev = row.get("severity") or None
    _bbox = row.get("bbox")  # issue 0043: best-effort [l,t,r,b] JSON string -> tuple, else None
    bbox = tuple(json.loads(_bbox)) if _bbox else None
    return Requirement(
        requirement_id=row["requirement_id"], source=row["source"], citation=row["citation"],
        deontic_type=DeonticType(row["deontic_type"]), actor=row["actor"],
        applicability_scope=scope, requirement_text=row["requirement_text"],
        evidence_standard=row.get("evidence_standard") or None,
        severity=Severity(sev) if sev else None,
        pages=[int(p) for p in (row.get("pages") or [])], bbox=bbox,  # issue 0043: policy page provenance
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
    present = ComplianceStore(store).requirement_sources()
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
        rows = ComplianceStore(store).all_requirements()
    else:
        reqs = ComplianceStore(store)
        present = reqs.requirement_sources()
        unknown = sorted(set(sources) - present)
        if unknown:
            raise UnknownComplianceSourceError(unknown, sorted(present))
        rows = reqs.all_requirements(sources=list(sources))
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
    from rag_wright.packs.compliance.capabilities.claim_extraction import aclaim_extraction
    from rag_wright.packs.compliance.capabilities.compliance_judgment import build_acompliance_judge_fn

    requirements = _load_requirements(store, sources)
    select_fn = build_select_fn(embedder, requirements, k=k) if embedder is not None else None
    # DEON-8 (issue 0012): the ad path gets the SAME deontic split as the generic path when an embedder is
    # available -- OBLIGATIONS judged ONCE over bounded, actor-gated evidence (not per-sentence), PROHIBITIONS
    # per-assertion (claim_type-routed via select_fn), PERMISSIONS excluded. Without an embedder (no semantic
    # narrowing) the prior per-assertion pairing is kept (back-compat).
    obligation_pairs_fn = build_obligation_pairs_fn(embedder) if embedder is not None else None
    # DEON-9: permission carve-outs linked as defenses to the O/F rules they modify (needs the embedder for ranking).
    defense_linker = build_defense_linker(embedder, requirements) if embedder is not None else None

    async def _default_claims_fn(text: str, source: str) -> list:
        return await aclaim_extraction(text, model=extract_model, source_doc=source)

    return build_compliance_check(
        claims_fn=claims_fn or _default_claims_fn,
        requirements_fn=lambda: requirements,
        judge_fn=build_acompliance_judge_fn(judge_model_id),
        select_fn=select_fn,
        obligation_pairs_fn=obligation_pairs_fn,
        defense_linker=defense_linker,
    )


_CLAIM_EXTRACT_ATTEMPTS = 3  # bounded retries for a per-chunk ad claim extraction (recover a transient docling blip)


async def _aextract_ad_claims(chunks: list[str], source_doc: str, extract_model: Any,
                              *, aclaim_fn: Any = None, max_concurrency: int = 4) -> list:
    """SEG-7b: the advertising claim extractor over the SAME semantic CHUNKS as the generic path -- extract typed
    `Claim`s PER CHUNK (concurrently, per the parallel-LLM rule), re-indexed globally for unique ids. The
    typed-Claim tail (claim_type / disclosures / routing) is UNTOUCHED. The structural locator (§/¶/bullet) is
    attached AFTER, by `attach_structural_locators` (SEG-4, verbatim match), same as the generic path. `aclaim_fn`
    is injected for hermetic tests."""
    from rag_wright.packs.compliance.capabilities.claim_extraction import aclaim_extraction
    from rag_wright.packs.compliance.schemas.compliance import Claim

    fn = aclaim_fn or aclaim_extraction
    sem = asyncio.Semaphore(max_concurrency)

    async def _one(chunk: str) -> list:
        text = (chunk or "").strip()
        if not text:
            return []
        async with sem:
            # PARTIAL-CAUSE-1 (compliance parity): the ad path extracts claims OUTSIDE the retry graph, so wrap
            # the per-chunk call in a bounded retry. docling-graph's `ExtractionFailed` is raised on ANY logged
            # error incl. TRANSIENT blips (empty content / gleaning / rate-limit / timeout), so retrying is what
            # recovers them (the same fix as the contract clause extractor). A PERSISTENT failure re-raises --
            # surfaced loudly, a chunk's claims are never silently dropped.
            last_exc: Optional[BaseException] = None
            for _attempt in range(_CLAIM_EXTRACT_ATTEMPTS):
                try:
                    return await fn(text, model=extract_model, source_doc=source_doc)
                except Exception as exc:  # noqa: BLE001 - transient docling/LLM error -> retry; persistent -> raise
                    last_exc = exc
            raise last_exc  # type: ignore[misc]  # persistent failure after retries (never None here)

    per_chunk = await asyncio.gather(*(_one(c) for c in chunks))
    claims = [c for group in per_chunk for c in group]
    for i, c in enumerate(claims):  # global re-index -> unique claim ids across chunks
        c.fact_id = Claim.make_id(source_doc, i, c.assertion_text)
    return claims


async def run_ad_compliance_check(
    subject_text: Optional[str] = None, source_doc: str = "", *, store: Any, extract_model: Any,
    judge_model_id: str, embedder: Any = None, k: int = 5, sources: Optional[list[str]] = None,
    name: Optional[str] = None, data: Optional[bytes] = None, discoverer: Any = None, aclaim_fn: Any = None,
    doc: Any = None,
) -> ComplianceReport:
    """The ADVERTISING compliance path (the subgraph behind the `check_ad_compliance` MCP tool; the generic
    counterpart is `run_generic_compliance_verdict`) -> a cited `ComplianceReport`. SEG-7b: accepts EITHER a pasted
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


def production_generic_compliance_check(store: Any, *, judge_model_id: str, embedder: Any, k: int = 8,
                                        sources: Optional[list[str]] = None, facts_fn: Any):
    """COMP-VERDICT-GENERIC: wire the DOMAIN-AGNOSTIC verdict path -- generic subject facts (no claim_type),
    SEMANTIC-ONLY requirement narrowing (`filter_applicability=False`, no domain applicability ontology needed),
    and the GENERIC judge (text-only). Gives a cited LLM verdict in ANY compliance domain; enrichment
    (COMP-APPLIC-1) only ADDS structured precision on top. `embedder` is required (semantic retrieval is the
    narrowing here). `sources` (issue 0007) optionally scopes the check to named policy `source`s (None = the
    whole store). `facts_fn` (required) is the async-adapted producer `(text, source) -> [CheckableFact]`; the
    caller (`run_subject_compliance_verdict`) precomputes the SEMANTIC facts and injects them here (SEG-7a)."""
    from rag_wright.packs.compliance.capabilities.compliance_judgment import build_ageneric_judge_fn

    requirements = _load_requirements(store, sources)
    # DEON-6: prohibitions narrow by the dimension-agnostic constraint router -- a claim's inferred scope (its
    # actor, DEON-5) matched against each requirement's effective scope. Recall-first: a claim with no scope, or a
    # requirement with a generic actor, is not excluded.
    constraint_scope_fn = lambda claim: getattr(claim, "scope", None) or []  # noqa: E731 (a claim's inferred scope)
    select_fn = build_select_fn(embedder, requirements, k=k, filter_applicability=False,
                                constraint_scope_fn=constraint_scope_fn)

    async def _claims_fn(text: str, source: str) -> list:
        return facts_fn(text, source)  # precomputed semantic facts, adapted to the async claims seam

    return build_compliance_check(
        claims_fn=_claims_fn,
        requirements_fn=lambda: requirements,
        judge_fn=build_ageneric_judge_fn(judge_model_id),
        select_fn=select_fn,
        obligation_pairs_fn=build_obligation_pairs_fn(embedder),  # DEON-2: bounded per-obligation evidence
        defense_linker=build_defense_linker(embedder, requirements),  # DEON-9: permission carve-outs as defenses
        constraint_scope_fn=constraint_scope_fn,  # ADR-0068: report the actor-gated prohibition pairs (issue 0013)
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
    from rag_wright.packs.compliance.capabilities.assertion_extraction import aassertion_extraction
    from rag_wright.packs.contracts.capabilities.dg_extraction import aextract_parties

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
    """SEG-7a: the SUBJECT fact producer -- the composed semantic pipeline that supersedes the old regex
    sections producer. Chunk the parsed doc (SEG-2/SEG-5, the production ingestion discoverer) -> extract the
    checkable assertions VERBATIM per chunk (SEG-3) -> attach each to its docling element for the structural
    locator (SEG-4). Returns `CheckableFact`s cited "doc § {section} ¶{n}: {verbatim}" (or just the span for a
    flat doc). `model` (assertion extractor) defaults to the production extraction model; `discoverer`/`aextract_fn`
    are injected for hermetic tests (no model/parse)."""
    m = model
    if m is None and aextract_fn is None:
        from rag_wright.packs.contracts.capabilities.dg_extraction import default_extraction_model

        m = default_extraction_model("subject-assert")
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
    hermetic tests. (SEG-7a replaced the old regex sections/granularity dial with the semantic producer.)"""
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


async def ainvoke(resources, inputs: dict):
    """EP-REF-1c (ADR-0118): the capability invoke factory (impl_ref target) for the GENERIC compliance check --
    NOT the FTC-tuned `run_ad_compliance_check` (the product owns that variant + its guardrails). The store, the
    judge model (STRUCTURED_REASONING), and the embedder come from the workspace handle; the subject + scope from
    `inputs`. Two subject shapes: `{subject_text, source_doc}` (text) or `{doc_name, data}` (raw document bytes).
    `inputs` may also carry `k` (retrieval depth) and `sources` (scope to named policies)."""
    from rag_wright.models.profiles import ModelRole

    judge = resources.model_id(ModelRole.STRUCTURED_REASONING)
    k = inputs.get("k", 8)
    sources = inputs.get("sources")
    if inputs.get("data") is not None:  # a subject DOCUMENT (bytes)
        return await run_compliance_document_verdict(
            inputs["doc_name"], inputs["data"], store=resources._store, judge_model_id=judge,
            embedder=resources._embedder, k=k, sources=sources)
    return await run_generic_compliance_verdict(  # a subject TEXT
        inputs["subject_text"], inputs["source_doc"], store=resources._store, judge_model_id=judge,
        embedder=resources._embedder, k=k, sources=sources)
