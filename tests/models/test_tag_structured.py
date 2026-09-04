"""ADR-0045: client-side XML-tag structured output. Hermetic -- no LLM (stub build_model), no network."""

from __future__ import annotations

from enum import Enum
from typing import Optional

import pytest
from pydantic import BaseModel, Field

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


class _Sub(BaseModel):
    label: str
    kind: _Choice = _Choice.A


class _Deep(BaseModel):
    """Mirrors the ingestion contracts' nesting: a nested single BaseModel (like Clause.bounded_by /
    ContractParties has parties), a list[BaseModel] (like ContractParties.parties / Clause.excepts), plus a
    flat scalar and a list[scalar] to guard that flat behaviour is unchanged."""

    title: str
    bound: Optional[_Sub] = None       # nested single BaseModel
    rows: list[_Sub] = []              # list[BaseModel]
    tags: list[str] = []               # list[scalar] (regression guard)
    ref: Optional[str] = Field(None, max_length=3)  # a CONSTRAINED optional scalar (for lenient prune)


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


# --- nested schemas (TAGPARSE-INGEST-1a): nested single BaseModel + list[BaseModel] ------------------------


def test_tag_instructions_emits_nested_and_list_of_model_blocks():
    instr = tag_instructions(_Deep)
    # the nested single-model field surfaces its OWN sub-field tags inside its block
    assert "<bound>" in instr and "</bound>" in instr
    assert "<label>" in instr and "<kind>" in instr
    # the list[BaseModel] field surfaces a repeatable <item> block with the sub-fields
    assert "<rows>" in instr and "<item>" in instr
    assert "alpha | beta" in instr  # sub-model enum options surfaced (recursion reached the leaf)


def test_parse_nested_single_model():
    text = "<title>t</title><bound><label>x</label><kind>beta</kind></bound>"
    out = parse_tagged(text, _Deep)
    assert out.bound is not None and out.bound.label == "x" and out.bound.kind is _Choice.B


def test_parse_list_of_models():
    text = ("<title>t</title><rows>"
            "<item><label>a</label><kind>alpha</kind></item>"
            "<item><label>b</label><kind>beta</kind></item>"
            "</rows>")
    out = parse_tagged(text, _Deep)
    assert [r.label for r in out.rows] == ["a", "b"]
    assert [r.kind for r in out.rows] == [_Choice.A, _Choice.B]


def test_parse_absent_nested_uses_defaults():
    out = parse_tagged("<title>only</title>", _Deep)
    assert out.bound is None and out.rows == [] and out.tags == []


def test_nested_and_flat_coexist():
    text = ("<title>t</title><tags>p, q</tags>"
            "<bound><label>z</label></bound>"
            "<rows><item><label>r1</label></item></rows>")
    out = parse_tagged(text, _Deep)
    assert out.tags == ["p", "q"]                      # flat list still works
    assert out.bound.label == "z" and out.bound.kind is _Choice.A  # sub default applies
    assert len(out.rows) == 1 and out.rows[0].label == "r1"


def test_real_ingestion_contracts_are_now_tag_parseable():
    # the actual reason for 1a: Clause (nested bounded_by/caps/governed_by + list excepts) and ContractParties
    # (parties: list[Party]) must emit + round-trip through tags without NotImplementedError.
    from rag_wright.capabilities.dg_extraction import ContractParties

    instr = tag_instructions(ContractParties)  # must not raise
    assert "<parties>" in instr and "<item>" in instr and "<name>" in instr
    parsed = parse_tagged(
        "<title>Mutual NDA</title><parties>"
        "<item><name>Northwind Robotics, Inc.</name></item>"
        "<item><name>Cascade Analytics LLC</name></item>"
        "</parties>",
        ContractParties,
    )
    assert parsed.title == "Mutual NDA"
    assert [p.name for p in parsed.parties] == ["Northwind Robotics, Inc.", "Cascade Analytics LLC"]


# --- lenient degrade (TAGPARSE-INGEST-1a): omit-to-default AFTER the re-ask ---------------------------------


def test_strict_raises_on_partial_nested_so_it_can_re_ask():
    from pydantic import ValidationError
    # a <bound> block missing the required sub-field `label` -> STRICT must raise (drives build_tag_structured's re-ask)
    with pytest.raises(ValidationError):
        parse_tagged("<title>t</title><bound><kind>beta</kind></bound>", _Deep)


def test_lenient_omits_a_partial_nested_to_its_default():
    out = parse_tagged("<title>t</title><bound><kind>beta</kind></bound>", _Deep, lenient=True)
    assert out.title == "t" and out.bound is None  # unbuildable optional nested -> default


def test_lenient_omits_a_constraint_violating_scalar_to_its_default():
    out = parse_tagged("<title>t</title><ref>toolong</ref>", _Deep, lenient=True)  # ref max_length=3
    assert out.ref is None


def test_lenient_drops_only_the_invalid_list_items():
    text = ("<title>t</title><rows>"
            "<item><label>keep</label></item>"
            "<item><kind>beta</kind></item>"   # no label -> invalid, dropped
            "</rows>")
    out = parse_tagged(text, _Deep, lenient=True)
    assert [r.label for r in out.rows] == ["keep"]


def test_lenient_still_raises_on_a_missing_required_field():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):  # `title` is required and absent -> cannot omit-to-default
        parse_tagged("<ref>ok</ref>", _Deep, lenient=True)


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


def test_build_tag_structured_accepts_a_message_list(monkeypatch):
    # build_structured runnables accept a [SystemMessage, HumanMessage] list; the drop-in must too.
    from langchain_core.messages import HumanMessage, SystemMessage

    captured = {}

    class _Msg:
        def __init__(self, c): self.content = c

    class _Client:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            return _Msg("<name>from-messages</name>")

    monkeypatch.setattr(ts, "build_model", lambda *a, **k: _Client())
    out = build_tag_structured("m", _Flat).invoke([SystemMessage(content="sys"), HumanMessage(content="q")])
    assert out.name == "from-messages"
    # the tag instructions were appended as a trailing human turn, system/human kept intact
    assert captured["prompt"][0].content == "sys" and captured["prompt"][-1][0] == "human"
    assert "<name>" in captured["prompt"][-1][1]


def test_build_tag_structured_retries_on_validation_error(monkeypatch):
    # first response is missing the required <name> -> ValidationError -> retry -> second is valid
    _stub_build_model(monkeypatch, ["<count>1</count>", "<name>ok</name>"])
    out = build_tag_structured("any/model", _Flat, retries=1).invoke("Q")
    assert out.name == "ok"


def test_build_tag_structured_re_asks_then_omits_partial_nested_to_default(monkeypatch):
    # a partial <bound> (missing required `label`) twice: attempt 0 is STRICT -> re-ask; the LAST attempt is
    # lenient -> omit-to-default instead of raising. So a persistently-partial nested field degrades, it does
    # not fail the whole extraction. (retries=1 -> 2 attempts.)
    partial = "<title>t</title><bound><kind>beta</kind></bound>"
    _stub_build_model(monkeypatch, [partial, partial])
    out = build_tag_structured("m", _Deep, retries=1).invoke("Q")
    assert out.title == "t" and out.bound is None  # degraded, not an exception


def test_build_tag_structured_re_ask_recovers_before_lenient(monkeypatch):
    # attempt 0 partial (strict raise -> re-ask); attempt 1 valid -> returned as-is (lenient path not needed)
    _stub_build_model(monkeypatch,
                      ["<title>t</title><bound><kind>beta</kind></bound>",
                       "<title>t</title><bound><label>good</label><kind>beta</kind></bound>"])
    out = build_tag_structured("m", _Deep, retries=1).invoke("Q")
    assert out.bound is not None and out.bound.label == "good"


def test_include_raw_is_rejected():
    with pytest.raises(NotImplementedError):
        build_tag_structured("m", _Flat, include_raw=True)
