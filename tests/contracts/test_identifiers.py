"""Tests for the shared identifier contracts (T1, FR-S.2 / FR-S.3, RAC-1).

These identifiers are load-bearing: a re-chunk that changes a `chunk_id` breaks the link between
a chunk and its extracted graph nodes, and a non-canonical `entity_id` fragments the graph. So the
tests pin the two properties that matter: determinism (identical inputs, identical id) and
canonical normalization (surface-form variants collapse to one id), plus frozen/validated shape.
"""

import hashlib

import pytest
from pydantic import ValidationError

from rag_wright.contracts.identifiers import ChunkId, EntityId


# --- ChunkId: determinism (RAC-1, FR-S.2) ---------------------------------------------------


def test_chunk_id_is_deterministic_for_identical_inputs():
    a = ChunkId.of("doc-1", 0, "the quick brown fox")
    b = ChunkId.of("doc-1", 0, "the quick brown fox")
    assert a == b
    assert a.value == b.value
    assert hash(a) == hash(b)


def test_chunk_id_content_hash_is_sha256_of_content():
    content = "clause 7: termination for convenience"
    cid = ChunkId.of("doc-1", 3, content)
    assert cid.content_hash == hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_chunk_id_of_accepts_bytes_and_matches_str():
    text = "identical payload"
    assert ChunkId.of("d", 0, text) == ChunkId.of("d", 0, text.encode("utf-8"))


@pytest.mark.parametrize(
    "a, b",
    [
        (ChunkId.of("doc-1", 0, "alpha"), ChunkId.of("doc-1", 0, "beta")),  # content differs
        (ChunkId.of("doc-1", 0, "alpha"), ChunkId.of("doc-1", 1, "alpha")),  # index differs
        (ChunkId.of("doc-1", 0, "alpha"), ChunkId.of("doc-2", 0, "alpha")),  # source differs
    ],
)
def test_chunk_id_differs_when_any_component_differs(a, b):
    assert a != b
    assert a.value != b.value


def test_chunk_id_canonical_value_shape():
    cid = ChunkId.of("doc-1", 5, "x")
    expected_hash = hashlib.sha256(b"x").hexdigest()
    assert cid.value == f"doc-1:5:{expected_hash}"
    assert str(cid) == cid.value


# --- ChunkId: frozen + validated shape (RAC-1) ----------------------------------------------


def test_chunk_id_is_frozen():
    cid = ChunkId.of("doc-1", 0, "x")
    with pytest.raises(ValidationError):
        cid.chunk_index = 9


def test_chunk_id_is_hashable_and_usable_as_key():
    cid = ChunkId.of("doc-1", 0, "x")
    seen = {cid: "record"}
    assert seen[ChunkId.of("doc-1", 0, "x")] == "record"


def test_chunk_id_rejects_negative_index():
    with pytest.raises(ValidationError):
        ChunkId(source_doc_id="doc-1", chunk_index=-1, content_hash="a" * 64)


def test_chunk_id_rejects_empty_source():
    with pytest.raises(ValidationError):
        ChunkId(source_doc_id="   ", chunk_index=0, content_hash="a" * 64)


@pytest.mark.parametrize("bad_source", ["a:b", "has space", "a/b", "a\tb", "a:1"])
def test_chunk_id_rejects_delimiter_unsafe_source(bad_source):
    # source_doc_id must stay delimiter-safe so `.value` is unambiguous to match and parse.
    with pytest.raises(ValidationError):
        ChunkId(source_doc_id=bad_source, chunk_index=0, content_hash="a" * 64)


def test_chunk_id_value_round_trips_via_rsplit():
    cid = ChunkId.of("doc-1", 7, "payload")
    source, index, digest = cid.value.rsplit(":", 2)
    assert source == cid.source_doc_id
    assert int(index) == cid.chunk_index
    assert digest == cid.content_hash


@pytest.mark.parametrize("bad", ["not-hex", "abc", "A" * 63, "g" * 64, "a" * 65])
def test_chunk_id_rejects_malformed_content_hash(bad):
    with pytest.raises(ValidationError):
        ChunkId(source_doc_id="doc-1", chunk_index=0, content_hash=bad)


def test_chunk_id_content_hash_normalized_to_lowercase():
    digest = hashlib.sha256(b"x").hexdigest()
    lower = ChunkId(source_doc_id="d", chunk_index=0, content_hash=digest)
    upper = ChunkId(source_doc_id="d", chunk_index=0, content_hash=digest.upper())
    assert lower == upper


# --- EntityId: an opaque non-empty canonical id (FR-S.3, DD-4, ADR-0067/0117) ----------------
# The engine is domain-agnostic: the id's FORMAT is the resolver/pack's concern (the SEC pack
# shapes a 10-digit CIK in packs/contracts/corpus/edgar.normalize_cik), so the contract's only invariant is a
# non-empty string. Any present, non-blank id is accepted, whatever the domain's scheme.


def test_entity_id_accepts_any_nonempty_canonical_id():
    cik = EntityId.of("0000320193")  # an SEC CIK -- still valid, just not privileged by the contract
    assert cik.value == "0000320193"
    assert str(cik) == "0000320193"
    assert EntityId(value="0000320193") == cik

    # a non-SEC domain's canonical id (a surface-form key) is equally valid -- the engine is generic
    sf = EntityId.of("acme-holdings-ltd")
    assert sf.value == "acme-holdings-ltd"
    assert str(sf) == "acme-holdings-ltd"


def test_entity_id_is_frozen():
    eid = EntityId.of("0000320193")
    with pytest.raises(ValidationError):
        eid.value = "0000000001"


def test_entity_id_is_hashable_and_usable_as_key():
    registry = {EntityId.of("0000320193"): "Apple Inc."}
    assert registry[EntityId.of("0000320193")] == "Apple Inc."


@pytest.mark.parametrize(
    "invalid",
    [
        "",  # empty
        "   ",  # whitespace-only is blank
        320193,  # wrong type (int)
        3.5,  # wrong type (float)
        True,  # bool is an int subclass; still not a string
        None,  # wrong type
    ],
)
def test_entity_id_rejects_empty_or_non_string(invalid):
    with pytest.raises(ValidationError):
        EntityId.of(invalid)


# --- the two ids are distinct types ---------------------------------------------------------


def test_chunk_id_and_entity_id_are_distinct_types():
    assert ChunkId is not EntityId
