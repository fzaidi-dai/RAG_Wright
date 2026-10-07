"""ING-9b: the residual (numeric/open) property dimensions on the decision model -- the SHIPPED lane
(`spans.residual_candidates`: deterministic candidates + one Jev `choice` per candidate, one call per provision),
scored at the value level against hand labels.

Data (`data/eval/residual_gate/`, LOCAL ONLY -- CUAD-derived):
  setA_provisions.json             provision texts + today's LLM residual values (`--build`, from the reference scratch DB)
  residual_heldout_{ids,labels}.json  held-out hand labels (role of each candidate span; labelled before scoring)
  residual_blind_{ids,labels}.json    the tuning sample (used to shape the role criteria -- not a fair test)

  --build   write setA_provisions.json from `rw_scratch_contract_ref` (no model calls)
  --run     the shipped decision lane over set A (one Jev call per provision with candidates) -> setA_decision.json
  --score   value-level recall / precision on the held-out labels (decision lane vs the LLM values)

Run: `uv run python -u eval/residual_decision_gold.py --build --run --score`
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "eval" / "residual_gate"


def log(msg: str) -> None:
    print(msg, flush=True)


def build() -> None:
    import rag_wright.packs.contracts.capabilities.contract_kg_store as k
    from rag_wright.packs.contracts.spans.property_extractor import RESIDUAL_LLM_DIMS
    from rag_wright.store.arcadedb import ArcadeDBStore

    s = ArcadeDBStore.from_env(database="rw_scratch_contract_ref")

    def key(sid: str):
        head, idx = sid.rsplit("#", 1)
        doc, chunk, _h = head.rsplit(":", 2)
        return doc, int(chunk), int(idx)

    spans = sorted(s._query("SELECT span_id, text FROM Span LIMIT 20000"), key=lambda r: key(r["span_id"]))
    anchors = {r["span_id"]: r["clause_id"] for r in s._query("SELECT clause_id, span_id FROM Clause LIMIT 20000")}
    vals = defaultdict(list)
    for d in RESIDUAL_LLM_DIMS:
        et = k._DIM_EDGE_STR[d.value]
        if et in s.type_names():
            for r in s._query(f"SELECT outV().clause_id AS c, inV().value AS v FROM {et} WHERE dimension = '{d.value}' LIMIT 20000"):
                vals[r["c"]].append([d.value, r["v"]])
    out, cur = [], None
    for r in spans:
        doc, chunk, _ = key(r["span_id"])
        if r["span_id"] in anchors:
            cur = {"clause_id": anchors[r["span_id"]], "doc": doc, "chunk": chunk, "text": r["text"] or ""}
            out.append(cur)
        elif cur and (cur["doc"], cur["chunk"]) == (doc, chunk):
            cur["text"] += "\n" + (r["text"] or "")
        else:
            cur = None
    for o in out:
        o["values"] = vals.get(o["clause_id"], [])
    (DATA / "setA_provisions.json").write_text(json.dumps(out))
    log(f"[build] {len(out)} provisions, {sum(len(o['values']) for o in out)} LLM residual values")


async def run(concurrency: int) -> None:
    from rag_wright.api import load_reference_pack
    from rag_wright.packs.contracts.spans.residual_candidates import candidates, select_residual_extractor

    load_reference_pack()
    lane = select_residual_extractor()
    if lane is None:
        raise SystemExit("no decision model configured (OPENROUTER_API_KEY) or RAG_RESIDUAL_EXTRACTOR=llm")
    P = [p for p in json.loads((DATA / "setA_provisions.json").read_text()) if candidates(p["text"])]
    sem, done, t0 = asyncio.Semaphore(concurrency), 0, time.time()
    log(f"[run] start N={len(P)} decision calls (provisions with candidates)")

    async def one(p):
        nonlocal done
        async with sem:
            values = await lane.aextract_values(p["text"])
        done += 1
        if done % 25 == 0 or done == len(P):
            log(f"[run] {done}/{len(P)} {time.time() - t0:.0f}s")
        return {"clause_id": p["clause_id"], "values": values}

    res = await asyncio.gather(*(one(p) for p in P))
    (DATA / "setA_decision.json").write_text(json.dumps(res))


def score() -> None:
    from rag_wright.packs.contracts.spans.residual_candidates import candidates

    P = {p["clause_id"]: p for p in json.loads((DATA / "setA_provisions.json").read_text())}
    D = {r["clause_id"]: r["values"] for r in json.loads((DATA / "setA_decision.json").read_text())}
    ids = json.loads((DATA / "residual_heldout_ids.json").read_text())
    labels = json.loads((DATA / "residual_heldout_labels.json").read_text())["labels"]
    norm = lambda x: " ".join(str(x).split()).lower().strip(" .,;:")  # noqa: E731
    match = lambda r, v, s: norm(v) in norm(s) or norm(s) in norm(v)  # noqa: E731
    for name, get in (("decision", lambda cid: [(d, v) for d, v, _p in D.get(cid, [])]),
                      ("llm", lambda cid: [tuple(x) for x in P[cid]["values"]])):
        tp_r = n_truth = tp_p = n_emit = 0
        for n, cid in enumerate(ids):
            lab = labels.get(str(n), {})
            cands = candidates(P[cid]["text"])
            truth = [(lab.get(str(i)), s) for i, (_k, s) in enumerate(cands) if lab.get(str(i)) not in (None, "ambiguous")]
            amb = [s for i, (_k, s) in enumerate(cands) if lab.get(str(i)) == "ambiguous"]
            out = [(d, v) for d, v in get(cid) if not any(match(None, v, a) for a in amb)]
            n_truth += len(truth)
            tp_r += sum(any(d == r and match(r, v, s) for d, v in out) for r, s in truth)
            n_emit += len(out)
            tp_p += sum(any(d == r and match(r, v, s) for r, s in truth) for d, v in out)
        log(f"[score] {name:8} held-out value recall {tp_r}/{n_truth} = {tp_r / max(n_truth, 1):.2f} | "
            f"precision {tp_p}/{n_emit} = {tp_p / max(n_emit, 1):.2f}")


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--concurrency", type=int, default=12)
    a = ap.parse_args()
    if a.build:
        build()
    if a.run:
        asyncio.run(run(a.concurrency))
    if a.score:
        score()
