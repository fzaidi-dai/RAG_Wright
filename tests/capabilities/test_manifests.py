"""ARD manifest authoring per capability (cross-cutting: every T15-T29 capability authors one).

Each capability's ARD manifest is authored from a committed spec in `capabilities/manifests.py` and
written to the shared registry root as `<slug>.json` under `urn:air:dreamai.io:rag_wright:<slug>`.
These tests validate authoring + on-disk shape locally; a live `RegistryStore` load happens later on
the GraphWright side, not here.
"""

from __future__ import annotations

import json

from rag_wright.capabilities.ard import RegistryEntry
from rag_wright.capabilities.manifests import MANIFEST_SPECS, author, publish


def test_rlm_method_manifest_is_specified_as_an_agent_skill():
    assert "rlm_method" in MANIFEST_SPECS
    spec = MANIFEST_SPECS["rlm_method"]
    assert spec.kind == "agent_skill"
    assert spec.requires == ()  # the base skill requires nothing; the RLM capabilities require IT


def test_author_rlm_method_yields_a_valid_agent_skill_entry():
    entry = author("rlm_method")
    assert isinstance(entry, RegistryEntry)
    assert entry.kind == "agent_skill"
    assert entry.envelope.identifier == "urn:air:dreamai.io:rag_wright:rlm_method"
    assert entry.response_bounds is None  # agent_skill is loaded, not callable
    assert 2 <= len(entry.envelope.representative_queries) <= 5  # discovery ranks on these


def test_publish_rlm_method_writes_slug_json_to_the_root(tmp_path):
    path = publish("rlm_method", root=tmp_path)

    assert path == tmp_path / "rlm_method.json"  # flat layout, named after the URN's final segment
    data = json.loads(path.read_text())
    assert data["kind"] == "agent_skill"
    assert data["envelope"]["identifier"] == "urn:air:dreamai.io:rag_wright:rlm_method"
    assert "representativeQueries" in data["envelope"]  # ARD-shaped camelCase on the wire
    # the manifest re-validates as a RegistryEntry (what GraphWright's RegistryStore will load)
    RegistryEntry.model_validate(data)
