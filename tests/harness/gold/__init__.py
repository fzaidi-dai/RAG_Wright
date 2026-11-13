"""The routing gold sets (HD-1): D1, D2 and B1 on the Loom product, read by the engine's own gold loader.

- `d1.jsonl`: a message (and what the person had open) -> the chat-manager's choice: a direct tool, `delegate`,
  `ask_the_user` or `answer`. `expected` is the item's own label, or a list of it followed by acceptable
  alternatives (scored by `scoring.score_choice`: any one is correct). `variant` says which chat-manager surface the
  item is valid for (`both`, or `wide` for the eight tools only the wide surface has).
- `d2.jsonl`: an orchestrator brief -> the workers it needs (`["none"]` when the orchestrator's own tools do).
- `b1.jsonl`: earlier turns and a message -> `self_contained` (the brief can be the message verbatim) or
  `refers_back` (it needs rewriting first).
"""

from __future__ import annotations

from pathlib import Path

from rag_wright.api import GoldItem, GoldMapping, GoldSet, load_gold_set
from tests.harness.gold.loom import d1_options
from tests.harness.gold.scoring import accepted

HERE = Path(__file__).parent
VERSION = "2026-11-13"

_MAPPINGS = {
    "d1": GoldMapping(id="id", input=["message", "view"], expected="expected", metadata=["variant"]),
    "d2": GoldMapping(id="id", input=["brief", "view"], expected="expected"),
    "b1": GoldMapping(id="id", input=["turns", "message", "view"], expected="expected"),
}


def gold(name: str, *, variant: str | None = None) -> GoldSet:
    """One routing gold set. For D1 on a chat-manager `variant` (`small` or `wide`): only the items valid there, each
    keeping only the alternatives that are options there."""
    if variant not in (None, "small", "wide"):
        raise ValueError(f"variant must be 'small' or 'wide', not {variant!r}")
    loaded = load_gold_set(HERE / f"{name}.jsonl", _MAPPINGS[name], name=f"harness-{name}", version=VERSION)
    if variant is None:
        return loaded
    items = [_on(item, set(d1_options(variant))) for item in loaded.items
             if variant == "wide" or item.metadata["variant"] == "both"]
    return loaded.model_copy(update={"name": f"harness-{name}-{variant}", "items": items})


def _on(item: GoldItem, options: set[str]) -> GoldItem:
    """An item with its alternatives cut to the options a surface has; its own label is always one of them."""
    kept = [label for label in accepted(item.expected) if label in options]
    return item.model_copy(update={"expected": kept[0] if len(kept) == 1 else kept})
