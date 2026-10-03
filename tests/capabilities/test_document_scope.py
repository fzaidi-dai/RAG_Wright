"""issue 0031 / ADR-0094: `validate_documents` + the scope wiring through graph_query and typed_property
retrieval. An unknown document id RAISES (mirrors issue 0007's `sources`); None/[] pass through. Hermetic."""

from __future__ import annotations

import pytest

from rag_wright.capabilities.document_scope import UnknownDocumentError, validate_documents


class _Store:
    def __init__(self, known):
        self._known = set(known)

    def known_document_ids(self):
        return set(self._known)


def test_validate_documents_passes_known():
    validate_documents(_Store({"docA", "docB"}), ["docA"])  # no raise


def test_validate_documents_raises_on_unknown():
    with pytest.raises(UnknownDocumentError) as ei:
        validate_documents(_Store({"docA"}), ["docA", "ghost"])
    assert ei.value.unknown == ["ghost"] and "docA" in ei.value.present
    assert "ghost" in str(ei.value)  # the message NAMES the offending id


def test_error_names_unknowns_and_samples_a_large_present_set():
    # .present carries the FULL set; the message samples it (a real store can hold thousands of documents)
    known = {f"doc{i}" for i in range(500)}
    with pytest.raises(UnknownDocumentError) as ei:
        validate_documents(_Store(known), ["ghost1", "ghost2"])
    assert set(ei.value.unknown) == {"ghost1", "ghost2"}
    assert len(ei.value.present) == 500  # full set retained for programmatic use
    msg = str(ei.value)
    assert "ghost1" in msg and "ghost2" in msg and "more)" in msg  # unknowns named, present sampled


def test_validate_documents_none_and_empty_pass():
    store = _Store({"docA"})
    validate_documents(store, None)  # whole store, not validated
    validate_documents(store, [])    # scope-to-nothing, valid


def test_validate_documents_store_without_method_passes():
    validate_documents(object(), ["anything"])  # a minimal fake -> un-validatable, no raise


# --- graph_query wiring: validates, then passes documents to graph_neighbors -----------------------------------

def test_graph_query_scopes_and_validates():
    from rag_wright.capabilities.graph_query import graph_query

    seen = {}

    class _S:
        def known_document_ids(self):
            return {"docA", "docB"}

        def graph_neighbors(self, entity_id, *, relationship_type, max_hops, documents=None):
            seen["documents"] = documents
            return []

    graph_query("e1", store=_S(), relationship_type="related_to", max_hops=1, documents=["docA"])
    assert seen["documents"] == ["docA"]  # threaded through to the store

    with pytest.raises(UnknownDocumentError):
        graph_query("e1", store=_S(), relationship_type="related_to", documents=["ghost"])
