"""JUDGE-ONTOLOGY-1 (ADR-0040): the symbolic ontology-validation gate -- layer 2 of the
neuro-symbolic extraction-fidelity cascade.

Where the lexical grounding judge (`property_grounding.reground`, ADR-0028) checks whether a value's
surface cue appears in the text, this gate checks whether the ASSERTED DIMENSION is even APPLICABLE to
the clause's FUNCTION -- a TYPE constraint the lexical judge structurally cannot see. Example: granite
emits `nonsolicit_target=employees` on an Anti-Assignment clause. The value is a valid nonsolicit_target
and its cue may appear in the text, so the lexical judge passes it; but `nonsolicit_target` does not
belong to Anti-Assignment at all -- a type error, caught here.

The applicability map (`FUNCTION_APPLICABLE_DIMS`) is the NEW ontology content ADR-0040 calls for: each
clause FUNCTION permits a set of property DIMENSIONS. It is compiled to SHACL `sh:closed` NodeShapes (one
per function, listing the applicable dimension paths) and validated with `pyshacl` -- the symbolic half of
neuro-symbolic. A non-applicable assertion is downgraded to AMBIGUOUS (kept but flagged), exactly like
`reground`, so the shared property-value nodes stay clean and soft-boost down-weights it.

The gate also enforces CARDINALITY (JUDGE-ONTOLOGY-2): a SCALAR dimension asserted with two conflicting
values (e.g. granite hedging `cap_basis` = both `fixed_fee` and `multiple_of_fees`) violates `sh:maxCount 1`
and both values are downgraded -- the real intra-clause "contradiction" class, since the dimensions are
orthogonal facets and same-dimension conflict is where extraction actually contradicts itself. The 3
multi-valued dimensions (`_LIST_ENUM_DIMS`: carve_out / covered_subject / damage_type) are left unbounded.

The gate also enforces DEONTIC consistency (JUDGE-ONTOLOGY-3, ODRL): a consent-regime dimension carries a
permission↔restriction polarity in its VALUES (`free`/`unrestricted` = "may freely"; `consent_required` =
restricted). A function whose defining purpose is to RESTRICT (Anti-Assignment / Non-Transferable License /
Change Of Control) contradicts a permission-polarity value -- the observed `assignment_consent=free` on a
"shall not assign" clause. The clause's rule type is DERIVED from its function (reliable, non-circular; not
parsed from the text), and the check is a `sh:in` (allowed = vocab minus the permission-polarity values) on
the scoped property shape.

Deliberately NOT enforced here (JUDGE-ONTOLOGY-2 scoping): value-in-vocabulary (`sh:in` over the full vocab)
is already enforced at the Pydantic contract boundary (`property.PropertyAssertion._value_in_vocab_or_ambiguous`),
so a whole-vocab SHACL shape would duplicate a working validator; and cross-DIMENSION `sh:sparql` rules are
omitted because this schema's dimensions are orthogonal facets with no hard intra-clause cross-dimension
contradiction (deferred to a post-MVP / beta-customer iteration on real production data).

Deterministic, no model, no network. Downgrade is confidence-independent (a type/cardinality/deontic error is
wrong whether EXTRACTED or INFERRED); an already-AMBIGUOUS assertion is left as is. A function NOT in the map
is PERMISSIVE (unvalidated) -- coverage is expanded deliberately, never by guessing a closed set we are unsure
of. All three checks reuse one record->RDF->pyshacl harness.
"""

from __future__ import annotations

from functools import lru_cache

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection
from rdflib.namespace import RDF, SH

from rag_wright.contracts.property import (
    CLOSED_VOCAB,
    ClausePropertyRecord,
    PropertyAssertion,
    PropertyDimension,
)
from rag_wright.contracts.provenance import ConfidenceTag

_D = PropertyDimension
_CBR = Namespace("https://ragwright.local/ontology/contract-bridge#")

# The function -> applicable-dimensions map (the ADR-0040 ontology addition). Each key is a member of the
# retrieval FUNCTION taxonomy (contracts/function.FUNCTION_LABELS); each value is the set of dimensions a
# clause of that function may legitimately carry. Metadata / structural functions carry NO property
# dimensions (an empty set -> any asserted dimension is a type error). Tier-1 cross-cutting dims
# (mutuality/favorability/party_asymmetry/carve_out/covered_*) are listed only where they genuinely recur,
# kept deliberately permissive on the liability/indemnity/warranty family to avoid false flags. DOMAIN
# CONTENT -- reviewed at the JUDGE-ONTOLOGY-1 gate; expand conservatively.
FUNCTION_APPLICABLE_DIMS: dict[str, frozenset[PropertyDimension]] = {
    # metadata / structural -- no property dimensions
    "Document Name": frozenset(),
    "Parties": frozenset(),
    "Agreement Date": frozenset(),
    "Effective Date": frozenset(),
    "Expiration Date": frozenset(),
    # term / renewal / termination
    "Renewal Term": frozenset({_D.RENEWAL_MECHANISM, _D.TEMPORAL_BOUND, _D.NOTICE_PERIOD}),
    "Notice Period To Terminate Renewal": frozenset({_D.NOTICE_PERIOD, _D.RENEWAL_MECHANISM}),
    "Termination For Convenience": frozenset({_D.TERMINATION_RIGHT, _D.NOTICE_PERIOD, _D.PARTY_ASYMMETRY}),
    "Post-Termination Services": frozenset({_D.TEMPORAL_BOUND, _D.COVERED_PARTIES}),
    # governing law
    "Governing Law": frozenset({_D.JURISDICTION, _D.LAW_MULTIPLICITY}),
    # commercial restrictions
    "Most Favored Nation": frozenset({_D.MFN_SCOPE, _D.PARTY_ASYMMETRY}),
    "Non-Compete": frozenset(
        {_D.RESTRICTION_SCOPE, _D.TEMPORAL_BOUND, _D.PARTY_ASYMMETRY, _D.COVERED_PARTIES}
    ),
    "Exclusivity": frozenset({_D.EXCLUSIVITY_TYPE, _D.RESTRICTION_SCOPE, _D.PARTY_ASYMMETRY}),
    "No-Solicit Of Customers": frozenset({_D.NONSOLICIT_TARGET, _D.TEMPORAL_BOUND, _D.PARTY_ASYMMETRY}),
    "No-Solicit Of Employees": frozenset({_D.NONSOLICIT_TARGET, _D.TEMPORAL_BOUND, _D.PARTY_ASYMMETRY}),
    "Competitive Restriction Exception": frozenset({_D.CARVE_OUT, _D.RESTRICTION_SCOPE}),
    "Non-Disparagement": frozenset({_D.MUTUALITY, _D.PARTY_ASYMMETRY}),
    "Price Restrictions": frozenset({_D.MFN_SCOPE, _D.RESTRICTION_SCOPE}),
    "Rofr/Rofo/Rofn": frozenset({_D.RIGHT_OF_FIRST_TYPE, _D.TEMPORAL_BOUND, _D.PARTY_ASYMMETRY}),
    "Change Of Control": frozenset({_D.COC_CONSENT, _D.PARTY_ASYMMETRY}),
    "Anti-Assignment": frozenset({_D.ASSIGNMENT_CONSENT, _D.PARTY_ASYMMETRY}),
    "Revenue/Profit Sharing": frozenset({_D.COMMITMENT_QUANTUM, _D.MUTUALITY}),
    "Minimum Commitment": frozenset({_D.COMMITMENT_QUANTUM, _D.TEMPORAL_BOUND}),
    "Volume Restriction": frozenset({_D.COMMITMENT_QUANTUM, _D.RESTRICTION_SCOPE}),
    # IP / licensing
    "IP Ownership Assignment": frozenset({_D.IP_OWNERSHIP, _D.COVERED_SUBJECT}),
    "Joint IP Ownership": frozenset({_D.IP_OWNERSHIP}),
    "License Grant": frozenset(
        {_D.EXCLUSIVITY_TYPE, _D.IP_OWNERSHIP, _D.COVERED_PARTIES, _D.RESTRICTION_SCOPE}
    ),
    "Non-Transferable License": frozenset(
        {_D.ASSIGNMENT_CONSENT, _D.EXCLUSIVITY_TYPE, _D.RESTRICTION_SCOPE}
    ),
    "Affiliate License-Licensor": frozenset({_D.COVERED_PARTIES, _D.EXCLUSIVITY_TYPE}),
    "Affiliate License-Licensee": frozenset({_D.COVERED_PARTIES, _D.EXCLUSIVITY_TYPE}),
    "Unlimited/All-You-Can-Eat-License": frozenset({_D.EXCLUSIVITY_TYPE, _D.COMMITMENT_QUANTUM}),
    "Irrevocable Or Perpetual License": frozenset({_D.TEMPORAL_BOUND, _D.EXCLUSIVITY_TYPE}),
    "Source Code Escrow": frozenset({_D.ESCROW_RELEASE_TRIGGER}),
    # audit / insurance
    "Audit Rights": frozenset({_D.AUDIT_FREQUENCY, _D.NOTICE_PERIOD, _D.TEMPORAL_BOUND}),
    "Insurance": frozenset({_D.CAP_QUANTUM, _D.COVERED_SUBJECT, _D.TEMPORAL_BOUND}),
    # liability / warranty / damages (the tier-1-heavy core family)
    "Uncapped Liability": frozenset(
        {_D.CAP_BASIS, _D.CARVE_OUT, _D.MUTUALITY, _D.FAVORABILITY, _D.PARTY_ASYMMETRY,
         _D.COVERED_SUBJECT, _D.DAMAGE_TYPE, _D.CLAIM_SCOPE}
    ),
    "Cap On Liability": frozenset(
        {_D.CAP_BASIS, _D.CAP_QUANTUM, _D.CARVE_OUT, _D.MUTUALITY, _D.FAVORABILITY,
         _D.PARTY_ASYMMETRY, _D.DAMAGE_TYPE, _D.CLAIM_SCOPE}
    ),
    "Liquidated Damages": frozenset({_D.LD_TRIGGER, _D.CAP_QUANTUM, _D.DAMAGE_TYPE}),
    "Warranty Duration": frozenset({_D.WARRANTY_SCOPE, _D.TEMPORAL_BOUND}),
    "Warranty Disclaimer": frozenset({_D.WARRANTY_SCOPE, _D.PARTY_ASYMMETRY}),
    "Covenant Not To Sue": frozenset(
        {_D.CARVE_OUT, _D.COVERED_SUBJECT, _D.COVERED_PARTIES, _D.TEMPORAL_BOUND,
         _D.PARTY_ASYMMETRY, _D.CLAIM_SCOPE, _D.PROCEDURAL}
    ),
    # FOLIO-extension functions (the natural homes for the indemnity-family dims: procedural, claim_scope,
    # damage_type, warranty_scope)
    "Indemnification": frozenset(
        {_D.CLAIM_SCOPE, _D.PROCEDURAL, _D.COVERED_SUBJECT, _D.COVERED_PARTIES, _D.CARVE_OUT,
         _D.PARTY_ASYMMETRY, _D.MUTUALITY}
    ),
    "Indirect/Consequential Damages Waiver": frozenset(
        {_D.DAMAGE_TYPE, _D.CAP_BASIS, _D.MUTUALITY, _D.PARTY_ASYMMETRY}
    ),
    "Third Party Beneficiary": frozenset({_D.COVERED_PARTIES, _D.PARTY_ASYMMETRY}),
    # ADR-0049 (1): the 8 taxonomy-gap functions, modeled with their EXISTING-dimension applicability from legal
    # domain knowledge (what each clause type carries for ANY customer, not fitted to CUAD). These sets are
    # PARTIAL by design -- the type-specific facet each one needs (e.g. dispute_method, royalty basis, force-
    # majeure events, confidentiality permitted-disclosures) has no existing dimension and is added as a NEW
    # dimension in step (2). Cross-cutting dims (party_asymmetry/temporal_bound/mutuality) included where they
    # genuinely recur, per the "expand conservatively" rule.
    # ADR-0049 (2): each type-specific facet dimension added to its type's applicability.
    "Confidentiality": frozenset(
        {_D.CONFIDENTIALITY_EXCEPTION, _D.MUTUALITY, _D.PARTY_ASYMMETRY, _D.TEMPORAL_BOUND}),
    "Payment Terms": frozenset({_D.COMMITMENT_QUANTUM, _D.TEMPORAL_BOUND, _D.PARTY_ASYMMETRY}),
    "Royalties": frozenset({_D.ROYALTY_BASIS, _D.COMMITMENT_QUANTUM, _D.TEMPORAL_BOUND}),
    "Dispute Resolution": frozenset({_D.DISPUTE_METHOD, _D.JURISDICTION, _D.PARTY_ASYMMETRY, _D.TEMPORAL_BOUND}),
    "Record Retention": frozenset({_D.TEMPORAL_BOUND, _D.PARTY_ASYMMETRY}),
    "Security Interest": frozenset({_D.COLLATERAL_TYPE, _D.COMMITMENT_QUANTUM, _D.PARTY_ASYMMETRY}),
    "Condition Precedent": frozenset({_D.CONDITION_TYPE, _D.TEMPORAL_BOUND, _D.PARTY_ASYMMETRY}),
    "Force Majeure": frozenset(
        {_D.FORCE_MAJEURE_EVENT, _D.NOTICE_PERIOD, _D.TEMPORAL_BOUND, _D.MUTUALITY, _D.PARTY_ASYMMETRY,
         _D.TERMINATION_RIGHT}),
}

# ADR-0049 (1): the 8 taxonomy-gap functions are now MODELED above (existing-dimension applicability), so nothing
# is permissive -- every one of the 52 labels is validated. The mechanism is kept (empty) so a future new clause
# type can be declared explicitly permissive rather than silently missing, per the coverage test. Their
# type-specific dimensions are added in ADR-0049 step (2).
PERMISSIVE_FUNCTIONS: frozenset[str] = frozenset()


# The multi-valued dimensions (`clause_kg_extractor._LIST_ENUM_DIMS`): a clause may carry several. Every
# other dimension is SCALAR (at most one value) -> `sh:maxCount 1`. Kept in code, referencing enum members,
# for the same no-ttl-drift reason as FUNCTION_APPLICABLE_DIMS.
MULTI_VALUED_DIMENSIONS: frozenset[PropertyDimension] = frozenset(
    # ADR-0049 (2): collateral / force-majeure events / confidentiality exceptions are all lists on a clause.
    {_D.CARVE_OUT, _D.COVERED_SUBJECT, _D.DAMAGE_TYPE, _D.COLLATERAL_TYPE, _D.FORCE_MAJEURE_EVENT,
     _D.CONFIDENTIALITY_EXCEPTION}
)

# Deontic consistency (JUDGE-ONTOLOGY-3, ODRL). The consent-regime VALUES that assert NO restriction
# (permission polarity) -- `free`/`unrestricted` are the "may freely" endpoints of their vocabularies
# (contract_bridge.ttl: cbr:free a cbr:AssignmentConsent ; cbr:unrestricted a cbr:CocConsent).
PERMISSION_POLARITY_VALUES: dict[PropertyDimension, frozenset[str]] = {
    _D.ASSIGNMENT_CONSENT: frozenset({"free"}),
    _D.COC_CONSENT: frozenset({"unrestricted"}),
}
# Functions whose defining purpose is to RESTRICT the thing their consent dimension governs. A
# permission-polarity value on such a function is a deontic inversion (the observed
# `assignment_consent=free` on a "shall not assign" clause) -- the clause's rule type is DERIVED from its
# function (reliable, non-circular), not parsed from the text. Realized as `sh:in` (allowed = vocab minus
# the permission-polarity values) on the scoped property shape.
RESTRICTIVE_FUNCTIONS: frozenset[str] = frozenset(
    {"Anti-Assignment", "Non-Transferable License", "Change Of Control"}
)


def _function_class(function: str) -> URIRef:
    """A stable CBR class IRI for a function label (RDF has no spaces; encode deterministically)."""
    return _CBR[f"Function_{function.replace(' ', '_')}"]


def _dim_property(dimension: PropertyDimension) -> URIRef:
    """The CBR predicate IRI carrying a dimension's asserted value on a clause node."""
    return _CBR[f"dim_{dimension.value}"]


@lru_cache(maxsize=1)
def _shapes_graph() -> Graph:
    """Compile `FUNCTION_APPLICABLE_DIMS` to a SHACL shapes graph -- one `sh:closed` NodeShape per function,
    listing its applicable dimension paths (any other dimension predicate is a violation). Built once."""
    g = Graph()
    for function, dims in FUNCTION_APPLICABLE_DIMS.items():
        shape = _CBR[f"Shape_{function.replace(' ', '_')}"]
        g.add((shape, RDF.type, SH.NodeShape))
        g.add((shape, SH.targetClass, _function_class(function)))
        g.add((shape, SH.closed, Literal(True)))
        # rdf:type is a structural predicate on the clause node -> never itself a violation
        ignored = Collection(g, URIRef(str(shape) + "_ignored"), [RDF.type])
        g.add((shape, SH.ignoredProperties, ignored.uri))
        for dim in dims:
            prop = URIRef(f"{shape}_prop_{dim.value}")
            g.add((shape, SH.property, prop))
            g.add((prop, SH.path, _dim_property(dim)))
            if dim not in MULTI_VALUED_DIMENSIONS:  # scalar dim -> at most one value (JUDGE-ONTOLOGY-2)
                g.add((prop, SH.maxCount, Literal(1)))
            if function in RESTRICTIVE_FUNCTIONS and dim in PERMISSION_POLARITY_VALUES:
                # deontic (JUDGE-ONTOLOGY-3): a restrictive function forbids the permission-polarity values
                allowed = sorted(CLOSED_VOCAB[dim] - PERMISSION_POLARITY_VALUES[dim])
                lst = Collection(g, URIRef(f"{prop}_allowed"), [Literal(v) for v in allowed])
                g.add((prop, SH["in"], lst.uri))
    return g


def _record_to_rdf(record: ClausePropertyRecord) -> Graph:
    """Serialize a record's function + assertions to a tiny data graph: the clause typed by its function,
    with one triple per assertion (dimension predicate -> value literal)."""
    g = Graph()
    clause = URIRef("urn:clause:" + record.clause_id)
    g.add((clause, RDF.type, _function_class(record.function)))
    for a in record.assertions:
        g.add((clause, _dim_property(a.dimension), Literal(a.value)))
    return g


def flagged_dimensions(record: ClausePropertyRecord) -> set[PropertyDimension]:
    """The dimensions on the record that violate a SHACL shape: NOT applicable to the function (`sh:closed`),
    a scalar dimension asserted with more than one value (`sh:maxCount 1`), or a permission-polarity value on
    a restrictive function (`sh:in`, deontic). Every violation reports `sh:resultPath` = the dimension
    predicate, so all fold into one flagged set. Empty if the function is unmodeled (permissive) or valid."""
    if record.function not in FUNCTION_APPLICABLE_DIMS or not record.assertions:
        return set()
    from pyshacl import validate

    conforms, results_graph, _ = validate(
        _record_to_rdf(record), shacl_graph=_shapes_graph(), inference="none", advanced=False
    )
    if conforms:
        return set()
    flagged: set[PropertyDimension] = set()
    _prefix = str(_CBR) + "dim_"
    for path in results_graph.objects(None, SH.resultPath):
        p = str(path)
        if p.startswith(_prefix):
            flagged.add(PropertyDimension(p[len(_prefix):]))
    return flagged


def symbolic_validate(record: ClausePropertyRecord) -> ClausePropertyRecord:
    """Quality gate (ADR-0040 layer 2): downgrade every assertion whose dimension violates a shape -- not
    applicable to the clause's function, or a scalar dimension asserted with conflicting values -- to
    AMBIGUOUS (kept but flagged, model-agnostic, confidence-independent). A no-op when the function is
    unmodeled or every dimension is valid -- mirrors `property_grounding.reground`."""
    bad = flagged_dimensions(record)
    if not bad:
        return record
    new: list[PropertyAssertion] = [
        a.model_copy(update={"confidence": ConfidenceTag.AMBIGUOUS})
        if a.dimension in bad and a.confidence != ConfidenceTag.AMBIGUOUS
        else a
        for a in record.assertions
    ]
    return record.model_copy(update={"assertions": new})
