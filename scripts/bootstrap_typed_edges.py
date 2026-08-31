"""ADR-0067 P5a, ONE-TIME BOOTSTRAP: lift the Python typed-edge map (store/arcadedb.py::_TYPED_DIMENSION_EDGE +
_edge_predicate_iri) into contract_bridge.ttl, so the KG edge types are ontology-authoritative (like P2 did for
the SHACL shapes). AUGMENTS the ttl (appends a section). Verified by tests + a live A/B. Run ONCE; then the ttl is
the source -- edit the ttl, not the Python.

Emits: one `cbr:KgEdgeType` per distinct edge (rdfs:label + cbr:predicateIri = the ODRL/CBR predicate IRI); and
`cbr:kgEdge` on each `cbr:dim_<value>` dimension node pointing at its edge type.

Usage: uv run python scripts/bootstrap_typed_edges.py
"""

from __future__ import annotations

from pathlib import Path

from rdflib import Graph, Literal, Namespace
from rdflib.namespace import RDF, RDFS

from rag_wright.store.arcadedb import _TYPED_DIMENSION_EDGE, _edge_predicate_iri

CBR = Namespace("https://ragwright.local/ontology/contract-bridge#")
TTL = Path(__file__).parents[1] / "src" / "rag_wright" / "ontology" / "contract_bridge.ttl"


def build_augmentation() -> Graph:
    g = Graph()
    g.bind("cbr", CBR)
    for edge in sorted({e for e in _TYPED_DIMENSION_EDGE.values()}):
        node = CBR[edge]
        g.add((node, RDF.type, CBR.KgEdgeType))
        g.add((node, RDFS.label, Literal(edge)))
        g.add((node, CBR.predicateIri, Literal(_edge_predicate_iri(edge))))
    for dim in sorted(_TYPED_DIMENSION_EDGE, key=lambda d: d.value):
        g.add((CBR[f"dim_{dim.value}"], CBR.kgEdge, CBR[_TYPED_DIMENSION_EDGE[dim]]))
    return g


def main() -> None:
    aug = build_augmentation()
    block = "\n".join(ln for ln in aug.serialize(format="turtle").splitlines()
                      if not ln.startswith(("@prefix", "@base", "PREFIX")))
    header = (
        "\n# =============================================================================\n"
        "# ADR-0067 P5a -- the KG typed-edge map (the property GRAPH storage schema), keyed by dimension.\n"
        "# cbr:KgEdgeType (predicate IRI: ODRL for deontic edges, cbr: otherwise) ; cbr:kgEdge per dimension.\n"
        "# Loaded by store/arcadedb.py; the property-graph edge types are now ontology-authoritative.\n"
        "# =============================================================================\n"
    )
    TTL.write_text(TTL.read_text(encoding="utf-8").rstrip() + "\n" + header + block + "\n", encoding="utf-8")
    print(f"appended {len(aug)} typed-edge triples ({len(set(_TYPED_DIMENSION_EDGE.values()))} edge types) to {TTL}")


if __name__ == "__main__":
    main()
