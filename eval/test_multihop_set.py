"""T10: the EDGAR-derived relational + multi-hop golden set (SPEC 12, 8).

The core rules are proven on a small inline fixture (deterministic, no dependency on the gitignored
verified set): variant spellings collapse by resolved identity, SKIP entities are excluded,
verified-PRIVATE entities are first-class (as co-parties and as hubs), distinct subsidiaries keep
separate answer keys, and a 2-hop chain is three distinct verified entities joined by two real
edges. A final coverage test runs against the real `data/edgar/verification_set.json` when present.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.harness import Archetype, evaluate
from eval.multihop import build_relational

# --- Inline fixture: a hub `acme` (CIK) with mixed co-parties, a distinct PRIVATE sub `acme sub`,
# a variant pair (`gamma` / `gamma alt`, same CIK), a SKIP entity, and a real 2-hop tail `epsilon`.
_FIXTURE = {
    "entities": [
        {
            "entity_key": "acme",
            "representative": "ACME CORPORATION",
            "resolution": "0000000001",
            "num_contracts": 3,
            "is_top_hub": True,
            "coparty_keys": ["beta", "gamma", "gamma alt", "delta"],
        },
        # beta: PRIVATE co-party of acme, and the bridge to the 2-hop tail epsilon.
        {
            "entity_key": "beta",
            "representative": "Beta Holdings LLC",
            "resolution": "PRIVATE",
            "num_contracts": 1,
            "coparty_keys": ["acme", "epsilon"],
        },
        # gamma / gamma alt: two surface forms of one filer (same CIK) -> collapse to one answer.
        {
            "entity_key": "gamma",
            "representative": "GAMMA INC",
            "resolution": "0000000002",
            "num_contracts": 1,
            "coparty_keys": ["acme"],
        },
        {
            "entity_key": "gamma alt",
            "representative": "Gamma Incorporated",
            "resolution": "0000000002",
            "num_contracts": 1,
            "coparty_keys": ["acme"],
        },
        # delta: SKIP -> excluded from the graph entirely.
        {
            "entity_key": "delta",
            "representative": "Delta ???",
            "resolution": "SKIP",
            "num_contracts": 1,
            "coparty_keys": ["acme"],
        },
        # epsilon: PRIVATE, two hops from acme via beta (not a direct co-party of acme).
        {
            "entity_key": "epsilon",
            "representative": "Epsilon Partners",
            "resolution": "PRIVATE",
            "num_contracts": 1,
            "coparty_keys": ["beta"],
        },
        # acme sub: a distinct PRIVATE hub; its co-party zeta must never enter acme's answer key.
        {
            "entity_key": "acme sub",
            "representative": "ACME Latin America, Inc.",
            "resolution": "PRIVATE",
            "num_contracts": 3,
            "is_top_hub": True,
            "coparty_keys": ["zeta"],
        },
        {
            "entity_key": "zeta",
            "representative": "ZETA CO",
            "resolution": "0000000003",
            "num_contracts": 1,
            "coparty_keys": ["acme sub"],
        },
    ],
}


def _by_qid(vset=_FIXTURE):
    return {q.qid: q for q in build_relational(vset)}


def test_one_hop_answer_dedupes_variants_excludes_skip_and_two_hop():
    q = _by_qid()["rel:1hop:acme"]
    assert q.hop_count == 1
    ids = {a.identity for a in q.answer_entities}
    # beta (PRIVATE) + gamma/gamma alt collapsed to the single CIK; delta (SKIP) and epsilon (2-hop) absent.
    assert ids == {"PRIVATE:beta", "0000000002"}
    assert "PRIVATE:epsilon" not in ids  # 2-hop, not a direct co-party
    # the variant pair collapses to exactly one answer entity, not two.
    assert sum(1 for a in q.answer_entities if a.identity == "0000000002") == 1


def test_private_entities_are_first_class():
    qs = _by_qid()
    # a PRIVATE co-party appears in a CIK hub's answer...
    beta = next(a for a in qs["rel:1hop:acme"].answer_entities if a.identity == "PRIVATE:beta")
    assert beta.private and beta.entity_id is None
    # ...and a PRIVATE entity is itself a first-class hub with its own answer key.
    assert qs["rel:1hop:acme sub"].anchor.private
    assert {a.identity for a in qs["rel:1hop:acme sub"].answer_entities} == {"0000000003"}


def test_distinct_subsidiaries_keep_separate_answer_keys():
    qs = _by_qid()
    acme_ids = {a.identity for a in qs["rel:1hop:acme"].answer_entities}
    # acme sub's counterparty (zeta) must not be attributed to acme.
    assert "0000000003" not in acme_ids


def test_two_hop_chain_is_real_and_distinct():
    q = _by_qid()["rel:2hop:acme"]
    assert q.hop_count == 2
    assert {a.identity for a in q.answer_entities} == {"PRIVATE:epsilon"}
    # exactly one witnessing chain: three distinct entities, two edges (acme-beta, beta-epsilon).
    (chain,) = q.entity_paths
    assert chain == ["0000000001", "PRIVATE:beta", "PRIVATE:epsilon"]
    assert len(set(chain)) == 3


def test_to_golden_registers_relational_archetype():
    goldens = [q.to_golden() for q in build_relational(_FIXTURE)]
    assert goldens and all(g.archetype is Archetype.RELATIONAL for g in goldens)
    assert all(g.category is None for g in goldens)
    one_hop = next(g for g in goldens if g.qid == "rel:1hop:acme")
    assert one_hop.relevant_ids == {"PRIVATE:beta", "0000000002"}
    # the relational split flows through the existing per-archetype/leg harness.
    report = evaluate(goldens, lambda q, leg: [], k=5)
    assert "relational/text" in report.per_archetype_leg
    assert "relational/graph" in report.per_archetype_leg
    assert report.counts["relational"] == len(goldens)


_VERIFIED = Path("data/edgar/verification_set.json")


@pytest.mark.skipif(not _VERIFIED.exists(), reason="verified set is gitignored / local-only")
def test_real_verified_set_coverage():
    vset = json.loads(_VERIFIED.read_text())
    qs = build_relational(vset)
    one_hop = [q for q in qs if q.hop_count == 1]
    two_hop = [q for q in qs if q.hop_count == 2]
    # 16 hubs -> a 1-hop question each; multi-hop is modest by the corpus's star shape (honest property).
    assert len(one_hop) == 16
    assert len(two_hop) >= 3
    # both classes of verified answer entity are present (axis is verified-vs-unverified).
    ids = {a.identity for q in qs for a in q.answer_entities}
    assert any(i.startswith("PRIVATE:") for i in ids)  # verified-private, first-class
    assert any(not i.startswith("PRIVATE:") for i in ids)  # CIK filer
    # pc quote's variant co-parties (a b watley / ab wately) collapse to one CIK answer.
    pc = next(q for q in one_hop if q.anchor.entity_key == "pc quote")
    assert {a.identity for a in pc.answer_entities} == {"0001035632"}
