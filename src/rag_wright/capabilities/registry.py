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
# ING-8b: the ENGINE's canonical capability slugs (its generic capabilities). A PACK adds its own with
# `register_canonical_slugs` when it loads (the reference contract/compliance pack does, in `reference.pack`), so
# the whitelist keeps rejecting typos and invented names without the engine knowing any domain's capabilities.
ENGINE_CAPABILITY_SLUGS: frozenset[str] = frozenset({
    "entity_disambiguation",
    "entity_resolution",
    "generation",
    "graph_extraction",
    "jev_decision",
    "ontology_registry_derivation",
    "rlm_chunking",
    "rlm_method",
    "rlm_synthesis",
    "span_relevance_judgment",
    "vision_to_text",
})
_canonical_slugs: set[str] = set(ENGINE_CAPABILITY_SLUGS)


def register_canonical_slugs(slugs) -> None:
    """Add a pack's canonical capability slugs to the registration whitelist (idempotent)."""
    _canonical_slugs.update(slugs)


def canonical_capability_slugs() -> frozenset[str]:
    """The current canonical capability slugs: the engine's plus those of every loaded pack."""
    return frozenset(_canonical_slugs)


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
        if name not in _canonical_slugs:
            raise ValueError(
                f"{name!r} is not a canonical capability slug (SPEC.md section 5, FR-C canonical "
                f"slugs, or a loaded pack's); register under the exact slug, one of {sorted(_canonical_slugs)}"
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
