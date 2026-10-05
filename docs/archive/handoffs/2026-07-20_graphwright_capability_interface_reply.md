# Reply to GraphWright: confirmed/corrected typed interfaces for 5 capabilities

Date: 2026-07-20. From: RAG_Wright (the capability half). To: GraphWright (the orchestration compiler).
Re: your "Request to the RAG side: confirm the governed typed interfaces for 5 capabilities" (GraphWright
ADR-0030, FR-2C.7).

---

## TL;DR

Your model is sound — a typed I/O interface on the manifest, optional, checker compares the type-sets. We
confirmed all five against the **actual bound callables** (grounded, not from the descriptions). Your
type-shapes are mostly right, but there are **three material corrections**, **one compatibility blocker**,
and **two more capabilities** that belong in the set. We will **author and emit `capabilityInterface`
ourselves** (it is governed data we own), but we need two things from you first (bottom of this doc) before
any manifest can carry it.

The single most important correction: **`reranking` requires the passage TEXT as input, not chunk_ids.** Your
current guess (`chunk_ids: chunk_id -> ranked_ids`) describes a chain that cannot actually run, and — because
your type vocabulary collapses everything to `chunk_id` — your checker would "verify" it anyway. That is the
exact force-fit failure mode ADR-0030 is meant to catch.

---

## 1. Corrected §5 table (grounded in the real signatures)

Every row is grounded in the bound callable in `src/rag_wright/capabilities/`. "config" = tuning/deps that are
NOT pipeline data (drop them from the interface, per your §3).

| capability (signature) | data inputs (name: type, required) | data outputs (name: type) | correction vs your guess |
|---|---|---|---|
| **`hybrid_search`** (`hybrid_search.py:44`) | `query: text` (required) | `candidates: candidate[]`, `candidate = {chunk_id, source_doc_id}` | Output is not bare `chunk_id` — each candidate carries a `source_doc_id` provenance. `filters` and `k` are **config, not data** — drop them. |
| **`reranking`** (`reranking.py:83`) | `query: text` (required); **`passages: passage[]`** (required), `passage = {chunk_id, source_doc_id, text}` | `ranked: scored_candidate[]`, `scored_candidate = {chunk_id, source_doc_id, score}` | **Biggest correction: the input is passages-WITH-TEXT, not chunk_ids.** It deliberately does not fetch text (`reranking.py:11-14`) — text must be attached upstream. Output is **ids-with-scores**. `top_k` is config. |
| **`fusion`** (`fusion.py:38`) | `reranked: rerank_result` (retrieval leg, required); `graph: graph_answer` (graph leg, required) | `fused: fused_chunk[]`, `fused_chunk = {chunk_id, sources[]}` | The two inputs are **distinguishable and differently typed** (a scored retrieval list vs a cited `GraphAnswer`), **not** a variadic id-set. Output carries a `sources` provenance tag and **no score** (it is a union, not a score fusion — `fusion.py:5-7`). `cap` is config. |
| **`chunk_read`** (`chunk_read.py:41`) | `chunk_ids: chunk_id[]` (required) | `chunks: chunk_text[]`, `chunk_text = {chunk_id, text, source_doc_id}` | Output is an **ordered list of `{chunk_id, text, source_doc_id}` records** (id↔text paired, order-preserving, one per input id) — not a bare mapping, not a flat text list. Raises on an orphan id: **drops nothing** (`chunk_read.py:12`). `text_store` is config. |
| **`rlm_synthesis`** (`rlm_synthesis.py:275`) | `query: text` (required); **`chunks: synthesis_chunk[]`** (required), `synthesis_chunk = {chunk_id, text}` | `answer: text` (the `synthesis` field); **`chunk_ids: chunk_id[]` (citations)**; `slice_outputs: slice_output[]`, `slice_output = {chunk_id, extract}` | It **takes text** (`{chunk_id, text}`) — it does **not** rehydrate ids itself. It **also emits citations** (the cited `chunk_ids` plus per-slice cited extracts), not just an answer. `extractor`/`synthesizer`/`fanout` are config/seams. |

---

## 2. Your specific inline questions, answered

- **`hybrid_search` — are metadata/source-doc filters a required data input or config?** Optional **config**, not
  a pipeline data input. The only required data input is `query`.
- **`reranking` — is top-k a data input or config?** Config. And the more important correction: its input is
  **passages with text** (`{chunk_id, source_doc_id, text}`), and its output carries **scores**.
- **`fusion` — are the two inputs distinguishable or variadic?** Distinguishable. A fixed
  `(retrieval-leg, graph-leg)` pair, typed differently (`rerank_result` vs `graph_answer`) — not variadic.
- **`chunk_read` — text-per-chunk-id mapping or a flat list?** An **ordered list of `{chunk_id, text,
  source_doc_id}` records** (id↔text paired, order preserved, one per input id).
- **`rlm_synthesis` — texts or ids it rehydrates itself? does it emit citations?** It takes `{chunk_id, text}`
  (already rehydrated upstream by `chunk_read`); it does **not** rehydrate. It **emits both** the answer and
  the citations (`chunk_ids` + per-slice `slice_outputs`, each tied to a `chunk_id`, FR-Q.6).

---

## 3. Two structural findings — please read before writing anything

### 3.1 The type vocabulary is too coarse, and that directly weakens your checker

Several ports carry **compound records**, not scalars:

- `candidate = {chunk_id, source_doc_id}` (hybrid_search out)
- `scored_candidate = {chunk_id, source_doc_id, score}` (reranking out)
- `chunk_text = {chunk_id, text, source_doc_id}` (chunk_read out)
- `synthesis_chunk = {chunk_id, text}` (rlm_synthesis in)
- `fused_chunk = {chunk_id, sources[]}` (fusion out)
- `graph_answer = {answer?, evidence: [{entity_id, chunk_ids[], ...}]}` (graph_query out → fusion in)

This is not cosmetic. If `reranking` is typed `chunk_id -> chunk_id`, your checker **cannot see that it needs
passage text**, so it would bless a chain that feeds it bare ids — the exact wrong-realization ADR-0030 is
built to prevent. The type granularity **is** the checker's correctness. Conversely, over-coarse types would
also let the checker chain `hybrid_search -> rlm_synthesis` directly (both "speak chunk_id / text") and drop
`chunk_read` — which is wrong, because synthesis needs `text` per chunk and hybrid_search only yields ids.

**Recommendation:** support a compound/record type (a named type with fields), or at minimum distinguish
`chunk_id` vs `chunk_with_text` vs `scored_chunk`. The **load-bearing chain fact**: text must be attached (via
`chunk_read`, or a summary source) **before `reranking` and before `rlm_synthesis`** — both consume text,
neither fetches it. Tell us which type model your checker will consume and we will shape the emitted field to
match. We do not want to emit a wire format your checker then can't read.

### 3.2 `capabilityInterface` is NOT silently backward-compatible against our schema (blocker)

Our ARD mirror `RegistryEntry` sets `extra="forbid"` (`src/rag_wright/capabilities/ard.py:55`, the `_ArdModel`
base). A manifest carrying an unknown top-level `capabilityInterface` field would **fail to load** through our
`RegistryEntry` and break our conformance / round-trip tests — and since your `entry.py` mirrors ours verbatim
per ADR-0005 (also `extra="forbid"`), most likely through your `RegistryStore` too.

So this is **not** a free additive drop. It is a **coordinated schema addition to both `ard.py` and
GraphWright's `entry.py`** — the exact cross-repo coordination point our `ard.py` docstring names — done
*before* any manifest carries the field. **Neither side ships a manifest with the field until both schemas
accept it.**

---

## 4. Is the set right? (your §6 Q5)

The five are correct for the retrieval→answer graph, and none is secretly two capabilities. But two more
belong if you want to govern this graph end-to-end:

- **`graph_query` (FR-C.5)** produces `fusion`'s *second* input — a `GraphAnswer{evidence:[{entity_id,
  chunk_ids[]}], ...}` (`graph_query.py:19,30`). You cannot check the fusion realization without it. Interface:
  `query: text -> graph_answer`.
- **`generation` (FR-C.9)** is the **alternative** answer step to `rlm_synthesis` (grounded/cited/abstaining).
  If a plan can realize "synthesize" via generation, it wants an interface too.

One note that is load-bearing for your lowering pass: **`reranking` is deliberately split from text-fetch** —
it never reads the store; text must be supplied to it. Treat that as a governed fact, not an accident.

---

## 5. Ownership: RAG authors and emits `capabilityInterface`

These manifests are **RAG-authored** (we emit them via `ManifestSkeleton.author(...)` / `write_manifest`), so
the clean ownership is for **us** to add `capabilityInterface` to our schema (`ard.py`) and author path and
emit it — governed data we own — rather than you hand-writing into our registry files. We have opened a task
(**T43**) to do exactly this. It is **blocked on the two asks below** and will not ship until they are resolved
and reviewed on our side.

---

## 6. Two asks back to you (before we ship the field)

1. **Add the mirrored optional field to `entry.py` in lockstep.** Per finding 3.2, both sides run
   `extra="forbid"`; neither ships a manifest carrying `capabilityInterface` until both schemas accept it.
   Confirm you will add the mirror, and let's agree the top-level field name (`capabilityInterface`) and its
   nested shape verbatim, ADR-0005 style.
2. **Agree the type vocabulary (scalar vs compound).** Per finding 3.1, tell us whether your checker consumes
   compound/record types or only scalars, and how you want the compound shapes named. We will shape the
   emitted `capabilityInterface` to match so your checker can read it and check the real chains.

On your answers to (1) and (2), we finalize T43 (extend `ard.py`, thread it through the author path, declare
the interfaces above for the 5 + `graph_query` + optionally `generation`, and re-emit the manifests). Nothing
else in the manifests changes.

Thanks — grounding these against the real callables is exactly the trust anchor you're after; the two asks are
just what it takes to make "verified" mean verified against *both* schemas without breaking either loader.
