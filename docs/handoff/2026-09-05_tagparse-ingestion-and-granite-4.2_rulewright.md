# RuleWright handoff: tag-parse ingestion + granite-4.2 model switch (engine)

Date: 2026-09-05
Engine commit range: `ed16e8e..c0e4fe7` (on `origin/main`)
ADRs: 0079 (model default), 0080 (nested tag-parse), 0081 (function-independent extraction), 0082 (symbolic gate function-independent)

This note is for the RuleWright (product) team. It summarizes a set of ENGINE changes that alter how contract
clauses are extracted during ingestion and which model runs everywhere. Product code does not change, but a few
behaviors, a new operational dependency, and several config knobs are worth knowing before the next product test.

---

## TL;DR

- **Model:** the product LLM moved `ibm-granite/granite-4.1-8b` → **`ibm-granite/granite-4.2-8b`** everywhere
  (ingestion, query, compliance) because 4.1 was **de-listed on OpenRouter (404)**. granite-4.2 is a reasoning
  model, so the profile disables reasoning on structured calls (handled in the engine; nothing to do).
- **Ingestion clause extraction was rewritten.** The old docling-graph path (server-side JSON) **hard-crashed
  ~89% of real CUAD clauses**. The new default is a **function-independent, client-side tag-parse** extractor.
  On a 45-clause grounded A/B this took success 0.13 → 1.00 and key-field recall (in-KG) 0.13 → 0.60.
- **A second model (gemma) is now called during ingestion** for list-valued fields (cross-model "union" that
  fixes granite's list under-enumeration). This is **on by default** and **configurable/disable-able**.
- **The KG, the ontology (`.ttl`) schema, party/entity extraction, and all downstream (grounding, compliance)
  are unchanged.** This is an extraction-layer change, not a data-model change.

To consume: **bump the engine dependency to `origin/main` (`c0e4fe7`)** and run a bulk product test on your corpus.

---

## What changed, and why

1. **granite-4.1-8b → granite-4.2-8b (ADR-0079).** 4.1 returns HTTP 404 on OpenRouter. 4.2 is the in-family
   successor; verified reliable on the real ingestion path. It is now the single product default (`_PRODUCT_LLM`),
   so **every text role** resolves to it: structured-reasoning, general, summarization, function-classify, etc.
   (VISION_OCR stays gemma; vision needs a vision model.)

2. **Ingestion clause extraction: docling-graph → tag-parse (ADR-0080/0081).** docling-graph forced the model to
   emit a 35-field JSON object in one call; granite flattened/failed it on the majority of real clauses. The new
   extractor decomposes the `Clause` schema into **thematic groups**, extracts each with **client-side XML-tag
   parsing** (free-text in, `<field>value</field>` out, parsed our side), and merges. It reuses the SAME downstream
   (adapt → ADR-0028 lexical grounding → ADR-0040 symbolic gate → KG write), so grounding/compliance are unchanged.

3. **Function is no longer load-bearing (ADR-0082).** We measured clause-function classification at ~0.37–0.50
   top-1 — too unreliable to gate on. So: extraction is **function-independent**, and the symbolic gate no longer
   downgrades on function-applicability (only on genuine intra-clause contradictions). **Function is now a soft KG
   tag / query-time signal, never a hard gate.** (If any product logic assumed `Clause.function` was authoritative
   at ingest, note it is a best-effort label.)

4. **Cross-model list union (default ON).** granite and gemma under-enumerate DIFFERENT items on list-valued
   fields (e.g. "punitive, exemplary, or consequential" → granite drops "consequential"). For **list-bearing
   groups only** (~4 of 8), the engine also runs a second model (gemma) and unions the list values — recovering
   the complete set. gemma's cost is scoped to those groups.

5. **Noise hardening & robustness:** garbage/prompt-echo guard, empty-tag-is-absent, nested-tag flatten recovery,
   and per-function ttl-definition tightening (Cap On Liability, Warranty Disclaimer lifted 1/8 → 3/8 each).

---

## What RuleWright must know / do

### Consume it
Bump the engine dependency to `origin/main` @ `c0e4fe7`. No product code change is required for the defaults to apply.

### New operational dependency (IMPORTANT)
Ingestion now issues, per clause:
- ~8 granite-4.2 calls (one per thematic group), plus
- gemma calls on ~4 list-bearing groups (cross-model union).

So the ingest environment **must have OpenRouter access to both `ibm-granite/granite-4.2-8b` and
`google/gemma-4-31b-it`** (or the vLLM equivalents via `RAG_SERVING=vllm`). Expect **higher LLM volume,
cost, and latency** than the old docling path (which was ~1 call/clause — but crashed on most real clauses).

### Config knobs (all have safe defaults)

| env var | default | effect |
|---|---|---|
| `RAG_INGEST_CLAUSE_EXTRACTOR` | `tagparse` | `tagparse` (new) or `docling` (legacy, rollback) |
| `RAG_INGEST_LIST_MODEL` | `google/gemma-4-31b-it` | second model for the list union; `off` to disable (granite-only) |
| `RAG_INGEST_CLAUSE_SAMPLES` | `1` | same-model multi-sample union for lists (N>1 = more calls) |
| `RAG_INGEST_CLAUSE_GATE` | `0` (off) | opt-in coarse aspect gate to skip groups (cost lever; granite under-selects, so off by default) |
| `RAG_MODEL_*` / `RAG_MODEL_ALL` | (profile) | per-role or global model override (unchanged mechanism) |

The clause extractor's seam (`granite_clause_extractor(model=, list_model=, samples=)`) also takes these as
**arguments** if the product wants to configure them in code rather than env.

### Cost / latency dials for the product test
- **Cheapest, granite-only:** `RAG_INGEST_LIST_MODEL=off` (drops the gemma dependency; list completeness reverts
  to granite's under-enumeration).
- **Default (recommended):** leave as-is (granite + gemma-on-list-groups) — best list-inclusive recall at a
  fraction of "gemma everywhere" cost.
- **Rollback to old behavior:** `RAG_INGEST_CLAUSE_EXTRACTOR=docling` (not advised — it crashes on most real
  clauses — but available).

---

## Behavior changes to expect

- **Far fewer dead-lettered / failed clauses.** docling hard-crashed most real clauses; tag-parse ingests them.
  A clause that can't be fully extracted degrades to **PARTIAL** (lossless: recorded, flagged) rather than crashing
  the document.
- **`Clause.function` is a soft label** (best-effort), not an ingest gate. Query-side routing should treat it as a
  soft signal (consistent with ADR-0047).
- **KG shape is unchanged** — same ontology (`.ttl`) schema, same `write_clause_kg` / `write_graph` / entities /
  edges / spans. Existing KGs and queries are unaffected structurally.
- **Values may carry `AMBIGUOUS` confidence** more visibly — the grounding gate keeps an extracted-but-unverified
  value (down-weighted) rather than dropping it. That is by design.

---

## Validation status (be honest about scope)

- **1457 engine unit tests pass.**
- **End-to-end real-NDA ingest** through the full default pipeline: INGESTED clean, no dead-letter,
  `{clauses:7, entities:2, edges:1, spans:7}`, 0 failures, ~10s.
- **Grounded A/B (45 CUAD clauses, span-noise-limited):** default config reaches key-field recall (in-KG) 0.60,
  matching "gemma-everywhere" at granite cost; docling was 0.13.

**Not yet done:** a bulk run on RuleWright's actual corpus. **That bulk product test is the real proving ground** —
please run it and report clause_failures / dead-letters / cost-per-doc so we can tune (aspect gate, samples,
list-model choice) against real data.

---

## Quick reference

- Model default + routing: `src/rag_wright/models/profiles.py` (`_PRODUCT_LLM`).
- Tag-parse extractor: `src/rag_wright/spans/tag_clause_extractor.py`; seam: `spans/clause_kg_extractor.py::granite_clause_extractor`.
- Client-side tag parsing: `src/rag_wright/models/tag_structured.py`.
- Symbolic gate (now function-independent): `src/rag_wright/spans/symbolic_validation.py`.
- ADRs: `docs/adr/0079..0082`. Session ledger: `tasks.md` (TAGPARSE-INGEST-1 block).
