# ADR-0071: De-fragmentation — reconstruct paragraphs from docling's per-line items

Date: 2026-09-04
Status: Accepted (implemented; DEFRAG-1, bulk-ingestion wall reported by RuleWright)

Refines the reading-order content view (ADR-0069, `content_items`): a clause must not be shattered into per-line
fragments before segmentation.

## Context

RuleWright's bulk CUAD ingest lost 6–10 clauses per run on
`NEONSYSTEMSINC_…DISTRIBUTOR AGREEMENT_Amendment.pdf` and burned minutes on failed retries. First-hand
reproduction showed the "failures" were not clauses: docling emits each PDF **line** as its own `TEXT` item, and
chunk-building joins items with `\n\n`, so `segment_clause` (which splits on `\n+`) shattered a single sentence
into 3–4 fragments —

```
...is made\n\nand entered into as of the 1st day of January, 1999, by and between\n\nPerseus Therapeutics, Inc....
```

— and those partial-sentence fragments (plus page furniture: page numbers, signature blocks) then hard-failed
docling-graph extraction (`No valid JSON returned` → `Pipeline failed at stage: Extraction`) and retried 3×. On a
mid-size born-digital contract that is 24 hard-failures out of 154 "clauses"; a clean contract (BIOAMBER, whose
docling parse already grouped paragraphs) had zero. So it is document-shape-dependent line-wrapping, not the model.

## Decision

Reconstruct paragraphs from the per-line items in `content_items`, before chunking/segmentation sees them.

`_merge_wrapped_lines` merges consecutive `TEXT` items into one paragraph and breaks **only** when the previous
line **ends a sentence** AND the next line **starts one** — a two-sided test:

- `_ends_sentence`: the line ends with terminal punctuation (`. : ; ? !`) plus any closers (`" ' ) ]`).
- `_starts_new_sentence`: the next line's first non-space char is a capital, digit, or opener (`( [ " ' § •`); a
  lowercase start (`and (ii) …`) is a wrapped continuation.

Two-sided is what avoids both failure modes: an abbreviation (`Inc.` / `Corp.`) followed by a lowercase
continuation does **not** false-split (the next line isn't a new sentence), and a clean paragraph-per-item
document is left untouched (each item already ends and the next begins a sentence → no merge). Soft line-breaks
are de-hyphenated (`Distribu-` + `tor` → `Distributor`); ordinary wraps join with a single space. Any NON-`TEXT`
item — heading (`SECTION_HEADER`/`TITLE`/`FIELD_HEADING`), `TABLE`, `PICTURE`, list item, page furniture — is a
hard boundary and is never merged across, so structural boundaries and the ADR-0069 table placement are preserved.

## Consequences

- **Fixed, live-verified.** NEONSYSTEMS: segments **219 → 87**, and granite clause extraction **24 failures → 0**
  (130/154 → 56/56, no retries) — de-fragmentation alone clears the failing contract.
- **Clean docs untouched, live-verified.** BIOAMBER: coherent clauses (median 134 chars, max 932), **0**
  over-merged mega-clauses. The two-sided break does not collapse distinct clauses.
- **Enumerations and headings round-trip.** A merged paragraph is re-split by `segment_clause`'s existing
  enumeration/sentence logic (`(a) … (b) …` reconstructs), and a heading that merges into its body simply folds
  the way the bare-heading rule already folds it.
- **EXTRACT-GUARD-1 demoted.** With the fragments gone, the residual furniture no longer fails at scale, so the
  graceful-degrade/guard task drops from "needed to clear the wall" to a robustness backstop (a furniture span on
  another contract could still hard-fail extraction; the guard remains cheap no-silent-loss insurance).
- **No API/identifier/schema change.** `_merge_wrapped_lines` is internal to `content_items`; the canonical text
  is still the reconstruction from chunks (CU-B1), now with whole sentences instead of per-line fragments.
