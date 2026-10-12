"""EV-2: the eval contracts -- a gold set (`GoldItem`, `GoldSet`) and the report of a run over it (`EvalReport`).

A gold item is one case: its `input`, what is `expected`, the `group` that keeps a split leakage-safe (the source
document, contract or batch it came from), and its `provenance` (`hand` from a domain expert, `silver` from a
curated teacher, `generated` for a synthetic case). A gold set is versioned and is `local_only` unless said
otherwise: client data never leaves the machine. Inputs, expectations and metadata are JSON-ready, so a gold set
saves, loads and travels to an optional backend unchanged.

`EvalReport` is what a run produces (EV-4 fills it): one `ItemResult` per gold item (an output and an error slot
per repeat; a failed call is an error, never an answer -- ADR-0133), one `ScorerResult` per scorer (its gated
measures against their thresholds, and diagnostics that are never gated), and what was measured. `passed`,
`errors` and `flips` are derived and serialised with it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

Provenance = Literal["hand", "silver", "generated"]


def _json_ready(value: Any) -> Any:
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"not JSON-ready: {exc}") from exc
    return value


class GoldItem(BaseModel):
    """One gold case. `id` is stable across versions (a backend keys runs on it)."""

    id: str = Field(min_length=1)
    input: Any
    expected: Any
    group: Optional[str] = None
    provenance: Provenance = "hand"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("input", "expected", "metadata")
    @classmethod
    def _serialisable(cls, value: Any) -> Any:
        return _json_ready(value)


class GoldSet(BaseModel):
    """A named, versioned set of gold items. `local_only` (default True) keeps it off any hosted backend."""

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    items: list[GoldItem] = Field(min_length=1)
    local_only: bool = True
    description: str = ""

    @model_validator(mode="after")
    def _unique_ids(self) -> "GoldSet":
        seen: set[str] = set()
        dupes = sorted({i.id for i in self.items if i.id in seen or seen.add(i.id)})
        if dupes:
            raise ValueError(f"duplicate item ids: {', '.join(dupes)}")
        return self

    def groups(self) -> dict[Optional[str], list[str]]:
        """Item ids by grouping key, in item order (split on these, never on items)."""
        out: dict[Optional[str], list[str]] = {}
        for item in self.items:
            out.setdefault(item.group, []).append(item.id)
        return out


class ItemResult(BaseModel):
    """One gold item's outputs and errors, one slot per repeat (`None` error = the call answered)."""

    item_id: str
    outputs: list[Any]
    errors: list[Optional[str]]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def flipped(self) -> bool:
        """Whether the answered repeats disagree (an error is not an answer)."""
        answered = [json.dumps(o, sort_keys=True) for o, e in zip(self.outputs, self.errors) if e is None]
        return len(set(answered)) > 1


class ScorerResult(BaseModel):
    """One scorer's verdict: `gate` holds the gated measures, `thresholds` what they must reach, `diagnostics`
    what is reported and never gated."""

    scorer: str
    passed: bool
    gate: dict[str, Optional[float]] = Field(default_factory=dict)
    thresholds: dict[str, float] = Field(default_factory=dict)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class EvalReport(BaseModel):
    """The JSON-ready result of an eval run: what was measured, per item, per scorer."""

    gold_set: str
    gold_version: str
    task: str
    engine_version: str
    models: list[str] = Field(default_factory=list)
    repeats: int = Field(default=1, ge=1)
    items: list[ItemResult]
    scores: list[ScorerResult]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    run_url: Optional[str] = None  # the first repeat's run on an eval backend (EV-5)
    run_urls: list[str] = Field(default_factory=list)  # every repeat's run, in order
    dataset_version: Optional[str] = None  # the backend dataset version the run read (a server timestamp)

    @model_validator(mode="after")
    def _one_slot_per_repeat(self) -> "EvalReport":
        bad = [i.item_id for i in self.items if len(i.outputs) != self.repeats or len(i.errors) != self.repeats]
        if bad:
            raise ValueError(f"items without one output and one error slot per repeat ({self.repeats}): "
                             f"{', '.join(bad[:10])}")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        """Every scorer passed (a run with no scorer has not passed)."""
        return bool(self.scores) and all(s.passed for s in self.scores)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def errors(self) -> int:
        """Failed calls across all items and repeats."""
        return sum(e is not None for i in self.items for e in i.errors)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def flips(self) -> list[str]:
        """Ids of the items whose answered repeats disagree."""
        return [i.item_id for i in self.items if i.flipped]
