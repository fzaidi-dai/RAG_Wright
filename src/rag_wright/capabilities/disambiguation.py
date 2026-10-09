"""Entity disambiguation and canonicalization (FR-C.7, T23b): normalize, reject, cluster.

The stage between recognition (T23) and closed-world linking (T24). It takes the raw `EntityMention`
stream from graph extraction and turns it into canonical mention clusters, each a proposal a human
verifies before T24 links it to an EDGAR CIK. Three deterministic stages (ADR-0004): normalize surface
variants to one key, reject non-entities, cluster survivors by key. Two things this capability adds on
top of the T10 rules it reuses (`corpus.canonicalize`): it carries `chunk_id` provenance and confidence
onto each cluster, and it applies the conservative-merge bias (ADR-0004 C3/C4) by FLAGGING ambiguous
near-duplicates (parent/subsidiary or shared-token pairs) for a human decision rather than merging them
— a false merge is a silent, invisible error, worse than a false split the human can see.

Full coreference (pronouns, definite descriptions like "the Company" bound to a party) is deferred
behind the `CoreferenceResolver` seam, the same discipline as OpenIE at T5: a stable interface
additional resolvers bind later, with nothing here reopened.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.extraction import ExtractionResult
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.corpus.canonicalize import EntityRules, is_entity, normalize_entity_name

# Confidence weakness rank: a cluster carries the WEAKEST tag among its mentions (a cluster is only as
# trustworthy as its least-certain member).
_CONFIDENCE_RANK = {ConfidenceTag.EXTRACTED: 0, ConfidenceTag.INFERRED: 1, ConfidenceTag.AMBIGUOUS: 2}
_RANK_TO_CONFIDENCE = {rank: tag for tag, rank in _CONFIDENCE_RANK.items()}

# Stopwords dropped only for the near-duplicate token comparison (not for the clustering key), so
# "Bank of America" vs "Bank of England" are not flagged on the shared "bank"/"of".
_NEAR_DUP_STOPWORDS = frozenset({"of", "the", "and", "for", "a", "an"})


class MentionCluster(BaseModel):
    """One candidate real-world entity: a human-verifiable proposal, never an auto-committed merge.

    Carries `chunk_id` provenance and confidence (FR-S.4). `ambiguous_with` lists the keys of near-
    duplicate clusters flagged for a human to decide off the contract language (ADR-0004 C4); it is the
    conservative-merge bias made visible — these are NOT merged automatically.
    """

    key: str  # the normalized clustering key
    representative: str  # the longest surface form, for display + registry matching
    variants: list[str]
    entity_type: str  # opaque domain entity type (DD-5); carried through from the mention
    confidence: ConfidenceTag  # weakest over the clustered mentions
    chunk_ids: list[str]  # provenance: the chunks the mentions came from
    ambiguous_with: list[str] = []  # keys of flagged near-duplicate clusters (human decides)


class DisambiguationResult(BaseModel):
    """The capability's output: canonical clusters (proposals) plus the rejected surface forms (audit)."""

    clusters: list[MentionCluster]
    rejected: list[str]


@runtime_checkable
class CoreferenceResolver(Protocol):
    """The deferred full-coreference seam (ADR-0004): additional resolvers (pronoun / definite-
    description coreference) bind this later and rewrite the cluster set; nothing here changes. A
    resolver returns the (possibly merged) clusters. Default: no resolvers — clustering is by surface
    form only."""

    name: str

    def resolve(self, clusters: list[MentionCluster]) -> list[MentionCluster]: ...


def _weakest(confidences: list[ConfidenceTag]) -> ConfidenceTag:
    return _RANK_TO_CONFIDENCE[max(_CONFIDENCE_RANK[c] for c in confidences)]


def _near_duplicate(key_a: str, key_b: str) -> bool:
    """Two cluster keys are ambiguous near-duplicates (flag, do not merge): one key's significant
    tokens are a proper subset of the other's (parent/subsidiary, extra-qualifier), or they share a
    first token and overlap substantially (shared-token pair). Biased to over-flag (human decides)."""
    tokens_a = [t for t in key_a.split() if t not in _NEAR_DUP_STOPWORDS]
    tokens_b = [t for t in key_b.split() if t not in _NEAR_DUP_STOPWORDS]
    set_a, set_b = set(tokens_a), set(tokens_b)
    if not set_a or not set_b or set_a == set_b:
        return False
    if set_a < set_b or set_b < set_a:  # proper token subset
        return True
    if tokens_a[0] == tokens_b[0]:  # shared first token + substantial overlap
        return len(set_a & set_b) / len(set_a | set_b) >= 0.5
    return False


def disambiguate(
    results: Sequence[ExtractionResult],
    *,
    coreference_resolvers: Sequence[CoreferenceResolver] = (),
    entity_rules: Optional[EntityRules] = None,
) -> DisambiguationResult:
    """Normalize, reject, and cluster the extracted entity mentions into human-verifiable proposals.

    Mentions are collected across the extraction results (their `chunk_id` is provenance), non-entities
    are rejected (never reach T24), survivors are clustered by (normalized key, type), each cluster
    carries its provenance and weakest confidence, deferred coreference resolvers (if any) rewrite the
    clusters, and ambiguous near-duplicates are flagged for human decision (never merged). `entity_rules` are the
    domain's role words and phrases that are not entities (PS-R5b; declared in its pack).
    """
    groups: dict[tuple[str, str], dict] = {}  # (normalized key, entity_type) -> cluster accumulator
    rejected: list[str] = []
    for result in results:
        chunk_id = result.chunk_id.value
        for mention in result.entity_mentions:
            if not is_entity(mention.text, entity_rules):
                rejected.append(mention.text)
                continue
            key = normalize_entity_name(mention.text)
            group = groups.setdefault(
                (key, mention.entity_type),
                {"variants": [], "confidences": [], "chunk_ids": set()},
            )
            if mention.text not in group["variants"]:
                group["variants"].append(mention.text)
            group["confidences"].append(mention.confidence)
            group["chunk_ids"].add(chunk_id)

    clusters = [
        MentionCluster(
            key=key,
            representative=max(group["variants"], key=len),
            variants=sorted(group["variants"]),
            entity_type=entity_type,
            confidence=_weakest(group["confidences"]),
            chunk_ids=sorted(group["chunk_ids"]),
        )
        for (key, entity_type), group in groups.items()
    ]

    for resolver in coreference_resolvers:  # deferred seam (default: none)
        clusters = resolver.resolve(clusters)

    _flag_near_duplicates(clusters)
    clusters.sort(key=lambda c: c.key)
    return DisambiguationResult(clusters=clusters, rejected=rejected)


def _flag_near_duplicates(clusters: list[MentionCluster]) -> None:
    """Set each cluster's `ambiguous_with` (symmetric) for same-type near-duplicate pairs (ADR-0004 C4)."""
    for i, a in enumerate(clusters):
        for b in clusters[i + 1 :]:
            if a.entity_type == b.entity_type and _near_duplicate(a.key, b.key):
                a.ambiguous_with.append(b.key)
                b.ambiguous_with.append(a.key)
    for cluster in clusters:
        cluster.ambiguous_with.sort()


def register_entity_disambiguation(registry: CapabilityRegistry) -> None:
    """Register under FR-C.7 (`entity_disambiguation`, an in-process `function`)."""
    registry.register(
        "entity_disambiguation",
        contract=DisambiguationResult,
        kind="function",
        display_name="Entity disambiguation (normalize / reject / cluster)",
    )
