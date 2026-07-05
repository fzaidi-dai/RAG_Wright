"""RAG-side mirror of GraphWright's ARD `RegistryEntry` schema (GraphWright ADR-0005).

This is a **shared wire-format contract**, mirrored here by **deliberate duplication** (see
docs/adr/0003). RAG_Wright authors Agentic Resource Discovery (ARD) manifests as JSON that
GraphWright's `RegistryStore` consumes; the capability half does not import the compiler half, so
its schema is duplicated rather than imported. A change to GraphWright's ADR-0005 schema is a
**cross-repo coordination point**: this mirror and GraphWright's `src/graphwright/registry/entry.py`
must be updated together, or a manifest that validates on one side fails on the other. The
conformance tests here catch a drift in RAG_Wright's own suite instead of only at GraphWright load.

Field names, shapes, and validators mirror GraphWright's `entry.py` verbatim (ARD v0.9, camelCase on
the wire via `to_camel`, `extra="forbid"`).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

# The six governed kinds (GraphWright FR-5.1). `agent_skill` is loaded by an agent node; the others
# are callables that declare response bounds (FR-5.2).
EntryKind = Literal["agent_skill", "mcp_tool", "function", "model", "subgraph", "dagster_asset"]
CALLABLE_KINDS: frozenset[str] = frozenset(
    {"mcp_tool", "function", "model", "subgraph", "dagster_asset"}
)

# kind -> ARD `type` (an IANA media type). Standard types where one exists, vendor types otherwise.
MEDIA_TYPE_BY_KIND: dict[str, str] = {
    "agent_skill": "application/ai-skill+md",
    "mcp_tool": "application/mcp-server-card+json",
    "function": "application/vnd.dreamai.graphwright.function+json",
    "model": "application/vnd.dreamai.graphwright.model+json",
    "subgraph": "application/vnd.dreamai.graphwright.subgraph+json",
    "dagster_asset": "application/vnd.dreamai.graphwright.dagster-asset+json",
}


class _ArdModel(BaseModel):
    """Base for the ARD-shaped models: camelCase on the wire, snake_case in Python."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class Attestation(_ArdModel):
    """An ARD trust attestation: a category, where the document lives, and an optional digest."""

    type: str
    uri: str
    digest: Optional[str] = None


class TrustManifest(_ArdModel):
    """ARD trust manifest: a verifiable identity plus attestations. Attestations may be empty for a
    new entry (GraphWright's control-level trust filter rejects an under-attested entry later)."""

    identity: str
    identity_type: str
    attestations: list[Attestation] = Field(default_factory=list)


class ArdEnvelope(_ArdModel):
    """The ARD v0.9 publishable face of a capability. Only ARD fields; no internal governance."""

    identifier: str  # domain-anchored URN: urn:air:<publisher>:<namespace>:<name>
    display_name: str
    type: str  # IANA media type; must match MEDIA_TYPE_BY_KIND[kind] (checked on the record)
    representative_queries: list[str] = Field(min_length=2, max_length=5)
    trust_manifest: TrustManifest
    description: Optional[str] = None
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_identifier_is_urn(self) -> ArdEnvelope:
        if not self.identifier.startswith("urn:air:"):
            raise ValueError(
                "identifier must be a domain-anchored ARD URN "
                f"(urn:air:<publisher>:<namespace>:<name>), got {self.identifier!r}"
            )
        return self


class ResponseBounds(_ArdModel):
    """Internal-only response bounds a callable entry declares (caps tool responses ~25,000 tokens)."""

    max_tokens: int = 25_000
    supports_pagination: bool = False
    supports_filtering: bool = False


class GovernanceBlock(_ArdModel):
    """Internal-only governance the ARD envelope does not carry."""

    owner: str
    control_level_min: Literal["high", "moderate", "low"] = "moderate"


class RegistryEntry(_ArdModel):
    """A governed registry record: the ARD envelope plus internal-only governance and eval fields.

    `kind` is the internal discriminator; the envelope's ARD `type` media-type is the interoperable
    face, kept consistent with `kind` here.
    """

    kind: EntryKind
    envelope: ArdEnvelope
    golden_eval_ref: Optional[str] = None
    response_bounds: Optional[ResponseBounds] = None  # required for callable kinds
    requires: list[str] = Field(default_factory=list)  # closure; agent_skill only
    governance: GovernanceBlock

    @model_validator(mode="after")
    def _check_record_invariants(self) -> RegistryEntry:
        if self.envelope.type != MEDIA_TYPE_BY_KIND[self.kind]:
            raise ValueError(
                f"envelope type {self.envelope.type!r} does not match kind {self.kind!r} "
                f"(expected {MEDIA_TYPE_BY_KIND[self.kind]!r})"
            )
        if self.kind in CALLABLE_KINDS:
            if self.response_bounds is None:
                raise ValueError(f"callable kind {self.kind!r} requires response_bounds")
        elif self.response_bounds is not None:
            raise ValueError(f"kind {self.kind!r} is not callable and carries no response_bounds")
        if self.requires and self.kind != "agent_skill":
            raise ValueError(f"a requires closure is only valid on an agent_skill, not {self.kind!r}")
        return self
