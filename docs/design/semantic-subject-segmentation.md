# Engine design: semantic segmentation for the subject compliance pipeline (SEG arc)

**Raised by:** engine (self-originated, product-owner approved 2026-08-30) · **Continues:** the UNIFY arc
(A–F, shipped) · **Component:** the subject front-end in `subgraphs/compliance_check.py`, reusing the ingestion
`semantic_chunking`/`rlm_chunking` machinery, the docling-graph verbatim extraction, and the 0009 tiered OCR +
0008/ENG-1 PARTIAL machinery · **Contract touch:** `CheckableFact` (structural-provenance fields — ask-first) ·
**Supersedes:** the regex-only subject segmentation shipped in UNIFY-B (`subject_facts_fn` → `segment_clause`).

---

## Why

The UNIFY arc unified the subject **entrypoints** (one front-end, text or upload) and the citation plumbing
(`CheckableFact.section`, `§ {section}: sentence`). But its **segmentation guts** were a shortcut: docling
heading-split (`document_to_sections`) + a **regex sentence splitter** (`segment_clause`). That is brittle on
real-world documents and — the core problem — it **skips the LLM semantic-chunk step that the canonical
ingestion front-end uses** (parse → **chunk** → segment). The subject pipeline used neither ingestion LLM step
(semantic chunking on the retrieval leg; rule extraction on the policy leg); it invented a weaker parallel path.

This arc replaces the subject segmentation with the **same semantic machinery ingestion uses**, so any document —
sections, sub-sections, bullets, lists, ragged flat text, or a scan — is segmented *semantically* and each
checkable fact is cited at its natural granularity.

## Settled decisions (product owner, 2026-08-30)

1. **Unit granularity = semantic chunk → per-chunk assertion extraction with verbatim spans** (option 1(b)). The
   checkable fact is an *extracted assertion tied to its exact source text*, not a raw sentence and not a whole
   chunk.
2. **Discoverer = single-call (the ingestion default), NOT RLM/working-set.** The subject reuses ingestion's
   `chunk()` entrypoint VERBATIM and inherits **exactly** its large-document behavior — there is NO subject-specific
   large-doc code path. Ingestion has no auto single→RLM escalation today, so neither does the subject. If a "no
   bottleneck" guarantee ever needs escalation, it is added to the SHARED chunker (benefiting ingestion too), never
   patched subject-side. One code path for large documents, same as everywhere else.
3. **`segment_clause` (0010) is retained as an INTERNAL deterministic sub-splitter** — used only where a finer
   split inside a coherent unit is warranted; never the primary mechanism, never surfaced to the user/product.
   (0011 is a different pipeline — answer-prose hygiene — and is untouched.)
4. **Citation = structural locator when available + verbatim text**, else just the verbatim span:
   `{doc} § 4.2 ¶3: {verbatim}` / `{doc} § 4.2 · bullet 2: {verbatim}` / `{doc} § 4.2: {verbatim}` / `{doc}: {verbatim}`.
5. **Flat text is first-class, not a fallback.** A long structureless document is segmented by the same semantic
   pipeline into sentence-granularity assertions; the absent `§` is honest, not a degraded blob. Large flat text is
   handled by the SAME shared `chunk()` as ingestion — no subject-specific large-doc code (decision 2).
6. **OCR is reused identically** (the subject path already parses through the `TieredOCRParser` chokepoint), **and**
   the OCR PARTIAL / unreadable-pages signal MUST reach the `ComplianceReport` (SEG-6) so no verdict is ever
   silently based on half-read text.

## The pipeline (target)

```
aparsed_source_document ─► SEMANTIC CHUNK ─► PER-CHUNK ASSERTION EXTRACTION ─► CheckableFact
  (docling + tiered OCR;      (single-call        (LLM, docling-graph verbatim      (verbatim text
   carries ocr_unreadable_     discoverer over      provenance binding; each          + structural locator
   pages)                      docling items;       assertion tied to its source      section/¶/bullet + span)
                               RLM escalation)      item + char span)
                                                                                   └► segment_clause: optional
                                                                                      internal finer sub-split
```

Each stage reuses existing, tested machinery:
- **Parse:** `aparsed_source_document` (docling + `TieredOCRParser`, carries `.ocr_unreadable_pages`).
- **Chunk:** the `semantic_chunking` subgraph / `chunk()` — `SingleCallBoundaryDiscoverer` over docling items
  (`{id, index, label, level, text}`), `token_cap`, floor, tiny-fragment merge.
- **Extract:** the docling-graph verbatim-binding extraction (the pattern behind `claim_extraction`), generalized
  to a domain-neutral **assertion extractor** for the generic path (`CheckableFact`, no `claim_type`); the ad
  path keeps its typed `Claim` extractor. Both run per chunk, concurrently.
- **Locate:** docling item labels (`section_header`/`paragraph`/`list_item`/…) + level + within-section ordinal →
  the structural locator; char offsets → `doc_start`/`doc_end` (already on `CheckableFact`).

## Grounding confirmed (2026-08-30)

- docling `DocItemLabel` includes `paragraph`, `list_item`, `text`, `section_header`, `title`, `code`, `table`,
  `field_item` — enough to tag paragraph vs bullet vs section.
- `SingleCallBoundaryDiscoverer` sends `[index] text[:140]` per item in ONE call, returns `{start,end}` index
  spans; `DEFAULT_TOKEN_CAP=20_000`. No auto single→RLM escalation exists yet (SEG-5 adds it).
- The docling-graph extraction already does verbatim provenance binding ("N/N grounded (verbatim)"); current
  `to_claims` cites `(source_doc, assertion)` only — the char-span/item mapping is the new wiring (SEG-3/4).
- `SourceDocument.ocr_unreadable_pages`, `aparsed_source_document`, and `build_partial_entry` exist; the subject
  path currently parses via raw `aparse_document_bytes` and drops the signal (SEG-6 fixes).

## Task breakdown (each TDD + a LIVE test at its gate, per the standing rule)

- **SEG-1 (contract, ask-first):** extend `CheckableFact` with structural provenance — `element_kind: str | None`
  (docling label: paragraph/list_item/section_header/…), `element_ordinal: int | None` (the ¶/bullet number within
  its section); keep `section`, reuse `doc_start`/`doc_end`. Add a `locator()` render helper and have
  `assemble_finding` cite `§ {section} {¶/bullet ordinal}` when present. Additive/optional (back-compat).
- **SEG-2 (semantic chunk step):** wire the existing `semantic_chunking`/`chunk()` machinery into the subject
  front-end — docling items → coherent chunk units carrying each item's label/level/section-path. Replaces
  `document_to_sections`. Hermetic via the injected discoverer stub the chunker already supports.
- **SEG-3 (generic assertion extractor, verbatim):** a domain-neutral per-chunk assertion extractor built on the
  docling-graph verbatim-binding pattern → `CheckableFact`s with verbatim `assertion_text` + source item index +
  char span. Parallelized (semaphore). (The ad path already extracts typed `Claim`s; SEG-7 moves it per-chunk.)
- **SEG-4 (structural locator + citation render):** map each assertion's source item → its structural locator
  (section + ¶/bullet ordinal from docling labels) → render per decision 4. Flat text → sentence span, no `§`.
- **SEG-5 (large docs = the SAME code as ingestion, no new path):** the subject calls ingestion's `chunk()`
  entrypoint verbatim and inherits its large-document behavior; this task is to VERIFY that (identical entrypoint,
  config, token_cap) and to add a test that a large flat document flows through the shared chunker — NOT to add a
  subject-specific escalation. Any future large-doc improvement (e.g. single→RLM escalation) is a change to the
  shared chunker, tracked separately, benefiting ingestion and subject together.
- **SEG-6 (OCR PARTIAL propagation):** parse the subject via `aparsed_source_document`; carry
  `ocr_unreadable_pages` into the `ComplianceReport` as an un-missable PARTIAL (mirrors ENG-1) — a verdict is
  never silently based on unreadable pages. Product can surface "pages X–Y unreadable; incomplete".
- **SEG-7 (wire the front-end; unify generic + ad):** replace `subject_facts_fn`'s guts with SEG-2→4; the ad
  path's per-section extraction (UNIFY-F) becomes per-chunk over the same chunking (typed `Claim` tail intact).
  `segment_clause` demoted to the internal sub-splitter. Deprecate the `document_to_sections → segment` producer
  (keep as a back-compat override if any caller needs it).
- **SEG-8 (arc-level live gate):** real end-to-end on THREE inputs — (a) a structured doc with sections + bullets
  (assertions cite `§ N ¶/bullet`), (b) a long flat text (sentence-granularity assertions, no `§`, no bottleneck),
  (c) a scanned PDF (tiered OCR runs; OCR PARTIAL surfaces in the report). Confirms semantic units + correct
  multi-level citation + flat-text robustness + OCR-PARTIAL propagation.

## Open sub-decisions to confirm at build time

1. **`CheckableFact` shape (SEG-1):** structured fields (`element_kind`/`element_ordinal`) + a render helper
   (proposed), vs a single pre-rendered `locator` string. Structured is more flexible for the product's UI chips.
2. **Generic assertion extractor (SEG-3):** reuse `claim_extraction`'s docling-graph act with a domain-neutral
   template (`CheckableFact`, no `claim_type`), vs a thin new extractor. Prefer reuse — same verbatim binder.
3. **Cost:** assertions × applicable-rules judge cross-product still applies, but assertions are fewer and more
   meaningful than raw sentences; SEG naturally reduces unit count vs per-sentence. Re-measure at SEG-8.

(Removed the earlier "escalation threshold" sub-decision: per decision 2, the subject adds NO large-doc code of
its own, so there is no subject-side threshold to pick.)
