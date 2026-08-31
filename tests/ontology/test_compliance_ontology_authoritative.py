"""ADR-0066 P3b gate: compliance_bridge.ttl is the authoritative source of the compliance vocabularies.

The Python enums (DeonticType / ClaimType / Severity / RuleScope / Verdict) are drift-locked to the ttl -- this
fails the build if an enum diverges from `compliance_bridge.ttl`, so the vocab lives in the ontology (edit the
ttl, not the enum). Mirrors the P1a value-enum drift-lock; the vocab is small and static, so the drift-check IS
the enforcement.
"""

from __future__ import annotations

from rag_wright.contracts.compliance import ClaimType, DeonticType, RuleScope, Severity, Verdict
from rag_wright.ontology.loader import load_actor_synonyms, load_compliance_vocab

_VOCAB = load_compliance_vocab()


def test_every_python_enum_matches_the_ttl() -> None:
    for name, enum_cls in {"DeonticType": DeonticType, "ClaimType": ClaimType, "Severity": Severity,
                           "RuleScope": RuleScope, "Verdict": Verdict}.items():
        assert name in _VOCAB, f"{name} is not declared in compliance_bridge.ttl"
        assert {m.value for m in enum_cls} == _VOCAB[name], (
            f"{name} drifted from compliance_bridge.ttl -- edit the ttl, not the enum")


def test_the_ttl_declares_exactly_the_five_closed_vocabs() -> None:
    assert set(_VOCAB) == {"DeonticType", "ClaimType", "Severity", "RuleScope", "Verdict"}


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
