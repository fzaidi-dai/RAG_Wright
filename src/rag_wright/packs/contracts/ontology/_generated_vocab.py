"""GENERATED FROM contract_bridge.ttl by scripts/generate_contract_python.py -- DO NOT EDIT BY HAND.

ADR-0066: the ontology `.ttl` is the single source of truth. To change the closed vocabulary, edit the ttl and
re-run the generator; a hand-edit here (or a stale regeneration) is caught by
tests/ontology/test_generated_vocab_in_sync.py."""

from __future__ import annotations


VOCAB: dict[str, frozenset[str]] = {
    'assignment_consent': frozenset({'consent_required', 'free', 'notice_only'}),
    'cap_basis': frozenset({'fixed_fee', 'multiple_of_fees', 'other'}),
    'carve_out': frozenset({'applicable_law', 'bodily_injury', 'confidentiality', 'fraud', 'gross_negligence', 'indemnification', 'third_party_ip_infringement', 'willful_misconduct'}),
    'claim_scope': frozenset({'broad_based', 'first_party', 'third_party'}),
    'coc_consent': frozenset({'consent_required', 'notice_only', 'unrestricted'}),
    'collateral_type': frozenset({'accounts_receivable', 'all_assets', 'deposit_accounts', 'equipment', 'fixtures', 'general_intangibles', 'inventory', 'ip', 'real_property'}),
    'condition_type': frozenset({'board_approval', 'closing_condition', 'court_approval', 'due_diligence', 'financing', 'no_material_adverse_change', 'regulatory_approval', 'third_party_consent'}),
    'confidentiality_exception': frozenset({'independently_developed', 'prior_possession', 'publicly_available', 'required_by_law', 'third_party_source'}),
    'covered_parties': frozenset({'affiliates', 'licensee_affiliates', 'licensor_affiliates'}),
    'covered_subject': frozenset({'copyright', 'fraud', 'gross_negligence', 'ip_infringement', 'trademark', 'violation_of_law'}),
    'damage_type': frozenset({'consequential', 'incidental', 'indirect', 'punitive', 'special'}),
    'dispute_method': frozenset({'arbitration', 'expert_determination', 'litigation', 'mediation', 'negotiation'}),
    'escrow_release_trigger': frozenset({'bankruptcy', 'breach', 'discontinuance'}),
    'exclusivity_type': frozenset({'exclusive', 'non_exclusive', 'sole'}),
    'favorability': frozenset({'buyer_favorable', 'seller_favorable'}),
    'force_majeure_event': frozenset({'act_of_god', 'government_action', 'labor_dispute', 'natural_disaster', 'pandemic', 'supply_failure', 'war'}),
    'ip_ownership': frozenset({'assigned', 'joint', 'retained'}),
    'law_multiplicity': frozenset({'multiple', 'single'}),
    'mfn_scope': frozenset({'price', 'price_and_terms', 'terms'}),
    'mutuality': frozenset({'mutual', 'unilateral'}),
    'nonsolicit_target': frozenset({'customers', 'employees'}),
    'party_asymmetry': frozenset({'different_per_party', 'symmetric'}),
    'procedural': frozenset({'control_of_defense', 'duty_to_defend'}),
    'renewal_mechanism': frozenset({'auto', 'requires_notice'}),
    'restriction_scope': frozenset({'activity', 'geographic', 'geographic_and_activity'}),
    'right_of_first_type': frozenset({'rofn', 'rofo', 'rofr'}),
    'royalty_basis': frozenset({'fixed', 'pct_gross_sales', 'pct_net_sales', 'per_unit', 'tiered'}),
    'termination_right': frozenset({'either_party', 'one_party'}),
    'warranty_scope': frozenset({'as_is', 'express', 'implied', 'non_reliance'}),
}

VALUE_SYNONYMS: dict[str, dict[str, str]] = {
    'damage_type': {'businessinterruption': 'consequential', 'costofcover': 'consequential', 'lossofdata': 'consequential', 'lossofprofit': 'consequential', 'lossofprofits': 'consequential', 'lossofrevenue': 'consequential', 'lossofsavings': 'consequential', 'lossofuse': 'consequential', 'lostbusinessrevenue': 'consequential', 'lostprofits': 'consequential', 'lostrevenue': 'consequential', 'lostsavings': 'consequential'},
}

AFFILIATE_OF = 'Affiliate Of'
CONTRACTS_WITH = 'Contracts With'
ORGANIZATION = 'Organization'
PERSON = 'Person'

ENTITY_TYPES: frozenset[str] = frozenset({'Organization', 'Person'})
RELATIONSHIP_TYPES: frozenset[str] = frozenset({'Affiliate Of', 'Contracts With'})
