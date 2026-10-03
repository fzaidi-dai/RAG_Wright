"""Tests for the capability registration seam and ARD manifest skeleton (T6, FR-S.5, RAC-6).

Two registrations from one key (the FR-C / FR-I / FR-Q capability name):
- internal: register a built capability by name with its contract (for the MCP surface, T31);
- ARD: emit a manifest skeleton conforming to GraphWright's RegistryEntry schema (ADR-0005, mirrored
  in `ard.py` by deliberate duplication) that the compiler discovers.

The tests pin: register/lookup + duplicate/unknown rejection; explicit-kind rule (no default);
the URN and media-type the skeleton derives; that a partial skeleton is NOT loadable (a draft, kept
out of the loadable path) and only `author(...)` yields a valid RegistryEntry; and that the mirrored
schema enforces ADR-0005's invariants so drift is caught in RAG_Wright's own suite.
"""

import pytest
from pydantic import ValidationError

from rag_wright.capabilities.ard import (
    MEDIA_TYPE_BY_KIND,
    RAG_URN_PREFIX,
    ArdEnvelope,
    GovernanceBlock,
    RegistryEntry,
    ResponseBounds,
    TrustManifest,
    registry_root,
    write_manifest,
)
import json
import re

from rag_wright.capabilities.registry import (
    CANONICAL_CAPABILITY_SLUGS,
    CapabilityRegistry,
    ManifestSkeleton,
    capability_urn,
)
from rag_wright.contracts.chunk import ChunkRecord


# --- internal registration (RAC-6) ----------------------------------------------------------


def test_register_and_lookup_by_name_returns_contract():
    reg = CapabilityRegistry()
    reg.register("graph_extraction", contract=ChunkRecord, kind="mcp_tool")
    got = reg.get("graph_extraction")
    assert got.name == "graph_extraction"
    assert got.contract is ChunkRecord
    assert got.kind == "mcp_tool"


def test_duplicate_registration_is_rejected():
    reg = CapabilityRegistry()
    reg.register("graph_extraction", contract=ChunkRecord, kind="function")
    with pytest.raises(ValueError):
        reg.register("graph_extraction", contract=ChunkRecord, kind="function")


def test_lookup_of_unknown_name_is_rejected():
    reg = CapabilityRegistry()
    with pytest.raises(KeyError):
        reg.get("nope")


@pytest.mark.parametrize("bad", ["not_a_capability", "fr-c-3", "Has Space", "hybridsearch", ""])
def test_register_rejects_non_canonical_name(bad):
    # The name is the cross-spec join key: only canonical slugs (SPEC section 5) register; a typo,
    # a requirement id, or an invented name is rejected.
    reg = CapabilityRegistry()
    with pytest.raises(ValueError):
        reg.register(bad, contract=ChunkRecord, kind="function")


def test_register_rejects_unknown_kind():
    reg = CapabilityRegistry()
    with pytest.raises(ValueError):
        reg.register("graph_extraction", contract=ChunkRecord, kind="widget")  # not one of the six


def test_all_canonical_slugs_are_urn_safe():
    urn_safe = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
    assert all(urn_safe.match(slug) for slug in CANONICAL_CAPABILITY_SLUGS)


def test_generation_and_vision_to_text_are_separate_capabilities():
    # FR-C.9 was split into two slugs so discovery ranks each on its own intents (ADR-0014):
    # answer generation and scanned-image transcription have different inputs, callers, failure modes.
    assert "generation" in CANONICAL_CAPABILITY_SLUGS
    assert "vision_to_text" in CANONICAL_CAPABILITY_SLUGS


# --- explicit kind, no default (RAC-6, review rule) -----------------------------------------


def test_kind_is_required_no_default():
    reg = CapabilityRegistry()
    with pytest.raises(TypeError):
        reg.register("graph_extraction", contract=ChunkRecord)  # kind is required


# --- ARD skeleton derivation (RAC-6) --------------------------------------------------------


def test_skeleton_urn_anchors_on_the_capability_name():
    reg = CapabilityRegistry()
    skel = reg.register("graph_extraction", contract=ChunkRecord, kind="mcp_tool").skeleton
    assert skel.identifier == capability_urn("graph_extraction")
    assert skel.identifier == "urn:air:dreamai.io:rag_wright:graph_extraction"


def test_callable_kind_gets_media_type_and_response_bounds():
    reg = CapabilityRegistry()
    skel = reg.register("graph_extraction", contract=ChunkRecord, kind="function").skeleton
    assert skel.media_type == MEDIA_TYPE_BY_KIND["function"]
    assert isinstance(skel.response_bounds, ResponseBounds)


def test_mcp_tool_media_type():
    reg = CapabilityRegistry()
    skel = reg.register("graph_extraction", contract=ChunkRecord, kind="mcp_tool").skeleton
    assert skel.media_type == "application/mcp-server-card+json"
    assert skel.response_bounds is not None


def test_agent_skill_is_loaded_no_response_bounds():
    reg = CapabilityRegistry()
    skel = reg.register("rlm_synthesis", contract=ChunkRecord, kind="agent_skill").skeleton
    assert skel.media_type == "application/ai-skill+md"
    assert skel.response_bounds is None


def test_agent_skill_rejects_response_bounds():
    reg = CapabilityRegistry()
    with pytest.raises(ValueError):
        reg.register(
            "rlm_chunking",
            contract=ChunkRecord,
            kind="agent_skill",
            response_bounds=ResponseBounds(),
        )


# --- a partial skeleton is a DRAFT, not loadable (review: keep out of the loadable path) -----


def test_partial_skeleton_is_not_a_loadable_registry_entry():
    reg = CapabilityRegistry()
    skel = reg.register("graph_extraction", contract=ChunkRecord, kind="function").skeleton
    assert isinstance(skel, ManifestSkeleton)
    # A skeleton lacks the authored fields (representative queries), so it cannot validate as a
    # RegistryEntry: a partial can never masquerade as a registered, loadable entry.
    with pytest.raises(ValidationError):
        RegistryEntry.model_validate(skel.model_dump(by_alias=True))


def test_author_produces_a_valid_loadable_registry_entry():
    reg = CapabilityRegistry()
    skel = reg.register("graph_extraction", contract=ChunkRecord, kind="mcp_tool").skeleton
    entry = skel.author(["find the governing law clause", "what indemnities apply"])
    assert isinstance(entry, RegistryEntry)
    assert entry.envelope.identifier == capability_urn("graph_extraction")
    assert entry.envelope.type == MEDIA_TYPE_BY_KIND["mcp_tool"]
    assert entry.kind == "mcp_tool"
    assert entry.response_bounds is not None
    assert entry.governance.owner  # defaulted


@pytest.mark.parametrize("queries", [[], ["only one"], ["a", "b", "c", "d", "e", "f"]])
def test_author_enforces_two_to_five_representative_queries(queries):
    reg = CapabilityRegistry()
    skel = reg.register("graph_extraction", contract=ChunkRecord, kind="function").skeleton
    with pytest.raises(ValidationError):
        skel.author(queries)


def test_authored_entry_round_trips_through_camelcase_json():
    reg = CapabilityRegistry()
    skel = reg.register("graph_extraction", contract=ChunkRecord, kind="function").skeleton
    entry = skel.author(["embed this chunk", "vectorize the summary"])
    reloaded = RegistryEntry.model_validate_json(entry.model_dump_json(by_alias=True))
    assert reloaded == entry
    # camelCase on the wire (ARD), matching GraphWright's RegistryStore expectations.
    assert '"representativeQueries"' in entry.model_dump_json(by_alias=True)


# --- mirrored ADR-0005 invariants: drift caught in RAG_Wright's own suite --------------------


def _envelope(**over):
    base = dict(
        identifier="urn:air:dreamai:rag_wright:x",
        display_name="X",
        type=MEDIA_TYPE_BY_KIND["mcp_tool"],
        representative_queries=["q one", "q two"],
        trust_manifest=TrustManifest(identity="urn:air:dreamai:rag_wright:x", identity_type="domain"),
    )
    base.update(over)
    return ArdEnvelope(**base)


def test_envelope_identifier_must_be_an_ard_urn():
    with pytest.raises(ValidationError):
        _envelope(identifier="not-a-urn")


def test_registry_entry_type_must_match_kind():
    with pytest.raises(ValidationError):
        RegistryEntry(
            kind="mcp_tool",
            envelope=_envelope(type=MEDIA_TYPE_BY_KIND["function"]),  # mismatched media type
            response_bounds=ResponseBounds(),
            governance=GovernanceBlock(owner="dreamai.io"),
        )


def test_callable_kind_requires_response_bounds():
    with pytest.raises(ValidationError):
        RegistryEntry(
            kind="mcp_tool",
            envelope=_envelope(),
            response_bounds=None,  # callable must declare bounds
            governance=GovernanceBlock(owner="dreamai.io"),
        )


def test_requires_closure_only_valid_on_agent_skill():
    with pytest.raises(ValidationError):
        RegistryEntry(
            kind="function",
            envelope=_envelope(type=MEDIA_TYPE_BY_KIND["function"]),
            response_bounds=ResponseBounds(),
            governance=GovernanceBlock(owner="dreamai.io"),
            requires=["some_other_capability"],
        )


# --- shared ARD registry root: config-addressed by ARD_REGISTRY_ROOT (registry-root.md) ------


def _authored_entry(name: str = "graph_extraction") -> RegistryEntry:
    skel = CapabilityRegistry().register(name, contract=ChunkRecord, kind="mcp_tool").skeleton
    return skel.author(["find the governing law clause", "what indemnities apply"])


def test_registry_root_resolves_from_env_and_creates_it(tmp_path, monkeypatch):
    target = tmp_path / "shared" / "registry"
    monkeypatch.setenv("ARD_REGISTRY_ROOT", str(target))
    root = registry_root()
    assert root == target
    assert root.is_dir()  # created on resolution


def test_registry_root_defaults_to_air_registry_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("ARD_REGISTRY_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # keep the default off the real home
    root = registry_root()
    assert root == tmp_path / ".air" / "registry"  # `.air` mirrors the urn:air: scheme
    assert root.is_dir()


def test_write_manifest_writes_flat_slug_json_to_the_root(tmp_path, monkeypatch):
    monkeypatch.setenv("ARD_REGISTRY_ROOT", str(tmp_path))
    entry = _authored_entry("graph_extraction")

    path = write_manifest(entry)

    assert path == tmp_path / "graph_extraction.json"  # flat, named after the URN's final segment
    data = json.loads(path.read_text())
    assert data["envelope"]["identifier"] == "urn:air:dreamai.io:rag_wright:graph_extraction"
    assert "representativeQueries" in data["envelope"]  # ARD-shaped camelCase on the wire


def test_write_manifest_rejects_a_foreign_publisher():
    # a valid urn:air: URN from another publisher passes the generic schema but is not ours to write
    foreign = RegistryEntry(
        kind="mcp_tool",
        envelope=_envelope(identifier="urn:air:someoneelse.com:their_ns:x"),
        response_bounds=ResponseBounds(),
        governance=GovernanceBlock(owner="dreamai.io"),
    )
    assert not foreign.envelope.identifier.startswith(RAG_URN_PREFIX)
    with pytest.raises(ValueError, match="not one of ours"):
        write_manifest(foreign)
