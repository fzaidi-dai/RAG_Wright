# Engine issue 0039 (follow-up to 0038): the provision detector reads `text`, but docling puts the section number in `marker`

**Raised by:** RuleWright (product) · **Date:** 2026-09-12 · **Severity:** high — it is the whole difference
between 15 and 71 provisions on the document 0038 was verified against
**Affects:** `spans/segment.py::starts_new_provision` · `subgraphs/contract_ingestion_pipeline.py::clause_extraction_jobs`
**Follows:** issue 0038 / ADR-0103, which is correct. This is an integration defect in front of it.
**Reproducible with ZERO model calls** from a cached parse — see the bottom.

---

## Summary

0038 is right and we are not asking for any of it back. But running it end to end on **the same document
the handoff reports (INTERSECT/Hovione supply agreement)**, we get **15 provisions where you measured 71**,
and property precision does not recover: **52 EXTRACTED : 216 AMBIGUOUS (80% ambiguous)** where the handoff
predicts mostly EXTRACTED.

The cause is a field mismatch, and your 71 is exactly right — it is the number of section markers in the
document.

**Docling emits a numbered provision as an enumerated list item and moves the number OUT of `text`:**

```json
{
  "orig":       "1.1. 'Active Pharmaceutical Ingredient' or 'API' shall have the meaning given such term in the preamble hereof.",
  "text":       "'Active Pharmaceutical Ingredient' or 'API' shall have the meaning given such term in the preamble hereof.",
  "enumerated": true,
  "marker":     "1.1."
}
```

`starts_new_provision` reads `text`. The number is not there. So on a document whose provisions are all
properly numbered, the detector sees **almost no headings**, every provision boundary falls back to the
chunk boundary, and a "provision" becomes a whole chunk.

## The measurement

Same parse, one line of difference:

| `starts_new_provision` applied to | headings detected |
|---|---|
| `text` — what the pipeline passes | **2** |
| `orig` — the number retained | **71** |

And in the parse itself:

| | |
|---|---|
| text nodes | 128 |
| `enumerated: true` with a `marker` | **71** |
| ... whose marker is a section number (`1.1.`, `10.5.1.1.`) | **71** |

**71 markers, 71 provisions.** Your figure is the right one; we produce 15 because the field the detector
reads has been stripped of the very thing it looks for.

## What we actually get, end to end

A clean run: parse cache cleared for this document, clause cache cleared, through
`aproduction_document_ingest` with our production arguments (`extract_model` / `graph_extract_model` /
`judge_model` / `chunk_model` = `qwen3.8-27b-modal-or`, `list_model="off"`, `samples=3`,
`classify_fn=production_batch_clause_classifier` on the same model).

| | pre-0036 | after 0036 | after 0038, **engine** | after 0038, **us** |
|---|---|---|---|---|
| spans | 183 | 183 | 183 | **183** |
| clause nodes | 29 | 180 | **71** | **15** |
| EXTRACTED : AMBIGUOUS | — | 332 : 1122 | mostly EXTRACTED | **52 : 216 (80%)** |

Cost is not the complaint (440 calls, $0.1235, 72s — under the pre-0036 baseline). **The typed layer is.**

**The precision failure has the same root.** 0038's argument is that a fragment has no properties to
extract, so seven thematic questions of it can only produce noise. A whole CHUNK spanning several
unrelated provisions is the same problem from the other side: asking "what is the cap basis" of a block
containing definitions, forecasting and delivery yields a value that belongs to none of them. Our 80%
ambiguous is what 0038 predicts for the wrong granularity — we simply landed on the coarse side of it
rather than the fine side.

## Why the span count matches and the clause count does not

Worth stating, because it narrows the fix: **segmentation and retrieval are untouched and identical** (183
spans, both sides). Only the grouping differs, and grouping is deterministic over those spans. So nothing
upstream needs to change — the detector is being handed the wrong string.

## What we are asking

`starts_new_provision` (or its caller) should see the section number when docling has one:

1. **Preferred:** consider docling's `marker` / `enumerated` fields — an `enumerated` node whose `marker`
   is a section number IS a provision start, without any text matching at all. That is stronger than the
   current regex, not weaker, and it is model-free.
2. **Or:** pass `orig` rather than `text` to the detector (keeping `text` as the extracted content), which
   we verified recovers all 71.
3. Either way, a note in the handoff for integrators whose parse path may differ.

We have not changed anything on our side and are not proposing a patch — where this belongs (the detector,
the caller, or the span record) is an engine judgement.

## Reproducing — no model, no ingest

From any cached docling parse of a numbered contract:

```python
import json, re
from rag_wright.spans.segment import starts_new_provision

doc = json.loads(open("<parse>.json").read())
nodes = [t for t in doc["texts"] if t.get("enumerated") and
         re.match(r"^\d+(\.\d+)*\.?$", str(t.get("marker", "")).strip())]

sum(starts_new_provision(" ".join(t["text"].split())) for t in nodes)   # 2  <- what the pipeline sees
sum(starts_new_provision(" ".join(t["orig"].split())) for t in nodes)   # 71 <- the real provision count
len(nodes)                                                             # 71
```

Document: INTERSECT ENT / Hovione supply agreement, CUAD
`full_contract_pdf/Part_III/Supply/INTERSECTENT,INC_05_11_2020-EX-10.1-SUPPLY AGREEMENT.PDF` — the same one
0036 and 0038 were measured on.
