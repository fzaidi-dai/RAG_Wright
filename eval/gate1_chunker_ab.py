"""GATE-1: A/B the RLM chunker (T17) against a simple fixed-window baseline (branch point).

Runs both chunkers over a sample of golden-set documents and reports, per chunker:
- boundary quality: the fraction of golden answer spans that land inside a single chunk (a chunker
  that splits a clause across a boundary loses that answer);
- retrieval recall@k: embed each chunk (RLM over its summary, baseline over its text) with BGE-M3,
  rank chunks by cosine similarity to the question, and check whether a top-k chunk contains the
  answer span.

This measures with T17 + T19 + T9 only (no store / no sparse leg), so it is a dense-only proxy for
the read side; the full hybrid recall bar is GATE-2. Result is printed and recorded; the go/no-go
(keep the RLM chunker + build T18, or drop to the simpler chunker) is a human decision.

Run: `uv run python -m eval.gate1_chunker_ab`
(heavy and opt-in: Docling parse + DeepSeek Flash summaries + BGE-M3; needs .env + the corpus).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import numpy as np

for _line in Path(".env").read_text().splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

from FlagEmbedding import BGEM3FlagModel  # noqa: E402
from rag_wright.capabilities.parsing import DoclingParser, load_document, parse  # noqa: E402
from rag_wright.capabilities.rlm_chunking import SeamSummarizer, chunk  # noqa: E402

PDF_DIR = Path("data/cuad/subset/pdf")
GOLDEN = Path("data/eval/golden.json")
N_DOCS = 4
BASELINE_WINDOW_CHARS = 1600  # ~400 tokens
BASELINE_OVERLAP_CHARS = 200  # ~50 tokens


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _baseline_chunks(full_text: str) -> list[str]:
    """A simple fixed-window chunker with overlap (the baseline to beat)."""
    step = BASELINE_WINDOW_CHARS - BASELINE_OVERLAP_CHARS
    return [full_text[i : i + BASELINE_WINDOW_CHARS] for i in range(0, len(full_text), step)] or [""]


def _recall(question_vecs, chunk_vecs, chunk_texts, spans_per_q, ks=(1, 3, 5)) -> dict[int, float]:
    """recall@k: is a top-k chunk (by cosine to the question) containing the answer span?"""
    chunk_norm = [_norm(t) for t in chunk_texts]
    sims = question_vecs @ chunk_vecs.T  # cosine (vectors are L2-normalized)
    hits = {k: 0 for k in ks}
    total = 0
    for qi, spans in enumerate(spans_per_q):
        norm_spans = [_norm(s) for s in spans if s.strip()]
        if not norm_spans:
            continue
        total += 1
        ranked = np.argsort(-sims[qi])
        for k in ks:
            topk = ranked[:k]
            if any(any(ns in chunk_norm[ci] for ns in norm_spans) for ci in topk):
                hits[k] += 1
    return {k: (hits[k] / total if total else 0.0) for k in ks}


def _boundary_quality(chunk_texts, spans_per_q) -> float:
    """Fraction of answer spans contained whole within some single chunk."""
    chunk_norm = [_norm(t) for t in chunk_texts]
    contained = total = 0
    for spans in spans_per_q:
        for span in spans:
            ns = _norm(span)
            if not ns:
                continue
            total += 1
            if any(ns in cn for cn in chunk_norm):
                contained += 1
    return contained / total if total else 0.0


def main() -> None:
    golden = json.loads(GOLDEN.read_text())["questions"]
    by_doc: dict[str, list[dict]] = {}
    for q in golden:
        by_doc.setdefault(q["source_doc_id"], []).append(q)
    # well-covered but bounded-length docs: >= MIN_Q questions and a PDF, shortest first (fewer
    # chunks -> fewer sequential summary calls), so the A/B runs in a reasonable time.
    MIN_Q = 12
    candidates = [
        d for d in by_doc
        if len(by_doc[d]) >= MIN_Q and (PDF_DIR / f"{d}.pdf").exists()
    ]
    candidates.sort(key=lambda d: (PDF_DIR / f"{d}.pdf").stat().st_size)
    docs = candidates[:N_DOCS]
    print(f"sample docs ({len(docs)}):")
    for d in docs:
        print(f"  {d[:60]}  ({len(by_doc[d])} questions)")

    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False)
    summarizer = SeamSummarizer()

    def embed(texts: list[str]) -> np.ndarray:
        vecs = np.asarray(  # batch encode (one forward pass over all texts) — far faster than a loop
            model.encode(texts, return_dense=True, return_sparse=False)["dense_vecs"], dtype=np.float32
        )
        return vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)

    agg = {"rlm": {"bq": [], "r": []}, "baseline": {"bq": [], "r": []}}
    tmp = Path("data/gate1_cache")  # persistent + gitignored: parses/chunks cached across runs
    tmp.mkdir(parents=True, exist_ok=True)
    for d in docs:
        pdf = PDF_DIR / f"{d}.pdf"
        parsed = parse(pdf, cache_dir=tmp / "parsed", parser=DoclingParser())
        document = load_document(parsed)
        full_text = document.export_to_markdown()
        questions = by_doc[d]
        q_texts = [q["question"] for q in questions]
        spans = [q["answer_spans"] for q in questions]
        q_vecs = embed(q_texts)

        # RLM: dense over the summary; boundary/containment over the chunk text
        manifest = chunk(parsed, summarizer=summarizer, cache_dir=tmp / "chunks")
        rlm_texts = [c.text for c in manifest.chunks]
        rlm_vecs = embed([c.summary for c in manifest.chunks])
        agg["rlm"]["bq"].append(_boundary_quality(rlm_texts, spans))
        agg["rlm"]["r"].append(_recall(q_vecs, rlm_vecs, rlm_texts, spans))

        # baseline: dense over the chunk text
        base_texts = _baseline_chunks(full_text)
        base_vecs = embed(base_texts)
        agg["baseline"]["bq"].append(_boundary_quality(base_texts, spans))
        agg["baseline"]["r"].append(_recall(q_vecs, base_vecs, base_texts, spans))
        print(f"  done {d[:40]}: rlm_chunks={len(rlm_texts)} base_chunks={len(base_texts)}", flush=True)

    print("\n=== GATE-1 result (mean over docs) ===")
    for name in ("rlm", "baseline"):
        bq = float(np.mean(agg[name]["bq"]))
        r = {k: float(np.mean([d[k] for d in agg[name]["r"]])) for k in (1, 3, 5)}
        print(f"  {name:8} boundary_quality={bq:.3f}  recall@1={r[1]:.3f} @3={r[3]:.3f} @5={r[5]:.3f}")


if __name__ == "__main__":
    main()
