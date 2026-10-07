"""PREP-1.5 + G5: the capability-registration surface is re-exported from rag_wright.api
so the public story is uniformly "everything is rag_wright.api", while the original
rag_wright.capabilities.* paths keep working (same objects, no fork). G5 adds the pack helpers
(`load_pack`, `engine_capabilities`, `CapabilityManifest`, `register_canonical_slugs`,
`canonical_capability_slugs`), so a product authors and loads its own pack without engine internals.
"""
from __future__ import annotations

import rag_wright.capabilities.manifests as manifests
import rag_wright.capabilities.registry as registry
from rag_wright import api

REEXPORTED = ("register_capability", "load_reference_pack", "reference_pack", "load_pack", "engine_capabilities",
              "CapabilityManifest")
REEXPORTED_FROM_REGISTRY = ("register_canonical_slugs", "canonical_capability_slugs")


def test_reexported_from_api_and_in_all():
    for name in REEXPORTED + REEXPORTED_FROM_REGISTRY:
        assert hasattr(api, name), f"{name} not re-exported from rag_wright.api"
        assert name in api.__all__, f"{name} missing from rag_wright.api.__all__"


def test_reexport_is_the_same_object_as_manifests():
    # Not a fork: the api symbol IS the manifests function.
    for name in REEXPORTED:
        assert getattr(api, name) is getattr(manifests, name)
    for name in REEXPORTED_FROM_REGISTRY:
        assert getattr(api, name) is getattr(registry, name)


def test_the_compliance_id_helper_is_not_engine_api():
    # the compliance pack's requirement-id parser is pack code (ComplianceStore.policy_of_requirement); the generic
    # first-segment parser is document_of
    assert not hasattr(api, "id_source") and "id_source" not in api.__all__


def test_old_import_path_still_works():
    from rag_wright.capabilities.manifests import (  # noqa: F401
        load_reference_pack,
        reference_pack,
        register_capability,
    )
