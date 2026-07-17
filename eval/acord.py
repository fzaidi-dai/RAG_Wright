"""ACORD (T33) loader: corpus, test queries, and graded qrels in BEIR format.

ACORD (Atticus Clause Retrieval Dataset, CC-BY-4.0, ADR-0011) is the content-bearing retrieval bar CUAD
cannot supply. This module loads the three BEIR artifacts under the extracted data dir:

  - corpus.jsonl   : 3,931 clauses, `{_id, text}` — each a pre-segmented clause (one chunk at ingest).
  - queries.jsonl  : 114 attorney queries, `{_id, text, metadata}` — the test split is selected here.
  - qrels/test.tsv : `query-id, corpus-id, score` on a 0-4 scale (readme 1-5 minus 1; 0 = irrelevant).

The relevance floor for the BINARY recall gate is `score >= RELEVANCE_FLOOR` (grade >= 2, ACORD's own
3-star official bar; grade 1 is "non-relevant but helpful", grade 0 irrelevant). nDCG uses the full graded
score and does not threshold (rewarding higher grades by design), so the graded map is kept alongside.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

ACORD_DIR = Path("data/acord/extracted/ACORD Dataset & ReadMe (external)")
RELEVANCE_FLOOR = 2  # qrels grade >= 2 == readme 3-star "partially relevant" and up (ACORD's official bar)


@dataclass(frozen=True)
class AcordClause:
    """One corpus clause: its ACORD `_id` (the qrels corpus-id) and full text."""

    clause_id: str
    text: str


@dataclass(frozen=True)
class AcordQuery:
    """One attorney query with its graded qrels: relevant = grade >= floor; graded = full 0-4 map."""

    query_id: str
    text: str
    relevant: frozenset[str]  # corpus-ids with grade >= RELEVANCE_FLOOR (the binary recall target)
    graded: dict[str, int]  # corpus-id -> grade (0-4), for graded nDCG (no threshold)


def load_corpus(acord_dir: Path = ACORD_DIR) -> list[AcordClause]:
    """Load all corpus clauses (each a pre-segmented clause, ingested as one chunk)."""
    clauses: list[AcordClause] = []
    with (acord_dir / "corpus.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            clauses.append(AcordClause(clause_id=str(row["_id"]), text=str(row["text"])))
    return clauses


def _query_text(acord_dir: Path) -> dict[str, str]:
    texts: dict[str, str] = {}
    with (acord_dir / "queries.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            texts[str(row["_id"])] = str(row["text"])
    return texts


def load_test_queries(acord_dir: Path = ACORD_DIR, *, floor: int = RELEVANCE_FLOOR) -> list[AcordQuery]:
    """Load the test-split queries joined to their graded qrels (`qrels/test.tsv`).

    A query's relevant set is its corpus-ids with grade >= `floor`. Queries whose test qrels contain no
    grade-`floor` clause are excluded (recall is undefined with an empty relevant set) — the caller is told
    how many via `load_test_queries` vs the raw query-id count.
    """
    texts = _query_text(acord_dir)
    graded: dict[str, dict[str, int]] = {}
    with (acord_dir / "qrels" / "test.tsv").open(encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t")
        header = next(reader)
        assert header == ["query-id", "corpus-id", "score"], f"unexpected qrels header: {header}"
        for query_id, corpus_id, score in reader:
            graded.setdefault(query_id, {})[corpus_id] = int(score)

    queries: list[AcordQuery] = []
    for query_id, grades in graded.items():
        relevant = frozenset(cid for cid, g in grades.items() if g >= floor)
        if not relevant:
            continue  # no grade->=floor clause: recall undefined, excluded from the population
        queries.append(
            AcordQuery(query_id=query_id, text=texts[query_id], relevant=relevant, graded=grades)
        )
    return queries
