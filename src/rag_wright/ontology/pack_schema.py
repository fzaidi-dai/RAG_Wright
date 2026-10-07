"""ING-8b: the GENERIC pack-schema reader -- a domain pack declares its KG vertex types and structural edges in the
engine's meta-vocabulary (`eng:KgVertexType` / `eng:vertexName` / `eng:kgProperty` / `eng:uniqueIndexOn` /
`eng:KgStructuralEdge` / `eng:edgeName`); the store creates exactly what a pack declares (ADR-0067 P5b). Domain
concepts (a pack's own extra vocabulary, e.g. the contract pack's typed property edges) are read by that pack."""

from __future__ import annotations

from dataclasses import dataclass

from rdflib import Graph
from rdflib.namespace import RDF

_ENG = "https://ragwright.local/ontology/engine#"


def _eng(frag: str):
    from rdflib import URIRef
    return URIRef(_ENG + frag)


@dataclass(frozen=True)
class KgVertexType:
    """ADR-0067 P5b: a domain KG vertex-type declaration the store creates -- name, its `(property, SQL type)`
    pairs, and the property to build a UNIQUE index on (if any)."""

    name: str
    properties: tuple[tuple[str, str], ...]
    unique_index: str | None


def load_kg_schema(path: str) -> tuple[tuple[KgVertexType, ...], frozenset[str]]:
    """ADR-0067 P5b: the DOMAIN KG node/edge storage schema from the ttl -- `(vertex types, structural edge names)`.
    The engine infra (Chunk/Span/Entity) stays generic in store code; these domain types are pack-declared. Cached.
    `path` is the pack's own `.ttl` (AC-journey); there is no default pack (ING-8a/8b)."""
    g = Graph()
    g.parse(str(path), format="turtle")
    vertices = []
    for v in g.subjects(RDF.type, _eng("KgVertexType")):
        props = tuple(sorted((str(p).split(":", 1)[0], str(p).split(":", 1)[1])
                             for p in g.objects(v, _eng("kgProperty")) if ":" in str(p)))
        ui = g.value(v, _eng("uniqueIndexOn"))
        vertices.append(KgVertexType(name=str(g.value(v, _eng("vertexName"))), properties=props,
                                     unique_index=(str(ui) if ui is not None else None)))
    vertices.sort(key=lambda x: x.name)
    edges = frozenset(str(g.value(e, _eng("edgeName")))
                      for e in g.subjects(RDF.type, _eng("KgStructuralEdge")))
    return tuple(vertices), edges
