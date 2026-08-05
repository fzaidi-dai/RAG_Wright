"""CUAD ingestion (relocated out of the generic pipeline, SKILL-SPLIT/naming audit): the `CuadAdapter`
reference `CorpusAdapter`. Hermetic -- a tiny CUAD-format JSON fixture, no LLM, no DB. The CUAD ingest driver
`run_cuad_ingestion` is exercised by `scripts/ingest_smoke.py` (needs a real store), not here.
"""

from __future__ import annotations

import json
from pathlib import Path

from rag_wright.contracts.identifiers import canonical_source_doc_id
from rag_wright.corpus.cuad_ingestion import CuadAdapter
from rag_wright.subgraphs.contract_ingestion_pipeline import SourceDocument


def _cuad_file(tmp_path: Path) -> Path:
    p = tmp_path / "cuad.json"
    p.write_text(json.dumps({"data": [
        {"title": "Acme-Beta Supply Agreement", "paragraphs": [{"context": "This Agreement is made...", "qas": []}]},
        {"title": "Gamma License", "paragraphs": [{"context": "Licensor grants...", "qas": []}]},
    ]}), encoding="utf-8")
    return p


def test_adapter_yields_one_source_document_per_contract(tmp_path):
    docs = list(CuadAdapter(_cuad_file(tmp_path)).documents())
    assert len(docs) == 2
    d = docs[0]
    assert isinstance(d, SourceDocument)
    # CUAD ships text in JSON: text = context, canonical id (HYG-1), raw title carried as metadata
    assert d.text == "This Agreement is made..."
    assert d.source_doc_id == canonical_source_doc_id("Acme-Beta Supply Agreement")
    assert d.metadata["raw_title"] == "Acme-Beta Supply Agreement"


def test_adapter_limit_caps_documents(tmp_path):
    docs = list(CuadAdapter(_cuad_file(tmp_path), limit=1).documents())
    assert [d.metadata["raw_title"] for d in docs] == ["Acme-Beta Supply Agreement"]
