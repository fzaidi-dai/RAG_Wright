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

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, SH

from rag_wright.packs.contracts.schemas.property import (
    ClausePropertyRecord,
    PropertyAssertion,
    PropertyDimension,
)
from rag_wright.pack_sdk import ConfidenceTag

_CBR = Namespace("https://ragwright.local/ontology/contract-bridge#")


def _function_class(function: str) -> URIRef:
    """A stable CBR class IRI for a function label (RDF has no spaces; encode deterministically)."""
    return _CBR[f"Function_{function.replace(' ', '_')}"]


def _dim_property(dimension: PropertyDimension) -> URIRef:
    """The CBR predicate IRI carrying a dimension's asserted value on a clause node."""
    return _CBR[f"dim_{dimension.value}"]


def _shapes_graph() -> Graph:
    """ADR-0066 P2: the SHACL shapes come FROM contract_bridge.ttl (the source of truth) -- pyshacl reads the
    persisted sh:NodeShapes directly; no Python-built shapes. The ttl non-SHACL triples are ignored by pyshacl.
    """
    from rag_wright.packs.contracts.ontology.loader import load_shapes_graph

    return load_shapes_graph()


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
    """The dimensions to downgrade: ONLY the FUNCTION-INDEPENDENT contradiction check -- a scalar dimension
    asserted with more than one value (`sh:maxCount 1`). The FUNCTION-DEPENDENT checks are DELIBERATELY IGNORED
    (ADR-0082): clause-function classification is not accurate enough to be load-bearing (~0.5 top-1; memory
    `function-classification-not-load-bearing`), so `sh:closed` (dimension-not-applicable-to-function) and `sh:in`
    (deontic polarity on a restrictive function) would downgrade CORRECT cross-cutting extractions based on an
    unreliable (and often narrow) function map. Function is a KG tag / query-time soft signal, never an ingest
    gate. Empty if the record conforms or the function is unmodeled."""
    if not record.assertions:
        return set()
    from pyshacl import validate

    conforms, results_graph, _ = validate(
        _record_to_rdf(record), shacl_graph=_shapes_graph(), inference="none", advanced=False
    )
    if conforms:
        return set()
    flagged: set[PropertyDimension] = set()
    _prefix = str(_CBR) + "dim_"
    for result in results_graph.subjects(RDF.type, SH.ValidationResult):
        # keep ONLY the contradiction (maxCount) violations; drop function-dependent closed/in violations
        if results_graph.value(result, SH.sourceConstraintComponent) != SH.MaxCountConstraintComponent:
            continue
        path = results_graph.value(result, SH.resultPath)
        if path is not None and str(path).startswith(_prefix):
            flagged.add(PropertyDimension(str(path)[len(_prefix):]))
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
