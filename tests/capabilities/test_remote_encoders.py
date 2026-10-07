"""MS1-6 (ADR-0039): the A100-backed query-encoder adapters. Hermetic -- injected `post`, no network."""

from __future__ import annotations

from rag_wright.capabilities.remote_encoders import (
    RemoteBGEEmbedder,
    query_embedder,
    stack_url,
)
from rag_wright.spans.legalbert_classifier import (  # the reference pack's query-side classifier (ING-8b)
    RemoteLegalBertClassifier,
    query_classifier,
)

_URL = "https://stack.modal.run"


def _recorder(response):
    calls = []

    def post(url, payload, timeout=120):
        calls.append((url, payload))
        return response

    return post, calls


def test_embedder_encode_batch_hits_embed_and_parses_sparse_int_keys():
    # JSON object keys arrive as strings; the adapter must restore int token ids
    post, calls = _recorder({"dense": [[0.1, 0.2], [0.3, 0.4]], "sparse": [{"5": 0.9}, {"7": 0.8}]})
    dense, sparse = RemoteBGEEmbedder(_URL, post=post).encode_batch(["a", "b"])
    assert dense == [[0.1, 0.2], [0.3, 0.4]]
    assert sparse == [{5: 0.9}, {7: 0.8}]  # keys are ints
    assert calls == [(_URL + "/embed", {"texts": ["a", "b"]})]


def test_embedder_encode_dense_and_sparse_single():
    post, calls = _recorder({"dense": [[1.0, 2.0]], "sparse": [{"42": 0.5}]})
    emb = RemoteBGEEmbedder(_URL, post=post)
    assert emb.encode_dense("x") == [1.0, 2.0]
    assert emb.encode_sparse("x") == {42: 0.5}
    assert all(payload == {"text": "x"} for _, payload in calls)


def test_classifier_classify_and_topk():
    post, calls = _recorder({"labels": ["Governing Law"], "topk": [["Governing Law", "Cap On Liability"]]})
    clf = RemoteLegalBertClassifier(_URL, post=post)
    assert clf.classify(["q"]) == ["Governing Law"]
    assert clf.classify_topk(["q"], k=2) == [["Governing Law", "Cap On Liability"]]
    assert calls[0] == (_URL + "/classify", {"texts": ["q"]})
    assert calls[1] == (_URL + "/classify", {"texts": ["q"], "k": 2})


def test_classifier_empty_input_is_empty_output_no_call():
    post, calls = _recorder({})
    clf = RemoteLegalBertClassifier(_URL, post=post)
    assert clf.classify([]) == [] and clf.classify_topk([]) == []
    assert calls == []  # no network for empty input


def test_query_seams_select_remote_when_stack_url_set(monkeypatch):
    monkeypatch.setenv("STACK_URL", _URL)
    assert stack_url() == _URL
    assert isinstance(query_embedder(), RemoteBGEEmbedder)
    assert isinstance(query_classifier(), RemoteLegalBertClassifier)


def test_stack_url_none_when_unset(monkeypatch):
    monkeypatch.delenv("STACK_URL", raising=False)
    assert stack_url() is None
