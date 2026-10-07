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
    from rag_wright.capabilities.contract_kg_store import ContractKGStore
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

        from pathlib import Path

        ckpt_dir = Path("data/eval/taxonomy_gaps")
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        ckpt_file = ckpt_dir / "gap_checkpoint.jsonl"

        def _dbg(cid):  # -> lightweight rows [[old, kind, label, text_snippet, raw_str]] (checkpoint-serializable)
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
                if in_tax:
                    kind, label = "intax", in_tax[0].function
                elif others:
                    kind, label = "other", others[0]
                else:
                    kind, label = "none", ""
                raw_str = ", ".join(
                    f"{r.function}{'/' + r.other_label if r.other_label else ''}={r.confidence}"
                    for r in raws) or "(none)"
                rows.append([existing[sid][1], kind, label, text[:75].strip(), raw_str])
            return rows

        cat: Counter = Counter()
        gaps: Counter = Counter()
        examples: list = []
        skipped = 0

        def _agg(rows):
            for old, kind, label, text, raw_str in rows:
                if kind == "intax":
                    new = label
                    key = "in-taxonomy (unchanged)" if new == old else "in-taxonomy (reclassified)"
                elif kind == "other":
                    new = f"OTHER:{label}"
                    key = "OTHER (taxonomy gap)"
                    gaps[label] += 1
                else:
                    new = "NONE"
                    key = "NONE (no function)"
                cat[key] += 1
                if new != old and len(examples) < 25:
                    examples.append((old, new, text, raw_str))

        done: set = set()
        if ckpt_file.exists():  # RESUME: aggregate already-done chunks + skip them
            for line in ckpt_file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    d = json.loads(line)
                    done.add(d["chunk"])
                    _agg(d["rows"])
            log(f"[reclassify][debug] RESUME: {len(done)} chunks from checkpoint")

        todo = [c for c in chunk_ids if c not in done]
        log(f"[reclassify][debug] {len(todo)} chunks to classify ({len(done)} resumed); checkpointing per batch")
        batch_n = 120
        with ckpt_file.open("a", encoding="utf-8") as ck:
            for bs in range(0, len(todo), batch_n):
                batch = todo[bs:bs + batch_n]
                res = map_concurrent(batch, _dbg, max_concurrency=conc, label="[reclassify][debug]", echo=True,
                                     timeout_s=tmo, timeout_retries=1)
                for cid, rows in zip(batch, res):
                    if rows is None:
                        skipped += 1
                        rows = []
                    ck.write(json.dumps({"chunk": cid, "rows": rows}, ensure_ascii=False) + "\n")
                    _agg(rows)
                ck.flush()
                log(f"[reclassify][debug] CHECKPOINT {len(done) + bs + len(batch)}/{len(chunk_ids)} chunks done")

        total = sum(cat.values())
        log("\n=== DEBUG: reclassification 3-way (option 2: in-taxonomy / OTHER / NONE) ===")
        log(f"clauses categorized: {total}  (chunks skipped/timeout: {skipped})")
        for k, c in cat.most_common():
            log(f"  {c:5d} ({100 * c / max(1, total):.0f}%)  {k}")
        log(f"\ntop OUT-OF-TAXONOMY clause types the LLM found ({len(gaps)} distinct; taxonomy gaps):")
        for label, c in gaps.most_common(40):
            log(f"  {c:4d}  {label}")
        log("\nexample flips (old -> new | RAW function[/other]=confidence):")
        for old, new, text, raw_str in examples:
            log(f"  [{old} -> {new}]  {text[:75].strip()!r}\n      RAW: {raw_str}")
        # save the FULL gap report for the taxonomy-extension step (2)
        report = {"clauses_categorized": total, "three_way": dict(cat.most_common()),
                  "out_of_taxonomy": dict(gaps.most_common())}
        (ckpt_dir / "gap_report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"\n[reclassify][debug] saved full gap report ({len(gaps)} distinct types) -> {ckpt_dir / 'gap_report.json'}")
        store.close()
        return

    n = len(chunk_ids)
    timeout_s = float(os.environ.get("TIMEOUT_S", "90"))  # a big multi-span chunk's structured call can stall

    from pathlib import Path

    from rag_wright.contracts.function import NO_FUNCTION, FunctionConfidence, FunctionScore
    from rag_wright.spans.reclassify import ClauseReclass

    ckpt_dir = Path("data/eval/taxonomy_gaps")
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    ckpt_file = ckpt_dir / (f"reclass_{'write' if write else 'dry'}_checkpoint.jsonl")
    skip_file = ckpt_dir / (f"reclass_{'write' if write else 'dry'}_skipped.txt")  # timed-out chunks (follow-up)

    def _rc_from_json(d: dict) -> ClauseReclass:
        scores = [FunctionScore(function=s["function"], confidence=FunctionConfidence(s["confidence"]))
                  for s in d["scores"]]
        return ClauseReclass(clause_id=d["clause_id"], span_id=d["span_id"], old_function=d["old"],
                             new_scores=scores, new_primary=d["new"])

    delta = ReclassDelta()
    done: set[str] = set()
    if ckpt_file.exists():  # RESUME: re-aggregate checkpointed chunks (their writes are already applied -> skip)
        for line in ckpt_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                done.add(d["chunk"])
                for r in d["reclass"]:
                    delta.add(_rc_from_json(r))
        log(f"[reclassify] RESUME: {len(done)} chunks from checkpoint ({delta.total} clauses re-aggregated)")

    todo = [c for c in chunk_ids if c not in done]
    log(f"[reclassify] {len(todo)} chunks to classify ({len(done)} resumed) of {n} | WRITE={write} conc={conc} "
        f"timeout={timeout_s:.0f}s | checkpoint={ckpt_file.name}")

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

    wrote = 0
    staled = 0
    skipped = 0
    kept_on_none = 0  # never-null-on-NONE (user-approved): a flip-to-NONE is a NO-OP, never overwrites the label
    batch_n = 120
    with ckpt_file.open("a", encoding="utf-8") as ck, skip_file.open("a", encoding="utf-8") as sk:
        for bs in range(0, len(todo), batch_n):
            batch = todo[bs:bs + batch_n]
            res = map_concurrent(batch, _one, max_concurrency=conc, label="[reclassify]", echo=True,
                                 timeout_s=timeout_s, timeout_retries=1)
            flipped_spans: list[str] = []
            ckpt_lines: list[str] = []
            for cid, rcs in zip(batch, res):
                if rcs is None:  # timed out even after retry -> checkpoint as empty (bounded), log id for follow-up
                    skipped += 1
                    sk.write(cid + "\n")
                    ckpt_lines.append(json.dumps({"chunk": cid, "reclass": []}, ensure_ascii=False))
                    continue
                for rc in rcs:
                    # NEVER-NULL-ON-NONE (user-approved): only a REAL new label overwrites. Gemma's per-label
                    # agreement is ~99.5%, but its "no function" (NONE) decision is noisy AND often a granularity
                    # artifact (the original ingestion propagated a section's function onto every header/fragment
                    # span), so a flip-to-NONE NEVER destroys the existing label -- it is a no-op.
                    if write and rc.new_primary != NO_FUNCTION:  # real -> real (or unchanged): safe to upsert
                        fjson = json.dumps(
                            [{"function": f.function, "confidence": f.confidence.value} for f in rc.new_scores])
                        try:
                            store._command(
                                f"UPDATE Clause SET function = {_q(rc.new_primary)}, functions = {_q(fjson)} "
                                f"WHERE clause_id = {_q(rc.clause_id)}")
                            wrote += 1
                        except Exception as e:  # noqa: BLE001
                            log(f"  write err {rc.clause_id[:40]}: {str(e)[:60]}")
                        if rc.primary_flipped:  # a real->real flip: properties were extracted for the OLD function
                            flipped_spans.append(rc.span_id)
                    elif write and rc.primary_flipped:  # flip-to-NONE: keep the old label, DO NOT mark stale
                        kept_on_none += 1
                    delta.add(rc)
                ckpt_lines.append(json.dumps({"chunk": cid, "reclass": [
                    {"clause_id": rc.clause_id, "span_id": rc.span_id, "old": rc.old_function,
                     "new": rc.new_primary,
                     "scores": [{"function": f.function, "confidence": f.confidence.value} for f in rc.new_scores]}
                    for rc in rcs]}, ensure_ascii=False))
            if write and flipped_spans:  # one batched mark-stale call (23 UPDATEs, not per-span)
                staled += ContractKGStore(store).mark_span_properties_ambiguous(flipped_spans)
            for line in ckpt_lines:  # checkpoint the batch ONLY after its writes + mark-stale applied
                ck.write(line + "\n")
            ck.flush()
            sk.flush()
            log(f"[reclassify] CHECKPOINT {len(done) + bs + len(batch)}/{n} chunks | "
                f"reclassified={delta.total} flipped={delta.flipped} wrote={wrote} staled_edges={staled} "
                f"skipped={skipped}")

    real_flips = delta.flipped - delta.to_none  # real -> real: the ONLY writes under never-null-on-NONE
    log("\n=== RECLASSIFY DELTA (Phase A, never-null-on-NONE) ===")
    log(f"chunks skipped (timeout {timeout_s:.0f}s): {skipped}/{n}  (logged to {skip_file.name} for follow-up)")
    log(f"clauses reclassified : {delta.total}")
    log(f"unchanged primary    : {delta.unchanged} ({100*delta.unchanged/max(1,delta.total):.1f}%)")
    log(f"REAL->REAL flip (WRITTEN): {real_flips} ({100*real_flips/max(1,delta.total):.1f}%)  <- the actual label fixes")
    log(f"flip-to-NONE (KEPT, no-op): {delta.to_none} ({100*delta.to_none/max(1,delta.total):.1f}%)  "
        f"<- label preserved, never nulled")
    log("top REAL->REAL transitions (old -> new; NONE flips excluded as no-ops):")
    real_trans = [((o, nw), c) for (o, nw), c in delta.top_transitions(80) if nw != NO_FUNCTION]
    for (old, new), c in real_trans[:20]:
        log(f"  {c:5d}  {old!r} -> {new!r}")
    report = {"write": write, "policy": "never-null-on-NONE", "chunks": n, "skipped": skipped,
              "clauses": delta.total, "unchanged": delta.unchanged, "real_flips_written": real_flips,
              "flip_to_none_kept": delta.to_none, "wrote": wrote, "staled_edges": staled,
              "kept_on_none": kept_on_none,
              "top_real_transitions": [{"old": o, "new": nw, "count": c} for (o, nw), c in real_trans]}
    (ckpt_dir / f"reclass_{'write' if write else 'dry'}_delta_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if write:
        log(f"\n[reclassify] WROTE {wrote} real labels + staled {staled} property edges on real->real flips; "
            f"KEPT {kept_on_none} flip-to-NONE labels untouched (non-destructive). "
            f"(Phase B re-extraction of the {real_flips} real flips = next step.)")
    else:
        log(f"\n[reclassify] DRY-RUN (no writes). {real_flips} real->real fixes would be written, "
            f"{delta.to_none} flip-to-NONE kept as no-ops. Re-run with WRITE=1.")
    store.close()


def _q(s: str) -> str:
    from rag_wright.store.arcadedb import _sql_str

    return _sql_str(s)


if __name__ == "__main__":
    main()
