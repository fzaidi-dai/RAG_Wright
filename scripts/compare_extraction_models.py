"""T58b bench: compare PROPERTY-extraction quality across models on hard, property-rich clauses.

Samples gold clauses from the cuad2 property-bearing queries (chosen to span the hard dimensions --
multi-value carve-outs, mutuality, favorability, party-asymmetry, cap basis, indemnity scope/subject/
procedural, warranty scope), classifies each clause's function (LegalBERT, as the real pipeline does),
then extracts properties with each model (DeepSeek V4 Pro / Flash / Gemma 4) via the same extractor +
contract, concurrently. Emits a side-by-side + coverage / vocab-adherence / agreement / latency so we can
judge quality by internal analysis. No ground-truth labels -> Pro is the reference AND we read the text.

  uv run python -m scripts.compare_extraction_models        # ~45 calls, provider-routed
"""

from __future__ import annotations

import time
from pathlib import Path

import torch
from dotenv import load_dotenv

from eval.acord import load_corpus, load_test_queries
from rag_wright.packs.contracts.spans.legalbert_classifier import LegalBertFunctionClassifier
from rag_wright.packs.contracts.spans.property_extractor import SeamPropertyExtractor
from rag_wright.util.concurrent import map_concurrent

MODEL_PATH = Path("data/models/legalbert_function")
OUT = Path("data/models/extraction_compare.md")
MODELS = {
    "pro": "deepseek/deepseek-v4-pro",
    "flash": "deepseek/deepseek-v4-flash",
    "gemma": "google/gemma-4-31b-it",
}
# Curated property-bearing queries spanning the hard dimensions; take the richest (longest-text) gold
# clauses, dedup, cap at N_SAMPLE. Substrings match the ACORD query text.
CURATED = [
    "indemnification carveout to cap on liability",
    "liability cap clauses that exclude third party IP infringement",
    "two parties having different liability caps",
    "mutual liability cap",
    "seller-favorable cap on liability",
    "Cap on liability equals 12 months payment",
    "warranty disclaimer clause that includes implied warranties",
    "mutual indemnification provisions",
    "IP infringement indemnity that covers trademark or copyright",
    "indemnification clause that allows indemnifying party to control defenses",
    "consequential damages waiver",
    "fraud, gross negligence or willful misconduct carveout to indirect damage waiver",
]
N_SAMPLE = 15


def _device() -> str:
    return "mps" if torch.backends.mps.is_available() else "cpu"


def _sample_clauses(corpus: dict[str, str]) -> list[str]:
    """Gold clause ids from the curated queries (richest first), deduped, capped at N_SAMPLE."""
    by_text = {q.text: q for q in load_test_queries()}
    picked: list[str] = []
    seen: set[str] = set()
    for sub in CURATED:
        q = next((qq for t, qq in by_text.items() if sub.lower() in t.lower()), None)
        if not q:
            continue
        gold = sorted(q.relevant, key=lambda c: -len(corpus.get(c, "")))  # richest text first
        for cid in gold[:2]:
            if cid not in seen and corpus.get(cid, "").strip():
                seen.add(cid)
                picked.append(cid)
            if len(picked) >= N_SAMPLE:
                return picked
    return picked


def main() -> None:
    load_dotenv()
    corpus = {c.clause_id: c.text for c in load_corpus()}
    clause_ids = _sample_clauses(corpus)
    clf = LegalBertFunctionClassifier.load(MODEL_PATH, device=_device())
    functions = clf.classify([corpus[c] for c in clause_ids])
    print(f"sampled {len(clause_ids)} hard clauses; functions: {sorted(set(functions))}", flush=True)

    extractors = {name: SeamPropertyExtractor(model_id=mid) for name, mid in MODELS.items()}
    jobs = [(name, cid, fn) for (cid, fn) in zip(clause_ids, functions) for name in MODELS]

    def _run(job):
        name, cid, fn = job
        from rag_wright.contracts.identifiers import ChunkId
        t0 = time.perf_counter()
        try:
            rec = extractors[name](chunk_id=ChunkId.of("bench-" + cid.replace(":", "-"), 0, corpus[cid]),
                                   function=fn, text=corpus[cid], span_id="")
            props = sorted((a.dimension.value, a.value, a.confidence.value) for a in rec.assertions)
            return (name, cid, props, time.perf_counter() - t0, None)
        except Exception as e:  # noqa: BLE001 - report a model's failure as data
            return (name, cid, [], time.perf_counter() - t0, f"{type(e).__name__}: {str(e)[:80]}")

    results = map_concurrent(jobs, _run, max_concurrency=6, label="[extract-bench]", echo=True, every=3)

    by_clause: dict[str, dict[str, tuple]] = {c: {} for c in clause_ids}
    lat: dict[str, list[float]] = {n: [] for n in MODELS}
    for name, cid, props, dt, err in results:
        by_clause[cid][name] = (props, err)
        lat[name].append(dt)

    lines = ["# Property-extraction model comparison (T58b)\n"]
    for cid, fn in zip(clause_ids, functions):
        lines.append(f"\n## `{cid}`  function={fn}\n")
        lines.append("```\n" + corpus[cid][:600].replace("\n", " ") + "\n```\n")
        for name in MODELS:
            props, err = by_clause[cid].get(name, ([], "no-result"))
            tag = f"  ERROR {err}" if err else ""
            lines.append(f"- **{name}** ({len(props)} props){tag}")
            for d, v, c in props:
                lines.append(f"    - {d} = {v} [{c}]")
    # summary metrics
    lines.append("\n## Summary\n")
    ref = {c: {p[:2] for p in by_clause[c].get("pro", ([], None))[0]} for c in clause_ids}  # (dim,val) from Pro
    for name in MODELS:
        n_props = [len(by_clause[c].get(name, ([], None))[0]) for c in clause_ids]
        allp = [p for c in clause_ids for p in by_clause[c].get(name, ([], None))[0]]
        amb = sum(1 for p in allp if p[2] == "AMBIGUOUS")
        errs = sum(1 for c in clause_ids if by_clause[c].get(name, ([], None))[1])
        # agreement with Pro: fraction of Pro's (dim,val) this model also produced (recall vs Pro)
        inter = tot = 0
        for c in clause_ids:
            mine = {p[:2] for p in by_clause[c].get(name, ([], None))[0]}
            inter += len(ref[c] & mine)
            tot += len(ref[c])
        agree = inter / tot if tot else 0.0
        avg_lat = sum(lat[name]) / len(lat[name]) if lat[name] else 0.0
        lines.append(f"- **{name}**: avg props/clause={sum(n_props)/len(n_props):.1f}  total={sum(n_props)}  "
                     f"AMBIGUOUS={amb}  errors={errs}  agree-with-Pro(dim,val)={agree:.2f}  avg_latency={avg_lat:.1f}s")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[-6:]), flush=True)
    print(f"\nfull side-by-side written to {OUT}", flush=True)


if __name__ == "__main__":
    main()
