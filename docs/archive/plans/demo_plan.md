# Demo / API plan — CUAD QnA + retrieval + graph legs (2026-07-28)

**Not built yet — plan for review.** The demo is the user-facing surface over the built capabilities. It is
also the seed of a **reusable API**: endpoints are named after the *capability* they front (corpus-agnostic),
not after a corpus/eval, so each is reusable in a real application and maps cleanly to a future orchestration
agent / graph node. Do **not** use names like `/cuad/ask` or `/acord/search`.

## Naming principle

Name each endpoint after the **registered capability** it exposes, generically. One capability = one endpoint
= one potential agent/graph node later. Corpus is a parameter/data concern, never part of the route.

## Three capability surfaces — one common FastAPI backend

A single FastAPI backend exposes three independent routers; a minimal **CLI** and a minimal **Web app** are
built separately and both call this API. Front end can be CLI or Web; the backend is shared.

| Surface | Endpoint | Fronts | Status |
|---|---|---|---|
| **Locate-in-document** (single known doc QnA / highlight / extract) | `POST /documents/{document_id}/ask` | query-understanding (glue) + serve/locate (CU-C2) | ✅ ready (`ragwright_cuad`: 102 docs / 27,074 spans) |
| **Retrieve-across-corpus** (text leg) | `POST /retrieve` | hybrid retrieval + reranking | ⚠️ built + tuned on `ragwright_acord_pivot`; GATE-R recall bar still pending (T58b) |
| **Traverse-relationships** (graph leg) | `POST /graph/query` | `graph_query` (FR-C.5) | ❌ needs the ER graph populated first (`entities: 0` today) |
| Shared | `GET /documents`, `GET /documents/{id}`, `GET /health` | store reads | ✅ |

(Alt REST-noun variants if preferred: `/passages/search` for retrieve; `GET /entities/{id}/related` for the
graph leg.)

### Endpoint contracts
- `GET /documents` → `[{document_id, title, type}]` (the picker).
- `GET /documents/{document_id}` → canonical document text (`data/cache/cuad/canonical/{slug}.txt`) + metadata.
- `POST /documents/{document_id}/ask {question}` → `understand_query` → `serve_highlight` → `HighlightResult`
  (spans with `doc_start`/`doc_end`, `function`, `text`, `extracted_value`, `confidence`; `present`,
  `in_taxonomy`, `low_confidence`). Offsets drive highlighting.
- `POST /retrieve {query, filters?}` → ranked, cited passages across the corpus (function/property filter →
  hybrid → reranker (b)). Cross-corpus relevance; not entity traversal.
- `POST /graph/query {anchor, relationship?, hops?}` → cited relational evidence (reached entities + path
  chunk_ids + confidence). Anchor resolved via entity-resolution; traversal independent of the text leg.
- `GET /health`.

### Backend design notes
- **Locate** loads: store + BGE-M3 (fallback only) + Gemma seam. **Classifier NOT loaded** — spans are
  pre-classified at ingest; serving is read-only + LLM glue.
- **Retrieve** loads: store + BGE-M3 (query embed) + reranker; routing lives in the existing retrieval path.
- **Graph** loads: store; calls `graph_query`. Inert until the ER graph is populated.
- The FastAPI layer is hand-wired **glue** (like `understand_query`, deliberately not a registered capability).
  The *registered* capabilities are what a future compiled query graph binds; this API is a manual face on
  them for the demo and for direct reuse.

## Frontends
- **CLI** (httpx → API): `list`, `ask <document_id> "<question>"`; renders the highlighted span + function /
  confidence / present / low_confidence / extracted_value. (Later: `retrieve "<query>"`, `graph <anchor>`.)
- **Web** (vanilla HTML+JS `fetch`, optionally served by FastAPI): document dropdown + question box → canonical
  text with `<mark>` spans + a side panel (intent / function / confidence / value; not-present / low-conf).

## Sequencing (per direction, 2026-07-28)
1. **ER-graph population precursor** (below) — do this before the full three-surface demo.
2. **Locate surface is demoable immediately** (store already populated).
3. **Retrieve surface** demoable on `ragwright_acord_pivot` (flag: GATE-R not cleared — present it as
   "cross-corpus retrieval + reranking," not a proven-recall system).
4. **Graph surface** after population.

## ER-graph population (precursor track) — scope

The graph capabilities are **code-complete + tested but never populated** (`entities: 0`; no driver calls
`extract_chunks`/`write_graph` outside tests). To make the graph leg real:
1. **Write a population driver:** per contract → `graph_extraction` (spaCy NER mentions + contract-party
   extractor + LLM-escalation for INFERRED relationships) → `disambiguation` → `entity_resolution`
   (closed-world to the EDGAR CIK registry, already built) → `write_graph` into ArcadeDB.
2. **Unwire the empty leg** (`_EMPTY_GRAPH` in `run_acord_retrieval.py`).
3. **Run the relational archetype** to produce the numbers.
- **Corpus:** the **EDGAR/CUAD entity graph** (`Entity`/`CONTRACTS_WITH`/`Mentions` — matches the schema and
  the EDGAR relational golden set). *Not* the ACORD clause-relation graph (design sketch only).
- **Costs/limits:** LLM-escalation is a per-corpus LLM cost; the GATE-2(b) adjudication payoff is bounded by
  the thin golden set (16×1-hop + 3×2-hop). Worth doing to light up the leg; don't expect a firm verdict.

## Chunker note (2026-07-28)
The ingestion pipeline is **docling parse → `chunk(discoverer)` → segment → classify → embed → store**, where
`chunk()` (one capability, `rlm_chunking.py`) takes a **pluggable discoverer seam**. Two discoverers exist and
have both been run on raw CUAD-type contracts:
- **RLM discoverer** — the general-RAG / GATE-2 **text-leg** ingestion (`eval/gate2_hybrid_rerank.py:238`,
  "RLM vs baseline chunker A/B on the text leg"): docling parse → RLM chunk → embed → store.
- **Single-call Gemma** (`SingleCallBoundaryDiscoverer`) — the CUAD highlight leg (`ingest_cuad.py`), same
  `chunk()` with the discoverer swapped.

So the chunker choice is **one argument to `chunk()`**, not a rewrite. (ACORD is the exception — its corpus
ships pre-segmented, so `ingest_acord.py` explicitly *does not exercise* any chunker; that is a benchmark data
shape, not the absence of an ingestion stage.) **Standing position:** default the discoverer to **single-call
Gemma** for raw-document ingestion (ADR-0031: single-call ≥ agentic RLM on structured contracts — 3.4s vs
5–9 min, integrity 1.0); keep **RLM available but optional** for large / deeply-hierarchical corpuses (ADR-0009
keep-vs-optional, never drop). Not yet A/B'd: downstream **retrieval/recall quality** of the two discoverers
(ADR-0031 measured integrity + speed only) — the GATE-1/GATE-2(b) axis; re-ingest with each discoverer and
compare recall before standardizing single-call on a *new* corpus.

## Prerequisites — status
- **ArcadeDB running:** ✅ container `arcadedb-ragwright` up (ledger "stopped/idle" note is stale).
- **`ragwright_cuad` populated:** ✅ 102 docs / 27,074 spans (SEED=0 holdout). No re-ingest.
- **New deps** `fastapi`, `uvicorn`, `httpx`: **ask-first** (`uv add`).

## Out of scope
Orchestration — query routing, use-case workflows, automatic fusion/union/sequencing of the legs — is the
**compiler half** (GraphWright, from the Orchestration Spec), binding the *registered* capabilities. This plan
builds the capability surfaces; the compiler assembles them later. See memory
`graph-layer-two-graphs-state.md`.
