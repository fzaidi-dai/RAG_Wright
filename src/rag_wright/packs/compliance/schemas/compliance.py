"""Compliance-module contracts (CC-1, roadmap §13.1): `Requirement` and `Claim`.

The compliance check is two-sided retrieval + entailment. The REGULATORY side ingests into `Requirement`
nodes (a single deontic rule); the SUBJECT side extracts `Claim` nodes (a checkable ad assertion). CC-6's
applicability match (claim -> applicable requirements) reuses the Leg-B retrieval router, so a `Constraint`
here is exactly the `(dimension, value)` pair the router matches on (`applicability_scope` <-> a claim's
scope). Provenance + confidence on everything (FR-S.4); no claim without a citation (FR-Q.6).

The vocab is the thin AUTHORED advertising layer (closed `DeonticType`/`ClaimType`/`Severity`), grounded on
the public deontic backbone (ODRL/LKIF) in the sibling `compliance_bridge.ttl`. These contracts double as the
registered capability contracts (`requirement_extraction`, `claim_extraction`, ...).
"""

from __future__ import annotations

import hashlib
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from rag_wright.pack_sdk import canonical_source_doc_id
from rag_wright.pack_sdk import ConfidenceTag

# the sibling ontology (CC-1): public deontic backbone (ODRL/LKIF) + PROV + the thin authored ad vocab
BRIDGE_TTL_PATH = Path(__file__).resolve().parent.parent / "ontology" / "compliance_bridge.ttl"


# ADR-0066 P3b: the closed vocabularies below (DeonticType / ClaimType / Severity / RuleScope / Verdict) are
# AUTHORITATIVE in compliance_bridge.ttl (owl:oneOf). To change one, edit the ttl -- these enums are drift-locked
# to it by tests/ontology/test_compliance_ontology_authoritative.py.
class DeonticType(str, Enum):
    """The rule's deontic force (LKIF/ODRL closed vocab): what it obliges, forbids, or permits."""

    OBLIGATION = "obligation"
    PROHIBITION = "prohibition"
    PERMISSION = "permission"


class ClaimType(str, Enum):
    """The kind of checkable assertion an ad makes (closed authored vocab, roadmap §13.1)."""

    EFFICACY = "efficacy"
    COMPARATIVE = "comparative"
    PRICING = "pricing"
    HEALTH = "health"
    ENVIRONMENTAL = "environmental"
    ENDORSEMENT = "endorsement"
    PERFORMANCE = "performance"
    GUARANTEE = "guarantee"


class Severity(str, Enum):
    """Optional severity of a requirement (drives triage, not the verdict)."""

    LOW = "low"
    MEDIUM = "med"
    HIGH = "high"


class Constraint(BaseModel):
    """One applicability condition, a `(dimension, value)` pair. The shape is deliberately identical to the
    retrieval router's constraint tuple (`property_boosted_retrieval` / `query_function_classifier`), so
    CC-6's claim -> applicable-requirement match reuses Leg-B rather than a new matcher."""

    model_config = ConfigDict(frozen=True)

    dimension: str
    value: str

    def as_tuple(self) -> tuple[str, str]:
        return (self.dimension, self.value)


def _nonblank(v: str, what: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{what} must be a non-empty string")
    return v.strip()


def _content_id(source: str, section: str, text: str) -> str:
    """`<canonical-source>:<section>:<hash16>` -- deterministic content-hash id (RAC-1), the clause_id
    scheme generalized (source-unit + locator + content hash), so an unchanged rule/claim keeps its id."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{canonical_source_doc_id(source)}:{section}:{digest}"


class Requirement(BaseModel):
    """A single regulatory rule extracted from the requirements corpus (roadmap §13.1).

    `requirement_id = <source_reg>:<section>:<hash>`. Carries its `citation` (always cited, FR-Q.6) and a
    `confidence` tag (graph-derived fact, FR-S.4). `applicability_scope` is matched against a claim's scope.
    """

    requirement_id: str
    source: str
    citation: str  # section / paragraph -- the human-readable provenance, always present
    deontic_type: DeonticType
    actor: str  # who it binds: advertiser, endorser, ... (open, matched against a claim's actor)
    applicability_scope: list[Constraint] = []
    requirement_text: str
    evidence_standard: str | None = None
    trigger_condition: str | None = None
    severity: Severity | None = None
    # issue 0043: page provenance for the POLICY side, in the same shape span provenance uses (issue 0032), so one
    # product code path serves both sides of a finding. `citation` stays the always-present human-readable
    # provenance; these are additive. A requirement is bound to a policy SECTION, so `pages` are that section's
    # source page(s) (from the parse's per-item provenance -- present on scans, where a text search would fail
    # silently). `bbox` is best-effort (`(l, t, r, b)`) and usually None for a multi-item section; never fabricated.
    pages: list[int] = []
    bbox: tuple[float, float, float, float] | None = None
    confidence: ConfidenceTag = ConfidenceTag.EXTRACTED
    defenses: list[str] = []  # DEON-9 (query-time only, never persisted): same-source PERMISSIONS linked as
    # carve-outs/exceptions that may EXCUSE this O/F rule -- passed to the judge as structured context so a
    # legitimate exception is not a false violation (ADR-0044 pattern, requirement side).

    @field_validator("requirement_id", "source", "citation", "requirement_text")
    @classmethod
    def _required_nonblank(cls, v: str, info) -> str:
        return _nonblank(v, info.field_name)

    @staticmethod
    def make_id(source: str, section: str, requirement_text: str) -> str:
        return _content_id(source, section, requirement_text)


class CheckableFact(BaseModel):
    """COMP-VERDICT-GENERIC: the DOMAIN-AGNOSTIC subject-fact the compliance verdict core consumes -- a checkable
    assertion (any domain) with its provenance. The verdict machinery (semantic retrieval + LLM judge + finding /
    report) needs only THIS (an id + text + provenance); advertising `Claim` is a SPECIALIZATION that adds typed
    claim fields for structured routing. A NEW compliance domain either uses a bare `CheckableFact` (generic
    verdict) or subclasses this with its own enrichment -- WITHOUT touching the base or other domains.

    `fact_id = <source_doc>:<index>:<hash>`; `(source_doc, doc_start, doc_end, assertion_text)` is the span
    provenance (cited, FR-Q.6); `confidence` is the graph-derived tag (FR-S.4). NB: never persisted -- a
    query-time object only (the KG stores `Requirement`), so this shape is a pure query-side/contract concern."""

    fact_id: str
    source_doc: str
    assertion_text: str  # the checkable statement text (domain-neutral: an "assertion" is any checkable claim/fact)
    # issue 0044: JUDGE-ONLY structured signals (e.g. DEON-8's ad-level disclosure/evidence union) rendered as
    # document context FOR THE JUDGE but NOT part of the citation -- so engine scaffolding never surfaces as a
    # quote from the user's document. The judge appends this; `assemble_finding`'s citation uses `assertion_text`
    # only. Empty for a plain fact (domain-neutral: the generic path carries none).
    document_signals: str = ""
    # issue 0044: whether `assertion_text` is one VERBATIM span from the document, or ASSEMBLED evidence (the
    # obligation path joins the top-N relevant spans with "\n\n"). Copied onto the finding so a consumer can tell
    # a verbatim quote from a synthesised excerpt WITHOUT parsing prose, and render/attribute it accordingly.
    citation_kind: Literal["verbatim", "assembled"] = "verbatim"
    section: str | None = None  # UNIFY-A: the section/heading locator this fact came from (e.g. "4.2"); the
    # finding cites "doc § {section}: {assertion}" when set. Additive/optional: None -> the old "doc: assertion".
    element_kind: str | None = None  # SEG-1: docling structural label of the source element (paragraph /
    # list_item / section_header / ...); selects the within-section marker in `locator()`.
    element_ordinal: int | None = None  # SEG-1: the element's within-section ordinal (the ¶ / bullet number).
    scope: list["Constraint"] = []  # DEON-5: the assertion's inferred applicability constraints (e.g.
    # Constraint("actor", "endorser")); dimension-agnostic, matched against a requirement's applicability_scope by
    # the generic `constraint_applies` router (DEON-6), and aggregated to the document's SubjectScope (DEON-7).
    doc_start: int | None = None  # span provenance: char offsets in source_doc (optional)
    doc_end: int | None = None
    confidence: ConfidenceTag = ConfidenceTag.EXTRACTED

    def locator(self) -> str:
        """SEG-1: the human structural locator -- `§ {section}` plus a within-section element marker when present
        (`¶N` for a paragraph, `· bullet N` for a list item). Empty string when the fact has no section (e.g. a
        structureless paste), so the finding cites just `doc: {assertion}`. The citation is built from this."""
        if not (self.section and self.section.strip()):
            return ""
        loc = f"§ {self.section}"
        if self.element_ordinal is not None:
            if self.element_kind == "list_item":
                loc += f" · bullet {self.element_ordinal}"
            else:  # paragraph / text / default
                loc += f" ¶{self.element_ordinal}"
        return loc

    @field_validator("fact_id", "source_doc", "assertion_text")
    @classmethod
    def _required_nonblank(cls, v: str, info) -> str:
        return _nonblank(v, info.field_name)

    @model_validator(mode="after")
    def _ordered_offsets(self) -> CheckableFact:
        if self.doc_start is not None and self.doc_end is not None and self.doc_start >= self.doc_end:
            raise ValueError(f"doc_start ({self.doc_start}) must be < doc_end ({self.doc_end})")
        return self

    @staticmethod
    def make_id(source_doc: str, index: int, text: str) -> str:
        return _content_id(source_doc, str(index), text)


class Claim(CheckableFact):
    """An ADVERTISING checkable element (roadmap §13.1) -- a `CheckableFact` SPECIALIZED with the typed claim
    fields the advertising compliance path uses for structured routing (claim_type) + disclosure/substantiation
    judging. Inherits id/text/provenance + validators + `make_id` from `CheckableFact`."""

    claim_type: ClaimType
    actor: str | None = None
    subject_product: str | None = None
    quantitative_value: str | None = None
    disclosures_present: list[str] = []
    evidence_referenced: bool = False
    medium: str | None = None


class RuleScope(str, Enum):
    """How a requirement is narrowed at check time (CC-8a, the ontology routing tag; `compliance_bridge.ttl`
    cmp:RuleScope). CONTENT rules (substantiation / claim-specific) narrow by semantic similarity to the claim;
    CONTEXT rules (disclosure / material connection -- apply to ANY claim in an endorsement regardless of its
    content) are ALWAYS included, never left to similarity. See [[ontology-lever-vs-extraction-lever]]."""

    CONTENT = "content"
    CONTEXT = "context"


class Verdict(str, Enum):
    """The compliance judgment output (CC-4, §13.2). Closed vocab; matches `compliance_bridge.ttl` cmp:Verdict.
    `needs_review` is the conservative default under uncertainty (never a silent compliant/violation)."""

    COMPLIANT = "compliant"
    VIOLATION = "violation"
    NEEDS_REVIEW = "needs_review"


class ComplianceFinding(BaseModel):
    """One `(claim, requirement)` judgment (CC-4, §13.2): a verdict + rationale + BOTH-SIDED citation +
    confidence. The citations are the trust product -- an auditor sees the exact ad span AND the exact reg
    clause. Every `violation`/`needs_review` is human-gated (`needs_human_review`); false-negatives are
    liability, so uncertainty never silently clears."""

    claim_id: str
    requirement_id: str
    verdict: Verdict
    rationale: str = ""
    citation_claim: str  # the subject span text (provenance, cited -- FR-Q.6)
    citation_requirement: str  # the reg clause / section (provenance, cited)
    # issue 0044: is `citation_claim` one VERBATIM span from the document, or ASSEMBLED evidence (the obligation
    # path cites the top-N relevant spans joined by "\n\n")? A consumer renders assembled evidence differently
    # instead of quoting it as the user's exact words. Default "verbatim" -> unchanged for every existing path.
    citation_claim_kind: Literal["verbatim", "assembled"] = "verbatim"
    confidence: float = 0.0

    @field_validator("confidence")
    @classmethod
    def _confidence_in_unit(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {v}")
        return v

    @property
    def needs_human_review(self) -> bool:
        """Every violation requires human confirmation before it leaves the tool; needs_review always does.
        A `compliant` finding does not gate (§13.2)."""
        return self.verdict in (Verdict.VIOLATION, Verdict.NEEDS_REVIEW)


_AD_VIOLATION_THRESHOLD = 2  # a lone violation finding among many rules escalates, not hard-flags (RG-5 aggregation)


class RequirementLocation(BaseModel):
    """Where a curated requirement sits in its policy document, for a citation preview (EP-REF-1b): the
    requirement id, its citation label, the source page number(s), an optional `[l, t, r, b]` rectangle, and
    the rule text. `pages`/`bbox` are best-effort -- a requirement curated before provenance landed (ADR-0107)
    reads back with `pages=[]` / `bbox=None`, which a preview renders honestly rather than erroring."""

    requirement_id: str
    citation: str
    pages: list[int] = []
    bbox: Optional[tuple[float, float, float, float]] = None
    text: str


class ComplianceReport(BaseModel):
    """The `compliance_check` output (CC-6, §13.3): a subject document's cited findings + a per-requirement gap
    matrix + a verdict summary. Both-sided cited; every violation/needs_review is human-gated per finding."""

    source_doc: str
    findings: list[ComplianceFinding] = []
    summary: dict[str, int] = {}  # verdict -> count (compliant/violation/needs_review)
    gap_matrix: list[dict] = []  # per-requirement rollup: {requirement_id, citation, verdict, claims_checked}
    # SEG-6 (0009-WIRE2 / ENG-1 principle): pages the tiered OCR could not read even after VLM escalation, so a
    # verdict on a scanned subject is never SILENTLY based on half-read text. Empty = fully readable. The product
    # surfaces this as "pages X-Y unreadable; results for those pages are incomplete".
    ocr_unreadable_pages: list[int] = []
    # ADR-0068 (engine issue 0013): (assertion, rule) pairs the symbolic ACTOR gate SKIPPED before any judge call
    # -- so the gate is never a SILENT recall loss. Each: {requirement_id, citation, actor, subject_actors, scope
    # ("assertion"|"document"), claim_id}. Empty is the norm (the recall-first gate skips only ontology-disjoint
    # roles); a non-empty list lets a consumer state honest coverage ("checked N rules; K pairs gated by role").
    gated_pairs: list[dict] = []

    @property
    def verdict(self) -> Verdict:
        """The AD-LEVEL verdict rolled up from the findings (RG-5): VIOLATION only when violation findings are a
        real signal (>= threshold, so one spurious finding among many rules does not hard-flag); else
        NEEDS_REVIEW if anything fired (a lone violation OR any needs_review -> escalate for a human); else
        COMPLIANT. It never CLEARS an ad that had a violation finding -- a real violation is never a silent pass."""
        v = self.summary.get("violation", 0)
        if v >= _AD_VIOLATION_THRESHOLD:
            return Verdict.VIOLATION
        if v or self.summary.get("needs_review", 0):
            return Verdict.NEEDS_REVIEW
        return Verdict.COMPLIANT
