# Engine issue: unify subject preprocessing with the ingestion front-end

**Raised by:** engine (self-originated, approved by the product owner 2026-08-30) · **Component:**
`subgraphs/compliance_check.py` (the two subject verdict entrypoints + the three facts producers),
reusing `corpus/document_parser.py` and `spans/segment.py` · **Type:** consolidation / capability ·
**Continues:** issue 0008 (subject document per-section), issue 0010 (subject text per-sentence) ·
**Contract touch:** `contracts/compliance.py::CheckableFact` (one optional field — ask-first)

---

## The problem

We accept a compliance **subject** two ways, and they preprocess it differently — an evolutionary accident,
not a principled split. All the pieces to do it uniformly already exist; they are just wired into two
entrypoints at two granularities.

| entrypoint | input | segmentation today | citation locator | sentence precision |
|---|---|---|---|---|
| `run_generic_compliance_verdict` | `subject_text: str` (paste) | per-**sentence** (`sentence_facts_fn`, 0010) | **none** — `fact_id = make_id(source, i, sentence)` | yes |
| `run_compliance_document_verdict` | `data: bytes` (upload) | per-**section** (`document_facts_fn`, 0008) | section (heading folded into `assertion_text`) | **none** |

So a pasted document cites the sentence but has **no section locator**; an uploaded document cites the
section but has **no sentence precision**. Neither gives the thing a multi-page subject actually needs —
*"§ 4.2 → the offending sentence"* — and a caller must pick the entrypoint by input type, not by intent.

The ingestion pipelines already solved the "parse-and-segment with a citable locator" problem. The subject
side should reuse that front-end, because for a subject **parse-and-segment is identical to ingestion**; only
the tail differs (ingestion extracts rules to persist; a subject makes ephemeral `CheckableFact`s to judge).

## What is already grounded (no new parsing surface needed)

- **Docling already ingests plain text.** `parse_document_bytes` (document_parser.py:44) wraps `.txt`/`.md`
  bytes directly and routes binaries (PDF/DOCX/HTML) through docling; `run_compliance_document_verdict`
  already lists `TXT`. A `.txt` upload flows through the existing bytes path today.
- **The two building blocks compose.** `document_to_sections(doc) -> [{section, heading, text}]`
  (document_parser.py:81) gives the locator; `segment_clause(source, text) -> [Span]` (spans/segment.py, the
  same splitter `sentence_facts_fn` uses) gives the precise sentence span. A unified producer is just
  *"for each section, segment its text into sentences."*
- **The subject is transient** — parsed, segmented, judged, discarded. No KG persist, no rule extraction, no
  embedding/store. We reuse steps 1–2 of the ingestion front-end (parse + heading-split) and drop steps 4–5.

## The design

**One subject front-end, one hierarchy (section locator → sentence span):**

```
subject_facts(source, *, text=None, name=None, data=None) -> [CheckableFact]:
    if data is not None:                                   # upload (PDF/DOCX/HTML/TXT bytes)
        sections = document_to_sections(await aparse_document_bytes(name, data))
    else:                                                  # paste (plain text)
        sections = [{"section": "1", "heading": "", "text": text}]   # short-circuit, no docling round-trip
    for sec in sections:                                   # LOCATOR = sec["section"] (+ heading)
        for sentence in segment_clause(source, sec["text"]):   # precise SPAN
            yield CheckableFact(..., section=sec["section"], assertion_text=sentence.text)
```

This **subsumes all three current producers** as points on one dial, instead of three parallel functions:
- whole-subject (`generic_facts_fn`) = don't segment,
- per-section (`document_facts_fn`, 0008) = stop at the section,
- per-sentence (`sentence_facts_fn`, 0010) = segment, **but now also carrying the section locator** — the
  capability neither current producer has.

Plain text degrades gracefully: no headings → one section → straight to sentences, i.e. **exactly today's
0010 behavior, losing nothing**, while an upload gains the section locator it lacks today.

## Two layers, not one — and where the advertising path fits

The front-end must be factored as **two layers**, because only the first is universal:

1. **Shared parse/segment layer** — `(text | name+data) -> sections` (docling parse + heading-split, or the
   plain-text short-circuit). Belongs to EVERY compliance path.
2. **Path-specific producer tail** — sections → judged units. This is where the paths diverge:
   - generic (text/document): deterministic **sentence `CheckableFact`s** (`subject_facts_fn`).
   - **advertising**: an **LLM claim extractor** (`aclaim_extraction`) producing typed **`Claim`s**
     (`claim_type`, `disclosures_present`, …) that drive the ad applicability routing (`applies_to`). The ad
     path must KEEP this tail — sentence facts would discard the typed-claim structure it routes on.

**The advertising path today (grounded):** `run_compliance_check` takes `subject_text: str` ONLY — no bytes,
no parse. So an uploaded ad (PDF flyer, DOCX) cannot be checked via the ad path without the caller parsing it
first; it has neither the upload support nor the sectioning issue 0008 gave the generic path.

**With the two-layer factoring, the ad path reuses layer 1 and keeps layer 2.** Routing the ad entrypoint
through the shared parse layer gives it, near-automatically: (a) upload support (parse PDF/DOCX/HTML/TXT), and
(b) optional **per-section** claim extraction (parallel, scale-friendly, mirroring the generic document path
and ingestion) instead of extraction over one blob. It is NOT automatic just by building the generic API —
the ad checker is a separate `str`-only function and must be explicitly routed through layer 1 (task UNIFY-F).
This keeps the ad path from being the half-fix left behind.

## The one ask-first decision: a first-class section locator on `CheckableFact`

`CheckableFact` today = `{fact_id, source_doc, assertion_text, doc_start?, doc_end?, confidence}` — **no
section field**. The finding cites `f"{source_doc}: {assertion_text}"`, so for a finding to read
*"§ 4.2 → 'the offending sentence'"* the section must be first-class, NOT folded into `assertion_text`
(folding the heading into every sentence, as `document_facts_fn` does per-section, would pollute the cited
sentence text — noisy and wrong at sentence granularity).

**Proposed (ask-first, per CLAUDE.md "changing the data model or schema"):** add `section: str | None = None`
to `CheckableFact`, and have `assemble_finding` include it in `citation_claim` when present. Additive,
optional, back-compatible (existing producers leave it None → today's citation unchanged). The existing
`doc_start`/`doc_end` can additionally carry the sentence's char offsets for span-level provenance.

Alternative if we do NOT want a contract change: keep the locator out of the fact and thread it through a
parallel structure — messier, and it splits the provenance across two places. Recommendation: the additive
field.

## Task breakdown (build after this write-up is approved)

- **UNIFY-A (contract, ask-first):** add optional `section: str | None` to `CheckableFact`; `assemble_finding`
  includes it in the citation when present. Tests: citation shows "§ …" when set, unchanged when None.
- **UNIFY-B (producer):** `subject_facts_fn` — the unified section→sentence producer above (reuses
  `document_to_sections` + `segment_clause`), each fact carrying `section` + clean sentence `assertion_text`.
  Tests: multi-section doc → per-(section, sentence) facts with the right locators; abbrev/decimal-safe.
- **UNIFY-C (entrypoint consolidation):** one subject front-end accepting either `text=` (paste) or
  `(name=, data=)` (upload). Keep `run_generic_compliance_verdict` / `run_compliance_document_verdict` as thin
  back-compat shims (or deprecate — decide at build time). The `facts_fn` seam stays as the granularity
  override (whole / section / sentence).
- **UNIFY-D (plain-text short-circuit):** a paste becomes one headingless section directly (no docling
  round-trip); a `.txt`/binary upload uses the bytes path. Test both reach the same producer.
- **UNIFY-E (live):** real end-to-end on a multi-section subject (upload) and a paste — confirm findings cite
  "§ N → sentence", and the paste path matches today's 0010 output.
- **UNIFY-F (advertising path — don't leave it a half-fix):** route the ad entrypoint (`run_compliance_check`)
  through the SHARED parse layer so it accepts `(text | bytes)`; the ad claim extractor stays the tail
  (typed `Claim`s preserved), optionally run PER-SECTION (parallel) over the parsed sections. Test: an uploaded
  ad (bytes) is parsed and claim-extracted; the typed-claim routing (`applies_to`/`claim_type`) is unchanged.

## Open questions to settle at build time

1. **Cost.** Section×sentence×`min(k,#requirements)` is the 0010 cross-product, now also on the upload path.
   Reuse ADR-0063's noted levers (cap fact count / paragraph fallback / lower `k`) if it bites; the `facts_fn`
   seam already admits them.
2. **Back-compat.** Keep both public entrypoints as shims, or migrate callers (RuleWright) to the unified one?
   Product-facing, so coordinate with RuleWright before removing either.
3. **Section granularity of the locator.** `_section_number` derives "§ 4.2" from the heading's leading numeric
   token, else the 1-based position — fine for structured docs; a headingless multi-paragraph paste collapses
   to one section "1". Acceptable (matches 0010), but worth naming.
