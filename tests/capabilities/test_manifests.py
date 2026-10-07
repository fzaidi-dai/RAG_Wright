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
    for slug in ("clause_disambiguation", "vision_to_text", "requirement_adaptation"):
        assert author(slug).skill_runtime is None


# --- CAP-REG-1: reclassify mis-kinded manifests to the settled taxonomy ---------------------------
# (docs/product/capability_profiles.md). rlm_chunking is intentionally NOT here: it carries a
# dynamic-dispatch skill_runtime (agent-decides RLM recursion), so a deterministic `subgraph` kind
# would be wrong -- that reclassification is a separate design decision.


def test_reclassified_capability_kinds():
    assert author("generation").kind == "agent_skill"  # a single grounded LLM act
    # (embedding/reranking were models but are de-registered from ARD — core API now, EP-CORE-1a/ADR-0118;
    #  graph_extraction was a subgraph but is de-registered — an internal pipeline step now, EP-CORE-1b-iii)


def test_generation_is_a_loaded_skill_without_runtime_or_bounds():
    entry = author("generation")
    assert entry.skill_runtime is None  # a plain LLM skill: no interpreter / RLM machinery
    assert entry.response_bounds is None  # agent_skill is loaded, not called -> carries no bounds


def test_reclassified_models_and_subgraph_stay_callable_with_bounds():
    for slug in ("clause_function_classification", "clause_property_classification"):
        assert author(slug).response_bounds is not None  # model kinds are callable


# --- CAP-REG-2: register the built contract-KG capabilities -----------------------------------

_CAP_REG_2_KINDS = {
    "extraction_grounding_judge": "function",
    "intra_document_scoped_query": "function",
    "clause_disambiguation": "function",
    "typed_value_normalization": "function",
    "clause_function_classification": "model",
    "query_function_classification": "agent_skill",
}


@pytest.mark.parametrize("slug,kind", sorted(_CAP_REG_2_KINDS.items()))
def test_cap_reg_2_capabilities_author_with_the_right_kind(slug, kind):
    entry = author(slug)
    assert entry.kind == kind
    # callable kinds (function/model) carry bounds; the agent_skill does not
    assert (entry.response_bounds is not None) == (kind != "agent_skill")


def test_cap_reg_2_register_functions_register_the_right_kind():
    from rag_wright.packs.contracts.capabilities.contract_kg_serve import (
        register_clause_disambiguation,
        register_intra_document_scoped_query,
    )
    from rag_wright.packs.contracts.capabilities.query_function_classifier import register_query_function_classification
    from rag_wright.capabilities.registry import CapabilityRegistry
    from rag_wright.packs.contracts.schemas.value_match import register_typed_value_normalization
    from rag_wright.packs.contracts.spans.legalbert_classifier import register_clause_function_classification
    from rag_wright.packs.contracts.spans.property_grounding import register_extraction_grounding_judge

    reg = CapabilityRegistry()
    register_typed_value_normalization(reg)
    register_extraction_grounding_judge(reg)
    register_intra_document_scoped_query(reg)
    register_clause_disambiguation(reg)
    register_clause_function_classification(reg)
    register_query_function_classification(reg)
    assert len(reg) == 6  # operative_span_segmentation de-registered (EP-CORE-1a)
    for slug, kind in _CAP_REG_2_KINDS.items():
        assert reg.get(slug).kind == kind


# (EP-CORE-1a: test_semantic_chunking_is_a_deterministic_subgraph removed — semantic_chunking is de-registered
# from ARD; it's a core helper now, not a catalogued capability. ADR-0118.)


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
    data = author("clause_disambiguation").model_dump(by_alias=True)  # a function entry
    data["skillRuntime"] = SkillRuntime(needs_interpreter=True).model_dump(by_alias=True)
    with pytest.raises(ValidationError):  # skill_runtime is agent_skill only (like requires)
        RegistryEntry.model_validate(data)


# --- capabilityInterface: the governed typed I/O (GraphWright ADR-0030 vendor extension, T43) ----

# The governed capabilities GraphWright's lowering checker verifies: the 7 retrieval->answer caps (T43) plus
# the 7 ingestion->graph caps (T44). rlm_method is deliberately excluded — a required shared skill, not a
# bound data node, so it has no data I/O to govern.
# EP-CORE-1a (ADR-0118): the generic retrieval/ingestion primitives (hybrid_search, chunk_read, reranking,
# graph_query, fusion, parsing, embedding) were de-registered from ARD — they are core API now, and their typed I/O
# lives in their Python signatures (GraphWright is parked; see ADR-0118). What remains governed are the capabilities
# still in ARD that declare an interface (the skills + the still-registered graph/entity caps, DD-3/4/5 pending).
_GOVERNED_INTERFACE_SLUGS = (
    "rlm_synthesis", "generation", "rlm_chunking", "vision_to_text",
)  # EP-CORE-1b-iii: graph_extraction/entity_disambiguation/entity_resolution de-registered (internal steps)

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
    # ingestion -> graph (T44)
    "parsing": ({"source": "document"}, {"parsed": "parsed_doc"}),
    "rlm_chunking": ({"parsed": "parsed_doc"}, {"chunks": "chunk"}),
    "embedding": ({"chunks": "chunk"}, {"embeddings": "embedding"}),
    "vision_to_text": ({"image": "image"}, {"text": "text"}),
}


@pytest.mark.parametrize("slug", _GOVERNED_INTERFACE_SLUGS)
def test_governed_capabilities_declare_the_confirmed_interface(slug):
    iface = author(slug).capability_interface
    assert iface is not None, f"{slug} must declare a capabilityInterface (governed for the query graph)"
    expected_inputs, expected_outputs = _EXPECTED_INTERFACES[slug]
    assert iface.inputs == expected_inputs
    assert iface.outputs == expected_outputs
    assert iface.success_criterion.strip()  # required, informational one-liner


# (EP-CORE-1a: the governed-composition tests for the de-registered primitives — reranking<-chunk_read,
# fusion->chunk_read, and the ingestion->graph chain through parsing/embedding — were removed; those primitives
# are core API now, not ARD capabilities, and GraphWright is parked.)


def test_rlm_method_declares_no_interface_it_is_a_required_skill_not_a_data_node():
    # rlm_method (T44): a shared METHOD skill required by rlm_chunking/rlm_synthesis, never bound as a
    # data-processing node — no pipeline data I/O, so no governed interface (a type with no producer/consumer).
    assert author("rlm_method").capability_interface is None


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
    data = json.loads(publish("generation", root=tmp_path).read_text())  # a still-registered governed cap
    iface = data["capabilityInterface"]
    # top-level field is camelCase; its inner keys stay snake_case (success_criterion), which the extra="forbid"
    # loader requires (TypedInterface carries no ARD alias).
    assert set(iface) == {"inputs", "outputs", "success_criterion"}
    assert isinstance(iface["inputs"], dict) and isinstance(iface["outputs"], dict)
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
