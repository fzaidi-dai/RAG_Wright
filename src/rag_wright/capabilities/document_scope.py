"""Issue 0031: a workspace document scope for corpus-wide retrieval.

A `documents` list narrows a corpus-wide query (span retrieval, graph traversal) to a subset of the store's
source documents, applied IN THE STORE so out-of-scope content is never pooled, embedded against, judged, or
cited (the same argument issue 0007 settled for compliance `sources`). This module holds the shared
validation both consumers use: an unknown document id RAISES rather than silently matching nothing (a filter
that quietly matches nothing is indistinguishable from an empty workspace -- the exact failure this scope
exists to prevent).
"""

from __future__ import annotations

from typing import Any, Optional


class UnknownDocumentError(ValueError):
    """A `documents` scope named a document id that is present in NEITHER the span index nor the graph edges
    of the store (issue 0031). Mirrors compliance's `UnknownComplianceSourceError` (issue 0007): naming a
    document that does not exist is a caller error, surfaced explicitly rather than silently returning empty.
    Carries `.unknown` and `.present`."""

    def __init__(self, unknown: list[str], present: list[str]) -> None:
        self.unknown = unknown
        self.present = present
        super().__init__(f"unknown document id(s): {unknown}; present in store: {present}")


def validate_documents(store: Any, documents: Optional[list[str]]) -> None:
    """Validate a `documents` scope against `store.known_document_ids()` BEFORE any retrieval spends (mirrors
    issue 0007's `_validate_sources`). `None` (whole store) is not validated. `[]` (scope-to-nothing) is a
    valid empty scope, not an error -- the retrieval surfaces short-circuit it. A non-empty list with any id
    absent from the store raises `UnknownDocumentError`. A store without `known_document_ids` (a minimal fake)
    is treated as un-validatable and passes through."""
    if not documents:  # None or [] -> nothing to validate
        return
    known = getattr(store, "known_document_ids", None)
    if known is None:
        return
    present = known()
    unknown = sorted(set(documents) - present)
    if unknown:
        raise UnknownDocumentError(unknown, sorted(present))
