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

from dataclasses import dataclass, field
from pathlib import Path

from rdflib import Graph
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, RDFS, SH, SKOS

_TTL_PATH = Path(__file__).with_name("contract_bridge.ttl")
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
    for narrower, broader in g.subject_objects(SKOS.broader):
        dim = value_dim_by_node.get(str(narrower))
        if dim is None:
            continue
        value_rollup.setdefault(dim, {}).setdefault(
            value_label_by_node.get(str(narrower), ""), set()).add(
            value_label_by_node.get(str(broader), ""))

    return ContractOntologyView(
        closed_vocab=closed_vocab, multivalued=multivalued,
        function_applicable_dims=function_applicable_dims,
        permission_polarity=permission_polarity, restrictive_functions=restrictive_functions,
        value_rollup=value_rollup)


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
