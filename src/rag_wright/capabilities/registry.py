"""The capability registration seam (FR-S.5, contract use).

Two registrations from one key, the capability's FR-C / FR-I / FR-Q name (see the "ARD registration"
note in tasks.md):

- **Internal registration:** a built capability is registered by its name with its contract, so the
  MCP skill surface (T31) can expose it.
- **ARD registration:** registration also emits an ARD manifest *skeleton* (RegistryEntry-shaped,
  the ADR-0005 mirror in `ard.py`) that GraphWright's compiler discovers and binds. The name is the
  single shared key and the URN anchor, so the internal registry and the ARD manifest speak one
  vocabulary. The `name` must be identical to the capability's name in the shared spec (the
  cross-spec join key the Orchestration Spec binds against).

**Kind is explicit per capability** (no default), following the binding rule (docs/adr/0003):
`mcp_tool` crosses the MCP boundary (query-side governed skills, FR-S.5); `function` is an in-process
graph-node call (parser, embedder, reranker, fusion); `agent_skill` is loaded knowledge (RLM
chunking / synthesis, the RLM skill), not callable. A capability that fits none of the six kinds is
flagged, not forced.

**The emitted skeleton is a DRAFT.** Its representative queries (2-5, required by the schema) and
trust attestations are authored at the capability's own task via `ManifestSkeleton.author(...)`,
which returns the complete, validated `RegistryEntry` for the loadable path. A draft is never a
`RegistryEntry` and must be kept out of the loadable registry directory: GraphWright's
`RegistryStore` globs `*.json` non-recursively at the root, so drafts belong in a `staging/`
subdirectory (never globbed) until authored. Nothing, including the compiler's glob, ever loads a
partial as if it were registered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel

from rag_wright.capabilities.ard import (
    CALLABLE_KINDS,
    MEDIA_TYPE_BY_KIND,
    URN_NAMESPACE,
    URN_PUBLISHER,
    ArdEnvelope,
    Attestation,
    CapabilityInterface,
    EntryKind,
    GovernanceBlock,
    RegistryEntry,
    ResponseBounds,
    SkillRuntime,
    TrustManifest,
)

# The ARD URN is domain-anchored (ARD v0.9 4.2.1): urn:air:<publisher>:<namespace>:<name>. The
# publisher and namespace are the single source in ard.py (dreamai.io / rag_wright).
_DEFAULT_OWNER = "dreamai.io"

# The canonical capability slugs are the cross-spec join keys (SPEC.md section 5, "FR-C canonical
# slugs"). The slug is the semantic capability name, never a requirement id (`hybrid_search`, never
# `fr-c-3`), so it survives spec renumbering. SPEC.md is authoritative; this frozenset mirrors it so
# registration rejects a name that is not a canonical capability (a typo, a requirement id, or an
# invented name) — the same shared-vocabulary discipline as the ARD schema mirror. Adding a
# capability updates both. FR-C.9 is a single slug (`generation`): reasoning, generation, and
# vision-to-text are one capability, bound at whichever node needs them.
CANONICAL_CAPABILITY_SLUGS: frozenset[str] = frozenset(
    {
        "parsing",  # FR-C.1
        "embedding",  # FR-C.2
        "hybrid_search",  # FR-C.3
        "reranking",  # FR-C.4
        "graph_query",  # FR-C.5
        "graph_extraction",  # FR-C.6
        "entity_disambiguation",  # FR-C.7 (canonicalize: normalize/reject/cluster; the T23b stage)
        "entity_resolution",  # FR-C.7 (closed-world linking to EDGAR CIK)
        "ontology_registry_derivation",  # FR-C.8 (foundation derivation: slug, but no ARD manifest)
        "generation",  # FR-C.9 (grounded/cited/abstaining answer generation)
        "vision_to_text",  # FR-C.9 (scanned-image transcription; split from generation, ADR-0014)
        "fusion",  # FR-Q.4
        "chunk_read",  # FR-Q (text rehydration between fusion and synthesis; T38)
        "rlm_chunking",  # FR-I.1 (applies the RLM skill; dynamic RLM discoverer -> agent_skill)
        "semantic_chunking",  # FR-I.1 (single-call deterministic discoverer -> subgraph; CAP-REG-1b)
        "rlm_synthesis",  # FR-Q.5 (applies the RLM skill)
        "rlm_method",  # FR-C.10 (the shared RLM method skill, if registered)
        "okf_compile",  # FR-K.1-K.4 (foundation derivation: slug, but no ARD manifest; T46)
        "okf_navigate",  # FR-K.6 (query-discovered traversal: slug + ARD manifest; T50)
        # --- CAP-REG-2: the built contract-KG capabilities ---
        "typed_value_normalization",  # function: canonicalization + subsumption (KG-5a)
        "extraction_grounding_judge",  # function: ADR-0028 lexical grounding gate
        "extraction_semantic_judge",  # function: ADR-0040 Layer 3 LLM semantic gate
        "operative_span_segmentation",  # function: chunk -> operative spans
        "intra_document_scoped_query",  # function: intra-contract scoped KG serving (Leg A)
        "clause_disambiguation",  # function: disambiguation by property (Leg A)
        "clause_function_classification",  # model: LegalBERT function classifier (T56)
        "query_function_classification",  # agent_skill: taxonomy-constrained query->function (KG-5e)
        # --- CAP-REG-3: the KG-primary retrieval core (packaged out of eval/kg_primary.py) ---
        "candidate_routing",  # function: union combiner -> candidate pool (KG-5e)
        "typed_constraint_match_rank",  # function: KG-5a graded constraint match (recall-safe, subsumption)
        "dense_rank_tiebreak",  # function: cosine order for meaningful tie-breaking (KG-6/V4)
        "property_boosted_retrieval",  # function: BGE pool + span-clause join + typed rerank (SPAN-CLAUSE-RERANK)
        # --- KG-7: the Party<->Contract unifying link over the one contract KG (ADR-0036) ---
        "party_clause_linking",  # function: PARTY_TO edges (Entity -> Contract) by normalized-name join
        # --- LG-1/LG-2: hardened LangGraph subgraphs ---
        "typed_clause_extraction",  # subgraph: extract -> adapt -> reground -> escalate -> [HITL] -> dead-letter
        "query_constraint_extraction",  # subgraph: query-side typed constraint extraction (graceful-empty)
        # --- LG-3: composite pipeline subgraphs (compose the component subgraphs + registered capabilities) ---
        "relational_qa",  # subgraph: graph_query -> chunk_read -> generate_answer (entity question -> cited answer)
        "intra_document_qa",  # subgraph: scoped KG query -> generate_answer (contract + question -> cited answer)
        "cross_corpus_retrieval",  # subgraph: constraints+functions -> route -> match_rank -> tiebreak -> cited clauses
        "typed_property_retrieval",  # subgraph: front-door + property_boosted_retrieval (Leg B, LEGB-SUBGRAPH)
        "contract_ingestion_pipeline",  # subgraph: generic corpus ingest (chunk->extract->resolve->write->link)
        # --- Compliance module rung 1 (roadmap §13): the ad-compliance engine ---
        "requirement_extraction",  # subgraph: extract(docling-graph, multi-call) -> adapt; regulatory section -> Requirement[] (CC-2, SKILL-SPLIT)
        "requirement_adaptation",  # function: ExtractedRegulationSection -> validated Requirement[] (deterministic)
        "claim_extraction",  # agent_skill: single LLM extraction act (ad -> ExtractedAd); SKILL-SPLIT
        "claim_adaptation",  # function: ExtractedAd -> validated Claim[] (deterministic)
        "compliance_judgment",  # agent_skill: single LLM judgment act ((claim, requirement) -> verdict); SKILL-SPLIT
        "compliance_finding_assembly",  # function: raw verdict + inputs -> cited ComplianceFinding (deterministic)
        "compliance_ingestion",  # subgraph: regulatory corpus -> Requirement KG (CC-5)
        "compliance_check",  # subgraph: subject doc x requirements -> cited findings + gap matrix (CC-6)
    }
)


def capability_urn(name: str) -> str:
    """The domain-anchored ARD URN for a capability name (urn:air:dreamai.io:rag_wright:<name>)."""
    return f"urn:air:{URN_PUBLISHER}:{URN_NAMESPACE}:{name}"


class ManifestSkeleton(BaseModel):
    """A DRAFT ARD manifest: the fields registration can derive, minus the authored fields.

    Not loadable as a `RegistryEntry` (it has no representative queries yet). `author(...)` fills the
    authored fields and returns the complete, validated `RegistryEntry`.
    """

    name: str
    kind: EntryKind
    identifier: str  # the URN
    media_type: str
    display_name: str
    response_bounds: Optional[ResponseBounds] = None  # present for callable kinds only
    owner: str = _DEFAULT_OWNER
    description: Optional[str] = None
    tags: list[str] = []

    def author(
        self,
        representative_queries: list[str],
        *,
        attestations: Optional[list[Attestation]] = None,
        identity: Optional[str] = None,
        identity_type: str = "domain",
        golden_eval_ref: Optional[str] = None,
        requires: Optional[list[str]] = None,
        skill_runtime: Optional[SkillRuntime] = None,
        capability_interface: Optional[CapabilityInterface] = None,
        description: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> RegistryEntry:
        """Author the draft into a complete, validated `RegistryEntry` for the loadable path.

        `representative_queries` (2-5) and any trust `attestations` are the fields that could not be
        derived at registration; the schema validators enforce them. `capability_interface` is the
        optional GraphWright vendor extension (ADR-0030), declared per capability at its own task.
        """
        envelope = ArdEnvelope(
            identifier=self.identifier,
            display_name=self.display_name,
            type=self.media_type,
            representative_queries=representative_queries,
            trust_manifest=TrustManifest(
                identity=identity or self.identifier,
                identity_type=identity_type,
                attestations=attestations or [],
            ),
            description=description if description is not None else self.description,
            tags=tags if tags is not None else self.tags,
        )
        return RegistryEntry(
            kind=self.kind,
            envelope=envelope,
            response_bounds=self.response_bounds,
            requires=requires or [],
            skill_runtime=skill_runtime,
            capability_interface=capability_interface,
            golden_eval_ref=golden_eval_ref,
            governance=GovernanceBlock(owner=self.owner),
        )


@dataclass(frozen=True)
class CapabilityRegistration:
    """One registered capability: its name, kind, contract, and its draft ARD manifest skeleton."""

    name: str
    kind: EntryKind
    contract: type[BaseModel]
    skeleton: ManifestSkeleton


class CapabilityRegistry:
    """An in-process registry of built capabilities, keyed by the capability name (FR-S.5)."""

    def __init__(self) -> None:
        self._by_name: dict[str, CapabilityRegistration] = {}

    def register(
        self,
        name: str,
        *,
        contract: type[BaseModel],
        kind: EntryKind,
        display_name: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[list[str]] = None,
        response_bounds: Optional[ResponseBounds] = None,
    ) -> CapabilityRegistration:
        """Register a capability by name with its contract and (explicit) kind, emitting its ARD
        skeleton. Rejects a malformed name, an unknown kind, or a duplicate registration."""
        if name not in CANONICAL_CAPABILITY_SLUGS:
            raise ValueError(
                f"{name!r} is not a canonical capability slug (SPEC.md section 5, FR-C canonical "
                f"slugs); register under the exact slug, one of {sorted(CANONICAL_CAPABILITY_SLUGS)}"
            )
        if kind not in MEDIA_TYPE_BY_KIND:
            raise ValueError(
                f"unknown capability kind {kind!r}; must be one of {sorted(MEDIA_TYPE_BY_KIND)} "
                "(flag a capability that fits none of the six rather than forcing it)"
            )
        if name in self._by_name:
            raise ValueError(f"capability {name!r} is already registered (duplicate)")

        if kind in CALLABLE_KINDS:
            bounds = response_bounds or ResponseBounds()
        else:  # agent_skill is loaded, not called
            if response_bounds is not None:
                raise ValueError(f"kind {kind!r} is not callable and must not declare response_bounds")
            bounds = None

        skeleton = ManifestSkeleton(
            name=name,
            kind=kind,
            identifier=capability_urn(name),
            media_type=MEDIA_TYPE_BY_KIND[kind],
            display_name=display_name or name,
            response_bounds=bounds,
            description=description,
            tags=tags or [],
        )
        registration = CapabilityRegistration(
            name=name, kind=kind, contract=contract, skeleton=skeleton
        )
        self._by_name[name] = registration
        return registration

    def get(self, name: str) -> CapabilityRegistration:
        """The registration for a name, or raise `KeyError` if the name is unknown."""
        if name not in self._by_name:
            raise KeyError(f"no capability registered under {name!r}")
        return self._by_name[name]

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def __len__(self) -> int:
        return len(self._by_name)

    def names(self) -> list[str]:
        return sorted(self._by_name)
