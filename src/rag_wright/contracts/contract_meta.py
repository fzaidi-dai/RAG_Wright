"""Contract-level metadata record (CU-A1, ADR-0029).

The CUAD pipeline is document-scoped: a contract is LOOKED UP by id (not searched), then all retrieval happens
within it. `ContractRecord` is that lookup/filter unit -- the contract node the `Span` records point back to
via `contract_id`. Metadata fields are best-effort (many come from a value-type extraction pass, CU-C2) and
default empty; only `contract_id` is required.
"""

from __future__ import annotations

from pydantic import BaseModel


class ContractRecord(BaseModel):
    """One contract's metadata for lookup + filtering (the parent of its clauses/spans)."""

    model_config = {"frozen": True}

    contract_id: str  # the canonical document id (== spans' contract_id / the parse source_doc_id)
    name: str = ""  # the contract's name/title (CUAD "Document Name")
    agreement_type: str = ""  # e.g. "Distributor Agreement"
    parties: list[str] = []  # signing parties (CUAD "Parties")
    agreement_date: str = ""  # as-written date string (not normalized here)
    effective_date: str = ""
    source_doc_id: str = ""  # the parse source id (usually == contract_id)
    content_hash: str = ""  # source content hash (provenance / idempotence)
    page_count: int | None = None  # for PDF-overlay citation, when known
