"""EV-2: the gold-set and report contracts (`GoldItem`, `GoldSet`, `EvalReport`)."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from rag_wright.api import EvalReport, GoldItem, GoldSet, ItemResult, ScorerResult


def _set(**kw) -> GoldSet:
    items = kw.pop("items", [GoldItem(id="a", input={"text": "x"}, expected="yes", group="doc-1"),
                             GoldItem(id="b", input={"text": "y"}, expected="no", group="doc-2")])
    return GoldSet(name="nli", version="2026-10-12", items=items, **kw)


def test_a_gold_set_is_local_only_unless_said_otherwise():
    assert _set().local_only is True
    assert _set(local_only=False).local_only is False


def test_a_gold_set_round_trips_through_json():
    gold = _set(description="ContractNLI slice")
    assert GoldSet.model_validate_json(gold.model_dump_json()) == gold


def test_item_ids_are_unique():
    with pytest.raises(ValidationError, match="duplicate item ids: a"):
        _set(items=[GoldItem(id="a", input="x", expected=1), GoldItem(id="a", input="y", expected=2)])


def test_a_gold_set_has_items():
    with pytest.raises(ValidationError):
        _set(items=[])


def test_inputs_and_expectations_must_be_json_ready():
    with pytest.raises(ValidationError, match="JSON"):
        GoldItem(id="a", input={"when": object()}, expected="x")


def test_provenance_is_hand_silver_or_generated():
    assert GoldItem(id="a", input="x", expected="y").provenance == "hand"
    with pytest.raises(ValidationError):
        GoldItem(id="a", input="x", expected="y", provenance="guess")


def test_groups_name_each_item_by_its_grouping_key():
    assert _set().groups() == {"doc-1": ["a"], "doc-2": ["b"]}


def _report(**kw) -> EvalReport:
    base = dict(gold_set="nli", gold_version="2026-10-12", task="nli-judge", engine_version="0.7.0",
                models=["qwen3.8-27b-modal"], repeats=2,
                items=[ItemResult(item_id="a", outputs=["yes", "yes"], errors=[None, None]),
                       ItemResult(item_id="b", outputs=["no", None], errors=[None, "ModelCallTimeout: 182 s"]),
                       ItemResult(item_id="c", outputs=["yes", "no"], errors=[None, None])],
                scores=[ScorerResult(scorer="judgment", passed=True, gate={"recall_actionable": 0.9},
                                     thresholds={"recall_actionable": 0.8}, diagnostics={"calibration": 0.1})])
    base.update(kw)
    return EvalReport(**base)


def test_a_report_counts_errors_and_flips_apart_from_the_answers():
    report = _report()
    assert report.errors == 1
    assert report.flips == ["c"]  # an error is not a flip
    assert report.passed is True


def test_a_report_with_a_failing_scorer_does_not_pass():
    failing = ScorerResult(scorer="judgment", passed=False, gate={"recall_actionable": 0.5},
                           thresholds={"recall_actionable": 0.8})
    assert _report(scores=[failing]).passed is False
    assert _report(scores=[]).passed is False  # nothing scored is not a pass


def test_a_report_is_json_ready_with_its_derived_fields():
    out = json.loads(_report().model_dump_json())
    assert out["passed"] is True and out["errors"] == 1 and out["flips"] == ["c"]
    assert out["items"][1]["errors"][1].startswith("ModelCallTimeout")


def test_each_item_has_one_output_and_one_error_slot_per_repeat():
    with pytest.raises(ValidationError, match="per repeat"):
        _report(items=[ItemResult(item_id="a", outputs=["yes"], errors=[None])])  # repeats=2
