"""EP-REF-1c: thin REFERENCE invoker wrappers for the GENERIC compliance leg -- worked examples showing a product
the exact call shape: open a workspace, then invoke the capability BY NAME via `ainvoke_subgraph`. Each is a
one-liner over the engine API -- no store/embedder/model-id/id-parsing. Reference-ONLY: a product owns the
FTC-routing / ad-compliance variants (`run_ad_compliance_check`) and its guardrails/human-gate policy.
"""
from __future__ import annotations

from typing import Any, Optional

from rag_wright.api import ainvoke_subgraph


async def invoke_compliance_check(ws: Any, *, subject_text: str, source_doc: str, k: int = 8,
                                  sources: Optional[list[str]] = None):
    """Check a subject TEXT against the Requirement KG -> a `ComplianceReport` (cited findings + gap matrix).
    `sources` scopes to named policies (None = store-wide)."""
    return await ainvoke_subgraph(
        "compliance_check",
        {"subject_text": subject_text, "source_doc": source_doc, "k": k, "sources": sources},
        resources=ws)


async def invoke_document_check(ws: Any, *, doc_name: str, data: bytes, k: int = 8,
                                sources: Optional[list[str]] = None):
    """Check a subject DOCUMENT (raw bytes: PDF/DOCX/HTML/TXT) against the Requirement KG -> a `ComplianceReport`."""
    return await ainvoke_subgraph(
        "compliance_check",
        {"doc_name": doc_name, "data": data, "k": k, "sources": sources},
        resources=ws)


async def invoke_policy_ingest(ws: Any, *, source: str, sections_path: Any = None,
                               doc_name: Optional[str] = None, data: Optional[bytes] = None):
    """Ingest a policy into the Requirement KG -> an `IngestionReport`. Give EITHER `sections_path` (a pre-sectioned
    eCFR-style `sections.json`) OR `doc_name` + `data` (a policy DOCUMENT's raw bytes, split at its headings)."""
    inputs: dict = {"source": source}
    if data is not None:
        inputs |= {"doc_name": doc_name, "data": data}
    else:
        inputs["sections_path"] = sections_path
    return await ainvoke_subgraph("compliance_ingestion", inputs, resources=ws)
