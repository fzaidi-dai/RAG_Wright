"""Tests for the golden eval harness + CUAD-annotation golden builder (T9, RAC-9).

Proves the plumbing before the capabilities it measures exist: recall@k is correct, the harness
keeps the text and graph legs separate, and the CUAD builder produces questions across the three
archetypes (skipping absent categories). The archetype map covers all 41 categories.
"""

from rag_wright.packs.contracts.schemas.ontology import ClauseCategory

from eval.golden import ARCHETYPE_BY_CATEGORY, build_golden
from eval.harness import Archetype, GoldenQuestion, evaluate, recall_at_k


# --- recall@k (RAC-9) -----------------------------------------------------------------------


def test_recall_at_k_basic():
    assert recall_at_k(["a", "b", "c"], {"a", "c"}, k=3) == 1.0
    assert recall_at_k(["a", "x", "y"], {"a", "c"}, k=3) == 0.5
    assert recall_at_k(["x", "y", "a"], {"a"}, k=2) == 0.0  # a is outside top-2
    assert recall_at_k([], set(), k=5) == 1.0  # nothing relevant -> vacuously 1.0


# --- harness keeps legs separate (RAC-9) ----------------------------------------------------


def _q(qid, archetype, relevant):
    return GoldenQuestion(
        qid=qid, source_doc_id="d", archetype=archetype, question="?", relevant_ids=set(relevant)
    )


def test_evaluate_reports_recall_per_archetype_and_leg():
    golden = [
        _q("q1", Archetype.EXACT_LEXICAL, {"c1"}),
        _q("q2", Archetype.SEMANTIC, {"c2", "c3"}),
    ]

    # text leg finds everything; graph leg finds nothing -> legs must differ in the report.
    def retrieve(question, leg):
        return list(question.relevant_ids) if leg == "text" else []

    report = evaluate(golden, retrieve, k=5)
    assert report.per_archetype_leg["exact_lexical/text"] == 1.0
    assert report.per_archetype_leg["exact_lexical/graph"] == 0.0
    assert report.per_archetype_leg["semantic/text"] == 1.0
    assert report.per_archetype_leg["semantic/graph"] == 0.0
    assert report.counts == {"exact_lexical": 1, "semantic": 1}


# --- CUAD golden builder (RAC-9) ------------------------------------------------------------


_SQUAD_FIXTURE = [
    {
        "title": "DOC1",
        "paragraphs": [
            {
                "context": "...",
                "qas": [
                    {
                        "question": 'Highlight the parts related to "Governing Law" ...',
                        "answers": [{"text": "State of Delaware", "answer_start": 5}],
                        "is_impossible": False,
                    },
                    {
                        "question": 'Highlight the parts related to "Non-Compete" ...',
                        "answers": [{"text": "shall not compete", "answer_start": 20}],
                        "is_impossible": False,
                    },
                    {
                        "question": 'Highlight the parts related to "Audit Rights" ...',
                        "answers": [{"text": "may audit the books", "answer_start": 40}],
                        "is_impossible": False,
                    },
                    {  # absent category -> skipped
                        "question": 'Highlight the parts related to "Insurance" ...',
                        "answers": [],
                        "is_impossible": True,
                    },
                ],
            }
        ],
    }
]


def test_build_golden_spans_the_three_archetypes_and_skips_absent():
    golden = build_golden(_SQUAD_FIXTURE)
    assert len(golden) == 3  # Insurance (is_impossible) skipped
    by_arch = {g.archetype for g in golden}
    assert by_arch == {Archetype.EXACT_LEXICAL, Archetype.SEMANTIC, Archetype.CLAUSE_FINDING}
    gl = next(g for g in golden if g.category is ClauseCategory.GOVERNING_LAW)
    assert gl.archetype is Archetype.EXACT_LEXICAL
    assert gl.answer_spans == ["State of Delaware"]
    assert gl.source_doc_id == "DOC1"


def test_build_golden_respects_include_docs():
    assert build_golden(_SQUAD_FIXTURE, include_docs={"OTHER"}) == []
    assert len(build_golden(_SQUAD_FIXTURE, include_docs={"DOC1"})) == 3


def test_archetype_map_covers_all_41_categories():
    assert set(ARCHETYPE_BY_CATEGORY) == set(ClauseCategory)
    assert len(ARCHETYPE_BY_CATEGORY) == 41
    # all three CUAD-derivable archetypes are represented (relational is T10, not here)
    assert {a for a in ARCHETYPE_BY_CATEGORY.values()} == {
        Archetype.EXACT_LEXICAL,
        Archetype.SEMANTIC,
        Archetype.CLAUSE_FINDING,
    }
