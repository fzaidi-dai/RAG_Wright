"""ADR-0045: client-side XML-tag structured output. Hermetic -- no LLM (stub build_model), no network."""

from __future__ import annotations

from enum import Enum
from typing import Optional

import pytest
from pydantic import BaseModel

from rag_wright.models import tag_structured as ts
from rag_wright.models.tag_structured import (
    build_tag_structured,
    parse_tagged,
    tag_instructions,
)


class _Choice(str, Enum):
    A = "alpha"
    B = "beta"


class _Flat(BaseModel):
    name: str
    count: int = 0
    flag: bool = False
    ratio: float = 1.0
    choice: _Choice = _Choice.A
    items: list[str] = []
    note: Optional[str] = None


class _Nested(BaseModel):
    rows: list[_Flat] = []


# --- instructions -------------------------------------------------------------------------------------------


def test_tag_instructions_lists_every_field_and_enum_values():
    instr = tag_instructions(_Flat)
    for tag in ("<name>", "<count>", "<flag>", "<choice>", "<items>", "<note>"):
        assert tag in instr
    assert "alpha | beta" in instr  # enum options surfaced
    assert "one value per line" in instr  # list guidance


# --- parsing ------------------------------------------------------------------------------------------------


def test_parse_flat_coerces_scalars_enums_and_lists():
    text = ("<name>cap clause</name><count>3</count><flag>true</flag><ratio>0.5</ratio>"
            "<choice>beta</choice><items>\nx\ny\nz\n</items><note>hi</note>")
    out = parse_tagged(text, _Flat)
    assert out.name == "cap clause" and out.count == 3 and out.flag is True and out.ratio == 0.5
    assert out.choice is _Choice.B and out.items == ["x", "y", "z"] and out.note == "hi"


def test_parse_absent_tag_uses_default():
    out = parse_tagged("<name>only</name>", _Flat)  # everything else omitted
    assert out.name == "only" and out.count == 0 and out.flag is False and out.note is None and out.items == []


def test_parse_list_splits_on_newlines_and_commas():
    out = parse_tagged("<name>n</name><items>a, b\nc</items>", _Flat)
    assert out.items == ["a", "b", "c"]


def test_parse_is_case_insensitive_and_dotall():
    out = parse_tagged("<NAME>multi\nline</NAME>", _Flat)
    assert out.name == "multi\nline"


def test_nested_list_of_models_is_a_documented_extension_point():
    with pytest.raises(NotImplementedError):
        tag_instructions(_Nested)  # list[BaseModel] not built yet (ingestion, later)


# --- the runnable (drop-in for build_structured) ------------------------------------------------------------


def _stub_build_model(monkeypatch, contents):
    """Make ts.build_model return a client whose .invoke(...).content yields the next canned string."""
    seq = iter(contents)

    class _Msg:
        def __init__(self, c): self.content = c

    class _Client:
        def invoke(self, prompt):
            return _Msg(next(seq))

    monkeypatch.setattr(ts, "build_model", lambda *a, **k: _Client())


def test_build_tag_structured_invokes_free_text_and_parses(monkeypatch):
    _stub_build_model(monkeypatch, ["<name>parsed</name><count>7</count>"])
    out = build_tag_structured("any/model", _Flat).invoke("Question?")
    assert isinstance(out, _Flat) and out.name == "parsed" and out.count == 7


def test_build_tag_structured_retries_on_validation_error(monkeypatch):
    # first response is missing the required <name> -> ValidationError -> retry -> second is valid
    _stub_build_model(monkeypatch, ["<count>1</count>", "<name>ok</name>"])
    out = build_tag_structured("any/model", _Flat, retries=1).invoke("Q")
    assert out.name == "ok"


def test_include_raw_is_rejected():
    with pytest.raises(NotImplementedError):
        build_tag_structured("m", _Flat, include_raw=True)
