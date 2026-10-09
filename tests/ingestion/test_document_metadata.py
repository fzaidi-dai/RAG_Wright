"""PS-13: a product stores its own metadata with an ingested document. `IngestSource(metadata={...})` lands on the
document's `Document` node in the same write as the engine's fields; embedded files carry their parent's metadata.
Keys become store field names, so they must be plain identifiers and may not shadow the engine's fields."""
from __future__ import annotations

import pytest

from rag_wright.api import IngestSource

from tests.corpus._ooxml_fixtures import make_xlsx, packager
from tests.ingestion.test_builder import _DB, FIXTURES, _report_docx, _run, _ws

META = {"owner": "team-a", "tags": ["q3", "audit"], "pages_expected": 4, "confidential": True, "note": None}


def _documents(ws) -> dict[str, dict]:
    return {n.props["doc_id"]: n.props for n in ws._store.nodes if n.type == "Document"}


def test_metadata_is_written_with_the_engine_fields(tmp_path):
    ws = _ws()
    _run(ws, [IngestSource(path=str(FIXTURES / "textile_spec_sheet.md"), doc_id="spec", metadata=META)], tmp_path)
    nodes = [n for n in ws._store.nodes if n.type == "Document"]
    assert len(nodes) == 1  # one write: the metadata is not a second, separate update
    props = nodes[0].props
    assert {k: props[k] for k in META} == META
    assert props["doc_id"] == "spec" and props["filename"] == "textile_spec_sheet.md" and props["sha256"]


def test_without_metadata_only_the_engine_fields_are_written(tmp_path):
    ws = _ws()
    _run(ws, [IngestSource(path=str(FIXTURES / "textile_spec_sheet.md"), doc_id="spec")], tmp_path)
    assert set(_documents(ws)["spec"]) == {"doc_id", "parent_doc_id", "filename", "media_type", "sha256"}


def test_embedded_files_carry_their_parents_metadata(tmp_path):
    r1 = _report_docx(tmp_path, "r1.docx", "Tear strength results for the 3101 trial.")
    x = make_xlsx(tmp_path / "db.xlsx", _DB, [(3, 1, "oleObject1.bin", packager("SDP 3101 report.docx", r1))])
    ws = _ws()
    report, _ = _run(ws, [IngestSource(path=str(x), doc_id="db", metadata={"owner": "team-b"})], tmp_path)
    docs = _documents(ws)
    children = [d for d in docs if d.startswith("db.emb.")]
    assert children and all(docs[c]["owner"] == "team-b" for c in ["db", *children])


@pytest.mark.parametrize("metadata", [
    {"owner name": "x"},          # not an identifier: it would become a store field name
    {"1st": "x"},
    {"doc_id": "x"},              # shadows an engine field
    {"sha256": "x"},
    {"owner": {"nested": "x"}},   # values: scalars, None, or lists of scalars
    {"owner": [["x"]]},
])
def test_invalid_metadata_is_refused(metadata):
    with pytest.raises(ValueError):
        IngestSource(path="a.pdf", metadata=metadata)
