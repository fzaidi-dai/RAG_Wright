# ADR-0046: ACORD folded into the one production KG (ADR-0033 realized); the OKF span field retired

## Context

ADR-0033 decided **one unified contract KG**; the three legs are scoped queries over it. In practice we drifted:
the enhanced KG (`ragwright_cuad_full`: 23/23 typed property-edge types, entity/party/exception layers) was built
CUAD-only, while ACORD — our attorney-graded retrieval benchmark (ADR-0011) — sat in a separate, stale
`ragwright_acord_pivot` (only 14/23 property edges, no entity/party/exception layer). Graded recall therefore
measured an old system, not what we ship.

ACORD is a **CUAD derivative** (both The Atticus Project). Measured overlap: **3,221 / 3,931 (82%)** ACORD
clauses already exist verbatim in `ragwright_cuad_full` (with the full enhanced treatment). Keeping a second KG
for a same-domain, 82%-duplicate corpus contradicts ADR-0033 and gave us no valid way to benchmark production.

Separately: **OKF is dropped** (tasks.md:434; SKILL-SPLIT FINDING 2 — OKF signpost enrichment removed from the
pipeline; CAP-REG-3 designed the retrieval core with "no ACORD okf_path identity baked in"). The
`SpanRecord.parent_okf_path` field is vestigial (empty on all CUAD spans).

## Decision

**Unify ACORD into `ragwright_cuad_full` surgically, and retire the separate KG.**

1. **Map (82%)** — `acord_id → production parent_chunk_id` by verbatim text match against the CUAD span corpus
   (idempotent: excludes `acord-` spans, so it always resolves the true CUAD overlap).
2. **Ingest the ~710 remainder** through the **enhanced clause layer** (segment → LegalBERT function-label →
   granite 23-dim property extraction + ADR-0040 semantic judge → typed edges + BGE span index). Isolated
   clauses → no contract/party layer. Identity uses the **canonical scheme only** (`source_doc_id=acord-{aid}`);
   **`parent_okf_path` stays empty** (OKF is dropped). `acord-` prefix makes the ingest fully reversible.
3. **qrels on production** — express the ACORD qrels against the unified KG's canonical `parent_chunk_id`s
   (overlap map ∪ ingested pcids). Coverage of the grade≥2 relevant set: **620/620 = 100%**; graded 61,988/61,988.
4. **Retire `ragwright_acord_pivot`** (backed up, then dropped). One KG, one source of truth.

Driver: `scripts/acord_unify.py {map|ingest|qrels}`. Artifacts regenerate under `data/eval/acord_unify/`
(gitignored); the script is the committed source of truth. Ingestion uses **granite** (not Cerebras/Gemma —
those 404 on granite), concurrency 8, monitored X/N.

## Consequences

- **One KG holds all 3,931 ACORD clauses** with the current enhanced schema. Graded recall now runs against the
  production system (`data/eval/acord_unify/acord_prod_qrels.json`), not a stale build.
- **OKF fully retired from span data:** the 3,495 ingested ACORD spans were cleared of `parent_okf_path` to match
  CUAD (all empty). ACORD identity is recoverable from the canonical `parent_chunk_id` (`acord-{aid}:0:<hash>`).
  The vestigial field remains in the schema but is unused; a later migration may drop it.
- **Reversible:** all ACORD-added nodes are `acord-`-prefixed (`DELETE FROM Clause/Span WHERE … LIKE 'acord-%'`).
  A pre-ingest backup of `ragwright_cuad_full` was taken.
- ACORD adds **evaluation + coverage**, not new ontology — same legal-contract domain. It does not by itself
  enrich symbolic reasoning; those levers are the ontology/property schema/extraction fidelity (the PREC work).
- The old pivot-DB eval scripts (`eval/function_ceiling.py`, `eval/condensed_pipeline.py`,
  `eval/function_property_rerank.py`) queried `parent_okf_path` on the pivot DB; the production benchmark keys off
  canonical ids instead. Those scripts are superseded for the unified KG.
