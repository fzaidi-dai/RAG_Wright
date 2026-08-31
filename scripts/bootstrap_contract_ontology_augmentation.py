"""ADR-0066 Phase 0, ONE-TIME BOOTSTRAP (like the original clause_template generation): lift today's Python
contract-domain knowledge into `contract_bridge.ttl` as a uniform, machine-round-trippable layer, so the ttl
becomes the COMPLETE authoritative source. AUGMENTS the existing ttl (keeps its FOLIO alignments + comments);
appends a new section. Run ONCE, review the emitted ttl, commit it. After Phase 1 the ttl is authoritative and
the Python is generated FROM it -- do NOT re-run this to "sync"; edit the ttl.

Emits, keyed uniformly by rdfs:label (so the loader never decodes an IRI):
- one `cbr:PropertyDimension` node per dimension: label, `cbr:cardinality scalar|list`, and (closed dims)
  `owl:oneOf` of `cbr:Value` individuals; the dim node IRI is `cbr:dim_<value>` == `_dim_property` so the shapes'
  `sh:path` points at it.
- one `cbr:ClauseFunction` node per function (label), IRI `cbr:Function_<enc>` == `_function_class`.
- the SHACL shapes from `_shapes_graph()` (function->applicable-dims `sh:closed`, scalar `sh:maxCount 1`,
  restrictive-function `sh:in` deontic) verbatim.
- `skos:broader` for the value rollups (VALUE_ROLLUP).

Usage: uv run python scripts/bootstrap_contract_ontology_augmentation.py
"""

from __future__ import annotations

from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, RDFS, SKOS

from rag_wright.contracts.property import CLOSED_VOCAB, PropertyDimension
from rag_wright.contracts.value_match import VALUE_ROLLUP
from rag_wright.spans.symbolic_validation import (
    FUNCTION_APPLICABLE_DIMS,
    MULTI_VALUED_DIMENSIONS,
    _dim_property,
    _function_class,
    _shapes_graph,
)

CBR = Namespace("https://ragwright.local/ontology/contract-bridge#")
TTL = Path(__file__).parents[1] / "src" / "rag_wright" / "ontology" / "contract_bridge.ttl"
SH_PREFIX = "@prefix sh:    <http://www.w3.org/ns/shacl#> .\n"


def build_augmentation() -> Graph:
    g = Graph()
    g.bind("cbr", CBR)
    g.bind("skos", SKOS)
    g.bind("owl", OWL)

    # 1. Dimension nodes: label + cardinality + (closed) owl:oneOf of Value individuals.
    for dim in PropertyDimension:
        node = _dim_property(dim)  # cbr:dim_<value> -- the SAME IRI the shapes use as sh:path
        g.add((node, RDF.type, CBR.PropertyDimension))
        g.add((node, RDFS.label, Literal(dim.value)))
        g.add((node, CBR.cardinality, Literal("list" if dim in MULTI_VALUED_DIMENSIONS else "scalar")))
        vocab = CLOSED_VOCAB.get(dim)
        if vocab is not None:
            members = []
            for v in sorted(vocab):
                vn = CBR[f"val_{dim.value}_{v}"]
                g.add((vn, RDF.type, CBR.Value))
                g.add((vn, RDFS.label, Literal(v)))
                members.append(vn)
            head = URIRef(f"{node}_vocab")
            Collection(g, head, members)
            g.add((node, OWL.oneOf, head))

    # 2. Function class nodes: label (IRI == _function_class, targeted by the shapes).
    for fn in FUNCTION_APPLICABLE_DIMS:
        fc = _function_class(fn)
        g.add((fc, RDF.type, CBR.ClauseFunction))
        g.add((fc, RDFS.label, Literal(fn)))

    # 3. The SHACL shapes (verbatim from the current in-memory builder).
    g += _shapes_graph()

    # 4. Value rollups -> skos:broader between the value individuals.
    for dim, mapping in VALUE_ROLLUP.items():
        for val, broaders in mapping.items():
            for b in broaders:
                g.add((CBR[f"val_{dim}_{val}"], SKOS.broader, CBR[f"val_{dim}_{b}"]))
    return g


def main() -> None:
    aug = build_augmentation()
    block = aug.serialize(format="turtle")
    # strip the aug block's own @prefix/@base lines (the main file declares them); keep only triples.
    body = "\n".join(ln for ln in block.splitlines() if not ln.startswith(("@prefix", "@base", "PREFIX")))

    text = TTL.read_text(encoding="utf-8")
    if "@prefix sh:" not in text:  # the shapes need the shacl prefix
        text = text.replace("@prefix skos:", SH_PREFIX + "@prefix skos:", 1)

    header = (
        "\n# =============================================================================\n"
        "# ADR-0066 Phase 0 -- the MACHINE-AUTHORITATIVE knowledge layer (uniform, label-keyed).\n"
        "# Bootstrapped ONCE from the Python constants; the ttl is the source of truth henceforth.\n"
        "#   cbr:PropertyDimension nodes (owl:oneOf vocab + cbr:cardinality) ; cbr:ClauseFunction nodes ;\n"
        "#   sh:NodeShape applicability/cardinality/deontic shapes ; skos:broader value rollups.\n"
        "# Verified by tests/ontology/test_ttl_is_source_of_truth.py (ttl == the Python constants).\n"
        "# =============================================================================\n"
    )
    TTL.write_text(text.rstrip() + "\n" + header + body + "\n", encoding="utf-8")
    print(f"augmented {TTL} (+{len(aug)} triples)")


if __name__ == "__main__":
    main()
