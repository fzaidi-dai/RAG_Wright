"""ADR-0066: the runtime loader for the contract ontology `.ttl` -- the seed of the ontology-as-source-of-truth
substrate. Parses `contract_bridge.ttl` into the domain knowledge structures the engine consumes: the closed
vocabularies, scalar/list cardinality, the function -> applicable-dimensions applicability, the deontic polarity
(+ restrictive functions), and the value rollups.

Phase 0 uses this only to PROVE the ttl reproduces today's Python constants (the equivalence gate). Phase 1
generates the Python vocab/enums FROM this loader; Phase 2 hands the SHACL shapes straight to pyshacl. The `.ttl`
uses a uniform, label-keyed layer (a `cbr:PropertyDimension` node per dimension with `owl:oneOf` + a
`cbr:cardinality` tag; a `sh:NodeShape` per clause function; `skos:broader` for rollups), so this loader is
robust to IRI encoding -- it reads `rdfs:label`, never decodes an IRI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from rdflib import Graph
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, RDFS, SH, SKOS

_TTL_PATH = Path(__file__).with_name("contract_bridge.ttl")
_COMPLIANCE_TTL_PATH = Path(__file__).with_name("compliance_bridge.ttl")


def load_compliance_vocab(path: Path | str = _COMPLIANCE_TTL_PATH) -> dict[str, set[str]]:
    """ADR-0066 P3b: the closed vocabularies declared in compliance_bridge.ttl, keyed by class local-name
    (`DeonticType`, `ClaimType`, `Severity`, `RuleScope`, `Verdict`) -> the set of `owl:oneOf` value local-names.
    The Python enums in contracts/compliance.py are drift-locked to this (the ttl is the source of truth)."""
    g = Graph()
    g.parse(str(path), format="turtle")
    out: dict[str, set[str]] = {}
    for cls in g.subjects(OWL.oneOf, None):
        local = str(cls).rsplit("#", 1)[-1]
        members = {str(m).rsplit("#", 1)[-1] for m in Collection(g, g.value(cls, OWL.oneOf))}
        out[local] = members
    return out


_CMP = "https://ragwright.local/ontology/compliance-bridge#"


@lru_cache(maxsize=4)
def load_deontic_cues(path: str = str(_COMPLIANCE_TTL_PATH)) -> frozenset[str]:
    """ADR-0066 P3c (Gap 1): the deontic CUES declared in compliance_bridge.ttl (`cmp:cue` on each deontic type) --
    the lexical markers of operative normative force. The requirement-ingestion validity gate uses them: a section
    with none of these cues is non-operative and is skipped. Cached per path."""
    from rdflib import URIRef

    g = Graph()
    g.parse(path, format="turtle")
    return frozenset(str(v).strip().lower() for v in g.objects(None, URIRef(_CMP + "cue")) if str(v).strip())


@lru_cache(maxsize=4)
def load_actor_synonyms(path: str = str(_COMPLIANCE_TTL_PATH)) -> dict[str, str]:
    """ADR-0066 P4a: the actor-role synonyms from compliance_bridge.ttl -- `{synonym -> canonical role}` built from
    each `cmp:ActorRole`'s `skos:altLabel` (synonym) -> `skos:prefLabel` (canonical). The query-side actor gate
    (`canonical_actor`) normalizes with this. Cached per path."""
    from rdflib import URIRef

    g = Graph()
    g.parse(path, format="turtle")
    out: dict[str, str] = {}
    for role in g.subjects(RDF.type, URIRef(_CMP + "ActorRole")):
        pref = str(g.value(role, SKOS.prefLabel) or "").strip().lower()
        if not pref:
            continue
        for alt in g.objects(role, SKOS.altLabel):
            out[str(alt).strip().lower()] = pref
    return out


@lru_cache(maxsize=4)
def load_role_domains(path: str = str(_COMPLIANCE_TTL_PATH)) -> dict[str, str]:
    """ADR-0068 (engine issue 0013): the DISJOINTNESS knowledge for the actor gate -- `{canonical role -> domain}`
    built from each `cmp:ActorRole`'s `skos:prefLabel` (canonical) -> `cmp:roleDomain`. Two roles are disjoint iff
    both appear here with DIFFERENT domains; the recall-first gate excludes only disjoint pairs (a role absent
    here, or two roles in the same domain, are compatible). A customer domain declares its roles' roleDomain in
    its own pack to get cross-domain narrowing. Cached per path."""
    from rdflib import URIRef

    g = Graph()
    g.parse(path, format="turtle")
    out: dict[str, str] = {}
    for role in g.subjects(RDF.type, URIRef(_CMP + "ActorRole")):
        pref = str(g.value(role, SKOS.prefLabel) or "").strip().lower()
        domain = str(g.value(role, URIRef(_CMP + "roleDomain")) or "").strip().lower()
        if pref and domain:
            out[pref] = domain
    return out


_FTC_PACK_PATH = Path(__file__).parent / "packs" / "ftc_16cfr255.ttl"


@lru_cache(maxsize=4)
def load_section_overrides(path: str = str(_FTC_PACK_PATH)) -> tuple[dict[str, str], dict[str, frozenset[str]]]:
    """ADR-0066 P4b: a domain pack's per-section overrides from `cmp:SectionOverride` instances. Returns
    `(rule_scope, claim_types)`: `{section -> 'content'|'context'}` (only sections that pin a scope) and
    `{section -> {claim type value}}` (all claim types when `cmp:appliesToAllClaimTypes` is true, else the explicit
    `cmp:appliesToClaimType` set -- empty for a definitions section). Cached per path."""
    from rdflib import URIRef

    g = Graph()
    g.parse(path, format="turtle")
    all_claim_types = frozenset(load_compliance_vocab().get("ClaimType", set()))
    rule_scope: dict[str, str] = {}
    claim_types: dict[str, frozenset[str]] = {}
    for so in g.subjects(RDF.type, URIRef(_CMP + "SectionOverride")):
        section = str(g.value(so, URIRef(_CMP + "section")) or "").strip()
        if not section:
            continue
        all_flag = g.value(so, URIRef(_CMP + "appliesToAllClaimTypes"))
        if all_flag is not None and bool(all_flag.toPython()):
            claim_types[section] = all_claim_types
        else:
            claim_types[section] = frozenset(
                str(ct).rsplit("#", 1)[-1] for ct in g.objects(so, URIRef(_CMP + "appliesToClaimType")))
        rs = g.value(so, URIRef(_CMP + "overrideRuleScope"))
        if rs is not None:
            rule_scope[section] = str(rs).rsplit("#", 1)[-1]
    return rule_scope, claim_types


@lru_cache(maxsize=4)
def load_shapes_graph(path: str = str(_TTL_PATH)) -> Graph:
    """ADR-0066 P2: the ttl parsed as an rdflib Graph -- its `sh:NodeShape`s ARE the SHACL shapes handed to pyshacl
    at runtime, so the symbolic layer reads the symbolic artifact directly (no Python-built shapes). pyshacl uses
    the shapes and ignores the ttl's non-SHACL triples. Cached per path."""
    g = Graph()
    g.parse(path, format="turtle")
    return g
_CBR = "https://ragwright.local/ontology/contract-bridge#"
_DIMENSION_CLASS = _CBR + "PropertyDimension"


@dataclass(frozen=True)
class ContractOntologyView:
    """The contract domain knowledge parsed out of `contract_bridge.ttl` (all string-keyed by label)."""

    closed_vocab: dict[str, set[str]] = field(default_factory=dict)
    multivalued: set[str] = field(default_factory=set)
    function_applicable_dims: dict[str, set[str]] = field(default_factory=dict)
    permission_polarity: dict[str, set[str]] = field(default_factory=dict)
    restrictive_functions: set[str] = field(default_factory=set)
    value_rollup: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    # issue 0037: ingest synonyms -- {dimension: {normalized surface term: canonical member}}. A skos:broader edge
    # whose BROADER is a closed-vocab member but whose NARROWER is not (a specific surface term). Used at ingest to
    # canonicalize an out-of-vocab extracted value onto its canonical member (else the value is kept verbatim).
    value_synonyms: dict[str, dict[str, str]] = field(default_factory=dict)
    # DD-7 (ADR-0066): the entity-graph taxonomy -- entity node types (cbr:EntityNodeType) + entity-to-entity
    # relationship types (cbr:EntityRelationshipType), by label. Was the Python EntityType/RelationshipType enum.
    entity_types: set[str] = field(default_factory=set)
    relationship_types: set[str] = field(default_factory=set)


def _label(g: Graph, node) -> str:
    lbl = g.value(node, RDFS.label)
    return str(lbl) if lbl is not None else ""


def load_contract_ontology(path: Path | str = _TTL_PATH) -> ContractOntologyView:
    """Parse the contract bridge ontology into a `ContractOntologyView`."""
    g = Graph()
    g.parse(str(path), format="turtle")

    closed_vocab: dict[str, set[str]] = {}
    multivalued: set[str] = set()
    dim_label_by_node: dict[str, str] = {}      # dimension IRI -> label (for the applicability shapes)
    value_label_by_node: dict[str, str] = {}    # value IRI -> label (for rollups + oneOf)
    value_dim_by_node: dict[str, str] = {}       # value IRI -> its dimension label (for rollups)

    for dim in g.subjects(RDF.type, _dim_class()):
        label = _label(g, dim)
        dim_label_by_node[str(dim)] = label
        if str(g.value(dim, _cbr("cardinality")) or "") == "list":
            multivalued.add(label)
        one_of = g.value(dim, OWL.oneOf)
        if one_of is not None:  # a CLOSED dimension (open-valued dims omit owl:oneOf)
            members = list(Collection(g, one_of))
            values = set()
            for m in members:
                vlabel = _label(g, m)
                values.add(vlabel)
                value_label_by_node[str(m)] = vlabel
                value_dim_by_node[str(m)] = label
            closed_vocab[label] = values

    # Applicability + deontic: one sh:NodeShape per clause function.
    function_applicable_dims: dict[str, set[str]] = {}
    permission_polarity: dict[str, set[str]] = {}
    restrictive_functions: set[str] = set()
    for shape in g.subjects(RDF.type, SH.NodeShape):
        target = g.value(shape, SH.targetClass)
        fn = _label(g, target)
        if not fn:
            continue
        dims: set[str] = set()
        for prop in g.objects(shape, SH.property):
            path = g.value(prop, SH.path)
            dlabel = dim_label_by_node.get(str(path), _label(g, path))
            dims.add(dlabel)
            in_list = g.value(prop, SH["in"])
            if in_list is not None:  # deontic: restrictive function forbids the permission-polarity values
                restrictive_functions.add(fn)
                allowed = {str(x) for x in Collection(g, in_list)}
                forbidden = closed_vocab.get(dlabel, set()) - allowed
                if forbidden:
                    permission_polarity.setdefault(dlabel, set()).update(forbidden)
        function_applicable_dims[fn] = dims

    # Value rollups: skos:broader between value individuals.
    value_rollup: dict[str, dict[str, set[str]]] = {}
    # issue 0037: ingest synonyms -- {dim: {normalized surface: canonical member}} from a skos:broader edge whose
    # BROADER is a closed member but whose NARROWER is not (a specific surface term); surface = narrower local-name
    # + its skos:altLabels. (A narrower that IS a member is a query-side rollup, handled above.)
    value_synonyms: dict[str, dict[str, str]] = {}
    for narrower, broader in g.subject_objects(SKOS.broader):
        dim = value_dim_by_node.get(str(narrower))
        if dim is not None:  # narrower is itself a vocab member -> a query-side value rollup
            value_rollup.setdefault(dim, {}).setdefault(
                value_label_by_node.get(str(narrower), ""), set()).add(
                value_label_by_node.get(str(broader), ""))
            continue
        bdim = value_dim_by_node.get(str(broader))  # narrower is a surface synonym -> map to the broader member
        bval = value_label_by_node.get(str(broader))
        if not bdim or not bval:
            continue
        surfaces = {_label(g, narrower) or str(narrower).rsplit("#", 1)[-1]}
        surfaces |= {str(a) for a in g.objects(narrower, SKOS.altLabel)}
        for s in surfaces:
            key = re.sub(r"[^A-Za-z0-9]+", "", s).lower()
            if key:
                value_synonyms.setdefault(bdim, {})[key] = bval

    # DD-7: the entity-graph taxonomy (entity node types + entity-to-entity relationship types), by label.
    entity_types = {_label(g, s) for s in g.subjects(RDF.type, _cbr("EntityNodeType"))}
    relationship_types = {_label(g, s) for s in g.subjects(RDF.type, _cbr("EntityRelationshipType"))}

    return ContractOntologyView(
        closed_vocab=closed_vocab, multivalued=multivalued,
        function_applicable_dims=function_applicable_dims,
        permission_polarity=permission_polarity, restrictive_functions=restrictive_functions,
        value_synonyms=value_synonyms,
        value_rollup=value_rollup,
        entity_types=entity_types, relationship_types=relationship_types)


@dataclass(frozen=True)
class KgVertexType:
    """ADR-0067 P5b: a domain KG vertex-type declaration the store creates -- name, its `(property, SQL type)`
    pairs, and the property to build a UNIQUE index on (if any)."""

    name: str
    properties: tuple[tuple[str, str], ...]
    unique_index: str | None


@lru_cache(maxsize=8)
def load_kg_schema(path: str | None = None) -> tuple[tuple[KgVertexType, ...], frozenset[str]]:
    """ADR-0067 P5b: the DOMAIN KG node/edge storage schema from the ttl -- `(vertex types, structural edge names)`.
    The engine infra (Chunk/Span/Entity) stays generic in store code; these domain types are pack-declared. Cached.
    `path=None` is the engine's reference CONTRACT pack; a new domain passes its OWN pack `.ttl` (AC-journey)."""
    g = Graph()
    g.parse(str(path or _TTL_PATH), format="turtle")
    vertices = []
    for v in g.subjects(RDF.type, _cbr("KgVertexType")):
        props = tuple(sorted((str(p).split(":", 1)[0], str(p).split(":", 1)[1])
                             for p in g.objects(v, _cbr("kgProperty")) if ":" in str(p)))
        ui = g.value(v, _cbr("uniqueIndexOn"))
        vertices.append(KgVertexType(name=str(g.value(v, _cbr("vertexName"))), properties=props,
                                     unique_index=(str(ui) if ui is not None else None)))
    vertices.sort(key=lambda x: x.name)
    edges = frozenset(str(g.value(e, _cbr("edgeName")))
                      for e in g.subjects(RDF.type, _cbr("KgStructuralEdge")))
    return tuple(vertices), edges


@lru_cache(maxsize=4)
def load_typed_edges(path: str = str(_TTL_PATH)) -> tuple[dict[str, str], dict[str, str]]:
    """ADR-0067 P5a: the KG typed-edge map from contract_bridge.ttl. Returns `(dim_edge, edge_iri)`:
    `{dimension value -> KG edge type}` (from `cbr:kgEdge`) and `{edge type -> predicate IRI}` (from
    `cbr:predicateIri` on each `cbr:KgEdgeType`). The property-graph edge types are ontology-authoritative;
    `store/arcadedb.py` builds `_TYPED_DIMENSION_EDGE` / `_edge_predicate_iri` from this. Cached per path."""
    g = Graph()
    g.parse(str(path), format="turtle")
    edge_iri = {str(g.value(e, RDFS.label)): str(g.value(e, _cbr("predicateIri")))
                for e in g.subjects(RDF.type, _cbr("KgEdgeType"))}
    dim_edge = {str(g.value(dim, RDFS.label)): str(g.value(edge, RDFS.label))
                for dim, edge in g.subject_objects(_cbr("kgEdge"))}
    return dim_edge, edge_iri


def _dim_class():
    from rdflib import URIRef
    return URIRef(_DIMENSION_CLASS)


def _cbr(frag: str):
    from rdflib import URIRef
    return URIRef(_CBR + frag)


def load_template_fields(path: Path | str = _TTL_PATH):
    """ADR-0066 P1b-1: the extraction template's fields as captured in the ttl, as `TemplateFieldSpec`s in the
    same order the template declares them (`cbr:fieldOrder`). Round-trips `bootstrap_template_capture` -- the drift
    test asserts this equals a fresh introspection of `clause_template.py`."""
    from rag_wright.ontology.template_introspect import TemplateFieldSpec

    g = Graph()
    g.parse(str(path), format="turtle")
    specs: list = []
    for node in g.subjects(RDF.type, _cbr("TemplateField")):
        order = g.value(node, _cbr("fieldOrder"))
        ml = g.value(node, _cbr("maxLength"))
        specs.append((int(order), TemplateFieldSpec(
            model=str(g.value(node, _cbr("onModel"))),
            name=str(g.value(node, RDFS.label)),
            kind=str(g.value(node, _cbr("fieldKind"))),
            default_token=str(g.value(node, _cbr("default"))),
            definition=str(g.value(node, SKOS.definition) or ""),
            enum_class=(str(v) if (v := g.value(node, _cbr("enumClass"))) is not None else None),
            model_ref=(str(v) if (v := g.value(node, _cbr("modelRef"))) is not None else None),
            edge_label=(str(v) if (v := g.value(node, _cbr("edgeLabel"))) is not None else None),
            max_length=(int(ml) if ml is not None else None),
            examples=(tuple(str(e) for e in Collection(g, exlist))
                      if (exlist := g.value(node, _cbr("examples"))) is not None else ()),
        )))
    return [spec for _, spec in sorted(specs, key=lambda t: t[0])]
