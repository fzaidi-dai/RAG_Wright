"""TAGPARSE-INGEST-1b: function-independent thematic-group clause extraction. Hermetic -- stub the model
(astream_text), exercise the real tag-parse passes + merge."""

from __future__ import annotations

import pytest

from rag_wright.models import tag_structured as ts
from rag_wright.ontology.clause_template import Clause, Mutuality
from rag_wright.spans.tag_clause_extractor import CLAUSE_GROUPS, atag_extract_clause


def test_clause_groups_cover_every_property_field_exactly_once():
    fields = set(Clause.model_fields) - {"document_reference"}  # the root id is filled separately
    assigned: list[str] = [f for g in CLAUSE_GROUPS.values() for f in g]
    assert set(assigned) == fields, f"uncovered: {fields - set(assigned)}"
    assert len(assigned) == len(set(assigned)), "a field is in more than one group"


# a canned model reply carrying tags across SEVERAL groups; each pass (fields=group) picks out only its own.
_CANNED = (
    "<clause_type>Confidentiality</clause_type>\n"
    "<has_mutuality>mutual</has_mutuality>\n"
    "<has_favorability>balanced</has_favorability>\n"
    "<caps>\n<cap_basis>fees</cap_basis>\n<cap_operator>lteq</cap_operator>\n"
    "<cap_quantum>12 months of fees</cap_quantum>\n</caps>\n"
    "<bounded_by>\n<temporal_duration>3 years</temporal_duration>\n<temporal_kind>term</temporal_kind>\n"
    "<temporal_operator>eq</temporal_operator>\n</bounded_by>\n"
    "<governed_by>\n<jurisdiction_name>Delaware</jurisdiction_name>\n<law_multiplicity>single</law_multiplicity>\n"
    "</governed_by>"
)


def _stub_astream(monkeypatch, reply):
    async def fake(model_id, prompt, **kw):
        return reply
    monkeypatch.setattr(ts, "astream_text", fake)


@pytest.mark.asyncio
async def test_atag_extract_clause_merges_the_group_passes(monkeypatch):
    _stub_astream(monkeypatch, _CANNED)
    c = await atag_extract_clause("some clause text", "any/model", document_reference="nda:1")
    assert c.document_reference == "nda:1"
    assert c.clause_type == "Confidentiality"                      # identity_scope group
    assert c.has_mutuality is Mutuality.MUTUAL                     # normalized on final construct
    assert c.caps is not None and c.caps.cap_quantum == "12 months of fees"   # liability_damages group
    assert c.bounded_by is not None and c.bounded_by.temporal_duration == "3 years"  # temporal group
    assert c.governed_by is not None and c.governed_by.jurisdiction_name == "Delaware"  # restrictions_duties group


@pytest.mark.asyncio
async def test_aspect_gate_prunes_unselected_groups(monkeypatch):
    # the gate selects only liability_damages -> restrictions_duties (governed_by) and temporal (bounded_by) are
    # SKIPPED, so those fields stay default even though the canned reply carries their tags. identity_scope always runs.
    _stub_astream(monkeypatch, "<aspects>liability_damages</aspects>\n" + _CANNED)
    c = await atag_extract_clause("txt", "any/model", gate=True)
    assert c.caps is not None and c.caps.cap_quantum == "12 months of fees"   # selected group extracted
    assert c.has_mutuality is Mutuality.MUTUAL                                  # identity_scope always runs
    assert c.bounded_by is None and c.governed_by is None                       # unselected groups pruned


@pytest.mark.asyncio
async def test_gate_false_runs_every_group(monkeypatch):
    _stub_astream(monkeypatch, _CANNED)  # no <aspects> tag
    c = await atag_extract_clause("txt", "any/model", gate=False)
    assert c.bounded_by is not None and c.governed_by is not None               # nothing pruned


@pytest.mark.asyncio
async def test_a_partial_group_degrades_but_others_survive(monkeypatch):
    # governed_by is emitted WITHOUT its required jurisdiction_name -> that field degrades to default (None);
    # every other group still extracts.
    bad = _CANNED.replace("<jurisdiction_name>Delaware</jurisdiction_name>\n", "")
    _stub_astream(monkeypatch, bad)
    c = await atag_extract_clause("txt", "any/model", gate=False)
    assert c.governed_by is None                                   # unbuildable optional nested -> omitted
    assert c.has_mutuality is Mutuality.MUTUAL and c.caps is not None  # unaffected groups intact


@pytest.mark.asyncio
async def test_value_sanity_guard_drops_leaked_reasoning(monkeypatch):
    # the model leaks its chain-of-thought (containing XML tags) into an open str field -> the guard drops it,
    # so no reasoning prose is ever stored as a clause property. A clean field in the same pass survives.
    leak = ("<clause_type>Cap on Liability</clause_type>\n"
            "<ld_trigger>Now write the tags: <has_claim_scope>first_party</has_claim_scope> etc.</ld_trigger>")
    _stub_astream(monkeypatch, leak)
    c = await atag_extract_clause("txt", "any/model", gate=False)
    assert c.ld_trigger is None            # garbage (contains <tags>) dropped
    assert c.clause_type == "Cap on Liability"  # clean field kept
