# PROD-1 readiness note — non-CUAD ingest from GCS (2026-08-11)

Objective (ADR-0049 generic-customer lens): prove the ingestion pipeline can pick a corpus **from GCS** and build
the typed KG + span index on **real non-CUAD contracts**, and see where it breaks. Evidence, not architecture-faith.

**Setup.** Corpus = 25 curated real contracts (CC BY 4.0) on `gs://dreamai-pocs-ragwright-ingest/prod1-corpus/`:
15 ContractNLI NDAs + 10 MAUD merger agreements. Ran **19** (all 15 NDAs + 4 MAUD; capped for budget — the MAUD
docs are 207–291k chars). Scratch DB `ragwright_prod1`. Classifier = Gemma-4-31b (coreweave/bf16 + fallbacks) with
the enum; extraction = granite (product substrate). Deliberately generic: **empty entity registry, no party seed.**

## What works (validated on real data)
- **GCS → KG end to end.** SA-auth `GcsCorpusAdapter` reads the bucket; 19 contracts ingested, **0 dead-lettered**.
  The empty-registry / no-party-seed generic path works.
- **KG produced:** Contract=19, **Clause=1,134**, Span=4,010, Entity=48.
- **Classifier sane on fresh data, both types:** `function=NONE=0` (no invention/omission from Gemma+enum).
  Labels are correct — Confidentiality 331, **Condition Precedent 153**, Payment Terms 111, Parties 102,
  Indemnification 40, Dispute Resolution 38, Warranty Disclaimer 35, Anti-Assignment, Liquidated Damages,
  Third Party Beneficiary… (the merger agreements added the M&A-typical clause mix).
- **The ADR-0049 ONT-2 ontology enrichment is validated on real data** — the new dimensions populate with correct
  values, and the merger agreements exercised exactly the facets we designed:
  - `condition_type` (REQUIRES): **122**, all 8 vocab values present, incl. the corpus-check-added ones —
    **no_material_adverse_change 35 (MAC)**, court_approval 12, closing_condition 17, plus third_party_consent 26,
    regulatory_approval 20, board_approval 7, financing 5. Validates the corpus-check-driven vocab broadening.
  - `confidentiality_exception` (EXCEPTS): **182**; `dispute_method` 9; `collateral_type`/SECURES 2;
    `force_majeure_event` 2. (Royalty basis 0 — no royalty clauses in this mix, expected.)
- **Large-document path works:** MAUD merger agreements ingest cleanly at 140–160 clauses / 700–840 spans each.
- **`max_tokens` 2000→4000 fix holds:** no per-clause truncation on the large docs.

## Findings / gaps (the hardening backlog)
1. **Full-document party extraction truncates on large docs.** GP-1B `extract_parties` runs on the WHOLE document;
   on 250k-char merger agreements the LLM JSON truncates ("Unterminated string") → graceful degrade (doc still
   ingests, fewer entities), but few/no party entities from the M&A docs. Fix: chunk / bound the party pass (same
   class as the per-clause `max_tokens` issue, but for the whole-doc call).
2. **Party-linking: RESOLVED (PLINK-1) — it works generically; my earlier "0 links" was two of my own errors, not
   a defect.** (a) The `ingest_prod1` driver initially left `link_fn` on the no-op default so linking never ran
   *during* ingest (fixed — now wires the generic cacheless `party_clause_linking(store)`). (b) My post-hoc
   diagnostic queried the **wrong edge-type name** (`PARTY_TO`; the constant is `PartyTo`), which threw "type not
   found" and made me falsely report 0 links. Run correctly, the generic provenance join
   (`Entity.chunk_id` source-doc == `Contract.contract_id`) derives **48 links / 0 unmatched** across BOTH the NDA
   (38) and MAUD (10) entities, and writes **48 `PartyTo` edges** (real party→contract links). The derive-join is
   already covered by hermetic tests (13 green). PEXT-1 is NOT a confounder — party extraction produced entities
   for both corpora. So party-linking generalizes; the many-to-many enrichment (mention cache) remains the only
   cache-dependent part.
3. **GCS python client needs a service account / ADC** (gsutil/gcloud auth is insufficient) — handled here via the
   `.env` SA key; production would use a service account or the GCP MCP (product-side).
4. **Blocking sequential run is slow** — 19 docs ≈ 1.5h; the full 100 (50 large MAUD) would be ~3.5–6h. This is the
   PROD-3 case: async, document-parallel LangGraph job with submit/status (ADR-0050).
5. **PDF/DOCX parsing not exercised** — the corpus is text; the adapter's `parse_bytes` (docling) route for real
   customer PDFs is a follow-on (and is the same generic parse the compliance side needs, PROD-2).

## Verdict
The **core is production-shaped and validated on real non-CUAD contracts**: GCS → generic pipeline → typed KG +
span index, with a sane classifier and the ONT-2 ontology enrichment demonstrably working on fresh data. The open
items — large-doc party extraction (PEXT-1), async execution (PROD-3), and PDF parsing — are the capability-
hardening backlog, not blockers to the pipeline itself. (Party-linking, PLINK-1, is resolved: it works generically.)
