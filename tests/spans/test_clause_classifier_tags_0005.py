"""Issue 0005 route (b): the clause-function classifier's CLIENT-SIDE free-text tag format + parse (the nested
list[BaseModel] schema can't use build_tag_structured, and server-side guided decoding runs away on Granite --
so the classifier flattens the nesting into the tag body, like answer_generator.parse_tagged_answer). Hermetic --
the parsers are pure; the runnable's stream is monkeypatched."""

from __future__ import annotations

from rag_wright.packs.contracts.spans.clause_function_classifier import (
    _TagClassifierRunnable,
    parse_batch_span_tags,
    parse_clause_function_tags,
)


def test_parse_batch_tags_multi_span_multi_label_primary_first():
    text = ('<span index="0">Cap On Liability:high, Indemnification:medium</span>\n'
            '<span index="3">Governing Law:high</span>')
    out = parse_batch_span_tags(text)
    assert [s.span_index for s in out.spans] == [0, 3]  # aligned by the EXPLICIT index, not list position
    assert [(r.function, r.confidence) for r in out.spans[0].functions] == \
        [("Cap On Liability", "high"), ("Indemnification", "medium")]  # order preserved (primary-first)


def test_parse_batch_tags_other_label_carries_the_taxonomy_gap():
    out = parse_batch_span_tags('<span index="1">OTHER:medium:Exclusive Supply</span>')
    r = out.spans[0].functions[0]
    assert (r.function, r.confidence, r.other_label) == ("OTHER", "medium", "Exclusive Supply")


def test_parse_batch_tags_is_sparse_and_tolerant():
    assert parse_batch_span_tags("no tags at all").spans == []      # no cut -> empty
    assert parse_batch_span_tags('<span index="2"></span>').spans == []  # empty body -> span omitted (sparse)


def test_parse_clause_tags_and_empty_and_garbage():
    out = parse_clause_function_tags("<functions>Cap On Liability:high, Indemnification:low</functions>")
    assert [(r.function, r.confidence) for r in out.functions] == \
        [("Cap On Liability", "high"), ("Indemnification", "low")]
    assert parse_clause_function_tags("<functions></functions>").functions == []  # none apply
    assert parse_clause_function_tags("model rambled with no tags").functions == []  # tolerant -> empty


async def test_tag_runnable_ainvoke_streams_with_label_then_parses(monkeypatch):
    async def fake_astream(model_id, prompt, **kw):
        assert kw.get("label") == "clause_function_classifier.classify_spans"  # the stage label reaches the seam
        assert prompt.endswith("TAGS")  # the instructions were appended to the prompt
        return '<span index="0">Governing Law:high</span>'

    monkeypatch.setattr("rag_wright.pack_sdk.astream_text", fake_astream)
    runnable = _TagClassifierRunnable(
        "m", instructions="TAGS", parse=parse_batch_span_tags, label="clause_function_classifier.classify_spans")
    out = await runnable.ainvoke("classify this section")
    assert out.spans[0].functions[0].function == "Governing Law"
