"""T33: ACORD loader tests (hermetic — a tiny BEIR fixture, not the gitignored real data).

Pin the parse + the relevance-floor join: corpus/query load, grade >= 2 relevant sets, graded map kept for
nDCG, and exclusion of a query with no grade->=floor clause (recall undefined)."""

from __future__ import annotations

from pathlib import Path

from eval.acord import RELEVANCE_FLOOR, load_corpus, load_test_queries


def _fixture(root: Path) -> Path:
    (root / "qrels").mkdir(parents=True)
    (root / "corpus.jsonl").write_text(
        '{"_id": "c1", "text": "governing law is Delaware"}\n'
        '{"_id": "c2", "text": "term and termination clause"}\n'
        '{"_id": "c3", "text": "unrelated boilerplate"}\n',
        encoding="utf-8",
    )
    (root / "queries.jsonl").write_text(
        '{"_id": "q_gov", "text": "Delaware governing law", "metadata": "{}"}\n'
        '{"_id": "q_none", "text": "a query with no relevant clause", "metadata": "{}"}\n',
        encoding="utf-8",
    )
    (root / "qrels" / "test.tsv").write_text(
        "query-id\tcorpus-id\tscore\n"
        "q_gov\tc1\t4\n"  # highly relevant (grade >= floor)
        "q_gov\tc2\t2\n"  # partially relevant (grade == floor)
        "q_gov\tc3\t1\n"  # non-relevant but helpful (grade < floor -> excluded from relevant set)
        "q_none\tc3\t1\n"  # only a grade-1 judgment -> no relevant clause -> query excluded
        "q_none\tc1\t0\n",
        encoding="utf-8",
    )
    return root


def test_load_corpus_reads_all_clauses(tmp_path):
    root = _fixture(tmp_path)
    clauses = load_corpus(root)
    assert [c.clause_id for c in clauses] == ["c1", "c2", "c3"]
    assert clauses[0].text == "governing law is Delaware"


def test_relevant_set_is_grade_at_least_floor_and_graded_map_is_full(tmp_path):
    root = _fixture(tmp_path)
    assert RELEVANCE_FLOOR == 2
    queries = load_test_queries(root)

    # q_none is excluded (its only judgments are grade 1 and 0 -> empty relevant set)
    assert [q.query_id for q in queries] == ["q_gov"]
    q = queries[0]
    assert q.relevant == frozenset({"c1", "c2"})  # grade 4 and 2 (>= floor); c3 grade 1 excluded
    assert q.graded == {"c1": 4, "c2": 2, "c3": 1}  # full 0-4 map kept for graded nDCG (no threshold)


def test_a_stricter_floor_shrinks_the_relevant_set(tmp_path):
    root = _fixture(tmp_path)
    queries = load_test_queries(root, floor=3)
    assert queries[0].relevant == frozenset({"c1"})  # only grade 4 clears floor 3; grade-2 c2 drops
