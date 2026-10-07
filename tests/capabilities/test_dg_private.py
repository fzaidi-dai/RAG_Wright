"""GP-1B.5a: PRIVATE-entity alignment. `build_private_map` maps a verified-PRIVATE entity's surface forms to
its golden `PRIVATE:<entity_key>` id (eval.multihop._identity), and `resolve_extracted(private_map=...)`
assigns that id to extracted private parties the CIK registry can't resolve -- so private anchors/answers
stop scoring 0 in the relational eval. Hermetic."""

from __future__ import annotations

from rag_wright.packs.contracts.capabilities.dg_extraction import (
    ContractParties,
    Party,
    build_private_map,
    build_verified_registry,
    resolve_extracted,
)
from rag_wright.packs.contracts.corpus.edgar import normalize_cik, normalize_name


def _vset():
    return {"entities": [
        {"entity_key": "premier nutrition", "representative": "Premier Nutrition Company, LLC",
         "resolution": "PRIVATE", "variants": ["PREMIER NUTRITION CORPORATION"]},
        {"entity_key": "fonterra", "representative": "Fonterra USA", "resolution": "0000000005",
         "variants": ["Fonterra"]},
        {"entity_key": "skip co", "representative": "Skip Co", "resolution": "SKIP", "variants": []},
    ]}


def test_private_map_covers_variants_not_ciks_or_skip():
    pm = build_private_map(_vset())
    assert pm[normalize_name("Premier Nutrition Company, LLC")] == "PRIVATE:premier nutrition"
    assert pm[normalize_name("PREMIER NUTRITION CORPORATION")] == "PRIVATE:premier nutrition"  # a variant
    assert normalize_name("Fonterra USA") not in pm  # a CIK entity is not private
    assert normalize_name("Skip Co") not in pm  # SKIP is excluded from the golden set


def test_extracted_private_party_gets_private_id():
    pm = build_private_map(_vset())
    reg = build_verified_registry(_vset())  # only Fonterra (CIK)
    cp = ContractParties(title="c", parties=[Party(name="Fonterra USA"),
                                             Party(name="Premier Nutrition Company, LLC")])

    res = resolve_extracted([("c", cp)], registry=reg, private_map=pm)
    ids = {e.entity_id for e in res.entities}
    assert normalize_cik("5").value in ids          # Fonterra -> CIK (as before)
    assert "PRIVATE:premier nutrition" in ids        # private -> PRIVATE:<key> (was None/unlinked)

    rel = res.relationships[0]  # the CONTRACTS_WITH edge now connects CIK <-> PRIVATE:<key>
    assert {rel.source_id, rel.target_id} == {normalize_cik("5").value, "PRIVATE:premier nutrition"}


def test_no_private_map_is_unchanged():
    reg = build_verified_registry(_vset())
    cp = ContractParties(title="c", parties=[Party(name="Premier Nutrition Company, LLC")])
    res = resolve_extracted([("c", cp)], registry=reg)  # no private_map
    assert res.entities[0].entity_id is None  # unlinked, as before
