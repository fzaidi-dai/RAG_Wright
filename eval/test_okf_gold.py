"""T45 (FR-K.8, §12): tests for the ACORD gold-chunk label builder.

Hermetic — a tiny BEIR fixture plus a real `ChunkTextStore` sidecar (not the gitignored ACORD data).
Fixture text is deliberately messy (brackets, newlines, embedded quotes) per the clean-fixtures-hide-
real-text-bugs lesson: the corpus-id -> chunk_id map and the sidecar integrity check run over the same
bytes the real ingest saw, so a whitespace or escaping bug surfaces here, not only on real data.
"""

from __future__ import annotations

import json
from pathlib import Path

from rag_wright.contracts.identifiers import ChunkId
from rag_wright.store.chunk_text import ChunkTextStore

from eval.okf_gold import (
    all_gold,
    any_gold,
    build_okf_gold,
    map_corpus_ids,
    select_debug_split,
)

# Messy clause text: brackets, a newline, embedded double quotes.
_CLAUSES = {
    "c1": 'Governing law: [Delaware].\nAll "disputes" are resolved there.',
    "c2": "This Agreement is governed by the laws of New York.",
    "c3": "The Supplier shall indemnify and hold harmless the Buyer.",
    "c4": "Unrelated boilerplate; not relevant to anything.",
    "c6": "An additional governing-law provision, in the corpus but never ingested.",
    # c5 is referenced by qrels but is absent from the corpus entirely (corpus-missing).
}


def _fixture(root: Path) -> tuple[Path, Path]:
    """Build an ACORD-shaped BEIR fixture + a sidecar. Returns (acord_dir, sidecar_root).

    Sidecar holds c1..c4 (ingested). c6 is in the corpus but NOT ingested (sidecar-missing). c5 is in
    qrels but not in the corpus at all (corpus-missing). Both c5 and c6 must be reported as unmapped.
    """
    acord = root / "acord"
    (acord / "qrels").mkdir(parents=True)
    (acord / "corpus.jsonl").write_text(
        "".join(json.dumps({"_id": cid, "text": text}) + "\n" for cid, text in _CLAUSES.items()),
        encoding="utf-8",
    )
    (acord / "queries.jsonl").write_text(
        '{"_id": "q_gov1", "text": "Delaware governing law", '
        '"metadata": {"category": "Governing Law", "split": "test"}}\n'
        '{"_id": "q_gov2", "text": "New York governing law", '
        '"metadata": {"category": "Governing Law", "split": "test"}}\n'
        '{"_id": "q_ind1", "text": "indemnification obligation", '
        '"metadata": {"category": "Indemnification", "split": "test"}}\n',
        encoding="utf-8",
    )
    (acord / "qrels" / "test.tsv").write_text(
        "query-id\tcorpus-id\tscore\n"
        "q_gov1\tc1\t4\n"  # gold
        "q_gov1\tc2\t2\n"  # gold (== floor)
        "q_gov1\tc4\t1\n"  # grade 1 -> not gold
        "q_gov1\tc6\t3\n"  # gold by grade, but sidecar-missing -> unmapped
        "q_gov2\tc2\t3\n"  # gold
        "q_gov2\tc1\t2\n"  # gold
        "q_ind1\tc3\t4\n"  # gold
        "q_ind1\tc5\t3\n",  # gold by grade, but corpus-missing -> unmapped
        encoding="utf-8",
    )

    sidecar = root / "sidecar"
    store = ChunkTextStore(sidecar)
    for cid in ("c1", "c2", "c3", "c4"):  # c6 deliberately NOT ingested
        text = _CLAUSES[cid]
        store.put(ChunkId.of(cid, 0, text), text)
    return acord, sidecar


def _cid(corpus_id: str) -> str:
    return ChunkId.of(corpus_id, 0, _CLAUSES[corpus_id]).value


# --- pure helpers (no data) ---------------------------------------------------------------------


def test_map_corpus_ids_exact_and_reports_missing():
    texts = {"c1": _CLAUSES["c1"], "c3": _CLAUSES["c3"]}
    mapped, unmapped = map_corpus_ids(texts, ["c1", "c3", "c5"])
    assert mapped["c1"] == _cid("c1")
    assert mapped["c3"] == _cid("c3")
    assert unmapped == ["c5"]  # missing corpus-id reported, not silently dropped
    # chunk_id round-trips back to the corpus-id (the source_doc_id segment)
    assert mapped["c1"].rsplit(":", 2)[0] == "c1"


def test_any_and_all_gold_readings():
    gold = {"a", "b"}
    assert any_gold(gold, {"b", "z"}) is True
    assert any_gold(gold, {"z"}) is False
    assert all_gold(gold, {"a", "b", "z"}) is True
    assert all_gold(gold, {"a"}) is False
    assert all_gold(set(), {"a"}) is False  # empty gold is never "all reached"


def test_select_debug_split_deterministic_and_stratified():
    cats = {f"q{i}": ("A" if i < 10 else "B") for i in range(15)}  # A:10, B:5
    a = select_debug_split(cats, fraction=0.2)
    b = select_debug_split(cats, fraction=0.2)
    assert a == b  # deterministic, no randomness
    assert a <= set(cats)  # a subset of the population
    # floor(0.2*10)=2 from A, floor(0.2*5)=1 from B
    assert sum(1 for q in a if cats[q] == "A") == 2
    assert sum(1 for q in a if cats[q] == "B") == 1


# --- build over the fixture ---------------------------------------------------------------------


def test_build_population_and_exact_mapping(tmp_path):
    acord, sidecar = _fixture(tmp_path)
    gold = build_okf_gold(acord, sidecar, debug_fraction=0.5)
    assert set(gold.queries) == {"q_gov1", "q_gov2", "q_ind1"}  # T33 population = qrels grade>=floor
    # q_gov2's gold maps exactly to the ingested chunk_ids
    assert set(gold.queries["q_gov2"].gold_chunk_ids) == {_cid("c1"), _cid("c2")}
    assert set(gold.queries["q_gov2"].gold_corpus_ids) == {"c1", "c2"}
    # every gold chunk_id resolves in the sidecar
    store = ChunkTextStore(sidecar)
    for q in gold.queries.values():
        for chunk_id in q.gold_chunk_ids:
            assert store.get(chunk_id) is not None


def test_unmapped_reported_not_dropped(tmp_path):
    acord, sidecar = _fixture(tmp_path)
    gold = build_okf_gold(acord, sidecar, debug_fraction=0.5)
    # c5 (corpus-missing) and c6 (sidecar-missing) are both reported, and neither leaks into a gold set
    assert set(gold.unmapped_corpus_ids) == {"c5", "c6"}
    assert "c6" not in gold.queries["q_gov1"].gold_corpus_ids
    assert _cid("c6") not in gold.queries["q_gov1"].gold_chunk_ids
    assert set(gold.queries["q_ind1"].gold_corpus_ids) == {"c3"}


def test_induced_category_labels(tmp_path):
    acord, sidecar = _fixture(tmp_path)
    gold = build_okf_gold(acord, sidecar, debug_fraction=0.5)
    # a gold clause inherits its relevant query's metadata.category
    assert gold.induced_category_labels[_cid("c1")] == "Governing Law"
    assert gold.induced_category_labels[_cid("c2")] == "Governing Law"
    assert gold.induced_category_labels[_cid("c3")] == "Indemnification"
    # only ingested gold chunks are labeled (c6 never ingested, c5 never in corpus)
    assert _cid("c6") not in gold.induced_category_labels


def test_debug_held_out_partition_and_determinism(tmp_path):
    acord, sidecar = _fixture(tmp_path)
    g1 = build_okf_gold(acord, sidecar, debug_fraction=0.5)
    g2 = build_okf_gold(acord, sidecar, debug_fraction=0.5)
    assert g1.model_dump() == g2.model_dump()  # regenerable, deterministic
    splits = {qid: q.split for qid, q in g1.queries.items()}
    assert set(splits.values()) <= {"debug", "held_out"}
    assert all(s in ("debug", "held_out") for s in splits.values())  # every query is one or the other
    debug = {qid for qid, s in splits.items() if s == "debug"}
    # floor(0.5*2)=1 Governing-Law query in debug; Indemnification (n=1) -> floor 0, stays held-out
    assert len(debug) == 1
    assert all(g1.queries[qid].category == "Governing Law" for qid in debug)
