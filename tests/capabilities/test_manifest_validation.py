"""ING-5: a manifest mistake fails where the manifest is written, and a publishing error says how to fix it."""
from __future__ import annotations

import pytest

from rag_wright.api import CapabilityManifest, register_capability
from rag_wright.capabilities.ard import ResponseBounds
from rag_wright.capabilities.manifests import MANIFEST_SPECS, author


def _manifest(**kw):
    base = dict(slug="my_skill", kind="agent_skill", display_name="My skill", description="d",
                representative_queries=("q one", "q two"))
    base.update(kw)
    return CapabilityManifest(**base)


def test_response_bounds_on_a_non_callable_kind_is_rejected_at_construction():
    with pytest.raises(ValueError, match="response_bounds"):
        _manifest(response_bounds=ResponseBounds())  # an agent_skill is loaded, never called
    assert _manifest().response_bounds is None


def test_a_non_canonical_slug_error_names_the_fix():
    register_capability(_manifest(slug="zz_product_only_skill"))
    try:
        with pytest.raises(ValueError, match="register_canonical_slugs"):
            author("zz_product_only_skill")
    finally:
        MANIFEST_SPECS.pop("zz_product_only_skill", None)
