# Engine issue 0041: Leg B discards the best dense matches on some queries — a document's #1 dense span lands at 11, or outside the top 30

**Raised by:** RuleWright (product) · **Date:** 2026-09-12 · **Severity:** high — it makes an answerable
question return "not found" to a user
**Affects:** `subgraphs/typed_property_retrieval.py::production_typed_property_retrieval` (Leg B) ·
`property_boosted_retrieval` (the pool + typed-constraint rerank)
**Not a granularity issue:** measured on a clean post-0040 corpus (68 provisions, 195 spans).

---

## Summary

On some questions Leg B returns spans that do not include the best dense matches, and the product then
abstains on a question the corpus answers. **Abstention is correct behaviour given the evidence it is
handed** (PR-11 — the generator refuses to fabricate); the problem is upstream, in what reaches it.

Measured across seven realistic questions on one contract. For each: rank the spans by **pure dense cosine
over the STORED vectors** (what retrieval searches), then find where Leg B places the dense #1 span.

| question | dense #1 lands at | dense top-5 kept in retrieved top-8 |
|---|---|---|
| How is HOVIONE's total liability capped? | **11** | 4/5 |
| **What are the payment terms?** | **absent from top-30** | **1/5** |
| How long does the confidentiality obligation last? | 1 | 3/5 |
| Under what circumstances may either party terminate? | 4 | 4/5 |
| What insurance must the parties maintain? | 2 | 5/5 |
| Which law governs this agreement? | 2 | 5/5 |
| What warranties does HOVIONE give about the API? | 2 | 5/5 |

**Five of seven are fine** (1, 2, 2, 2, 4). Two are not, and one fails completely. So this is not a
systematic reordering — it is a failure mode that some queries trigger.

## The severe case, in full

Question: **"What are the payment terms?"**

Dense top-5 — three of them are typed `Payment Terms`:

```
0.6309  Payment Terms    'All sums shall be expressed in and payable in US Dollars.'
0.6117  NONE             '2 4.3. Delivery Terms. Each purchase order shall specify:'
0.6066  Payment Terms    'Payments shall be made to HOVIONE by wire transfer.'
0.6062  Confidentiality  'The Parties specifically agree that all terms of this Agreement...'
0.6026  Payment Terms    '4. Price, Orders and Terms of Payment 4.1. Pricing.'
```

What Leg B returns at k=8 — **not one `Payment Terms` clause**:

```
1. NONE              'The terms and conditions of this Agreement shall control over ...'
2. NONE              'If there is a conflict between the terms of any purchase order ...'
3. NONE              'In no event shall any termination or expiration of this Agreement ...'
4. Insurance         '9. Insurance. Unless the Parties otherwise agree in writing, ...'
5. Confidentiality   'The Parties specifically agree that all terms of this Agreement ...'
6. NONE              '4.5. Scope of Agreement. In no event shall any terms or conditions ...'
7. Change Of Control '(a) Buyout. In the case that either company is acquired by, or ...'
8. NONE              'Subject to the terms and conditions of this Agreement, each of ...'
```

Every returned span contains the **word** "terms". The three spans that are actually about payment, and
that dense ranked 1st, 3rd and 5th, are all absent. That looks like the lexical/sparse half of the fusion
dominating a short, generic query whose key token is common across the document.

## The liability case, and why it matters to us

The `cap_quantum` provision — *"THE TOTAL LIABILITY PER YEAR OF HOVIONE SHALL BE LIMITED TO THE VALUE OF
THE REVENUES COLLECTED IN THE PREVIOUS CONTRACTUAL YEAR"* — is **dense rank 1 of 190** in its own document
and lands at **retrieved rank 11**. Our default is `k=8`, so it is cut by three places and the user is told
no clause matched. The clause carrying it has `cap_quantum` at **EXTRACTED** confidence in the KG, so the
typed layer knows the answer while retrieval does not surface its span.

## Hypotheses we ELIMINATED (so they need not be re-run)

| hypothesis | test | result |
|---|---|---|
| the span's ALL-CAPS text hurts the embedding | encode the same sentence lower/sentence-cased | **no** — lowercasing *lowers* similarity, 0.6575 vs 0.7009 |
| the embedding simply misses it | cosine over all 190 stored vectors | **no** — it is **rank 1 of 190** |
| the soft `function` tag misdirects the rerank | retrieve with `clause_type=None`, `'Cap On Liability'`, and the clause's actual label | **no** — rank 11 in all three |
| the pool is too small | `pool_k=100` against 195 spans | **no** — and `pool_k=200` gives the same rank 11 |

## What we are asking

1. **Is the dense/sparse fusion weighting right for short generic queries?** The payment-terms case looks
   like lexical match on a ubiquitous token outvoting every semantic match.
2. **Should a strong dense match be floor-protected** — e.g. the top-N dense always survive into the
   returned set, with fusion deciding order rather than membership?
3. **Does the typed-constraint rerank contribute here?** Passing `clause_type` explicitly changed nothing,
   which surprised us given the capability extracts constraints itself; if a caller-supplied constraint is
   ignored in favour of internally-extracted ones, that is worth documenting either way.

We are not proposing a patch — the fusion weighting is an engine judgement and we have one corpus.

## Reproducing

```python
# rank every span by cosine over the STORED vector, then compare with what Leg B returns
spans  = store._query("SELECT span_id, text, dense FROM Span")
ranked = sorted(((cos(encode_dense(question), s["dense"]), s["span_id"]) for s in spans), reverse=True)
got    = [span_of(j).span_id for j in retrieved_spans(
              await invoke_corpus_retrieval(graph, query=question, documents=None))]
got.index(ranked[0][1]) + 1      # where the best dense match ends up
```

Corpus: INTERSECT ENT / Hovione supply agreement (CUAD
`full_contract_pdf/Part_III/Supply/INTERSECTENT,INC_05_11_2020-EX-10.1-SUPPLY AGREEMENT.PDF`) plus one
small markdown contract, ingested post-0040: 195 spans, 68 provisions.
