"""PREP-1.5: the capability-registration surface is re-exported from rag_wright.api
so the public story is uniformly "everything is rag_wright.api", while the original
rag_wright.capabilities.manifests path keeps working (same objects, no fork).
"""
from __future__ import annotations

import rag_wright.capabilities.manifests as manifests
from rag_wright import api

REEXPORTED = ("register_capability", "load_reference_pack", "reference_pack")


def test_reexported_from_api_and_in_all():
    for name in REEXPORTED:
        assert hasattr(api, name), f"{name} not re-exported from rag_wright.api"
        assert name in api.__all__, f"{name} missing from rag_wright.api.__all__"


def test_reexport_is_the_same_object_as_manifests():
    # Not a fork: the api symbol IS the manifests function.
    for name in REEXPORTED:
        assert getattr(api, name) is getattr(manifests, name)


def test_old_import_path_still_works():
    from rag_wright.capabilities.manifests import (  # noqa: F401
        load_reference_pack,
        reference_pack,
        register_capability,
    )
