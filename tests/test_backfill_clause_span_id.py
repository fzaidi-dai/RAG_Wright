"""A1 fix (persist-clause-span-id): the backfill planner -- the deterministic content-hash join that recovers a
clause's operative span_id from the Span index. Pure, no store."""

from __future__ import annotations

from scripts.backfill_clause_span_id import _sha, plan_backfill


def _clause(source: str, index: int, text: str, span_id: str = "") -> dict:
    # clause_id = <source>:<index>:<sha256(op.text)> -- the id embeds the content hash (identifiers.ChunkId)
    return {"clause_id": f"{source}:{index}:{_sha(text)}", "function": "Cap On Liability", "span_id": span_id}


def _span(span_id: str, text: str) -> dict:
    return {"span_id": span_id, "text": text}


def test_content_hash_join_matches_a_clause_to_its_span():
    spans = [_span("C:0:h#0", "Liability is capped at the fees paid."), _span("C:0:h#1", "Unrelated text.")]
    clauses = [_clause("C", 5, "Liability is capped at the fees paid.")]  # property-less, span_id not set yet
    updates, stats = plan_backfill(spans, clauses)
    assert stats == {"total": 1, "matched": 1, "already": 0, "unmatched": 0}
    assert updates[clauses[0]["clause_id"]] == "C:0:h#0"  # the span whose RAW text hashes to the clause_id's hash


def test_idempotent_skips_a_clause_already_set_correctly():
    text = "Liability is capped."
    clause = _clause("C", 1, text, span_id="S#0")  # already carries the right span_id
    updates, stats = plan_backfill([_span("S#0", text)], [clause])
    assert updates == {} and stats["matched"] == 1 and stats["already"] == 1


def test_unmatched_clause_is_left_alone():
    # a clause whose span never got indexed (best-effort span write failed) -> no match, no write, safe
    updates, stats = plan_backfill([], [_clause("C", 1, "text with no indexed span")])
    assert updates == {} and stats["unmatched"] == 1 and stats["matched"] == 0


def test_identical_text_spans_first_wins_and_stays_faithful():
    # two spans with byte-identical text hash the same; either span_id cites the SAME text -> no mis-citation
    text = "Termination requires 30 days notice."
    updates, stats = plan_backfill([_span("S#0", text), _span("S#1", text)], [_clause("C", 3, text)])
    assert updates[_clause("C", 3, text)["clause_id"]] in {"S#0", "S#1"} and stats["matched"] == 1
