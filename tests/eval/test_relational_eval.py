"""GP-2: the relational-archetype GRAPH leg. `graph_leg_recall` answers each EDGAR relational golden
question by traversing the entity KG (graph_query from the anchor's IDENTITY to the question's hop depth)
and scores recall of the golden answer identities. Hermetic: a stub store returns fixture neighbor rows,
so no ArcadeDB. Verifies recall math, per-hop aggregation, and that the anchor identity (incl. a
PRIVATE:<key> sentinel) is the query key."""

from __future__ import annotations

from eval.multihop import AnswerEntity, RelationalQuestion
from eval.relational_eval import graph_leg_recall


class _StubStore:
    """Implements just `graph_neighbors` (what graph_query needs), keyed by (entity_id, max_hops)."""

    def __init__(self, rows_by_call: dict):
        self._rows = rows_by_call

    def graph_neighbors(self, entity_id, *, relationship_type, max_hops):
        return self._rows.get((entity_id, max_hops), [])


def _row(target_id: str, name: str, hops: int = 1) -> dict:
    return {"target_id": target_id, "target_name": name, "hops": hops,
            "path_entity_ids": ["anchor", target_id], "path_chunk_ids": ["c"],
            "path_confidences": ["Extracted"]}


def test_full_and_partial_recall_and_per_hop_mean():
    hub = AnswerEntity(entity_key="acme", representative="Acme", entity_id="0000000001")
    beta = AnswerEntity(entity_key="beta", representative="Beta", entity_id="0000000002")
    gamma = AnswerEntity(entity_key="gamma", representative="Gamma", entity_id="0000000003")
    solo = AnswerEntity(entity_key="solo", representative="Solo", entity_id="0000000009")

    q_full = RelationalQuestion(qid="rel:1hop:acme", hop_count=1, question="?", anchor=hub,
                                answer_entities=[beta, gamma])
    q_partial = RelationalQuestion(qid="rel:1hop:solo", hop_count=1, question="?", anchor=solo,
                                   answer_entities=[beta, gamma])
    store = _StubStore({
        ("0000000001", 1): [_row("0000000002", "Beta"), _row("0000000003", "Gamma")],  # both -> 1.0
        ("0000000009", 1): [_row("0000000002", "Beta")],  # one of two -> 0.5
    })

    res = graph_leg_recall([q_full, q_partial], store)
    by_qid = {d["qid"]: d for d in res["per_question"]}
    assert by_qid["rel:1hop:acme"]["recall"] == 1.0
    assert by_qid["rel:1hop:solo"]["recall"] == 0.5
    assert res["1hop"] == 0.75  # mean(1.0, 0.5)
    assert res["overall"] == 0.75
    assert res["counts"]["1hop"] == 2


def test_private_anchor_identity_is_the_query_key():
    # a PRIVATE anchor -> identity "PRIVATE:premier nutrition"; the runner must query THAT id, not the key.
    hub = AnswerEntity(entity_key="premier nutrition", representative="Premier", entity_id=None, private=True)
    fonterra = AnswerEntity(entity_key="fonterra", representative="Fonterra", entity_id="0000000005")
    q = RelationalQuestion(qid="rel:1hop:pn", hop_count=1, question="?", anchor=hub,
                           answer_entities=[fonterra])
    store = _StubStore({("PRIVATE:premier nutrition", 1): [_row("0000000005", "Fonterra")]})

    res = graph_leg_recall([q], store)
    assert res["per_question"][0]["recall"] == 1.0  # only passes if PRIVATE:<key> was the traversal key


def test_two_hop_uses_hop_depth_two():
    hub = AnswerEntity(entity_key="h", representative="Hub", entity_id="0000000001")
    tgt = AnswerEntity(entity_key="t", representative="Target", entity_id="0000000007")
    q = RelationalQuestion(qid="rel:2hop:h", hop_count=2, question="?", anchor=hub, answer_entities=[tgt])
    store = _StubStore({("0000000001", 2): [_row("0000000007", "Target", hops=2)]})  # only answers at hops=2

    res = graph_leg_recall([q], store)
    assert res["2hop"] == 1.0 and res["counts"]["2hop"] == 1
