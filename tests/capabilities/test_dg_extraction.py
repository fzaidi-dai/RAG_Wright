"""GP-1B.1: the docling-graph contract template. Verifies the entity/edge markers docling-graph reads
(graph_id_fields identity, PARTY_TO edge_label) and the local `edge()` helper. Hermetic (no docling-graph
pipeline, no LLM)."""

from __future__ import annotations

from pydantic import BaseModel

from rag_wright.capabilities.dg_extraction import ContractParties, Party, edge


def test_entity_identity_markers():
    assert ContractParties.model_config["graph_id_fields"] == ["title"]
    assert Party.model_config["graph_id_fields"] == ["name"]


def test_parties_is_a_party_to_edge():
    jse = ContractParties.model_fields["parties"].json_schema_extra
    assert jse["edge_label"] == "PARTY_TO"


def test_edge_helper_sets_flags():
    class _T(BaseModel):
        x: list = edge("R", default_factory=list, reference=True, closed_catalog=True)

    jse = _T.model_fields["x"].json_schema_extra
    assert jse["edge_label"] == "R"
    assert jse["graph_reference"] is True
    assert jse["reference_closed_catalog"] is True


def test_instance_roundtrips():
    c = ContractParties(title="Acme-Beta Agreement",
                        parties=[Party(name="Acme Corp"), Party(name="Beta Inc")])
    assert c.title == "Acme-Beta Agreement"
    assert [p.name for p in c.parties] == ["Acme Corp", "Beta Inc"]
    assert ContractParties(title="Empty").parties == []  # default_factory=list, extract-nothing case


def test_docling_graph_accepts_the_template():
    """Lint: docling-graph's own GraphConverter turns a ContractParties instance into the expected
    entity nodes + PARTY_TO edges (no LLM) -- proves the template is a valid docling-graph template."""
    from docling_graph.core.converters.graph_converter import GraphConverter

    inst = ContractParties(title="Acme-Beta Sponsorship Agreement",
                           parties=[Party(name="Acme Corp"), Party(name="Beta Inc")])
    graph, _meta = GraphConverter().pydantic_list_to_graph([inst])
    labels = [d.get("label") for _, d in graph.nodes(data=True)]
    assert "ContractParties" in labels and labels.count("Party") == 2
    assert "PARTY_TO" in {d.get("label") for _, _, d in graph.edges(data=True)}
