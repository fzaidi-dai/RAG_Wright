# Engine issue 0038: after 0036, a clause node is created per SENTENCE — 98% of spans become clauses

**Raised by:** RuleWright (product) · **Date:** 2026-09-12 · **Severity:** high — it multiplies ingest cost
5.4x and fills the typed layer with fragments
**Affects:** `spans/segment.py::is_extractable_span` · `spans/segment.py::segment_clause` ·
`subgraphs/contract_ingestion_pipeline.py::clause_extraction_jobs`
**Follows:** issue 0036 / ADR-0101 (which we raised, and which was fixed correctly)
**Not a regression report:** 0036 fixed a real bug. This is about what replaced the gate it removed.

---

## Summary

0036 was right: a ~0.37-accuracy soft tag must not decide what becomes a clause. But removing that gate
left `is_extractable_span` as the only thing standing between a span and a clause node, and
**`is_extractable_span` is a furniture filter, not a provision detector.** On the same contract the 0036
issue was written from:

| | before 0036 | after 0036 |
|---|---|---|
| spans | 183 | 183 |
| **clause nodes** | 29 | **180 (98% of spans)** |
| ingest cost, this document | ~$0.18 | **$0.9677** (4,776 calls, 2.45M tokens, 366s) |

**An 11-page supply agreement does not have 180 provisions.** It has perhaps 40-60.

## The actual finding: a clause is now a SENTENCE, not a provision

Consecutive spans, as segmented, from the confidentiality and indemnification sections:

```
If either Party is required under Applicable Law to disclose Confidential Information by any court ...
The notified Party shall have the right, at its expense, to object to such disclosure and to seek ...
Each party reserves the right to defend itself in any such proceedings; provided, however, that, ...
The Parties shall cooperate with each other to the extent reasonably necessary in the defense of ...
(i) the Product Specifications; (ii) the methods processes and procedures, including the site ...
(i) HOVIONE loses any approval(s) from the US FDA required to perform its obligations under ...
```

Those are **sentences and list items of a few provisions**, each now a separate `Clause` node, each
extracted independently with ~7 thematic tag-parse calls. One confidentiality provision becomes five
clauses, none of which is the provision.

**We believe the old function gate was accidentally doing provision detection.** A classifier tagging a
span "Confidentiality" was, in effect, answering "is this a provision, and which one" — badly (0.37), but
it happened to suppress fragments. Removing it was correct; what it exposed is that nothing else answers
the first half of that question.

## What passes the gate that plainly should not

Deterministic forms, counted over the 180 admitted spans (no model involved — `is_extractable_span` is
pure, so this is reproducible exactly):

| count | form | example |
|---|---|---|
| 26 | shorter than 60 characters | `'Continuing Obligations; Survival.'` |
| 20 | lead-in fragment ending in `:` — the provision is the list that FOLLOWS | `'HOVIONE represents and warrants to INTERSECT that:'` |
| 9 | numbered heading, no verb | `'10. Term and Termination.'` · `'14. General Provisions. Assignment.'` |
| 4 | definition POINTER, defines nothing | `"'Term' shall have the meaning assigned to such term in Section 10."` |
| 3 | notice-block line with the label mid-line | `'Chief Operations Officer email: purchasing@intersectent.com If to INTERSECT, then to:'` |
| **44** | **match at least one of the above (24% of all clause nodes)** | |
| 136 | match none — and 136 is still ~3x the number of real provisions | |

The notice-block row is worth a note: 0036 added a rule for contact labels, but it matches a *bare label
line*. These lines embed `email:` / `Attention:` mid-line, so the rule does not fire.

## Why this is not only a cost problem

**The typed layer fills with fragments that have no properties to extract**, and the model supplies
something anyway. In our post-fix corpus, `contract_terms` serves **1,122 AMBIGUOUS against 332 EXTRACTED**
properties — 77% ambiguous — and the ambiguous tail is visibly mis-categorised:

- `covered_subject`: `'HOVIONE INTER AG'`, `'INTERSECT ENT'`, `'Inc.'` — party names, not subjects
- `damage_type`: `'REPRESENTATION'`, `'WARRANTY'`, `'WARRANTIES OF MERCHANTABILITY'` — not damage types
- `carve_out`: 330 AMBIGUOUS against 21 EXTRACTED

Some of that is the model over-extracting (your 0037 handoff invites exactly this feedback, and we are
giving it). But a fragment like `'(i) the Product Specifications;'` has no clause properties by
construction, so asking seven thematic questions of it can only produce noise. **Fewer, better-bounded
clauses would improve precision and cost at the same time.**

## What we are asking

We are not asking for the 0036 gate back. The question is what constitutes a clause:

1. **Should a `Clause` be a PROVISION rather than a span?** The natural unit looks like the numbered
   section (`10.5. Obligations on Termination`) with its sentences and list items merged, extracted once.
   That would cut clause count toward the real provision count, cut cost by the same factor, and give the
   extractor whole provisions — which is what the thematic questions assume.
2. **If segmentation must stay sentence-level**, can contiguous spans under one heading be MERGED before
   `clause_extraction_jobs`, so extraction is per provision even though retrieval stays per span?
3. **Failing both, `is_extractable_span` needs to decline the forms above.** We would rather have (1) —
   these rules are a tourniquet, and 136 spans still pass them.

We are happy to run any A/B you want on our side; we have the document, the before corpus and the after
corpus, and the probes are deterministic.

## Reproducing

`is_extractable_span` is pure, so no ingest is needed to see the admission rate:

```python
from rag_wright.spans.segment import is_extractable_span
admitted = [t for t in span_texts if is_extractable_span(t)]   # 180 of 183 on this contract
```

The document is the INTERSECT ENT / Hovione supply agreement (CUAD
`full_contract_pdf/Part_III/Supply/INTERSECTENT,INC_05_11_2020-EX-10.1-SUPPLY AGREEMENT.PDF`), the same one
issue 0036 was measured on.
