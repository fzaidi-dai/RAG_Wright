"""T61 (FR-C.6, ADR-0028): the deterministic property-grounding judge.

Most of the property vocabulary maps to legal TERMS OF ART that must physically appear in the clause. This
judge checks, locally and deterministically (no model, no network), whether an `EXTRACTED` value on a
lexically-anchored dimension actually has its surface cue in the text. If not, the value is UNGROUNDED --
almost certainly hallucinated (e.g. Flash emitting `carve_out=fraud` on a clause with no "fraud").

Two uses (double duty):
1. **Escalation trigger** (T58b cascade): run the fast model (Flash) for the bulk, and re-extract only the
   clauses the judge flags with the precise model (Pro). Concentrates the throttled Pro calls on the
   deterministically-suspect cases; a false flag costs one extra Pro call, never correctness.
2. **Permanent quality gate** on the final graph: `reground` downgrades an ungrounded `EXTRACTED` assertion
   to `AMBIGUOUS` (kept, but marked unverified so soft-boost down-weights it) regardless of which model
   produced it -- so the shared property-value nodes stay clean.

Coverage: lexically-anchored closed values (cue check), plus open-valued scalars (token overlap,
GROUNDING-OPENVALUED). LIMITATION: the closed SEMANTIC dimensions (mutuality, favorability, party_asymmetry,
cap_basis, the consent regimes) carry no surface form and are NOT checkable here; their errors pass through
unflagged and are the target of the Layer-3 semantic judge (ADR-0040, JUDGE-SEMANTIC).
"""

from __future__ import annotations

import re

from rag_wright.packs.contracts.schemas.property import (
    CLOSED_VOCAB,
    ClausePropertyRecord,
    PropertyAssertion,
    PropertyDimension,
)
from rag_wright.pack_sdk import ConfidenceTag

_D = PropertyDimension

# Open-valued dimensions carry a free-text scalar (jurisdiction, cap_quantum, temporal_bound, notice_period,
# audit_frequency, commitment_quantum, ld_trigger) rather than a closed vocabulary. GROUNDING-OPENVALUED
# (ADR-0040): unlike a closed SEMANTIC dim (mutuality/etc., which stays Layer-3's job), an open value SHOULD
# have a textual anchor -- an EXTRACTED `jurisdiction=Delaware` on a clause that never mentions Delaware is a
# fabrication. Checked by TOKEN OVERLAP (lenient: grounded if ANY significant token of the value appears), so
# normalized forms survive (`12_months` grounded by "months" in "twelve (12) months") while pure inventions
# with no overlapping token are flagged.
OPEN_VALUED_DIMENSIONS: frozenset[PropertyDimension] = frozenset(
    d for d in PropertyDimension if d not in CLOSED_VOCAB
)

# dimension -> value -> surface cues (lowercased substrings). A value ABSENT from this map is NOT
# lexically anchored (semantic/open-valued) and is treated as grounded (the judge cannot disprove it).
GROUNDING_CUES: dict[PropertyDimension, dict[str, tuple[str, ...]]] = {
    _D.CARVE_OUT: {
        "indemnification": ("indemnif", "hold harmless"),
        "confidentiality": ("confidential",),
        "third_party_ip_infringement": ("infring",),
        "fraud": ("fraud",),
        "gross_negligence": ("gross negligence",),
        "willful_misconduct": ("willful misconduct", "wilful misconduct"),
        "bodily_injury": ("bodily injury", "personal injury", "death"),
        "applicable_law": ("law",),
    },
    _D.COVERED_SUBJECT: {
        "ip_infringement": ("infring",),
        "trademark": ("trademark",),
        "copyright": ("copyright",),
        "violation_of_law": ("violation of law", "breach of law", "applicable law", "law"),
        "fraud": ("fraud",),
        "gross_negligence": ("gross negligence",),
    },
    _D.DAMAGE_TYPE: {
        "indirect": ("indirect",),
        "consequential": ("consequential",),
        "incidental": ("incidental",),
        "punitive": ("punitive", "exemplary"),
        "special": ("special",),
    },
    _D.WARRANTY_SCOPE: {
        "implied": ("implied", "merchantability", "fitness for"),
        "express": ("express",),
        "as_is": ("as is", "as-is", "with all faults"),
        "non_reliance": ("non-reliance", "nonreliance", "no reliance", "not relied"),
    },
    _D.PROCEDURAL: {
        "duty_to_defend": ("defend", "defense", "defence"),
        "control_of_defense": ("control of the defense", "control of the defence", "control of defense",
                               "sole control", "conduct of the defense", "conduct the defense"),
    },
    _D.COVERED_PARTIES: {
        "affiliates": ("affiliate",),
        "licensor_affiliates": ("affiliate",),
        "licensee_affiliates": ("affiliate",),
    },
    _D.CLAIM_SCOPE: {
        "third_party": ("third party", "third-party"),
    },
    # tier 3 -- CUAD-family extensions (KG-4). Only the strongly lexically-anchored values; the semantic
    # ones (consent regimes, mfn_scope, termination_right) are not checkable and pass through.
    _D.EXCLUSIVITY_TYPE: {
        "exclusive": ("exclusive",),
        "sole": ("sole",),
        "non_exclusive": ("non-exclusive", "nonexclusive", "non exclusive"),
    },
    _D.RIGHT_OF_FIRST_TYPE: {
        "rofr": ("first refusal",),
        "rofo": ("first offer",),
        "rofn": ("first negotiation",),
    },
    _D.ESCROW_RELEASE_TRIGGER: {
        "bankruptcy": ("bankrupt", "insolven"),
        "breach": ("breach", "default"),
        "discontinuance": ("discontinu", "cease", "no longer"),
    },
    _D.RESTRICTION_SCOPE: {
        "geographic": ("geographic", "territor", "worldwide", "region"),
        "activity": ("activit", "business", "compet"),
    },
}


def is_lexically_anchored(dimension: PropertyDimension, value: str) -> bool:
    """Whether this (dimension, value) has a defined surface cue the judge can check at all."""
    return value in GROUNDING_CUES.get(dimension, {})


def _open_value_grounded(value: str, text: str) -> bool:
    """An open-valued scalar is grounded if ANY significant token of its (normalized) value appears in the
    text -- lenient so normalized forms survive, strict enough to flag a value with no textual anchor at all."""
    low = text.lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", value.lower()) if len(t) >= 2]
    if not tokens:  # nothing checkable (e.g. a single-char value) -> cannot disprove
        return True
    return any(t in low for t in tokens)


def is_grounded(dimension: PropertyDimension, value: str, text: str) -> bool:
    """True if the value is supported by the text. Lexically-anchored (closed-vocab) values require their cue
    to appear; open-valued scalars require a token overlap (GROUNDING-OPENVALUED); closed SEMANTIC values
    (mutuality/etc.) carry no surface form and are treated as grounded -- the judge cannot disprove them."""
    cues = GROUNDING_CUES.get(dimension, {}).get(value)
    if cues is not None:
        low = text.lower()
        return any(cue in low for cue in cues)
    if dimension in OPEN_VALUED_DIMENSIONS:
        return _open_value_grounded(value, text)
    return True


def ungrounded_assertions(record: ClausePropertyRecord, text: str) -> list[PropertyAssertion]:
    """The record's `EXTRACTED` assertions on a lexically-anchored dimension whose cue is ABSENT from the
    text -- the likely hallucinations. `INFERRED`/`AMBIGUOUS` are not flagged (they do not claim to be
    stated verbatim)."""
    return [
        a for a in record.assertions
        if a.confidence == ConfidenceTag.EXTRACTED and not is_grounded(a.dimension, a.value, text)
    ]


def needs_escalation(record: ClausePropertyRecord, text: str) -> bool:
    """Escalation trigger: True if any EXTRACTED value is ungrounded -> re-extract this clause with the
    precise model (the T58b Flash->Pro cascade)."""
    return bool(ungrounded_assertions(record, text))


def reground(record: ClausePropertyRecord, text: str) -> ClausePropertyRecord:
    """Quality gate (double duty): downgrade every ungrounded `EXTRACTED` assertion to `AMBIGUOUS` (kept but
    flagged unverified, so soft-boost down-weights it) -- a model-agnostic gate on the final graph."""
    flagged = {id(a) for a in ungrounded_assertions(record, text)}
    if not flagged:
        return record
    new = [
        a.model_copy(update={"confidence": ConfidenceTag.AMBIGUOUS}) if id(a) in flagged else a
        for a in record.assertions
    ]
    return record.model_copy(update={"assertions": new})


def register_extraction_grounding_judge(registry) -> None:
    """CAP-REG-2: register `extraction_grounding_judge` (function; ADR-0028 lexical grounding gate)."""
    registry.register(
        "extraction_grounding_judge",
        contract=ClausePropertyRecord,
        kind="function",
        display_name="Extraction grounding judge",
    )
