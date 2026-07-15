"""ARD manifest authoring per capability (cross-cutting: every T15-T29 capability authors one).

Each capability's ARD manifest is authored from a committed spec in `capabilities/manifests.py` and
written to the shared registry root as `<slug>.json` under `urn:air:dreamai.io:rag_wright:<slug>`.
These tests validate authoring + on-disk shape locally; a live `RegistryStore` load happens later on
the GraphWright side, not here.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from rag_wright.capabilities.ard import CALLABLE_KINDS, RegistryEntry, SkillRuntime
from rag_wright.capabilities.manifests import MANIFEST_SPECS, author, publish


# --- skill_runtime: the agent_skill intrinsic-runtime block (mirrored per ADR-0003) --------------


def test_agent_skill_manifests_carry_skill_runtime():
    for slug in ("rlm_method", "rlm_chunking", "rlm_synthesis"):
        runtime = author(slug).skill_runtime
        assert runtime is not None
        assert runtime.needs_interpreter is True and runtime.rlm is True
        assert runtime.requires_dynamic_dispatch is True  # RLM needs code-driven fan-out (ADR-0017)


def test_function_manifests_have_no_skill_runtime():
    for slug in ("parsing", "generation", "vision_to_text", "hybrid_search"):
        assert author(slug).skill_runtime is None


def test_skill_runtime_serializes_camelcase_on_the_wire(tmp_path):
    data = json.loads(publish("rlm_method", root=tmp_path).read_text())
    assert data["skillRuntime"] == {
        "needsInterpreter": True, "rlm": True, "grantedSubagents": [], "requiresDynamicDispatch": True,
    }
    RegistryEntry.model_validate(data)  # re-validates as GraphWright's store will load it


def test_requires_dynamic_dispatch_implies_interpreter():
    # the typed flag carries the requirement, not the trigger word; it implies an interpreter (ADR-0017)
    SkillRuntime(needs_interpreter=True, requires_dynamic_dispatch=True)  # ok
    with pytest.raises(ValidationError):
        SkillRuntime(needs_interpreter=False, requires_dynamic_dispatch=True)


def test_skill_runtime_is_rejected_on_a_non_agent_skill():
    data = author("parsing").model_dump(by_alias=True)  # a function entry
    data["skillRuntime"] = SkillRuntime(needs_interpreter=True).model_dump(by_alias=True)
    with pytest.raises(ValidationError):  # skill_runtime is agent_skill only (like requires)
        RegistryEntry.model_validate(data)


@pytest.mark.parametrize("slug", sorted(MANIFEST_SPECS))
def test_every_spec_authors_and_publishes_a_valid_manifest(slug, tmp_path):
    entry = author(slug)
    assert entry.envelope.identifier == f"urn:air:dreamai.io:rag_wright:{slug}"
    # callable kinds declare response bounds; agent_skill (loaded) does not
    assert (entry.response_bounds is not None) == (entry.kind in CALLABLE_KINDS)

    path = publish(slug, root=tmp_path)
    assert path == tmp_path / f"{slug}.json"
    RegistryEntry.model_validate(json.loads(path.read_text()))  # what GraphWright's store will load


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
