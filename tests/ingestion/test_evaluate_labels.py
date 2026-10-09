"""PS-R4: `evaluate_ingestion` measures unit LABELLING without a knowledge graph. With a `span_tagger`, an optional
`unit_representative` and `unit_labels` (gold: per document, units found by a text snippet, each with its expected
label), it reports label accuracy, per-label results and the confusion pairs. A gold snippet that finds no unit, or
more than one, is a failure: the gold cannot be scored."""
from __future__ import annotations

from pathlib import Path

from rag_wright.api import LabelEvaluation, TaggedSpan, evaluate_ingestion

SHEET = Path(__file__).resolve().parents[1] / "fixtures" / "ingestion" / "textile_spec_sheet.md"
DOC = SHEET.name

GOLD = {DOC: [
    {"text": 'knitted on a 30" dia. machine', "label": "construction"},
    {"text": "5% elastane", "label": "composition"},
    {"text": "Washing (soft flow)", "label": "finishing"},
    {"text": "Customer: Northwind Apparel", "label": ""},  # the header unit carries no label
]}


def _scores(text: str) -> dict[str, float]:
    if "knitted" in text:
        return {"construction": 0.9}
    if "cotton" in text or "elastane" in text:
        return {"composition": 0.8}
    if "Heat-setting" in text:  # the section's first span, tagged wrongly on purpose
        return {"construction": 0.6, "finishing": 0.4}
    if "Washing" in text or "Drying" in text:
        return {"finishing": 0.9}
    return {}


async def tagger(chunk_text, spans):
    out = []
    for s in spans:
        scores = _scores(s.text)
        out.append(TaggedSpan(span=s, tags=sorted(scores, key=scores.get, reverse=True), scores=scores))
    return out


def vote(members):
    totals: dict[str, float] = {}
    for m in members:
        for label, p in m.scores.items():
            totals[label] = totals.get(label, 0.0) + p
    if not totals:
        return members[0]
    label = max(totals, key=totals.get)
    return max(members, key=lambda m: m.scores.get(label, 0.0)).model_copy(update={"primary": label})


def test_labels_are_scored_against_the_gold(tmp_path):
    ev = evaluate_ingestion([str(SHEET)], cache_dir=tmp_path, span_tagger=tagger, unit_labels=GOLD)
    labels = ev.labels
    assert isinstance(labels, LabelEvaluation)
    assert (labels.gold, labels.correct) == (4, 3) and labels.accuracy == 0.75
    assert labels.confusions == [{"gold": "finishing", "predicted": "construction", "count": 1}]
    assert labels.per_label["construction"] == {"gold": 1, "predicted": 2, "correct": 1, "recall": 1.0,
                                                "precision": 0.5}
    assert labels.per_label["finishing"] == {"gold": 1, "predicted": 0, "correct": 0, "recall": 0.0,
                                             "precision": None}
    assert ev.passed, ev.failures  # accuracy is a measure, not a pass/fail check
    results = ev.documents[0].label_results
    assert [r["predicted"] for r in results] == ["construction", "composition", "construction", ""]


def test_a_unit_representative_changes_the_measured_labels(tmp_path):
    ev = evaluate_ingestion([str(SHEET)], cache_dir=tmp_path, span_tagger=tagger, unit_representative=vote,
                            unit_labels=GOLD)
    assert ev.labels.accuracy == 1.0 and ev.labels.confusions == []


def test_a_snippet_that_finds_no_unit_or_several_is_a_failure(tmp_path):
    gold = {DOC: [{"text": "not in this document", "label": "x"}, {"text": "Lycra", "label": "x"},
                  {"text": "5% elastane", "label": "composition"}]}
    ev = evaluate_ingestion([str(SHEET)], cache_dir=tmp_path, span_tagger=tagger, unit_labels=gold)
    assert ev.labels.unmatched == [f"{DOC}: not in this document"]
    assert ev.labels.ambiguous == [f"{DOC}: Lycra"]
    assert (ev.labels.gold, ev.labels.correct) == (1, 1)
    assert not ev.passed
    assert any("matches no unit" in f for f in ev.failures) and any("matches 3 units" in f for f in ev.failures)


def test_gold_for_a_document_not_evaluated_is_a_failure(tmp_path):
    ev = evaluate_ingestion([str(SHEET)], cache_dir=tmp_path, span_tagger=tagger,
                            unit_labels={**GOLD, "missing.pdf": [{"text": "x", "label": "y"}]})
    assert any("missing.pdf" in f and "not among the sources" in f for f in ev.failures)


def test_without_gold_no_labels_are_reported(tmp_path):
    ev = evaluate_ingestion([str(SHEET)], cache_dir=tmp_path, span_tagger=tagger)
    assert ev.labels is None and ev.passed


def test_gold_is_scored_per_unit_and_disagreeing_gold_is_a_conflict(tmp_path):
    gold = {DOC: [
        {"text": "5% elastane", "label": "composition"},
        {"text": "95% cotton", "label": "composition"},       # the same unit, the same label: counted once
        {"text": "Washing (soft flow)", "label": "finishing"},
        {"text": "Drying, then compacting", "label": "pressing"},  # the same unit, another label: a conflict
    ]}
    ev = evaluate_ingestion([str(SHEET)], cache_dir=tmp_path, span_tagger=tagger, unit_labels=gold)
    assert (ev.labels.gold, ev.labels.correct) == (1, 1)
    assert ev.labels.conflicting == [f"{DOC}: unit 4: finishing, pressing"]
    assert not ev.passed and any("different labels" in f for f in ev.failures)
