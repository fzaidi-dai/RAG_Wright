"""GENERATED FROM contract_bridge.ttl by scripts/generate_contract_python.py -- DO NOT EDIT BY HAND.

ADR-0066 P1b-2 (Rule 3): the extraction template's KNOWLEDGE -- each field's LOOK-FOR description and examples,
generated from the ttl. `clause_template.py` CONSUMES this (via `_d()` / `_ex()`); the mechanism (validators,
normalizers, __str__, config) stays clean hand-code there. To change a description/example, edit the ttl and
re-run the generator; a hand-edit here is caught by tests/ontology/test_generated_template_meta_in_sync.py."""

from __future__ import annotations


DESCRIPTIONS: dict[str, str] = {
    'Clause.document_reference': "OPTIONAL. ONLY the clause's own section number or short heading if one is written in the text (e.g. 'Section 8', '8.1 Limitation of Liability'). If there is NO explicit section number or heading, leave this null/empty -- do NOT invent one and NEVER quote the clause body or any sentence of it here.",
    'Clause.audit_frequency': "How often an audit-rights clause permits audits, a short value ONLY, e.g. 'annual', 'quarterly', 'once per year'.",
    'Clause.clause_type': "The clause function/type LABEL ONLY (a few words), e.g. 'Cap on Liability', 'Governing Law', 'Non-Solicit of Employees'.",
    'Clause.collateral_type': 'The collateral / assets a SECURITY-INTEREST clause attaches to (may be several), e.g. inventory, equipment, accounts receivable, all assets.',
    'Clause.commitment_quantum': "The minimum-commitment / volume amount ONLY (a short value), e.g. '$1,000,000', '100 units/year'.",
    'Clause.condition_type': 'The kind of condition a CONDITION-PRECEDENT clause requires: regulatory approval / financing / third-party consent / due diligence / board approval / no material adverse change / court approval / closing condition.',
    'Clause.confidentiality_exception': 'Permitted disclosures / exceptions to a CONFIDENTIALITY obligation (may be several): required by law, publicly available, independently developed, prior possession, received from a third party.',
    'Clause.force_majeure_event': 'Events a FORCE-MAJEURE clause lists as excusing performance (may be several): act of god, war, pandemic, government action, labor dispute, supply failure, natural disaster.',
    'Clause.royalty_basis': 'How a ROYALTY is calculated: percentage of net sales / percentage of gross sales / per unit / fixed / tiered.',
    'Clause.covers': 'Subjects the clause covers (ODRL target; may be several).',
    'Clause.covers_party_scope': 'Which affiliated parties the clause extends to: affiliates (either side) / licensor_affiliates / licensee_affiliates.',
    'Clause.dispute_method': 'How a DISPUTE-RESOLUTION clause resolves disputes: arbitration / litigation (courts) / mediation / expert determination / negotiation.',
    'Clause.excepts': 'Carve-outs / exceptions the clause lists (may be several).',
    'Clause.has_assignment_consent': 'How an anti-assignment clause treats consent (required / notice-only / free).',
    'Clause.has_asymmetry': "Whether the clause's terms apply the same to both parties (symmetric) or differently per party (different_per_party).",
    'Clause.has_claim_scope': "The scope of claims covered: first_party (the parties' own claims) / third_party (third-party claims) / broad_based (both / any).",
    'Clause.has_coc_consent': 'How a change-of-control clause treats consent (required / notice-only / unrestricted).',
    'Clause.has_escrow_release_trigger': 'What triggers a source-code escrow release (bankruptcy / breach / discontinuance).',
    'Clause.has_exclusivity_type': 'The exclusivity a licensing/distribution clause grants (exclusive / sole / non-exclusive).',
    'Clause.has_favorability': 'Which side the clause favors: buyer_favorable (the buyer / customer) or seller_favorable (the seller / vendor).',
    'Clause.has_ip_ownership': 'How intellectual-property ownership is treated: assigned (transferred) / joint (shared) / retained (kept by the originating party).',
    'Clause.has_mfn_scope': 'What a most-favored-nation clause covers (price / terms / both).',
    'Clause.has_mutuality': "Whether the clause's obligation runs both ways (mutual) or one way (unilateral).",
    'Clause.has_renewal': 'How the term renews: auto (automatic renewal) or requires_notice (renews only on notice / an affirmative election).',
    'Clause.has_restriction_scope': 'What a non-compete restricts: geographic area, activity, or both.',
    'Clause.has_right_of_first_type': 'The first-refusal/offer/negotiation right the clause grants (ROFR / ROFO / ROFN).',
    'Clause.has_termination_right': 'Who may terminate for convenience (either party / one party).',
    'Clause.has_warranty_scope': 'The warranty scope the clause sets: implied / express / as_is (warranties disclaimed) / non_reliance.',
    'Clause.ld_trigger': "What triggers liquidated damages, e.g. 'late delivery', 'early termination'.",
    'Clause.prohibits_damage': 'Damage types the clause waives/excludes (may be several).',
    'Clause.prohibits_solicit': 'Whom the clause forbids soliciting (employees / customers).',
    'Clause.requires_duty': 'A procedural duty the clause imposes (duty to defend / control of defense).',
    'Clause.bounded_by': 'A temporal bound (term or notice period), if any.',
    'Clause.caps': "The liability cap, if the clause limits the AMOUNT of liability (e.g. 'liability shall not exceed', 'limited to the fees paid', 'in no event shall ... exceed', 'total/aggregate liability ... capped at'). Fill its basis and quantum. Leave absent if the clause states no monetary limit (a pure exclusion or waiver of a damage TYPE is not a cap).",
    'Clause.governed_by': 'The governing-law jurisdiction, if the clause states one.',
    'CapConstraint.cap_basis': 'The basis of the liability cap: fixed_fee (a fixed amount) / multiple_of_fees (a multiple of fees paid) / other.',
    'CapConstraint.cap_operator': "The comparison operator ONLY (a few words), e.g. 'lteq' (at most), 'eq' (fixed).",
    'CapConstraint.cap_quantum': "The stated limit on liability. Look for 'shall not exceed', 'limited to', 'capped at', 'in no event ... exceed', 'total/aggregate liability ... shall not exceed'. Copy the limit VERBATIM even if it is a phrase, e.g. '$1,000,000', '12 months of fees', 'the fees paid in the prior 12 months', 'the greater of X or Y'.",
    'TemporalConstraint.temporal_duration': "The duration ONLY (a short value), e.g. '12_months', '30_days', 'unbounded'.",
    'TemporalConstraint.temporal_kind': "What is bounded: 'term' (temporal_bound) or 'notice_period'.",
    'TemporalConstraint.temporal_operator': "The temporal comparison operator ONLY (a few words), e.g. 'lteq' (within / at most), 'gteq' (at least), 'eq' (exactly).",
    'Jurisdiction.jurisdiction_name': "The jurisdiction NAME ONLY (a few words, not a sentence), e.g. 'New York', 'England and Wales', 'Delaware'.",
    'Jurisdiction.law_multiplicity': 'Whether one law governs (single) or multiple laws govern (multiple).',
}

EXAMPLES: dict[str, tuple[str, ...]] = {
    'Clause.document_reference': ('Section 8', '8.1 Limitation of Liability', 'Governing Law'),
    'Jurisdiction.jurisdiction_name': ('New York', 'England and Wales', 'Delaware'),
}
