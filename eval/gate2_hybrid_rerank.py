"""GATE-2: the full hybrid + rerank recall/precision bar, on the real ArcadeDB store (branch point).

Unlike GATE-1 (a dense-only proxy), this runs the real read side end to end: ingest a stratified,
representative document sample (short AND long docs, all CUAD archetypes) into ArcadeDB via
parse -> chunk -> embed (dense over summary + native sparse over full text) -> write, then answer every
golden question across the WHOLE ingested corpus (distractors included) through server-side RRF hybrid
search (T21) and the BGE cross-encoder rerank (T22). It reports, per archetype:

- (a) store recall bar: hybrid recall@k on ArcadeDB (decides ArcadeDB vs the LanceDB fallback);
- RAC-22 b2: rerank precision@k vs the raw fused list;
- (b) RLM vs baseline chunker A/B on the text leg (an informative signal; the FINAL RLM
  earns-its-cost call is GATE-2b, after the graph layer, where the KG synergy is measurable, ADR-0009).

Scope (decision 2026-07-11): the relational archetype is a graph-leg archetype and is deferred to
GATE-2b with the KG; measuring its text leg alone here would be noise, not rigor. The three CUAD
archetypes (exact_lexical, semantic, clause_finding) are measured in full on the representative sample.

Run: `uv run python -m eval.gate2_hybrid_rerank [--docs N]`
(heavy and opt-in: Docling parse + DeepSeek summaries + BGE-M3 + BGE-reranker + live ArcadeDB; needs
.env and the corpus. Parses/chunks/embeddings are cached under data/gate2_cache so re-runs are cheap.)
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

for _line in Path(".env").read_text().splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

from FlagEmbedding import BGEM3FlagModel  # noqa: E402
from rag_wright.capabilities.parsing import DoclingParser, load_document, parse  # noqa: E402
from rag_wright.capabilities.reranking import BGEReranker, Passage, rerank  # noqa: E402
from rag_wright.capabilities.rlm_chunking import Chunk, SeamSummarizer, chunk  # noqa: E402
from rag_wright.contracts.chunk import ChunkRecord  # noqa: E402
from rag_wright.contracts.identifiers import ChunkId  # noqa: E402
from rag_wright.store.arcadedb import ArcadeDBStore  # noqa: E402

PDF_DIR = Path("data/cuad/subset/pdf")
GOLDEN = Path("data/eval/golden.json")
CACHE = Path("data/gate2_cache")
CUAD_ARCHETYPES = ("exact_lexical", "semantic", "clause_finding")

BASELINE_WINDOW_CHARS = 1600  # ~400 tokens (the GATE-1 baseline, unchanged)
BASELINE_OVERLAP_CHARS = 200
MIN_Q = 8  # a doc needs at least this many golden questions to enter the sample
RETRIEVE_K = 20  # candidates the hybrid leg fetches per query (the rerank pool)
RECALL_KS = (1, 3, 5, 10)
PRECISION_KS = (1, 3, 5)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _baseline_chunks(full_text: str) -> list[str]:
    step = BASELINE_WINDOW_CHARS - BASELINE_OVERLAP_CHARS
    return [full_text[i : i + BASELINE_WINDOW_CHARS] for i in range(0, len(full_text), step)] or [""]


def _stratified_docs(by_doc: dict[str, list[dict]], n_docs: int) -> list[str]:
    """A representative sample spanning the length range: split eligible docs into size tertiles and
    take an even spread from each, so short AND long docs are covered (not a shortest-first shortcut)."""
    eligible = [
        d for d in by_doc if len(by_doc[d]) >= MIN_Q and (PDF_DIR / f"{d}.pdf").exists()
    ]
    eligible.sort(key=lambda d: (PDF_DIR / f"{d}.pdf").stat().st_size)
    if len(eligible) <= n_docs:
        return eligible
    tertiles = np.array_split(np.array(eligible, dtype=object), 3)
    per = max(1, n_docs // 3)
    picked: list[str] = []
    for band in tertiles:  # even spread within each size band (deterministic)
        if len(band) == 0:
            continue
        idx = np.linspace(0, len(band) - 1, min(per, len(band))).round().astype(int)
        picked.extend(band[i] for i in sorted(set(idx)))
    return picked[:n_docs]


class _Embedder:
    """BGE-M3 dense + native sparse, batched. Query vectors are cached by text across the run."""

    def __init__(self) -> None:
        self._model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False)

    def encode(self, texts: list[str]) -> tuple[list[list[float]], list[dict[int, float]]]:
        out = self._model.encode(texts, return_dense=True, return_sparse=True)
        dense = [v.tolist() for v in out["dense_vecs"]]
        sparse = [{int(k): float(w) for k, w in lw.items()} for lw in out["lexical_weights"]]
        return dense, sparse


class _RobustSummarizer:
    """Wraps the real summarizer so an occasional structured-output miss (SeamSummarizer returns None
    when the model emits no valid tool call) does not abort the whole eval run. On a miss it retries
    once, then falls back to a text-derived proxy summary. Applied to BOTH chunkers so the A/B stays
    fair; the fallback count is reported for honesty."""

    def __init__(self, inner: SeamSummarizer) -> None:
        self._inner = inner
        self.fallbacks = 0

    def summarize(self, text: str) -> str:
        if not text.strip():
            return "(empty)"
        for _ in range(2):
            try:
                summary = self._inner.summarize(text)
                if summary and summary.strip():
                    return summary
            except Exception:  # noqa: BLE001 — structured-output miss; retry then fall back
                pass
        self.fallbacks += 1
        return " ".join(text.split()[:60])  # proxy: the chunk's leading words


def _chunk_id(value: str) -> ChunkId:
    """Reconstruct a ChunkId from its canonical `<source_doc_id>:<index>:<hash>` string (value is a
    computed property, not a field, so it is parsed back with rsplit per the contract docstring)."""
    source_doc_id, index, content_hash = value.rsplit(":", 2)
    return ChunkId(source_doc_id=source_doc_id, chunk_index=int(index), content_hash=content_hash)


def _ingest(chunks: list[Chunk], store: ArcadeDBStore, embedder: _Embedder) -> None:
    """Embed (dense over summary, sparse over full text) and write each chunk record to the store."""
    dense, _ = embedder.encode([c.summary for c in chunks])
    _, sparse = embedder.encode([c.text for c in chunks])
    for c, d, s in zip(chunks, dense, sparse):
        store.upsert_chunk(
            ChunkRecord(
                chunk_id=_chunk_id(c.chunk_id), summary=c.summary, dense_vector=d, sparse_vector=s
            )
        )


def _baseline_manifest_chunks(parsed, full_text: str, summarizer: SeamSummarizer) -> list[Chunk]:
    """Fixed-window baseline chunks, given summaries (so both chunkers go through the identical
    dense-over-summary + sparse-over-full-text hybrid pipeline — a fair A/B)."""
    cache = CACHE / "baseline_chunks" / f"{parsed.source_doc_id}.{parsed.content_hash[:16]}.json"
    if cache.exists():
        return [Chunk.model_validate(c) for c in json.loads(cache.read_text())]
    chunks: list[Chunk] = []
    for i, text in enumerate(_baseline_chunks(full_text)):
        summary = summarizer.summarize(text)
        chunks.append(
            Chunk(
                chunk_id=ChunkId.of(parsed.source_doc_id, i, text).value,
                chunk_index=i,
                text=text,
                summary=summary,
                token_estimate=len(text) // 4,
            )
        )
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps([c.model_dump() for c in chunks], ensure_ascii=False))
    return chunks


def _relevant(question: dict, doc_chunks: dict[str, str]) -> set[str]:
    """The chunk_ids (within the question's own doc) whose text contains a golden answer span."""
    spans = [_norm(s) for s in question["answer_spans"] if s.strip()]
    return {
        cid for cid, text in doc_chunks.items() if any(ns in _norm(text) for ns in spans)
    } if spans else set()


def _evaluate(chunker: str, store: ArcadeDBStore, embedder: _Embedder, reranker: BGEReranker,
              questions: list[dict], chunk_meta: dict[str, tuple[str, str]],
              *, per_doc: bool, sdid_by_golden: dict[str, str]) -> dict:
    """Run every question through hybrid search + rerank; aggregate recall@k / precision@k per archetype.

    per_doc=True filters retrieval to the question's own document (the T21 source_doc_id filter), i.e. a
    within-document haystack — an honest rerank + chunker signal, NOT the cross-corpus store bar."""
    q_texts = [q["question"] for q in questions]
    dense_q, sparse_q = embedder.encode(q_texts)

    # per archetype: recall hits (fused), precision sums (fused vs reranked), counts
    recall = {a: {k: 0 for k in RECALL_KS} for a in CUAD_ARCHETYPES}
    prec_fused = {a: {k: 0.0 for k in PRECISION_KS} for a in CUAD_ARCHETYPES}
    prec_rerank = {a: {k: 0.0 for k in PRECISION_KS} for a in CUAD_ARCHETYPES}
    counts = {a: 0 for a in CUAD_ARCHETYPES}

    for q, dq, sq in zip(questions, dense_q, sparse_q):
        arch = q["archetype"]
        doc = q["source_doc_id"]
        doc_chunks = {cid: text for cid, (d, text) in chunk_meta.items() if d == doc}
        relevant = _relevant(q, doc_chunks)
        if not relevant:
            continue  # no answer-bearing chunk under this chunker -> not scoreable for this question
        counts[arch] += 1

        filters = {"source_doc_id": sdid_by_golden[doc]} if per_doc else None
        fused = store.hybrid_search(dq, sq, k=RETRIEVE_K, filters=filters)
        fused_ids = [r["chunk_id"] for r in fused]

        passages = [Passage(chunk_id=cid, source_doc_id=chunk_meta[cid][0], text=chunk_meta[cid][1])
                    for cid in fused_ids if cid in chunk_meta]
        reranked = rerank(q["question"], passages, reranker=reranker, top_k=RETRIEVE_K).candidates
        rerank_ids = [c.chunk_id for c in reranked]

        for k in RECALL_KS:
            if relevant & set(fused_ids[:k]):
                recall[arch][k] += 1
        for k in PRECISION_KS:
            prec_fused[arch][k] += len(relevant & set(fused_ids[:k])) / k
            prec_rerank[arch][k] += len(relevant & set(rerank_ids[:k])) / k

    def _means(sums):
        return {a: {k: (sums[a][k] / counts[a] if counts[a] else 0.0) for k in sums[a]}
                for a in CUAD_ARCHETYPES}

    return {"chunker": chunker, "counts": counts, "recall": _means(recall),
            "precision_fused": _means(prec_fused), "precision_rerank": _means(prec_rerank)}


def _run_chunker(name: str, docs: list[str], by_doc, embedder, reranker, summarizer,
                 *, per_doc: bool) -> dict:
    db = f"ragwright_gate2_{name}"
    store = ArcadeDBStore.from_env(database=db, reset=True)
    store.ensure_schema()
    chunk_meta: dict[str, tuple[str, str]] = {}  # chunk_id -> (golden_doc_id, chunk_text)
    sdid_by_golden: dict[str, str] = {}  # golden_doc_id -> stored (sanitized) source_doc_id
    questions: list[dict] = []
    for d in docs:
        parsed = parse(PDF_DIR / f"{d}.pdf", cache_dir=CACHE / "parsed", parser=DoclingParser())
        sdid_by_golden[d] = parsed.source_doc_id
        if name == "rlm":
            chunks = chunk(parsed, summarizer=summarizer, cache_dir=CACHE / "chunks").chunks
        else:
            full_text = load_document(parsed).export_to_markdown()
            chunks = _baseline_manifest_chunks(parsed, full_text, summarizer)
        _ingest(chunks, store, embedder)
        for c in chunks:
            chunk_meta[c.chunk_id] = (d, c.text)
        questions.extend(q for q in by_doc[d] if q["archetype"] in CUAD_ARCHETYPES)
        print(f"  [{name}] {d[:44]:44} chunks={len(chunks):3d} total_q={len(questions)}", flush=True)
    result = _evaluate(name, store, embedder, reranker, questions, chunk_meta,
                       per_doc=per_doc, sdid_by_golden=sdid_by_golden)
    store.drop()
    store.close()
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=21, help="stratified sample size (short+long spread)")
    ap.add_argument("--per-doc", action="store_true",
                    help="retrieve within each answer's document (within-doc rerank + chunker signal)")
    args = ap.parse_args()

    golden = json.loads(GOLDEN.read_text())["questions"]
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for q in golden:
        by_doc[q["source_doc_id"]].append(q)
    docs = _stratified_docs(by_doc, args.docs)

    sizes = [(PDF_DIR / f"{d}.pdf").stat().st_size // 1024 for d in docs]
    arch_spread = defaultdict(int)
    for d in docs:
        for q in by_doc[d]:
            if q["archetype"] in CUAD_ARCHETYPES:
                arch_spread[q["archetype"]] += 1
    print(f"sample: {len(docs)} docs, sizes {min(sizes)}-{max(sizes)}KB (median {sorted(sizes)[len(sizes)//2]}KB)")
    print(f"archetype question spread: {dict(arch_spread)}")

    CACHE.mkdir(parents=True, exist_ok=True)
    embedder = _Embedder()
    reranker = BGEReranker()
    summarizer = _RobustSummarizer(SeamSummarizer())

    results = [_run_chunker(name, docs, by_doc, embedder, reranker, summarizer, per_doc=args.per_doc)
               for name in ("rlm", "baseline")]
    print(f"\nsummarizer text-fallbacks (structured-output misses): {summarizer.fallbacks}")

    mode = "PER-DOC (within-document; rerank + chunker signal only)" if args.per_doc else "cross-corpus"
    print(f"\n=== GATE-2 result [{mode}] (text leg; mean over the sample) ===")
    for r in results:
        print(f"\n[{r['chunker']}]  scoreable questions: {r['counts']}")
        for a in CUAD_ARCHETYPES:
            rc = r["recall"][a]
            pf, pr = r["precision_fused"][a], r["precision_rerank"][a]
            print(f"  {a:14} recall@{{1,3,5,10}}="
                  f"{rc[1]:.3f}/{rc[3]:.3f}/{rc[5]:.3f}/{rc[10]:.3f}  "
                  f"prec@5 fused={pf[5]:.3f} rerank={pr[5]:.3f}")

    (CACHE / "report.json").write_text(json.dumps(results, indent=2))
    print(f"\nreport written to {CACHE / 'report.json'}")


if __name__ == "__main__":
    main()
