"""Entity disambiguation (T23b, FR-C.7): normalize / reject / cluster into human-verifiable proposals.

Hermetic tests over the extracted `EntityMention` stream: the T10-surfaced normalization and rejection
misses (regression fixtures per ADR-0004), the conservative-merge bias (near-duplicates flagged, never
merged; distinct entities neither merged nor flagged), provenance + confidence on clusters, the deferred
coreference seam, cluster precision/recall on a small labeled fixture, and registration.
"""

from __future__ import annotations

from rag_wright.capabilities.disambiguation import (
    CoreferenceResolver,
    DisambiguationResult,
    MentionCluster,
    disambiguate,
    register_entity_disambiguation,
)
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.extraction import EntityMention, ExtractionResult
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.provenance import ConfidenceTag

_ORG = "Organization"
_X = ConfidenceTag.EXTRACTED


def _result(idx: int, *mentions: tuple[str, str, ConfidenceTag]) -> ExtractionResult:
    return ExtractionResult(
        chunk_id=ChunkId.of("docA", idx, f"chunk {idx}"),
        entity_mentions=[EntityMention(text=t, entity_type=et, confidence=c) for t, et, c in mentions],
    )


def _cluster_for(result: DisambiguationResult, token: str) -> MentionCluster:
    return next(c for c in result.clusters if token in c.key)


# --- normalization / merge regression (ADR-0004 N1-N5) -------------------------------------------


def test_legal_suffix_and_stray_space_variants_collapse_to_one_cluster():
    result = disambiguate([
        _result(0, ("Bank of America", _ORG, _X), ("Bank of America, N.A.", _ORG, _X)),
        _result(1, ("Bank of America, N. A", _ORG, _X)),
    ])
    boa = _cluster_for(result, "bank of america")
    assert len(result.clusters) == 1
    assert set(boa.variants) == {"Bank of America", "Bank of America, N.A.", "Bank of America, N. A"}
    assert boa.chunk_ids == sorted({ChunkId.of("docA", 0, "chunk 0").value,
                                    ChunkId.of("docA", 1, "chunk 1").value})  # provenance from both chunks


def test_possessive_apostrophe_variants_collapse():
    result = disambiguate([_result(0, ("Stremick's", _ORG, _X), ("Stremicks", _ORG, _X))])
    assert len(result.clusters) == 1  # Stremick's == Stremicks (T10-surfaced miss)


# --- rejection regression (ADR-0004 R1-R5) -------------------------------------------------------


def test_rejects_placeholders_role_artifacts_generics_and_alias_only_mentions():
    from rag_wright.packs.contracts.ontology.loader import load_entity_rules

    result = disambiguate([_result(
        0,
        ("<<enter Company Name>>", _ORG, _X),          # R1 placeholder
        ('(collectively the "Company")', _ORG, _X),    # R2 role artifact (a CONTRACT rule: the pack's EntityRules)
        ("Services", _ORG, _X),                        # R3 bare generic token
        ("formerly known as Tradeum, Inc.", _ORG, _X),  # R4 alias-only -> strips to empty
        ("Acme Corporation", _ORG, _X),                # a real entity survives
    )], entity_rules=load_entity_rules())  # PS-R5b: a domain's role words/phrases come from its pack
    assert [c.representative for c in result.clusters] == ["Acme Corporation"]
    assert len(result.rejected) == 4  # the four non-entities dropped, never reach T24


def test_alias_prefix_recovers_the_trailing_real_name():
    # "Acme Inc. d/b/a Brand" keeps the legal name before the alias marker
    result = disambiguate([_result(0, ("Acme Inc. d/b/a SuperBrand", _ORG, _X))])
    assert len(result.clusters) == 1
    assert _cluster_for(result, "acme").key == "acme"  # d/b/a alias stripped


# --- clustering: conservative-merge bias (ADR-0004 C3/C4) ----------------------------------------


def test_shared_token_near_duplicates_are_flagged_not_merged():
    result = disambiguate([_result(
        0,
        ("ScanSource", _ORG, _X), ("ScanSource Latin America", _ORG, _X),
        ("Armstrong Flooring", _ORG, _X), ("Armstrong Hardwood Flooring", _ORG, _X),
    )])
    keys = {c.key for c in result.clusters}
    assert len(result.clusters) == 4  # never auto-merged (four distinct clusters)
    scansource = _cluster_for(result, "scansource")
    assert "scansource latin america" in scansource.ambiguous_with  # flagged for a human
    armstrong = next(c for c in result.clusters if c.key == "armstrong flooring")
    assert "armstrong hardwood flooring" in armstrong.ambiguous_with
    assert keys == {"scansource", "scansource latin america", "armstrong flooring",
                    "armstrong hardwood flooring"}


def test_distinct_entities_are_neither_merged_nor_flagged():
    result = disambiguate([_result(0, ("Bank of America", _ORG, _X), ("Bank of England", _ORG, _X))])
    assert len(result.clusters) == 2  # distinct
    assert all(c.ambiguous_with == [] for c in result.clusters)  # not flagged (shared stopwords ignored)


# --- provenance + confidence (FR-S.4) ------------------------------------------------------------


def test_cluster_carries_weakest_confidence_over_its_mentions():
    result = disambiguate([_result(
        0,
        ("Acme Corp", _ORG, ConfidenceTag.EXTRACTED),
        ("Acme Corporation", _ORG, ConfidenceTag.AMBIGUOUS),
    )])
    assert result.clusters[0].confidence is ConfidenceTag.AMBIGUOUS  # weakest wins


# --- deferred coreference seam (mirrors T5) ------------------------------------------------------


class _MergeAllResolver:
    """A stub coreference resolver: collapses the cluster set (proves the seam is load-bearing)."""

    name = "merge_all_stub"

    def resolve(self, clusters: list[MentionCluster]) -> list[MentionCluster]:
        return clusters[:1] if clusters else clusters


def test_coreference_seam_is_load_bearing():
    assert isinstance(_MergeAllResolver(), CoreferenceResolver)  # structural conformance
    mentions = [_result(0, ("Acme Corp", _ORG, _X), ("Beta LLC", _ORG, _X))]

    without = disambiguate(mentions)
    withres = disambiguate(mentions, coreference_resolvers=[_MergeAllResolver()])

    assert len(without.clusters) == 2
    assert len(withres.clusters) == 1  # the bound resolver rewrote the cluster set


# --- cluster precision / recall on a small labeled fixture ---------------------------------------


def test_cluster_precision_and_recall_on_a_labeled_fixture():
    mentions = [
        ("Acme Corporation", _ORG, _X), ("Acme Corp.", _ORG, _X), ("ACME CORPORATION", _ORG, _X),
        ("Beta Distribution LLC", _ORG, _X), ("Beta Distribution, L.L.C.", _ORG, _X),
        ("Gamma Holdings Inc", _ORG, _X),
        ("Services", _ORG, _X),  # rejected, not a cluster
    ]
    gold = [
        {"Acme Corporation", "Acme Corp.", "ACME CORPORATION"},
        {"Beta Distribution LLC", "Beta Distribution, L.L.C."},
        {"Gamma Holdings Inc"},
    ]
    result = disambiguate([_result(0, *mentions)])

    produced = [set(c.variants) for c in result.clusters]
    tp = sum(1 for g in gold if g in produced)
    precision = tp / len(produced)
    recall = tp / len(gold)
    assert precision == 1.0 and recall == 1.0  # clean fixture: exact clustering, generic rejected


def test_registers_under_fr_c_7():
    registry = CapabilityRegistry()
    register_entity_disambiguation(registry)
    reg = registry.get("entity_disambiguation")
    assert reg.name == "entity_disambiguation"
    assert reg.contract is DisambiguationResult
    assert reg.kind == "function"
