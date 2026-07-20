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

from rag_wright.capabilities.ard import (
    CALLABLE_KINDS,
    NOMINAL_TYPE_VOCABULARY,
    CapabilityInterface,
    RegistryEntry,
    SkillRuntime,
)
from rag_wright.capabilities.manifests import MANIFEST_SPECS, author, publish


# --- skill_runtime: the agent_skill intrinsic-runtime block (mirrored per ADR-0003) --------------


def test_agent_skill_manifests_carry_skill_runtime():
    for slug in ("rlm_method", "rlm_chunking", "rlm_synthesis"):
        runtime = author(slug).skill_runtime
        assert runtime is not None
        assert runtime.needs_interpreter is True and runtime.rlm is True
        assert runtime.requires_dynamic_dispatch is True  # RLM needs code-driven fan-out (ADR-0017)
        # populated since the recursive rebuild (T15/T17/T28): the sub-agents are real (ADR-0015)
        assert runtime.granted_subagents == ["rlm_decomposer", "rlm_slice_worker"]


def test_granted_subagents_matches_the_names_the_skill_actually_declares():
    # grantedSubagents is a claim the runtime binds against: it MUST equal the sub-agent names the RLM
    # skill itself declares and dispatches (skills/rlm/agent.GRANTED_SUBAGENTS). Drift between the manifest
    # roster and the real sub-agent configs passes RAG_Wright's tests but fails at GraphWright's bind, so
    # assert self-consistency here where it is cheap (ADR-0015).
    from rag_wright.skills.rlm.agent import (
        RLM_DECOMPOSER,
        RLM_SLICE_WORKER,
        GRANTED_SUBAGENTS,
    )

    declared = list(GRANTED_SUBAGENTS)
    assert declared == [RLM_DECOMPOSER, RLM_SLICE_WORKER]  # the roster is exactly the two named sub-agents
    for slug in ("rlm_method", "rlm_chunking", "rlm_synthesis"):
        assert author(slug).skill_runtime.granted_subagents == declared  # manifest roster == skill's roster


def test_function_manifests_have_no_skill_runtime():
    for slug in ("parsing", "generation", "vision_to_text", "hybrid_search"):
        assert author(slug).skill_runtime is None


def test_skill_runtime_serializes_camelcase_on_the_wire(tmp_path):
    data = json.loads(publish("rlm_method", root=tmp_path).read_text())
    assert data["skillRuntime"] == {
        "needsInterpreter": True, "rlm": True,
        "grantedSubagents": ["rlm_decomposer", "rlm_slice_worker"], "requiresDynamicDispatch": True,
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


# --- capabilityInterface: the governed typed I/O (GraphWright ADR-0030 vendor extension, T43) ----

# The 7 query-graph capabilities GraphWright's lowering checker verifies (the 5 + graph_query + generation).
_GOVERNED_INTERFACE_SLUGS = (
    "hybrid_search", "chunk_read", "reranking", "graph_query", "fusion", "rlm_synthesis", "generation",
)

# The confirmed interfaces, grounded in the real callables (the reply to GraphWright). Types are what the
# checker uses; this pins them so a change to a capability's real I/O that drifts from the governed manifest
# fails here, not silently at GraphWright's bind.
_EXPECTED_INTERFACES = {
    "hybrid_search": ({"query": "text"}, {"candidates": "chunk_id"}),
    "chunk_read": ({"chunk_ids": "chunk_id"}, {"chunks": "chunk_with_text"}),
    "reranking": ({"query": "text", "passages": "chunk_with_text"}, {"ranked": "scored_chunk"}),
    "graph_query": ({"query": "text"}, {"graph": "graph_answer"}),
    "fusion": ({"reranked": "scored_chunk", "graph": "graph_answer"}, {"fused": "chunk_id"}),
    "rlm_synthesis": (
        {"query": "text", "chunks": "chunk_with_text"},
        {"answer": "text", "cited_chunk_ids": "chunk_id", "cited_extracts": "cited_extract"},
    ),
    "generation": (
        {"query": "text", "evidence": "chunk_with_text"},
        {"answer": "text", "cited_chunk_ids": "chunk_id"},
    ),
}


@pytest.mark.parametrize("slug", _GOVERNED_INTERFACE_SLUGS)
def test_governed_capabilities_declare_the_confirmed_interface(slug):
    iface = author(slug).capability_interface
    assert iface is not None, f"{slug} must declare a capabilityInterface (governed for the query graph)"
    expected_inputs, expected_outputs = _EXPECTED_INTERFACES[slug]
    assert iface.inputs == expected_inputs
    assert iface.outputs == expected_outputs
    assert iface.success_criterion.strip()  # required, informational one-liner


def test_reranking_consumes_text_so_the_checker_forces_chunk_read_upstream():
    # The load-bearing fact of the whole exchange: reranking's input is chunk_with_text, and the only
    # producer of chunk_with_text is chunk_read. Under nominal typing that mismatch (chunk_id != chunk_with_text)
    # is exactly what forces a rehydrate between an id-only producer and reranking.
    assert author("reranking").capability_interface.inputs["passages"] == "chunk_with_text"
    assert author("chunk_read").capability_interface.outputs["chunks"] == "chunk_with_text"
    assert author("hybrid_search").capability_interface.outputs["candidates"] == "chunk_id"


def test_fusion_output_type_checks_into_chunk_read(tmp_path):
    # GraphWright's resolved call (ADR-0021): fusion output is `chunk_id` (id-only, the `sources[]`
    # provenance does not fork the type name), so `fusion -> chunk_read` type-checks under nominal typing —
    # the fused evidence set can be rehydrated before synthesis. `fused_chunk` is retired from the vocabulary.
    assert author("fusion").capability_interface.outputs["fused"] == "chunk_id"
    assert author("fusion").capability_interface.outputs["fused"] == author("chunk_read").capability_interface.inputs["chunk_ids"]
    assert "fused_chunk" not in NOMINAL_TYPE_VOCABULARY


def test_non_query_graph_capabilities_declare_no_interface():
    # Scope: only the query→answer graph is governed for now (GraphWright's request). The rest are None.
    for slug in ("parsing", "embedding", "graph_extraction", "vision_to_text", "rlm_method"):
        assert author(slug).capability_interface is None


def test_declared_interface_types_are_all_in_the_agreed_vocabulary():
    for slug in _GOVERNED_INTERFACE_SLUGS:
        iface = author(slug).capability_interface
        for type_name in list(iface.inputs.values()) + list(iface.outputs.values()):
            assert type_name in NOMINAL_TYPE_VOCABULARY


def test_capability_interface_rejects_a_type_outside_the_vocabulary():
    CapabilityInterface(inputs={"q": "text"}, outputs={"c": "chunk_id"}, success_criterion="ok")  # ok
    with pytest.raises(ValidationError):  # list sugar / unknown name is a typo that would break a chain check
        CapabilityInterface(inputs={"q": "text"}, outputs={"c": "chunk_id[]"}, success_criterion="ok")
    with pytest.raises(ValidationError):
        CapabilityInterface(inputs={"q": "not_a_type"}, outputs={"c": "chunk_id"}, success_criterion="ok")


def test_capability_interface_serializes_snake_case_inner_keys_under_a_camelcase_manifest(tmp_path):
    # GraphWright ADR-0030 section 2: the top-level field is capabilityInterface (camelCase), but its inner
    # keys stay snake_case (success_criterion), because TypedInterface carries no ARD alias and their
    # extra="forbid" loader rejects camelCased inner keys. Publish and assert the exact on-disk shape.
    data = json.loads(publish("hybrid_search", root=tmp_path).read_text())
    assert data["capabilityInterface"] == {
        "inputs": {"query": "text"},
        "outputs": {"candidates": "chunk_id"},
        "success_criterion": "retrieve RRF-fused candidate chunk references for a natural-language query",
    }
    RegistryEntry.model_validate(data)  # re-validates as GraphWright's store will load it


def test_capability_interface_round_trips_through_registry_entry(tmp_path):
    # extra="forbid" on RegistryEntry now ACCEPTS capabilityInterface (declared) while still rejecting any
    # other unknown field — the finding-3.2 lockstep, proven on our side.
    for slug in _GOVERNED_INTERFACE_SLUGS:
        data = json.loads(publish(slug, root=tmp_path).read_text())
        assert "capabilityInterface" in data
        reloaded = RegistryEntry.model_validate(data)
        assert reloaded.capability_interface == author(slug).capability_interface


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
