"""ADR-0066 P1b-1, ONE-TIME BOOTSTRAP: capture the hand-maintained extraction template's schema + knowledge into
`contract_bridge.ttl`, so the ttl fully describes the template (the substrate P1b-2 generates from). AUGMENTS the
ttl (appends a section). Faithful capture -- verified by tests/ontology/test_template_captured_in_ttl.py (ttl ==
a fresh introspection of clause_template.py). After P1b-2 the template is generated FROM the ttl; do NOT re-run
this to "sync" -- edit the ttl.

Per field (`cbr:field_<Model>__<name>`): kind, default, enum/model ref, edge label, max_length, order, the
`skos:definition` (verbatim LOOK-FOR description; omitted when empty), and `skos:example`s.

Usage: uv run python scripts/bootstrap_template_capture.py
"""

from __future__ import annotations

from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection
from rdflib.namespace import RDF, RDFS, SKOS, XSD

from rag_wright.ontology.template_introspect import introspect_template_fields

CBR = Namespace("https://ragwright.local/ontology/contract-bridge#")
TTL = Path(__file__).parents[1] / "src" / "rag_wright" / "ontology" / "contract_bridge.ttl"


def build_augmentation() -> Graph:
    g = Graph()
    g.bind("cbr", CBR)
    g.bind("skos", SKOS)
    for i, s in enumerate(introspect_template_fields()):
        node = CBR[f"field_{s.model}__{s.name}"]
        g.add((node, RDF.type, CBR.TemplateField))
        g.add((node, RDFS.label, Literal(s.name)))
        g.add((node, CBR.onModel, Literal(s.model)))
        g.add((node, CBR.fieldKind, Literal(s.kind)))
        g.add((node, CBR.default, Literal(s.default_token)))
        g.add((node, CBR.fieldOrder, Literal(i, datatype=XSD.integer)))
        if s.definition:
            g.add((node, SKOS.definition, Literal(s.definition)))
        if s.enum_class:
            g.add((node, CBR.enumClass, Literal(s.enum_class)))
        if s.model_ref:
            g.add((node, CBR.modelRef, Literal(s.model_ref)))
        if s.edge_label:
            g.add((node, CBR.edgeLabel, Literal(s.edge_label)))
        if s.max_length is not None:
            g.add((node, CBR.maxLength, Literal(s.max_length, datatype=XSD.integer)))
        if s.examples:  # ORDERED (an rdf:List): the prompt shows examples in the template's declared order
            head = URIRef(f"{node}_examples")
            Collection(g, head, [Literal(ex) for ex in s.examples])
            g.add((node, CBR.examples, head))
    return g


def main() -> None:
    aug = build_augmentation()
    block = aug.serialize(format="turtle")
    body = "\n".join(ln for ln in block.splitlines() if not ln.startswith(("@prefix", "@base", "PREFIX")))
    header = (
        "\n# =============================================================================\n"
        "# ADR-0066 P1b-1 -- the EXTRACTION TEMPLATE captured (schema + knowledge), keyed by field.\n"
        "# Bootstrapped ONCE from clause_template.py; the ttl describes the template henceforth.\n"
        "# Verified by tests/ontology/test_template_captured_in_ttl.py (ttl == introspect(clause_template)).\n"
        "# =============================================================================\n"
    )
    TTL.write_text(TTL.read_text(encoding="utf-8").rstrip() + "\n" + header + body + "\n", encoding="utf-8")
    print(f"captured {len(aug)} triples for the extraction template into {TTL}")


if __name__ == "__main__":
    main()
