"""ADR-0066: the runtime loader for the COMPLIANCE ontology (`compliance_bridge.ttl`) and the regulation packs built
on it (`packs/ftc_16cfr255.ttl`): the closed compliance vocabularies, the deontic cues, actor synonyms and role
criteria, the claim-type criteria, the operative rubric, role domains and per-section overrides. Split out of the
contracts pack's loader (ING-8c) so the contracts pack never reads compliance knowledge.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from rdflib import Graph
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, SKOS

_COMPLIANCE_TTL_PATH = Path(__file__).with_name("compliance_bridge.ttl")


def load_compliance_vocab(path: Path | str = _COMPLIANCE_TTL_PATH) -> dict[str, set[str]]:
    """ADR-0066 P3b: the closed vocabularies declared in compliance_bridge.ttl, keyed by class local-name
    (`DeonticType`, `ClaimType`, `Severity`, `RuleScope`, `Verdict`) -> the set of `owl:oneOf` value local-names.
    The Python enums in schemas/compliance.py are drift-locked to this (the ttl is the source of truth)."""
    g = Graph()
    g.parse(str(path), format="turtle")
    out: dict[str, set[str]] = {}
    for cls in g.subjects(OWL.oneOf, None):
        local = str(cls).rsplit("#", 1)[-1]
        members = {str(m).rsplit("#", 1)[-1] for m in Collection(g, g.value(cls, OWL.oneOf))}
        out[local] = members
    return out


_CMP = "https://ragwright.local/ontology/compliance-bridge#"


@lru_cache(maxsize=4)
def load_deontic_cues(path: str = str(_COMPLIANCE_TTL_PATH)) -> frozenset[str]:
    """ADR-0066 P3c (Gap 1): the deontic CUES declared in compliance_bridge.ttl (`cmp:cue` on each deontic type) --
    the lexical markers of operative normative force. The requirement-ingestion validity gate uses them: a section
    with none of these cues is non-operative and is skipped. Cached per path."""
    from rdflib import URIRef

    g = Graph()
    g.parse(path, format="turtle")
    return frozenset(str(v).strip().lower() for v in g.objects(None, URIRef(_CMP + "cue")) if str(v).strip())


@lru_cache(maxsize=4)
def load_deontic_cue_map(path: str = str(_COMPLIANCE_TTL_PATH)) -> dict[str, str]:
    """CIC-0 (ADR-0066): the deontic CUE -> deontic TYPE map authored in compliance_bridge.ttl (`cmp:cue` on each
    `cmp:DeonticType`), e.g. {'must': 'obligation', 'must not': 'prohibition', 'may': 'permission'}. Keys are
    lowercased cue phrases; values are the DeonticType local-names. This is the ttl-driven source for the ingest
    cue-RULE (`deontic_type_of`): a rule's deontic_type is derived deterministically from its text's cue instead of
    from an LLM field. Shares its cue set with `load_deontic_cues` (the operative gate). Cached per path."""
    from rdflib import URIRef

    g = Graph()
    g.parse(path, format="turtle")
    cue = URIRef(_CMP + "cue")
    out: dict[str, str] = {}
    for subj, obj in g.subject_objects(cue):
        value = str(obj).strip().lower()
        if value:
            out[value] = str(subj).rsplit("#", 1)[-1]
    return out


@lru_cache(maxsize=1)
def _deontic_cue_type_pattern() -> tuple[re.Pattern, dict[str, str]]:
    """The compiled cue regex (longest cue first, so 'must not' is tried before 'must') + the cue->type map."""
    cue_map = load_deontic_cue_map()
    cues = sorted(cue_map, key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(re.escape(c) for c in cues) + r")\b", re.IGNORECASE), cue_map


def deontic_type_of(text: str) -> str | None:
    """CIC-0 (ADR-0066): the deontic TYPE of a rule span, from its FIRST deontic cue (ttl `cmp:cue`), longest cue
    first so 'must not' / 'may not' (prohibition) win over 'must' / 'may'. Returns the DeonticType local-name
    (obligation / prohibition / permission) or None when the text carries no cue (non-operative). The deterministic
    cue-rule that replaces the LLM's deontic_type field at ingest; a non-None result also means the span is
    operative (same cue basis as `is_operative`)."""
    if not text:
        return None
    pattern, cue_map = _deontic_cue_type_pattern()
    m = pattern.search(text)
    return cue_map[m.group(0).lower()] if m else None


@lru_cache(maxsize=4)
def load_actor_synonyms(path: str = str(_COMPLIANCE_TTL_PATH)) -> dict[str, str]:
    """ADR-0066 P4a: the actor-role synonyms from compliance_bridge.ttl -- `{synonym -> canonical role}` built from
    each `cmp:ActorRole`'s `skos:altLabel` (synonym) -> `skos:prefLabel` (canonical). The query-side actor gate
    (`canonical_actor`) normalizes with this. Cached per path."""
    from rdflib import URIRef

    g = Graph()
    g.parse(path, format="turtle")
    out: dict[str, str] = {}
    for role in g.subjects(RDF.type, URIRef(_CMP + "ActorRole")):
        pref = str(g.value(role, SKOS.prefLabel) or "").strip().lower()
        if not pref:
            continue
        for alt in g.objects(role, SKOS.altLabel):
            out[str(alt).strip().lower()] = pref
    return out


@lru_cache(maxsize=4)
def load_claim_type_criteria(path: str = str(_COMPLIANCE_TTL_PATH)) -> dict[str, str]:
    """ADR-0119: `{ClaimType local-name -> cmp:decisionCriterion}` -- the one-line criterion each claim type uses
    as its typed-decision (noul) question. Authored in compliance_bridge.ttl, not in capability code (ADR-0066)."""
    from rdflib import URIRef

    g = Graph()
    g.parse(path, format="turtle")
    crit = URIRef(_CMP + "decisionCriterion")
    out: dict[str, str] = {}
    for m in g.subjects(RDF.type, URIRef(_CMP + "ClaimType")):
        c = g.value(m, crit)
        if c is not None:
            out[str(m).rsplit("#", 1)[-1]] = str(c)
    return out


@lru_cache(maxsize=4)
def load_actor_role_criteria(path: str = str(_COMPLIANCE_TTL_PATH)) -> dict[str, str]:
    """ADR-0119: `{ActorRole prefLabel -> cmp:decisionCriterion}` -- the actor `choice` options + their criteria,
    from the ttl (includes the workplace-domain `employer`). Authored in the ontology, not capability code."""
    from rdflib import URIRef

    g = Graph()
    g.parse(path, format="turtle")
    crit = URIRef(_CMP + "decisionCriterion")
    out: dict[str, str] = {}
    for role in g.subjects(RDF.type, URIRef(_CMP + "ActorRole")):
        c = g.value(role, crit)
        label = str(g.value(role, SKOS.prefLabel) or str(role).rsplit("#", 1)[-1]).strip().lower()
        if c is not None and label:
            out[label] = str(c)
    return out


@lru_cache(maxsize=4)
def load_operative_rubric(path: str = str(_COMPLIANCE_TTL_PATH)) -> dict[str, str]:
    """ADR-0119: the operative-rule binary gate's `{instructions, true, false}` from `cmp:operativeRuleQuestion`
    in compliance_bridge.ttl -- the decision knowledge the `jev_decision` gate asks, authored in the ontology."""
    from rdflib import URIRef

    g = Graph()
    g.parse(path, format="turtle")
    q = URIRef(_CMP + "operativeRuleQuestion")

    def _v(prop: str) -> str:
        v = g.value(q, URIRef(_CMP + prop))
        return str(v) if v is not None else ""

    return {"instructions": _v("questionInstructions"), "true": _v("criterionTrue"), "false": _v("criterionFalse")}


@lru_cache(maxsize=4)
def load_role_domains(path: str = str(_COMPLIANCE_TTL_PATH)) -> dict[str, str]:
    """ADR-0068 (engine issue 0013): the DISJOINTNESS knowledge for the actor gate -- `{canonical role -> domain}`
    built from each `cmp:ActorRole`'s `skos:prefLabel` (canonical) -> `cmp:roleDomain`. Two roles are disjoint iff
    both appear here with DIFFERENT domains; the recall-first gate excludes only disjoint pairs (a role absent
    here, or two roles in the same domain, are compatible). A customer domain declares its roles' roleDomain in
    its own pack to get cross-domain narrowing. Cached per path."""
    from rdflib import URIRef

    g = Graph()
    g.parse(path, format="turtle")
    out: dict[str, str] = {}
    for role in g.subjects(RDF.type, URIRef(_CMP + "ActorRole")):
        pref = str(g.value(role, SKOS.prefLabel) or "").strip().lower()
        domain = str(g.value(role, URIRef(_CMP + "roleDomain")) or "").strip().lower()
        if pref and domain:
            out[pref] = domain
    return out


_FTC_PACK_PATH = Path(__file__).parent / "packs" / "ftc_16cfr255.ttl"


@lru_cache(maxsize=4)
def load_section_overrides(path: str = str(_FTC_PACK_PATH)) -> tuple[dict[str, str], dict[str, frozenset[str]]]:
    """ADR-0066 P4b: a domain pack's per-section overrides from `cmp:SectionOverride` instances. Returns
    `(rule_scope, claim_types)`: `{section -> 'content'|'context'}` (only sections that pin a scope) and
    `{section -> {claim type value}}` (all claim types when `cmp:appliesToAllClaimTypes` is true, else the explicit
    `cmp:appliesToClaimType` set -- empty for a definitions section). Cached per path."""
    from rdflib import URIRef

    g = Graph()
    g.parse(path, format="turtle")
    all_claim_types = frozenset(load_compliance_vocab().get("ClaimType", set()))
    rule_scope: dict[str, str] = {}
    claim_types: dict[str, frozenset[str]] = {}
    for so in g.subjects(RDF.type, URIRef(_CMP + "SectionOverride")):
        section = str(g.value(so, URIRef(_CMP + "section")) or "").strip()
        if not section:
            continue
        all_flag = g.value(so, URIRef(_CMP + "appliesToAllClaimTypes"))
        if all_flag is not None and bool(all_flag.toPython()):
            claim_types[section] = all_claim_types
        else:
            claim_types[section] = frozenset(
                str(ct).rsplit("#", 1)[-1] for ct in g.objects(so, URIRef(_CMP + "appliesToClaimType")))
        rs = g.value(so, URIRef(_CMP + "overrideRuleScope"))
        if rs is not None:
            rule_scope[section] = str(rs).rsplit("#", 1)[-1]
    return rule_scope, claim_types
