"""ADR-0066 P3b gate: compliance_bridge.ttl is the authoritative source of the compliance vocabularies.

The Python enums (DeonticType / ClaimType / Severity / RuleScope / Verdict) are drift-locked to the ttl -- this
fails the build if an enum diverges from `compliance_bridge.ttl`, so the vocab lives in the ontology (edit the
ttl, not the enum). Mirrors the P1a value-enum drift-lock; the vocab is small and static, so the drift-check IS
the enforcement.
"""

from __future__ import annotations

from rag_wright.contracts.compliance import ClaimType, DeonticType, RuleScope, Severity, Verdict
from rag_wright.ontology.loader import (
    deontic_type_of,
    load_actor_synonyms,
    load_compliance_vocab,
    load_deontic_cue_map,
    load_deontic_cues,
)

_VOCAB = load_compliance_vocab()


def test_every_python_enum_matches_the_ttl() -> None:
    for name, enum_cls in {"DeonticType": DeonticType, "ClaimType": ClaimType, "Severity": Severity,
                           "RuleScope": RuleScope, "Verdict": Verdict}.items():
        assert name in _VOCAB, f"{name} is not declared in compliance_bridge.ttl"
        assert {m.value for m in enum_cls} == _VOCAB[name], (
            f"{name} drifted from compliance_bridge.ttl -- edit the ttl, not the enum")


def test_the_ttl_declares_exactly_the_five_closed_vocabs() -> None:
    assert set(_VOCAB) == {"DeonticType", "ClaimType", "Severity", "RuleScope", "Verdict"}


def test_deontic_cue_map_is_authoritative_in_the_ttl() -> None:
    # CIC-0: the cue -> deontic TYPE map is loaded from compliance_bridge.ttl (`cmp:cue` per cmp:DeonticType),
    # so the ingest-time deontic_type cue-rule is ttl-driven, not a Python literal.
    cue_map = load_deontic_cue_map()
    assert cue_map["must"] == "obligation"
    assert cue_map["must not"] == "prohibition"
    assert cue_map["shall not"] == "prohibition"
    assert cue_map["may"] == "permission"
    assert set(cue_map.values()) == {"obligation", "prohibition", "permission"}  # the three closed deontic values
    assert set(cue_map) == set(load_deontic_cues())  # same cue set the operative gate uses


def test_deontic_type_of_prefers_the_longest_cue() -> None:
    # CIC-0: the deterministic deontic-type derivation -- longest cue first so 'must not' beats 'must'.
    assert deontic_type_of("The advertiser must not misrepresent the results.") == "prohibition"
    assert deontic_type_of("The advertiser must disclose the connection.") == "obligation"
    assert deontic_type_of("The endorser may decline the request.") == "permission"
    assert deontic_type_of("This section defines the terms used herein.") is None  # no cue -> non-operative
    assert deontic_type_of("") is None


def test_actor_synonyms_are_authoritative_in_the_ttl() -> None:
    # ADR-0066 P4a: the query-side actor-role synonyms are loaded from compliance_bridge.ttl (skos:altLabel),
    # not a Python literal -- a ttl edit that breaks a mapping is caught here.
    syn = load_actor_synonyms()
    assert syn["manufacturer"] == "advertiser"
    assert syn["influencer"] == "endorser"
    assert syn["physician"] == "expert"
    assert syn["customer"] == "consumer"
    assert syn["vendor"] == "seller"
    assert len(syn) == 20  # the full role pack (5 roles x their synonyms)


def test_ftc_domain_pack_section_overrides() -> None:
    # ADR-0066 P4b: the FTC section overrides (DEON-1 rule scope + DEON-8 applicable claim types) load from the
    # domain pack (packs/ftc_16cfr255.ttl), not Python literals.
    from rag_wright.ontology.loader import load_section_overrides

    rule_scope, claim_types = load_section_overrides()
    assert rule_scope == {"255.4": "context", "255.5": "context"}
    assert claim_types["255.0"] == frozenset()                              # definitions -> applies to nothing
    assert claim_types["255.5"] == frozenset(m.value for m in ClaimType)    # every operative section -> all types
    assert set(claim_types) == {"255.0", "255.1", "255.2", "255.3", "255.4", "255.5", "255.6"}
