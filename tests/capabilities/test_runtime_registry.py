"""EP-CORE-3 (ADR-0118): the ARD catalog ships EMPTY and is developer-populated at runtime. A product registers its
own capabilities (manifest + impl_ref) and they become invocable with zero engine edits; the engine's reference pack
is opt-in via `load_reference_pack()`."""
from __future__ import annotations

from rag_wright.capabilities import manifests as m
from rag_wright.capabilities.invoke import capability_impl
from rag_wright.capabilities.manifests import CapabilityManifest, reference_pack


def test_a_registered_capability_becomes_invocable_over_an_empty_catalog(monkeypatch):
    # simulate a FRESH install: an empty catalog (the conftest-loaded reference pack is swapped out for this test).
    monkeypatch.setattr(m, "MANIFEST_SPECS", {})
    from rag_wright.api.invoke import capability_index

    assert capability_index() == {}  # ships empty

    man = CapabilityManifest(
        slug="demo_cap", kind="model", display_name="Demo", description="a product capability",
        representative_queries=("do the demo thing", "run demo"),
        impl_ref="rag_wright.capabilities.invoke:capability_impl")  # any importable callable (the resolver itself)
    m.register_capability(man)

    assert "demo_cap" in capability_index()                 # the developer's cap is now in the catalog
    assert capability_impl("demo_cap") is capability_impl   # and resolves via its impl_ref -- zero engine edits


def test_reference_pack_is_opt_in_and_contains_the_contract_worked_example():
    # the conftest loaded the reference pack for the engine's own suite; a downstream product would not.
    assert "contract_ingestion_pipeline" in m.MANIFEST_SPECS
    assert {s.slug for s in reference_pack()} <= set(m.MANIFEST_SPECS)   # every reference cap registered
    assert len(reference_pack()) == 36                                   # EP-CORE-1b-i: -3, -iii: -3; +1 jev_decision (ADR-0119)
