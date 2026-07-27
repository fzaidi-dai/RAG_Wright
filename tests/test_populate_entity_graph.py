"""GP-1(A): `build_graph` materializes the verified coparty adjacency into Entity nodes + undirected
CONTRACTS_WITH edges, with node ids matching eval.multihop (CIK / PRIVATE:<key>), SKIP excluded, and
co-parties outside the verified set dropped. Hermetic (tiny fake verification set; no store)."""

from __future__ import annotations

from scripts.populate_entity_graph import build_graph


def _vset(entities):
    return {"entities": entities}


def test_cik_and_private_nodes_and_undirected_edges():
    vset = _vset([
        {"entity_key": "acme", "representative": "Acme Corp", "resolution": "0000000001",
         "coparty_keys": ["beta"]},
        {"entity_key": "beta", "representative": "Beta Inc", "resolution": "0000000002",
         "coparty_keys": ["acme", "gamma"]},
        {"entity_key": "gamma", "representative": "Gamma LLC", "resolution": "PRIVATE",
         "coparty_keys": ["beta"]},
    ])
    nodes, edges = build_graph(vset)
    by = {n.node_key: n for n in nodes}
    assert set(by) == {"0000000001", "0000000002", "PRIVATE:gamma"}  # CIK filers + PRIVATE sentinel
    assert by["0000000001"].entity_id == "0000000001"  # CIK carried for filers
    assert by["PRIVATE:gamma"].entity_id == ""  # empty for private
    # undirected: acme~beta once (declared on both sides), beta~gamma once -> 2 edges
    assert {tuple(sorted((e.source_key, e.target_key))) for e in edges} == {
        ("0000000001", "0000000002"), ("0000000002", "PRIVATE:gamma")}
    assert all(e.relationship_type == "Contracts With" for e in edges)


def test_skip_excluded_and_outside_coparty_dropped():
    vset = _vset([
        {"entity_key": "acme", "representative": "Acme", "resolution": "0000000001",
         "coparty_keys": ["ghost", "skipco"]},  # ghost not in set; skipco is SKIP
        {"entity_key": "skipco", "representative": "Skip", "resolution": "SKIP",
         "coparty_keys": ["acme"]},
    ])
    nodes, edges = build_graph(vset)
    assert {n.node_key for n in nodes} == {"0000000001"}  # SKIP entity excluded
    assert edges == []  # ghost outside the set, skipco excluded -> no ground-truth edge
