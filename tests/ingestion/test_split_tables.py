"""PS-60 (TexWright, 2026-11-05): a table larger than the chunk cap is hard-split, and the old fixed-length cut fell
mid-row: the rows after it lost their table layout, so the default segmenter gave them ONE span between them, no
unit carried their `TableRow`, and a record extractor never saw them (4 of 140 records lost, silently). The split now
cuts between lines and repeats the table's header in each continuation chunk; `chunk_layouts` no longer lets one item
it cannot place block every later item; a manifest cached with the old cut is re-chunked once."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest import mock

from rag_wright.api import IngestSource, KgNode, UnitExtraction, build_ingestion
from rag_wright.capabilities.rlm_chunking import _hard_split
from rag_wright.ingestion import chunk_layouts, segment_layout

from tests.ingestion.test_builder import _FakeEmbedder, _ws

_HEADER = "| Sr# | Sample | Remarks |\n|---|---|---|"


def _table(n: int) -> str:
    return _HEADER + "".join(f"\n| {i} | SDP {3000 + i} | trial note {i} for the wash test |" for i in range(1, n + 1))


def _data_lines(pieces):
    return [ln for p in pieces for ln in p.split("\n") if ln.startswith("| ") and not ln.startswith("| Sr#")]


def test_a_split_table_keeps_every_row_whole_and_each_piece_opens_with_the_header():
    table = _table(60)
    pieces = _hard_split(table, 200)  # cap: 800 chars
    assert len(pieces) > 2 and all(len(p) <= 800 for p in pieces)
    assert all(p.startswith(_HEADER + "\n") for p in pieces)  # every piece is a well-formed table
    assert _data_lines(pieces) == table.split("\n")[2:]  # every row once, whole, in order


def test_a_table_after_prose_gets_its_header_only_where_it_continues():
    text = "An opening paragraph that is not part of any table.\n\n" + _table(60)
    pieces = _hard_split(text, 200)
    assert pieces[0].startswith("An opening paragraph") and all(p.startswith(_HEADER) for p in pieces[1:])
    assert _data_lines(pieces) == _table(60).split("\n")[2:]


def test_prose_is_cut_between_words_and_never_over_the_cap():
    words = [f"word{i}" for i in range(400)]
    pieces = _hard_split(" ".join(words), 100)  # cap: 400 chars, no line breaks to cut at
    assert all(len(p) < 400 for p in pieces)
    assert " ".join(pieces).split() == words  # no word broken, none lost


def test_a_line_with_no_break_at_all_is_still_cut_under_the_cap():
    pieces = _hard_split("x" * 1000, 100)
    assert all(len(p) < 400 for p in pieces) and "".join(pieces) == "x" * 1000


def _item(text, label):
    return SimpleNamespace(text=text, label=label, level=None, page=1)


def test_an_item_no_chunk_holds_whole_does_not_block_the_items_after_it():
    table, after = _table(60), "A closing paragraph. It has two sentences."
    chunks = _hard_split(table, 200) + [after]
    with mock.patch("rag_wright.corpus.document_parser.content_items",
                    lambda d: [_item(table, "table"), _item(after, "text")]):
        layouts = chunk_layouts(None, chunks)
    assert [it.kind for it in layouts[-1]] == ["paragraph"]  # the paragraph is placed (it was lost before)


def test_the_default_segmenter_gives_every_row_of_a_split_table_its_own_span():
    chunks = _hard_split(_table(60), 200)
    with mock.patch("rag_wright.corpus.document_parser.content_items", lambda d: [_item(_table(60), "table")]):
        layouts = chunk_layouts(None, chunks)
    rows = [s for i, (c, lay) in enumerate(zip(chunks, layouts)) for s in segment_layout(f"c{i}", c, lay)
            if s.kind == "table_row"]
    assert [s.text.strip() for s in rows] == _table(60).split("\n")[2:]


def test_a_wide_sheet_over_the_cap_yields_a_record_with_its_table_row_for_every_row(tmp_path):
    """The TexWright shape: a record sheet wide enough that the default 20,000-token cap splits it."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Sr#", "SDF", "SDP"] + [f"Column {c}" for c in range(73)])
    for i in range(1, 141):
        ws.append([i, 2800 + i, 3000 + i] + [f"value {i}-{c}" for c in range(73)])
    path = tmp_path / "database.xlsx"
    wb.save(path)

    async def extract(unit, *, source_doc_id):
        if unit.table_row is None:
            return UnitExtraction()
        return UnitExtraction(nodes=[KgNode("Record", "sdp", {"sdp": unit.table_row.cell("SDP"),
                                                              "span_id": unit.anchor.span_id})])

    handle = _ws()
    pipe = build_ingestion(extract, embedder=_FakeEmbedder(), progress=lambda _l: None)
    report = asyncio.run(pipe.aingest(handle, [IngestSource(path=str(path), doc_id="db", table_mode="record")],
                                      cache_dir=tmp_path / "cache"))
    (doc,) = report.documents
    chunks = {s.parent_chunk_id for s in handle._store.spans.values()}
    assert doc.dead_letter is None and len(chunks) > 1  # really split by the cap
    assert sorted(int(n.props["sdp"]) for n in handle._store.nodes if n.type == "Record") == list(range(3001, 3141))


def test_a_manifest_cached_with_the_old_cut_is_rechunked_once(tmp_path):
    from docling_core.types.doc.document import DoclingDocument

    from rag_wright.capabilities.rlm_chunking import ChunkManifest, chunk

    from tests.capabilities.test_rlm_chunking import _parsed, _StubDiscoverer, _StubSummarizer

    doc = DoclingDocument(name="sheet")
    doc.add_text(label="text", text=_table(60))
    parsed = _parsed(tmp_path, doc)
    disc = _StubDiscoverer([(0, 0)])

    def run():
        return chunk(parsed, summarizer=_StubSummarizer(), discoverer=disc, cache_dir=tmp_path / "chunks",
                     token_cap=200)

    run()
    (path,) = (tmp_path / "chunks").glob("*.chunks.json")
    cached = ChunkManifest.model_validate_json(path.read_text())
    old = [_table(60)[i:i + 800] for i in range(0, len(_table(60)), 800)]  # what the old fixed-length cut cached
    path.write_text(cached.model_copy(update={"chunks": [c.model_copy(update={"text": t}) for c, t in
                                                         zip(cached.chunks, old)]}).model_dump_json())
    assert disc.calls == 1
    manifest = run()
    assert disc.calls == 2 and all(c.text.startswith(_HEADER) for c in manifest.chunks)  # re-chunked
    run()
    assert disc.calls == 2  # the new manifest is reused
