"""Unify the ACORD benchmark corpus into the ONE production KG (ADR-0033 one unified contract KG; ADR-0046).

ACORD is a CUAD-derivative retrieval benchmark (both The Atticus Project); 82 percent of its 3,931 clauses
already exist verbatim in `ragwright_cuad_full`. Rather than keep a stale separate KG (`ragwright_acord_pivot`,
only 14/23 property edges), we unify surgically:

  map    - acord_id to production parent_chunk_id: the ~82pct verbatim overlap to the existing CUAD clause.
           Writes acord_to_pcid.json + acord_nomatch.json (the ~710 remainder).
  ingest - ingest the remainder through the ENHANCED clause layer (segment, LegalBERT function-label, granite
           23-dim property extraction + ADR-0040 judge, typed edges + BGE span index). Isolated clauses so no
           contract/party layer. Canonical ids only (source_doc_id=acord-{aid}); OKF is dropped so
           parent_okf_path stays empty (tasks.md:434, CAP-REG-3 "no okf_path identity baked in"). Granite (NOT
           Cerebras, which 404s on granite) at concurrency 8; extraction concurrent, writes sequential. X/N
           progress -- MONITOR IT.
  qrels  - express the ACORD qrels against the unified KG canonical parent_chunk_ids (overlap map UNION the
           ingested clauses' acord- pcids recovered from the KG). Writes acord_prod_qrels.json.

  PYTHONPATH=. uv run --no-sync python scripts/acord_unify.py {map|ingest|qrels}
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

OUT = Path("data/eval/acord_unify")   # gitignored (regenerable); the script is the committed source of truth
DB = os.environ.get("QA_DB", "ragwright_cuad_full")
_WS = re.compile(r"\s+")


def _norm(t: str) -> str:
    return _WS.sub(" ", (t or "").lower()).strip()


def log(m: str) -> None:
    print(m, flush=True)


def cmd_map() -> None:
    import bisect

    from dotenv import load_dotenv

    load_dotenv()
    from eval.acord import load_corpus
    from rag_wright.store.arcadedb import ArcadeDBStore

    OUT.mkdir(parents=True, exist_ok=True)
    acord = load_corpus()
    log(f"[map] ACORD clauses: {len(acord)}")
    store = ArcadeDBStore.from_env(database=DB, reset=False)
    sep = "\n\x00\n"
    starts: list[int] = []
    pcids: list[str] = []
    parts: list[str] = []
    pos, skip, page = 0, 0, 20000
    while True:  # exclude acord- spans so map is idempotent (matches the ORIGINAL CUAD corpus, no self-match)
        rows = store._query(
            f"SELECT parent_chunk_id, text FROM Span WHERE parent_chunk_id NOT LIKE 'acord-%' SKIP {skip} LIMIT {page}")
        if not rows:
            break
        for r in rows:
            nt = _norm(r.get("text"))
            starts.append(pos)
            pcids.append(r.get("parent_chunk_id") or "")
            parts.append(nt)
            pos += len(nt) + len(sep)
        skip += len(rows)
        if len(rows) < page:
            break
    blob = sep.join(parts)
    log(f"[map] indexed {skip} spans | blob {len(blob):,} chars")

    def pcid_at(p: int) -> str:
        i = bisect.bisect_right(starts, p) - 1
        return pcids[i] if 0 <= i < len(pcids) else ""

    mapping: dict[str, str] = {}
    nomatch: list[dict[str, str]] = []
    for c in acord:
        t = _norm(c.text)
        snip = t if len(t) < 40 else t[len(t) // 2 - 25: len(t) // 2 + 25]
        p = blob.find(snip) if snip else -1
        if p >= 0:
            mapping[c.clause_id] = pcid_at(p)
        else:
            nomatch.append({"acord_id": c.clause_id, "text": c.text})
    json.dump(mapping, (OUT / "acord_to_pcid.json").open("w"))
    json.dump(nomatch, (OUT / "acord_nomatch.json").open("w"), ensure_ascii=False)
    log(f"[map] mapped {len(mapping)} -> {len(set(mapping.values()))} distinct clauses | remainder {len(nomatch)}")
    store.close()


def cmd_ingest() -> None:
    os.environ.setdefault("RAG_SERVING", "openrouter")   # granite via OpenRouter, NO Cerebras pin (granite 404s)
    os.environ.pop("OPENROUTER_PROVIDER", None)
    os.environ.pop("RAG_MODEL_GENERAL", None)            # keep GENERAL=granite (the extractor's model)
    from dotenv import load_dotenv

    load_dotenv()
    from rag_wright.capabilities.embedding import BGEM3Embedder, _resolve_device
    from rag_wright.packs.contracts.schemas.function import canonical_function
    from rag_wright.contracts.identifiers import ChunkId
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.packs.contracts.spans.clause_kg_extractor import granite_clause_extractor
    from rag_wright.packs.contracts.spans.legalbert_classifier import LegalBertFunctionClassifier
    from rag_wright.packs.contracts.spans.segment import segment_clause, to_span_record
    from rag_wright.packs.contracts.spans.semantic_judge import build_semantic_judge_fn
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.util.concurrent import map_concurrent

    conc = int(os.environ.get("CLAUSE_CONCURRENCY", "8"))
    items = json.load((OUT / "acord_nomatch.json").open())
    lim = int(os.environ.get("LIMIT", "0"))
    if lim:
        items = items[:lim]
    n = len(items)
    log(f"[ingest] {n} clauses -> {DB} | conc={conc}")
    store = ArcadeDBStore.from_env(database=DB)
    store.ensure_schema()
    classifier = LegalBertFunctionClassifier.load(Path("data/models/legalbert_function"), device=_resolve_device(None))
    embedder = BGEM3Embedder()
    extractor = granite_clause_extractor(
        semantic_judge_fn=build_semantic_judge_fn(model_for(ModelRole.STRUCTURED_REASONING)))

    clause_jobs: list = []
    spans_written = 0
    for i, it in enumerate(items, 1):
        aid, text = it["acord_id"], it["text"]
        cid0 = ChunkId.of(f"acord-{aid}", 0, text)
        ops = [op for op in segment_clause(cid0.value, text) if op.text.strip()]
        if not ops:
            continue
        raws = classifier.classify([op.text for op in ops])
        dvecs, svecs = embedder.encode_batch([op.text.strip() for op in ops])
        for idx, (op, raw, dv, sv) in enumerate(zip(ops, raws, dvecs, svecs)):
            fn = canonical_function(raw) or raw
            try:  # OKF dropped: no parent_okf_path; the ACORD id lives in the canonical parent_chunk_id
                store.upsert_span(to_span_record(op, document_id="", chunk_doc_start=0,
                                                 dense_vector=list(dv), sparse_vector=sv, primary_tag=fn))
                spans_written += 1
            except Exception:  # noqa: BLE001 - a per-span index write must not sink the clause
                pass
            cf = canonical_function(raw)
            if cf is not None:
                clause_jobs.append((ChunkId.of(f"acord-{aid}", idx, op.text), cf, op.text, op.span_id))
        if i % 50 == 0 or i == n:
            log(f"[ingest] segment/index {i}/{n}  spans={spans_written}  clause_jobs={len(clause_jobs)}")

    log(f"[ingest] granite extraction: {len(clause_jobs)} real-function spans (conc={conc})")

    def _extract(job):
        cid, fn, text, span_id = job
        try:
            return extractor(chunk_id=cid, function=fn, text=text, span_id=span_id)
        except Exception:  # noqa: BLE001 - a per-span extract failure is skipped (matches the pipeline)
            return None

    records = map_concurrent(clause_jobs, _extract, max_concurrency=conc, label="[ingest][granite]", echo=True)
    n_written = 0
    for rec in records:
        if rec is None:
            continue
        try:
            ContractKGStore(store).write_clause_kg(rec)
            n_written += 1
        except Exception:  # noqa: BLE001
            pass
    log(f"[ingest] DONE  spans_indexed={spans_written}  clauses_written={n_written}/{len(clause_jobs)}")
    got = store._query("SELECT count(*) AS n FROM Clause WHERE clause_id LIKE 'acord-%'")[0]["n"]
    gotsp = store._query("SELECT count(*) AS n FROM Span WHERE parent_chunk_id LIKE 'acord-%'")[0]["n"]
    log(f"[ingest] VERIFY in-KG: Clause(acord-)={got}  Span(acord-)={gotsp}")
    store.close()


def cmd_qrels() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    from eval.acord import load_test_queries
    from rag_wright.store.arcadedb import ArcadeDBStore

    overlap = json.load((OUT / "acord_to_pcid.json").open())
    store = ArcadeDBStore.from_env(database=DB, reset=False)
    ingested: dict[str, str] = {}
    for r in store._query("SELECT DISTINCT(parent_chunk_id) AS p FROM Span WHERE parent_chunk_id LIKE 'acord-%'"):
        p = r["p"]
        ingested[p[len("acord-"):].split(":")[0]] = p
    full = {**overlap, **ingested}
    log(f"[qrels] full acord_id -> production pcid: {len(full)}")

    queries = load_test_queries()
    prod: dict[str, dict] = {}
    cov_rel, tot_rel = 0, 0
    for q in queries:
        rel_prod: set[str] = set()
        graded_prod: dict[str, int] = {}
        for cid in q.relevant:
            tot_rel += 1
            p = full.get(cid)
            if p:
                rel_prod.add(p)
                cov_rel += 1
        for cid, g in q.graded.items():
            p = full.get(cid)
            if p:
                graded_prod[p] = max(graded_prod.get(p, 0), g)
        prod[q.query_id] = {"relevant": sorted(rel_prod), "graded": graded_prod, "text": q.text}
    json.dump(prod, (OUT / "acord_prod_qrels.json").open("w"))
    log(f"[qrels] {len(queries)} queries | relevant coverage {cov_rel}/{tot_rel} = "
        f"{100 * cov_rel / max(1, tot_rel):.1f}% -> {OUT / 'acord_prod_qrels.json'}")
    store.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = {"map": cmd_map, "ingest": cmd_ingest, "qrels": cmd_qrels}.get(cmd)
    if fn is None:
        log("usage: acord_unify.py map|ingest|qrels")
    else:
        fn()
