"""GP-1B.2: the adapter bridging docling-graph extractions into our resolution pipeline. Extracted
ContractParties -> parties_to_extraction (CONTRACTS_WITH between them) -> disambiguate -> resolve to
EDGAR CIK -> to_graph, using a registry built from the verified set (each CIK entity's `variants` are
aliases, so an extracted surface form matching a verified variant resolves; PRIVATE/unknown -> unlinked).
Hermetic (fixture extracted models + fixture verified set; no docling-graph pipeline, no LLM, no store)."""

from __future__ import annotations

from rag_wright.packs.contracts.capabilities.dg_extraction import (
    ContractParties,
    Party,
    build_verified_registry,
    resolve_extracted,
)
from rag_wright.capabilities.graph_storage import to_graph
from rag_wright.packs.contracts.corpus.edgar import normalize_cik


def _vset():
    return {"entities": [
        {"entity_key": "acme", "representative": "Acme Corporation", "resolution": "0000000001",
         "variants": ["Acme Corp", "ACME CORPORATION"]},
        {"entity_key": "beta", "representative": "Beta Inc", "resolution": "0000000002",
         "variants": ["Beta Inc", "BETA, INC."]},
        {"entity_key": "private co", "representative": "Private LLC", "resolution": "PRIVATE",
         "variants": ["Private LLC"]},
    ]}


def test_registry_resolves_verified_variants():
    reg = build_verified_registry(_vset())
    assert reg.resolve("ACME CORPORATION").value == normalize_cik("1").value  # a variant surface form
    assert reg.resolve("Acme Corp").value == normalize_cik("1").value
    assert reg.resolve("Beta Inc").value == normalize_cik("2").value
    assert reg.resolve("Private LLC") is None  # PRIVATE is not in the closed CIK registry


def test_extracted_parties_resolve_to_a_cik_graph():
    reg = build_verified_registry(_vset())
    cik1, cik2 = normalize_cik("1").value, normalize_cik("2").value
    cp = ContractParties(title="Acme-Beta Agreement",
                         parties=[Party(name="ACME CORPORATION"), Party(name="Beta Inc")])

    resolution = resolve_extracted([("acme_beta_contract", cp)], registry=reg)
    assert {e.entity_id for e in resolution.entities} == {cik1, cik2}
    assert len(resolution.relationships) == 1
    rr = resolution.relationships[0]
    assert {rr.source_id, rr.target_id} == {cik1, cik2}

    nodes, edges = to_graph(resolution)
    assert {n.node_key for n in nodes} == {cik1, cik2}
    assert len(edges) == 1 and edges[0].relationship_type == "Contracts With"


def test_unresolved_party_is_unlinked():
    # a party the extractor found but the registry doesn't know -> None (an extraction/resolution miss)
    reg = build_verified_registry(_vset())
    cp = ContractParties(title="X", parties=[Party(name="ACME CORPORATION"), Party(name="Unknown Startup Inc")])
    resolution = resolve_extracted([("c", cp)], registry=reg)
    ids = {e.entity_id for e in resolution.entities}
    assert normalize_cik("1").value in ids  # Acme resolved
    assert None in ids  # Unknown -> unlinked
