"""EP-RT-3 (ADR-0117): the capability-authoring contract, pinned as a conformance guardrail.

These invariants are exactly what `.claude/skills/authoring-a-capability/SKILL.md` teaches, so the skill cannot
silently drift from the code: a canonical slug is either ARD-published (has a `CapabilityManifest`) or reserved/
internal (no manifest); no manifest exists for a non-canonical slug; every manifest kind is a real ARD kind; and
every wired invoker adapter is a canonical slug whose manifest declares the matching kind (the authoring-level
counterpart of the invoker's own drift guard)."""
from __future__ import annotations

from rag_wright.api import invoke as _invoke
from rag_wright.capabilities.ard import MEDIA_TYPE_BY_KIND
from rag_wright.capabilities.manifests import MANIFEST_SPECS
from rag_wright.capabilities.registry import CANONICAL_CAPABILITY_SLUGS

# Canonical slugs that are deliberately NOT ARD-published (reserved / internal-only; no CapabilityManifest).
# Both are FR "foundation derivation" slugs -- a reserved name with no standalone invocation: okf_compile builds
# the OKF bundle (FR-K.1-K.4, experimental ADR-0022), ontology_registry_derivation is the FR-C.8 placeholder with
# no implementation yet. Pinning this set means adding a NEW canonical slug without its manifest fails here -- the
# exact authoring mistake the skill warns about. (EP-RT-1b published span_relevance_judgment + okf_navigate.)
_RESERVED_WITHOUT_MANIFEST = {
    "okf_compile",
    "ontology_registry_derivation",
    # EP-CORE-1b-iii: internal ingestion-pipeline steps, composed by direct import (not agent-facing ARD
    # caps), de-registered from the reference pack after DD-3/4/5 removed their domain-vocab coupling.
    "graph_extraction",
    "entity_disambiguation",
    "entity_resolution",
}


def test_no_manifest_authored_for_a_non_canonical_slug():
    assert set(MANIFEST_SPECS) <= set(CANONICAL_CAPABILITY_SLUGS), (
        f"manifest(s) authored under non-canonical slug(s): {sorted(set(MANIFEST_SPECS) - set(CANONICAL_CAPABILITY_SLUGS))}")


def test_reserved_without_manifest_is_the_known_allowlist():
    """A canonical slug is ARD-published (manifest) OR reserved/internal (no manifest) -- and the reserved set is
    fixed, so a newly-added slug without its manifest surfaces here."""
    unpublished = set(CANONICAL_CAPABILITY_SLUGS) - set(MANIFEST_SPECS)
    assert unpublished == _RESERVED_WITHOUT_MANIFEST, (
        f"unexpected unpublished slug(s) {sorted(unpublished - _RESERVED_WITHOUT_MANIFEST)} "
        f"(author a manifest, or add to the reserved allowlist); "
        f"stale reserved entries {sorted(_RESERVED_WITHOUT_MANIFEST - unpublished)}")


def test_every_manifest_declares_a_real_ard_kind():
    for slug, spec in MANIFEST_SPECS.items():
        assert spec.kind in MEDIA_TYPE_BY_KIND, f"{slug}: kind {spec.kind!r} is not an ARD kind"


def test_every_invoker_adapter_is_a_canonical_slug_with_matching_manifest_kind():
    """Authoring-level drift guard: an adapter wired in the invoker must name a published capability whose manifest
    kind matches the invoker that dispatches it."""
    for name, kind in _invoke._invocable_names().items():
        assert name in CANONICAL_CAPABILITY_SLUGS, f"adapter {name!r} is not a canonical slug"
        assert name in MANIFEST_SPECS, f"adapter {name!r} has no ARD manifest"
        assert MANIFEST_SPECS[name].kind == kind, (
            f"adapter {name!r} dispatched as {kind!r} but its manifest declares {MANIFEST_SPECS[name].kind!r}")
