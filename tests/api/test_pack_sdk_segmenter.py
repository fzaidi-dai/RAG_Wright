"""PS-61 (TexWright, 2026-11-05): the default segmenter is on the pack tier, so a pack can wrap it rather than replace
it. `build_ingestion(segmenter=)` replaces segmentation for the whole pipeline; a pipeline that ingests both a
domain's tables and ordinary prose keeps the engine's evaluated segmentation for the prose by delegating to it."""
from __future__ import annotations

import asyncio

from rag_wright import pack_sdk
from rag_wright.api import KgNode, UnitExtraction, build_ingestion, check_tiling
from rag_wright.ingestion.segment import segment_layout

from tests.ingestion.test_builder import FIXTURES, _FakeEmbedder, _ws


def test_the_default_segmenter_is_on_the_pack_tier():
    assert "segment_layout" in pack_sdk.__all__
    assert pack_sdk.segment_layout is segment_layout  # a re-export of the engine's own object, not a copy


def test_a_pack_segmenter_can_delegate_to_the_default_and_adjust_its_spans(tmp_path):
    seen = []

    def segmenter(chunk_id, text, layout):
        spans = pack_sdk.segment_layout(chunk_id, text, layout, min_alnum=2)  # the engine's evaluated default
        seen.append(len(spans))
        return spans  # (a pack would adjust them here, e.g. split a span its domain reads as two)

    async def extract(unit, *, source_doc_id):
        return UnitExtraction(nodes=[KgNode("Record", "record_id", {"record_id": f"{source_doc_id}:{unit.index}"})])

    handle = _ws()
    pipe = build_ingestion(extract, segmenter=segmenter, embedder=_FakeEmbedder(), progress=lambda _l: None)
    report = asyncio.run(pipe.aingest(handle, [str(FIXTURES / "textile_spec_sheet.md")], cache_dir=tmp_path / "c"))
    (doc,) = report.documents
    assert doc.dead_letter is None and seen and doc.spans == sum(seen)


def test_the_export_tiles_a_chunk_like_the_builder_default():
    text = "| Sr# | GSM |\n|---|---|\n| 1 | 380 |\n| 2 | 412 |\n\nA closing note. It has two sentences."
    spans = pack_sdk.segment_layout("doc:0:h", text, [])
    check_tiling("doc:0:h", text, spans)
    assert [s.kind for s in spans] == ["table", "table_row", "table_row", "paragraph", "paragraph"]
