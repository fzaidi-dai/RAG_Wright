# ADR-0011: CUAD is an extraction benchmark, not a retrieval one — ACORD supplies retrieval queries; LLM-generated queries are rejected as circular

Date: 2026-07-12. Status: Accepted. Records a durable eval-infrastructure finding from the first GATE-2
run: CUAD's questions and category labels must never be used as cross-corpus retrieval queries, a valid
corpus-level retrieval bar requires independently-authored content queries (ACORD is the chosen source),
and generating queries from answer spans with an LLM is rejected as circular.

## Context

The first GATE-2 run (`eval/gate2_hybrid_rerank.py`, 21-doc representative sample) used CUAD golden
questions as cross-corpus retrieval queries and returned implausibly low recall — exact_lexical
recall@1 = 0.027, recall@10 in the 0.13–0.29 band across archetypes, for BOTH chunkers, with rerank
roughly neutral. A number that low for *exact lexical* matching is a measurement red flag, not a
retrieval verdict, so it was diagnosed before being reported.

Root cause (measured, not assumed): CUAD is an **extraction and classification** benchmark. Its
"questions" are lawyer-review annotation prompts keyed to a clause-type label ("Highlight the parts (if
any) of this contract related to 'Document Name'… Details: The name of the contract"), and its answers
are the extracted clause spans. The two share almost no vocabulary by construction. Fraction of
answer-span content-words present in the query:

| query source | exact_lexical | semantic | clause_finding |
|---|---|---|---|
| the CUAD **question** | 0.033 | 0.087 | 0.073 |
| the CUAD **category** label | 0.019 | 0.020 | 0.031 |

So there is no content-bearing query in the T9 golden set. Using these prompts as cross-corpus retrieval
queries measures query formulation, not the retriever.

Evidence it is an artifact and not a system defect: GATE-1's PER-DOC retrieval (haystack = one document)
got RLM recall@5 0.799 / baseline 0.685 — the pipeline ranks correctly in a small haystack. GATE-2's
collapse is the big-haystack (630–840 chunks) × content-free-query combination. The artifact hits both
chunkers and would hit any store equally, so it says nothing about ArcadeDB, the hybrid pipeline (T21),
RLM chunking (T17), or reranking (T22).

## Decision

1. **CUAD's templated questions and clause-type category labels are never used as cross-corpus retrieval
   queries.** They remain valid for the archetype/clause ground truth they were built for.
2. **A corpus-level retrieval bar requires content-bearing, independently-authored queries.** The chosen
   source is **ACORD** (Atticus Clause Retrieval Dataset): CC-BY-4.0, BEIR format, 114 attorney-authored
   queries over ~126k graded query-clause pairs, corpus of contract clauses from SEC/EDGAR filings and
   Fortune 500 ToS (the same contract family as our corpus, ADR-0002; a distinct, self-contained clause
   pool, not our CUAD subset). ACORD ingestion + golden-set extension is its own ask-first task extending
   T9. Because ACORD's corpus is pre-segmented clauses, it adjudicates the store recall bar (GATE-2a) and
   the rerank precision bullet (RAC-22 b2); it does not exercise the RLM chunker, which stays at
   post-graph GATE-2b (ADR-0009). License and corpus provenance were verified before adoption.
3. **LLM-generating queries from answer spans is rejected as circular.** If the generator sees the clause
   and writes a question about it, the query inherits the clause's vocabulary, retrieval becomes easy by
   construction, and the number measures the generator, not the retriever — replacing a visible artifact
   with an invisible, flattering one, which is strictly worse. Independently-authored expert queries do
   not have this failure mode. If ACORD turns out unusable (license or corpus-mismatch), return to the
   human before generating queries; do not fall back to LLM generation without a decision.

## Consequences

- **GATE-2 run 1 does not adjudicate the gate.** GATE-2a (store bar): recorded as *not adjudicated, no
  signal favoring LanceDB*; ArcadeDB is kept; the bar is NOT marked met. RAC-22 b2 (rerank improves
  precision@k): *pending valid queries*, not checked — rerank was neutral only because the relevant clause
  was rarely in the candidate pool, which is not a rerank result.
- **What run 1 genuinely proved (recorded as its actual result):** the full read+ingest pipeline ran end
  to end at scale with zero crashes — 21 documents, both chunkers, parse → chunk → embed (dense + sparse)
  → ArcadeDB write → server-side RRF hybrid → BGE rerank, over 630–840 chunks. Real integration validation
  of T16–T22 composed together.
- A separate defect surfaced in the same run — the RLM chunker over-fragments some documents and emits
  near-empty heading-only chunks (no minimum-chunk-size floor). That is a T17 chunker bug, tracked
  separately; it must be resolved before any RLM-vs-baseline chunker comparison, so a chunker bug is not
  attributed to the chunking strategy.
- Reference: [[cuad-not-a-retrieval-benchmark]] (memory), [[evals-in-depth-no-shortcuts]],
  [[rlm-chunking-kg-enterprise-design]].
