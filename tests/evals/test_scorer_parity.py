"""EV-3: each packaged scorer reproduces an engine eval's published numbers from that eval's SAVED outputs, so the
scorers are the method, not an approximation of it. The saved outputs are local data (CUAD-derived, gitignored):
each test skips when its files are absent."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from rag_wright.api import (GoldItem, GoldSet, score_candidate_choice, score_classification, score_relational,
                            score_retrieval)

ROOT = Path(__file__).resolve().parents[2]
HOLDOUT = ROOT / "data" / "cache" / "legalbert_holdout_eval.json"
AB = ROOT / "data" / "models" / "ab_preds"
LOGITS = ROOT / "data" / "models" / "holdout_logits.npz"
RESIDUAL = ROOT / "data" / "eval" / "residual_gate"


def _need(*paths: Path) -> None:
    missing = [str(p.relative_to(ROOT)) for p in paths if not p.exists()]
    if missing:
        pytest.skip(f"local eval data absent: {', '.join(missing)}")


# --- 1. classification: the clause-function A/B (scripts/train_legalbert_modal.py, mean non-NONE recall) ----------

@pytest.mark.parametrize("model", ["nlpaueb_legal-bert-base-uncased", "microsoft_deberta-v3-base",
                                   "nlpaueb_bert-base-uncased-contracts", "Qwen_Qwen2-5-0-5B"])
def test_classification_reproduces_the_ab_mean_class_recall(model):
    _need(HOLDOUT, AB / f"{model}.json")
    gold_labels = json.loads(HOLDOUT.read_text())["gold"]
    saved = json.loads((AB / f"{model}.json").read_text())
    gold = GoldSet(name="holdout", version="1", items=[GoldItem(id=str(i), input="", expected=g)
                                                        for i, g in enumerate(gold_labels)])
    r = score_classification(gold, {str(i): p for i, p in enumerate(saved["hpreds"])}, floor=0.0, exclude=["NONE"])
    assert r.diagnostics["mean_class_recall@1"] == pytest.approx(saved["mean_recall"], abs=1e-12)


def test_top_k_recall_matches_an_independent_count_on_the_holdout_logits():
    _need(LOGITS)
    z = np.load(LOGITS, allow_pickle=True)
    labels, logits, gold_idx = list(z["labels"]), z["logits"], z["gold"]
    top3 = np.argsort(-logits, axis=1)[:, :3]
    expected = {}
    for c in set(gold_idx.tolist()):
        rows = gold_idx == c
        expected[labels[c]] = float((top3[rows] == c).any(axis=1).mean())
    gold = GoldSet(name="holdout", version="1", items=[GoldItem(id=str(i), input="", expected=labels[g])
                                                        for i, g in enumerate(gold_idx.tolist())])
    preds = {str(i): dict(zip(labels, row.tolist())) for i, row in enumerate(logits)}
    r = score_classification(gold, preds, floor=0.0, k=3)
    assert {c: v["recall"] for c, v in r.diagnostics["per_class"].items()} == pytest.approx(expected, abs=1e-12)
    assert r.diagnostics["tags_per_item"] == 3


# --- 8. candidate-then-choose: the residual decision lane (eval/residual_decision_gold.py --score) -----------------

def _residual():
    from rag_wright.packs.contracts.spans.residual_candidates import candidates

    provisions = {p["clause_id"]: p for p in json.loads((RESIDUAL / "setA_provisions.json").read_text())}
    decision = {r["clause_id"]: r["values"] for r in json.loads((RESIDUAL / "setA_decision.json").read_text())}
    ids = json.loads((RESIDUAL / "residual_heldout_ids.json").read_text())
    labels = json.loads((RESIDUAL / "residual_heldout_labels.json").read_text())["labels"]
    items, cands = [], {}
    for n, cid in enumerate(ids):
        lab = labels.get(str(n), {})
        spans = [s for _k, s in candidates(provisions[cid]["text"])]
        cands[cid] = spans
        items.append(GoldItem(id=cid, input="", expected={
            "values": [[lab[str(i)], s] for i, s in enumerate(spans) if lab.get(str(i)) not in (None, "ambiguous")],
            "ignore": [s for i, s in enumerate(spans) if lab.get(str(i)) == "ambiguous"]}))
    gold = GoldSet(name="residual-heldout", version="1", items=items)
    return gold, provisions, decision, cands


@pytest.mark.parametrize("lane, hits", [("decision", (50, 60, 43, 49)), ("llm", (36, 60, 31, 39))])
def test_candidate_choice_reproduces_the_residual_lane_numbers(lane, hits):
    _need(RESIDUAL / "setA_provisions.json", RESIDUAL / "setA_decision.json", RESIDUAL / "residual_heldout_ids.json")
    gold, provisions, decision, cands = _residual()
    values = ({cid: [[d, v] for d, v, _p in decision.get(cid, [])] for cid in cands} if lane == "decision"
              else {cid: provisions[cid]["values"] for cid in cands})
    r = score_candidate_choice(gold, {cid: {"candidates": cands[cid], "values": values[cid]} for cid in cands},
                               recall=0.0, precision=0.0, coverage=0.0)
    d = r.diagnostics
    assert (d["recall_hits"], d["expected_values"], d["precision_hits"], d["emitted_values"]) == hits


def test_candidate_coverage_of_the_llm_values_matches_the_recount():
    _need(RESIDUAL / "setA_provisions.json")
    from rag_wright.packs.contracts.spans.residual_candidates import candidates

    provisions = json.loads((RESIDUAL / "setA_provisions.json").read_text())
    gold = GoldSet(name="setA", version="1", items=[GoldItem(id=p["clause_id"], input="", expected=p["values"])
                                                     for p in provisions])
    preds = {p["clause_id"]: {"candidates": [s for _k, s in candidates(p["text"])], "values": []} for p in provisions}
    d = score_candidate_choice(gold, preds, recall=0.0, precision=0.0, coverage=0.0).diagnostics
    assert (d["covered_values"], d["expected_values"]) == (188, 203)


# --- 2. retrieval: the ACORD bar (eval/acord_retrieval.py) on real rankings -----------------------------------------

ACORD = ROOT / "data" / "acord" / "extracted" / "ACORD Dataset & ReadMe (external)"


def test_retrieval_matches_the_acord_bar_on_real_rankings():
    """No ACORD run kept its rankings, so real ones are made here: a TF-IDF retriever over the ACORD corpus for the
    test queries (deterministic, no model). The engine's eval and the scorer score the same rankings."""
    _need(ACORD / "corpus.jsonl", ACORD / "qrels" / "test.tsv")
    from sklearn.feature_extraction.text import TfidfVectorizer

    from eval.acord import RELEVANCE_FLOOR, load_corpus, load_test_queries
    from eval.acord_retrieval import evaluate_retrieval

    corpus = load_corpus(ACORD)
    queries = load_test_queries(ACORD)
    vec = TfidfVectorizer(sublinear_tf=True, stop_words="english").fit([c.text for c in corpus])
    docs = vec.transform([c.text for c in corpus])
    ids = [c.clause_id for c in corpus]
    ranked = {}
    for q in queries:
        sims = (docs @ vec.transform([q.text]).T).toarray().ravel()
        ranked[q.text] = [ids[i] for i in np.argsort(-sims, kind="stable")[:50]]
    ref = evaluate_retrieval(queries, lambda text: ranked[text], k_gate=50, k_secondary=10)
    gold = GoldSet(name="acord", version="test", items=[GoldItem(id=q.query_id, input=q.text, expected=q.graded)
                                                         for q in queries])
    r = score_retrieval(gold, {q.query_id: ranked[q.text] for q in queries}, k=50, recall=0.0,
                        relevant_grade=RELEVANCE_FLOOR, ndcg_k=10, also_recall_at=[10])
    assert r.gate["recall@50"] == pytest.approx(ref.recall_at_50, abs=1e-12)
    assert r.diagnostics["recall@10"] == pytest.approx(ref.recall_at_10, abs=1e-12)
    assert r.diagnostics["ndcg@10"] == pytest.approx(ref.ndcg_at_10, abs=1e-12)
    assert r.diagnostics["per_item_recall@50"] == pytest.approx(ref.per_query_recall_at_50, abs=1e-12)


# --- 6. graph / relational: the EDGAR graph leg (eval/relational_eval.py), live on the local store --------------------

@pytest.mark.store
def test_relational_matches_the_graph_leg_live(monkeypatch):
    """The graph is built from the human-verified EDGAR set (no model) into a throwaway `*_live` database, which the
    store-test cleanup drops afterwards."""
    _need(ROOT / "data" / "edgar" / "verification_set.json")
    import importlib

    monkeypatch.setenv("DB", "edgar_relational_live")
    monkeypatch.setenv("RESET", "1")
    importlib.reload(importlib.import_module("scripts.populate_entity_graph")).main()
    from eval.multihop import build_relational
    from eval.relational_eval import graph_leg_recall
    from rag_wright.capabilities.graph_query import graph_query
    from rag_wright.packs.contracts.ontology.contract_taxonomy import CONTRACTS_WITH
    from rag_wright.store.arcadedb import ArcadeDBStore

    questions = build_relational(json.loads((ROOT / "data" / "edgar" / "verification_set.json").read_text()))
    store = ArcadeDBStore.from_env(database="edgar_relational_live")
    ref = graph_leg_recall(questions, store)
    reached = {q.qid: [ev.entity_id for ev in graph_query(q.anchor.identity, store=store,
                                                           relationship_type=CONTRACTS_WITH,
                                                           max_hops=q.hop_count).evidence] for q in questions}
    gold = GoldSet(name="edgar-relational", version="1", items=[
        GoldItem(id=q.qid, input=q.anchor.identity, expected=sorted({a.identity for a in q.answer_entities}),
                 metadata={"hops": q.hop_count}) for q in questions])
    r = score_relational(gold, reached, recall=0.0, k=50, by="hops")
    assert r.gate["recall"] == pytest.approx(ref["overall"], abs=1e-12)
    for hop, row in r.diagnostics["by_hops"].items():
        assert row["recall"] == pytest.approx(ref[f"{hop}hop"], abs=1e-12)
        assert row["n"] == ref["counts"][f"{hop}hop"]
    # the live graph answers every question fully, so also compare where recall is below 1: the same answers cut to
    # the first k reached ids, against the engine eval's own metric (eval.harness.recall_at_k)
    from statistics import mean

    from eval.harness import recall_at_k

    for k in (1, 2):
        per = {q.qid: recall_at_k(reached[q.qid], {a.identity for a in q.answer_entities}, k) for q in questions}
        assert min(per.values()) < 1.0
        cut = score_relational(gold, reached, recall=0.0, k=k, by="hops")
        assert cut.diagnostics["per_item_recall"] == pytest.approx(per, abs=1e-12)
        for hop, row in cut.diagnostics["by_hops"].items():
            assert row["recall"] == pytest.approx(mean(per[q.qid] for q in questions if str(q.hop_count) == hop))
