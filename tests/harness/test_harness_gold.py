"""The routing gold sets measure the harness's own decisions on a product built from it (HD-1).

**A gold label that is not an option the tier actually holds measures nothing.** So every D1 label is checked against
the tools Loom's chat-manager is really built with, every D2 label against Loom's roster, and every set is balanced
per option, because the gate is each option's floor (`score_classification`), not overall accuracy.
"""

from __future__ import annotations

import re
from collections import Counter

import pytest

from rag_wright.api import GoldItem, GoldSet
from rag_wright.harness import chat_manager_tools, worker_operations
from tests.harness.fabric import _a_context, _resources
from tests.harness.gold import gold
from tests.harness.gold.loom import D1_EXTRA, WORKERS, d1_options, loom
from tests.harness.gold.scoring import accepted, score_choice

#: Words that would mean an item came from another product or a client's data rather than being authored here.
NOT_LOOM = re.compile(r"\b(rulewright|texwright|escorts|contract|clause|matter|indemnit\w*|liabilit\w*)\b", re.I)


@pytest.mark.parametrize("variant", ["small", "wide"])
def test_D1_OPTIONS_ARE_WHAT_THE_CHAT_MANAGER_IS_BUILT_WITH(variant: str) -> None:
    held = {t.name for t in chat_manager_tools(_a_context(), _resources(), definition=loom(variant=variant))}

    assert set(d1_options(variant)) == (held - {"ask_the_user"}) | set(D1_EXTRA)


@pytest.mark.parametrize("variant", ["small", "wide"])
def test_D1_LABELS_ARE_OPTIONS_AND_EVERY_OPTION_HAS_THE_SAME_COUNT(variant: str) -> None:
    items = gold("d1", variant=variant).items
    options = set(d1_options(variant))
    counts = Counter(accepted(i.expected)[0] for i in items)

    assert set(counts) == options, f"options with no items: {options - set(counts)}"
    assert len(set(counts.values())) == 1, f"unbalanced: {counts}"
    for item in items:
        labels = accepted(item.expected)
        assert set(labels) <= options, f"{item.id}: {labels} on {variant}"
        assert len(labels) == len(set(labels)), item.id


def test_AN_ALTERNATIVE_THE_SMALL_SURFACE_LACKS_IS_DROPPED_THERE() -> None:
    """The item "Is the order on time?" accepts `list_open_orders`, which only the wide surface has."""
    assert _item("d1-ask_the_user-2", "wide").expected == ["ask_the_user", "list_open_orders"]
    assert _item("d1-ask_the_user-2", "small").expected == "ask_the_user"


def test_D2_LABELS_ARE_THE_ROSTER_AND_EACH_WORKER_IS_SEEN_BOTH_WAYS() -> None:
    items = gold("d2").items
    # the roster's workers hold exactly the planning operations, so a "none" brief is one the orchestrator can serve
    assert worker_operations(loom(), _a_context()) == {op for _d, op in WORKERS.values()}
    for item in items:
        assert item.expected == ["none"] or set(item.expected) <= set(WORKERS), item.id
    for worker in WORKERS:
        needed = sum(worker in i.expected for i in items)
        assert needed >= 10 and len(items) - needed >= 10, f"{worker}: {needed} of {len(items)}"
    assert sum(i.expected == ["none"] for i in items) >= 4


def test_B1_IS_BALANCED_AND_EVERY_REFERS_BACK_ITEM_HAS_EARLIER_TURNS() -> None:
    items = gold("b1").items
    counts = Counter(i.expected for i in items)

    assert counts == {"self_contained": 10, "refers_back": 10}
    assert all(i.input["turns"] for i in items if i.expected == "refers_back")


def test_NO_ITEM_COMES_FROM_ANOTHER_PRODUCT_OR_A_CLIENT() -> None:
    for name in ("d1", "d2", "b1"):
        for item in gold(name).items:
            assert not NOT_LOOM.search(str(item.input)), f"{item.id}: {item.input}"


def test_THE_GUARD_CATCHES_A_PLANTED_FOREIGN_ITEM() -> None:
    """Planted, because the real sets comply and would prove nothing about the pattern."""
    assert NOT_LOOM.search("Which clause caps liability in this matter?")
    assert not NOT_LOOM.search("Which mills can we order from?")


def test_AN_UNKNOWN_VARIANT_IS_REFUSED() -> None:
    """A misspelt variant would otherwise read as `small` and quietly measure the wrong option space."""
    with pytest.raises(ValueError, match="variant"):
        gold("d1", variant="smal")


def _item(item_id: str, variant: str) -> GoldItem:
    return next(i for i in gold("d1", variant=variant).items if i.id == item_id)


def _planted() -> GoldSet:
    """Planted, so each rule of the scorer is shown on a case built to exercise it."""
    return GoldSet(name="planted", version="1", items=[
        GoldItem(id="own", input="", expected="read_order"),
        GoldItem(id="alternative", input="", expected=["read_lab_report", "delegate"]),
        GoldItem(id="wrong", input="", expected=["read_lab_report", "delegate"]),
        GoldItem(id="missing", input="", expected="read_order"),
    ])


def test_AN_ACCEPTABLE_ALTERNATIVE_IS_CORRECT_AND_COUNTED_UNDER_THE_ITEMS_OWN_LABEL() -> None:
    result = score_choice(_planted(), {"own": "read_order", "alternative": "delegate", "wrong": "answer"}, floor=0.5)

    assert result.diagnostics["per_label"] == {"read_order": {"n": 2, "accuracy": 0.5},
                                               "read_lab_report": {"n": 2, "accuracy": 0.5}}
    assert result.diagnostics["alternatives"] == 1
    assert result.diagnostics["accuracy"] == 0.5


def test_A_WRONG_CHOICE_IS_A_CONFUSION_AND_A_MISSING_ONE_AN_ERROR() -> None:
    result = score_choice(_planted(), {"own": "read_order", "alternative": "delegate", "wrong": "answer"}, floor=0.5)

    assert result.diagnostics["errors"] == 1
    assert {"own": "read_lab_report", "chose": "answer", "count": 1} in result.diagnostics["confusions"]
    assert {"own": "read_order", "chose": "None", "count": 1} in result.diagnostics["confusions"]


def test_THE_GATE_IS_THE_WEAKEST_LABEL() -> None:
    every = {"own": "read_order", "alternative": "delegate", "wrong": "delegate", "missing": "answer"}

    assert score_choice(_planted(), every, floor=0.5).passed
    assert not score_choice(_planted(), every, floor=0.6).passed
    assert score_choice(_planted(), every, floor=0.6).gate == {"min_label_accuracy": 0.5}
