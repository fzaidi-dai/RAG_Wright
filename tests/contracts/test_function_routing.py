"""KG-5e: the dimension->function router (pure logic; no store)."""

from __future__ import annotations

from rag_wright.contracts.function_routing import build_cooccurrence, route_functions


def test_cooccurrence_counts_once_per_clause_dimension_function():
    # clause c1 has dimension `mutuality` twice (two values) -> counted once for Indemnification
    rows = [
        ("c1", "Indemnification", "mutuality"),
        ("c1", "Indemnification", "mutuality"),
        ("c1", "Indemnification", "claim_scope"),
        ("c2", "Cap On Liability", "liability_cap"),
    ]
    cooc = build_cooccurrence(rows)
    assert cooc["mutuality"] == {"Indemnification": 1}
    assert cooc["claim_scope"] == {"Indemnification": 1}
    assert cooc["liability_cap"] == {"Cap On Liability": 1}


def test_cooccurrence_drops_none_and_empty_function():
    cooc = build_cooccurrence([
        ("c1", "NONE", "mutuality"),
        ("c2", "", "mutuality"),
        ("c3", "Indemnification", ""),
    ])
    assert cooc == {}


def test_route_scores_by_conditional_probability_and_takes_top_k():
    # jurisdiction points almost entirely at Governing Law; mutuality at Indemnification
    cooc = {
        "jurisdiction": {"Governing Law": 9, "Indemnification": 1},
        "mutuality": {"Indemnification": 8, "Cap On Liability": 2},
    }
    # a query with only jurisdiction -> Governing Law first
    assert route_functions(["jurisdiction"], cooc, k=1) == ["Governing Law"]
    # a query with both dims -> both functions surface, top-2 = the two majorities
    top2 = route_functions(["jurisdiction", "mutuality"], cooc, k=2)
    assert set(top2) == {"Governing Law", "Indemnification"}


def test_lift_corrects_the_frequency_bias_conditional_gets_wrong():
    # the real map's problem: cap_quantum co-occurs with a COMMON function (Minimum Commitment, huge base
    # rate) slightly more than its DISTINCTIVE one (Cap On Liability, small base rate).
    cooc = {
        "cap_quantum": {"Minimum Commitment": 83, "Cap On Liability": 67},
        # base rates: Minimum Commitment is everywhere, Cap On Liability is niche
        "commitment_quantum": {"Minimum Commitment": 900},
        "carve_out": {"Cap On Liability": 10},
    }
    # conditional routes to the common function (the bug KG-5e observed)
    assert route_functions(["cap_quantum"], cooc, k=1, score="conditional") == ["Minimum Commitment"]
    # lift divides out the base rate -> routes to the distinctive one
    assert route_functions(["cap_quantum"], cooc, k=1, score="lift") == ["Cap On Liability"]
    # pmi (log form) agrees
    assert route_functions(["cap_quantum"], cooc, k=1, score="pmi") == ["Cap On Liability"]


def test_min_support_drops_single_clause_coincidences():
    cooc = {"dim": {"Rare Function": 1, "Solid Function": 20}, "other": {"Solid Function": 40}}
    assert route_functions(["dim"], cooc, k=2, score="lift", min_support=3) == ["Solid Function"]


def test_route_ignores_unseen_dimensions_and_is_empty_without_signal():
    cooc = {"jurisdiction": {"Governing Law": 5}}
    assert route_functions(["not_a_dim", "another"], cooc, k=3) == []
    assert route_functions([], cooc, k=3) == []
