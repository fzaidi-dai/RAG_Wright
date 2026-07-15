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

import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

# The ARD identifier scheme (ARD v0.9 section 4.2.1, https://github.com/ards-project/ard-spec):
# urn:air:<publisher>:<namespace>:<name>. Our vertical's publisher is the FQDN dreamai.io and the
# namespace is rag_wright, so every capability we author carries `RAG_URN_PREFIX + <slug>`. The
# generic schema mirror (ArdEnvelope, below) validates only the `urn:air:` prefix, matching
# GraphWright's schema which accepts any publisher; the exact-publisher check is applied where we
# author/write OUR manifests (write_manifest, and the emitter in registry.py).
URN_PUBLISHER = "dreamai.io"
URN_NAMESPACE = "rag_wright"
RAG_URN_PREFIX = f"urn:air:{URN_PUBLISHER}:{URN_NAMESPACE}:"

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


class SkillRuntime(_ArdModel):
    """An agent skill's intrinsic runtime requirements, so the compiler can hydrate an interpreter node
    without fabricating anything (GraphWright RegistryEntry; mirrored per ADR-0003). Internal-only,
    never the ARD envelope; `agent_skill` only (validated like `requires`); absent when a skill has no
    special runtime needs. Intrinsic requirements only — the model is deployment config and stays out."""

    needs_interpreter: bool = False  # the skill runs code in the interpreter
    rlm: bool = False  # the auditable RLM-pattern marker (FR-1.4, FR-4.10)
    granted_subagents: list[str] = Field(default_factory=list)  # sub-agent names it may dispatch to
    # This skill's execution requires the interpreter's code-driven fan-out (task() dispatch) to be
    # triggered. A TYPED flag, NOT the trigger phrasing: the exact word (langchain-quickjs's "workflow")
    # is owned by GraphWright's runtime, which translates this flag into whatever the installed
    # interpreter version expects — so the magic word never enters the wire contract (ADR-0017). Implies
    # `needs_interpreter` (dynamic dispatch is exposed by the interpreter), but kept a distinct field:
    # a future interpreter-using skill might not need dynamic-dispatch triggering.
    requires_dynamic_dispatch: bool = False

    @model_validator(mode="after")
    def _dispatch_implies_interpreter(self) -> SkillRuntime:
        if self.requires_dynamic_dispatch and not self.needs_interpreter:
            raise ValueError(
                "requires_dynamic_dispatch implies needs_interpreter (task() fan-out is exposed by the "
                "interpreter); set needs_interpreter=True"
            )
        return self


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
    skill_runtime: Optional[SkillRuntime] = None  # intrinsic runtime; agent_skill only (like requires)
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
        if self.skill_runtime is not None and self.kind != "agent_skill":
            raise ValueError(f"skill_runtime is only valid on an agent_skill, not {self.kind!r}")
        return self


# --- the shared ARD registry root (GraphWright docs/authoring/registry-root.md) ------------------
#
# One shared registry root, config-addressed by the ARD_REGISTRY_ROOT env var that GraphWright's
# RegistryStore also reads. RAG_Wright writes its manifests here; the compiler discovers them from
# the same directory. We never create a second or project-local root and never hardcode a path.

_DEFAULT_REGISTRY_ROOT = Path("~/.air/registry")  # `.air` mirrors the urn:air: scheme (registry-root.md)


def registry_root() -> Path:
    """Resolve the shared ARD registry root from `ARD_REGISTRY_ROOT`, creating it if absent.

    Defaults to `~/.air/registry` when the env var is unset (same default as GraphWright). A leading
    `~` is expanded; no absolute path is baked into source. An empty root is a valid, zero-entry
    catalog, so this always returns a usable directory.
    """
    raw = os.environ.get("ARD_REGISTRY_ROOT", "").strip()
    root = Path(raw).expanduser() if raw else _DEFAULT_REGISTRY_ROOT.expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_manifest(entry: RegistryEntry, *, root: Optional[Path] = None) -> Path:
    """Write an authored manifest to `<root>/<slug>.json`, the flat top-level ARD layout.

    The identifier must be one of our own URNs (`urn:air:dreamai.io:rag_wright:<slug>`) — this is the
    exact-publisher check for what we author, distinct from `ArdEnvelope`'s generic `urn:air:` schema
    mirror. The file is named after the URN's final segment; identity is the `identifier` field
    inside. Serialized ARD-shaped (camelCase on the wire) per ADR-0005.
    """
    identifier = entry.envelope.identifier
    if not identifier.startswith(RAG_URN_PREFIX):
        raise ValueError(
            f"manifest identifier {identifier!r} is not one of ours; expected it to start with "
            f"{RAG_URN_PREFIX!r} (urn:air:dreamai.io:rag_wright:<slug>)"
        )
    slug = identifier.rsplit(":", 1)[-1]
    target = (root if root is not None else registry_root()) / f"{slug}.json"
    target.write_text(entry.model_dump_json(by_alias=True, indent=2), encoding="utf-8")
    return target
