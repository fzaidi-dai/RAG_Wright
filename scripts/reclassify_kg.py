"""INGEST-LLM-CLASSIFIER (ADR-0048) Phase A: non-destructive reclassify pass over the EXISTING KG.

Reads each chunk's spans from the KG (no re-parse/chunk/segment/embed), batched-classifies them with the
graph-building LLM (one call per chunk, chunk as context), maps the result to the EXISTING Clause nodes, and
reports the reclassification DELTA (how many primaries flip -> the Phase B re-extraction candidates). DRY-RUN by
default (no writes); WRITE=1 upserts the new primary + `functions` JSON (mark-stale + Phase B are the next step).

  LIMIT=50 uv run --no-sync python -m scripts.reclassify_kg          # sample 50 chunks, dry-run
  uv run --no-sync python -m scripts.reclassify_kg                   # all chunks, dry-run
  WRITE=1 uv run --no-sync python -m scripts.reclassify_kg           # upsert the labels

Env: RAG_MODEL_GENERAL (classifier model; default = the GENERAL role), CONC (concurrency, default 8),
LIMIT (chunks to sample; 0 = all), WRITE (0 dry-run / 1 upsert), QA_DB.
"""
from __future__ import annotations

import json
import os

from dotenv import load_dotenv


def log(m: str) -> None:
    print(m, flush=True)


def main() -> None:
    os.environ.setdefault("RAG_SERVING", "openrouter")
    load_dotenv()
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.spans.clause_function_classifier import production_batch_clause_classifier
    from rag_wright.spans.reclassify import ReclassDelta, reclassify_chunk
    from rag_wright.store.arcadedb import ArcadeDBStore, _str_array
    from rag_wright.util.concurrent import map_concurrent

    db = os.environ.get("QA_DB", "ragwright_cuad_full")
    conc = int(os.environ.get("CONC", "8"))
    limit = int(os.environ.get("LIMIT", "0"))
    write = os.environ.get("WRITE", "0") == "1"
    store = ArcadeDBStore.from_env(database=db)

    # 1. sampled chunks (distinct parent_chunk_id), SCOPED -- never pull the whole KG for a LIMIT run
    chunk_rows = store._query(
        f"SELECT parent_chunk_id AS p FROM (SELECT DISTINCT(parent_chunk_id) FROM Span){f' LIMIT {limit}' if limit else ''}")
    chunk_ids = [r["p"] for r in chunk_rows if r.get("p")]
    log(f"[reclassify] chunks to process: {len(chunk_ids)}{' (LIMIT sample)' if limit else ''}")

    # 2. spans for those chunks only (batched by chunk-id IN), with read progress
    spans_by_chunk: dict[str, list[dict]] = {}
    for i in range(0, len(chunk_ids), 200):
        idlist = _str_array(chunk_ids[i:i + 200])
        for r in store._query(
                f"SELECT parent_chunk_id, span_id, span_index, text FROM Span WHERE parent_chunk_id IN {idlist}"):
            spans_by_chunk.setdefault(r["parent_chunk_id"], []).append(r)
        log(f"[reclassify] read spans {min(i + 200, len(chunk_ids))}/{len(chunk_ids)} chunks")
    for spans in spans_by_chunk.values():
        spans.sort(key=lambda s: s.get("span_index") or 0)

    # 3. existing Clause labels: span_id -> (clause_id, old_function)
    existing: dict[str, tuple[str, str]] = {}
    if limit:  # scoped sample: only the sampled chunks' spans (span_id IN, batched)
        all_span_ids = [s["span_id"] for spans in spans_by_chunk.values() for s in spans]
        for i in range(0, len(all_span_ids), 200):
            idlist = _str_array(all_span_ids[i:i + 200])
            for r in store._query(f"SELECT clause_id, span_id, function FROM Clause WHERE span_id IN {idlist}"):
                existing[r["span_id"]] = (r["clause_id"], r.get("function") or "")
    else:  # full run: page ALL clauses directly (a few queries, not ~700 span-id-IN queries)
        skip = 0
        while True:
            rows = store._query(f"SELECT clause_id, span_id, function FROM Clause WHERE span_id <> '' SKIP {skip} LIMIT 20000")
            if not rows:
                break
            for r in rows:
                existing[r["span_id"]] = (r["clause_id"], r.get("function") or "")
            skip += len(rows)
            log(f"[reclassify] read clauses {skip} ...")
            if len(rows) < 20000:
                break
    log(f"[reclassify] existing clauses: {len(existing)}")

    classifier = production_batch_clause_classifier(model_for(ModelRole.GENERAL))

    if os.environ.get("DEBUG") == "1":
        from collections import Counter

        from rag_wright.spans.clause_function_classifier import categorize_raw

        tmo = float(os.environ.get("TIMEOUT_S", "90"))
        log(f"[reclassify][debug] categorizing verdicts over {len(chunk_ids)} chunks (conc={conc}) ...")

        def _dbg(cid):
            spans = spans_by_chunk.get(cid, [])
            if not spans:
                return []
            ct = "".join(s.get("text") or "" for s in spans)
            ordered = [(s["span_id"], s.get("text") or "") for s in spans]
            raw_per = classifier.classify_spans_raw(ct, [t for _, t in ordered])
            rows = []
            for (sid, text), raws in zip(ordered, raw_per):
                if sid not in existing:
                    continue
                in_tax, others = categorize_raw(raws)
                rows.append((existing[sid][1], in_tax, others, text, raws))
            return rows

        dbg = map_concurrent(chunk_ids, _dbg, max_concurrency=conc, label="[reclassify][debug]", echo=True,
                             timeout_s=tmo, timeout_retries=1)
        cat: Counter = Counter()
        gaps: Counter = Counter()
        examples = []
        for rows in dbg:
            for old, in_tax, others, text, raws in (rows or []):
                if in_tax:
                    new = in_tax[0].function
                    kind = "in-taxonomy (unchanged)" if new == old else "in-taxonomy (reclassified)"
                elif others:
                    new = f"OTHER:{others[0]}"
                    kind = "OTHER (taxonomy gap)"
                    gaps[others[0]] += 1
                else:
                    new = "NONE"
                    kind = "NONE (no function)"
                cat[kind] += 1
                if new != old and len(examples) < 25:
                    raw_str = ", ".join(
                        f"{r.function}{'/' + r.other_label if r.other_label else ''}={r.confidence}"
                        for r in raws) or "(none)"
                    examples.append((old, new, text, raw_str))

        total = sum(cat.values())
        log("\n=== DEBUG: reclassification 3-way (option 2: in-taxonomy / OTHER / NONE) ===")
        log(f"clauses categorized: {total}  (chunks skipped/timeout: {sum(1 for r in dbg if r is None)})")
        for k, c in cat.most_common():
            log(f"  {c:5d} ({100 * c / max(1, total):.0f}%)  {k}")
        log(f"\ntop OUT-OF-TAXONOMY clause types the LLM found ({len(gaps)} distinct; taxonomy gaps):")
        for label, c in gaps.most_common(40):
            log(f"  {c:4d}  {label}")
        log("\nexample flips (old -> new | RAW function[/other]=confidence):")
        for old, new, text, raw_str in examples:
            log(f"  [{old} -> {new}]  {text[:75].strip()!r}\n      RAW: {raw_str}")
        # save the FULL gap report for the taxonomy-extension step (2)
        from pathlib import Path

        out = Path("data/eval/taxonomy_gaps")
        out.mkdir(parents=True, exist_ok=True)
        report = {"clauses_categorized": total, "three_way": dict(cat.most_common()),
                  "out_of_taxonomy": dict(gaps.most_common())}
        (out / "gap_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"\n[reclassify][debug] saved full gap report ({len(gaps)} distinct types) -> {out / 'gap_report.json'}")
        store.close()
        return

    n = len(chunk_ids)
    timeout_s = float(os.environ.get("TIMEOUT_S", "90"))  # a big multi-span chunk's structured call can stall
    log(f"[reclassify] classifying {n} chunks (batched, conc={conc}, timeout={timeout_s:.0f}s) | WRITE={write} ...")

    def _one(chunk_id: str):
        spans = spans_by_chunk.get(chunk_id, [])
        if not spans:
            return []
        chunk_text = "".join(s.get("text") or "" for s in spans)
        ordered = [(s["span_id"], s.get("text") or "") for s in spans]
        by_span = {sid: existing[sid] for sid, _ in ordered if sid in existing}
        if not by_span:
            return []
        return reclassify_chunk(chunk_text, ordered, by_span, classifier)

    results = map_concurrent(chunk_ids, _one, max_concurrency=conc, label="[reclassify]", echo=True,
                             timeout_s=timeout_s, timeout_retries=1)
    skipped = sum(1 for r in results if r is None)  # None = timed out (a stalled/too-big chunk); [] = no clauses

    delta = ReclassDelta()
    for rcs in results:
        for rc in (rcs or []):
            delta.add(rc)

    log("\n=== RECLASSIFY DELTA (Phase A) ===")
    log(f"chunks skipped (timeout {timeout_s:.0f}s): {skipped}/{n}  (big multi-span chunks stall the structured call)")
    log(f"clauses reclassified : {delta.total}")
    log(f"unchanged primary    : {delta.unchanged} ({100*delta.unchanged/max(1,delta.total):.1f}%)")
    log(f"PRIMARY FLIPPED      : {delta.flipped} ({100*delta.flipped/max(1,delta.total):.1f}%)  <- Phase B candidates")
    log(f"  ...flipped to NONE : {delta.to_none}  (clause should be retired)")
    log("top transitions (old -> new):")
    for (old, new), c in delta.top_transitions(15):
        log(f"  {c:5d}  {old!r} -> {new!r}")

    if write:
        log(f"\n[reclassify] WRITE: upserting function + functions on {delta.total} clauses ...")
        wrote = 0
        for rcs in results:
            for rc in (rcs or []):
                fjson = json.dumps([{"function": f.function, "confidence": f.confidence.value} for f in rc.new_scores])
                try:
                    store._command(
                        f"UPDATE Clause SET function = {_q(rc.new_primary)}, functions = {_q(fjson)} "
                        f"WHERE clause_id = {_q(rc.clause_id)}")
                    wrote += 1
                except Exception as e:  # noqa: BLE001
                    log(f"  write err {rc.clause_id[:40]}: {str(e)[:60]}")
        log(f"[reclassify] wrote {wrote} clause labels. (mark-stale + Phase B re-extraction = next step.)")
    else:
        log("\n[reclassify] DRY-RUN (no writes). Re-run with WRITE=1 to upsert the labels.")
    store.close()


def _q(s: str) -> str:
    from rag_wright.store.arcadedb import _sql_str

    return _sql_str(s)


if __name__ == "__main__":
    main()
