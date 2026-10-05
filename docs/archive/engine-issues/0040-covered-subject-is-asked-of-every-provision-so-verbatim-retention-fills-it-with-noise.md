# Engine issue 0040 (on 0037): `covered_subject` is asked of every provision, so verbatim retention fills it with noise — 354 AMBIGUOUS : 1 EXTRACTED

**Raised by:** RuleWright (product) · **Date:** 2026-09-12 · **Severity:** high — this dimension is not
shippable to a user in its current state
**Affects:** `ontology/clause_template.py::Subject` · `spans/clause_kg_extractor.py::_open_list_value`
(issue 0037 / ADR-0102) · the thematic group that asks `covers`
**Related:** 0037 (verbatim retention, which we asked for and still want), 0036/ADR-0101 (the aspect gate
removal, which we think is half the cause)
**Measured on:** a clean post-0039 ingest — all three caches cleared, `aproduction_document_ingest`,
68 provisions, the INTERSECT/Hovione contract

---

## Summary

0036 → 0039 all land, and we are asking for none of them back. Granularity is now right (68 provisions
against your 65) and the closed-vocabulary dimensions behave exactly as the handoff predicts —
`mutuality` **37 EXTRACTED : 14 AMBIGUOUS**, `party_asymmetry` **30 : 22**.

**`covered_subject` does not.** On the same run:

| dimension | EXTRACTED | AMBIGUOUS |
|---|---|---|
| `covered_subject` | **1** | **354** |
| `carve_out` | 12 | 186 |
| `damage_type` | 4 | 8 |
| `mutuality` | 37 | 14 |
| `party_asymmetry` | 30 | 22 |

355 assertions across 68 provisions — **262 DISTINCT values**, on **67 of 68 clauses**. Exactly one ever
matched the vocabulary, and it was `fraud`.

The most frequent values are the contract's defined terms and its parties:

```
 23  'API'                     6  'HOVIONE'            3  'Quality Agreement'
 11  'Agreement'               5  'INTERSECT'          3  'expenses'
  9  'Finished Product'        5  'Product Specifications'   3  'costs'
```

No user can be shown this. "This clause covers: API, Agreement, HOVIONE, expenses" is not a fact about a
contract.

## Why we think it happens

`Subject` has **seven** members:

```python
IP_INFRINGEMENT · TRADEMARK · COPYRIGHT · VIOLATION_OF_LAW · FRAUD · GROSS_NEGLIGENCE · OTHER
```

That is a **liability/indemnity carve-out conduct** vocabulary. It answers "what conduct survives the cap"
— a question that is meaningful for an indemnity or limitation-of-liability provision and meaningless for a
forecasting, delivery or notices provision.

But the thematic group carrying `covers` is asked of **every** provision, and the model answers the
question it was given. Asked "what does this provision cover?" of a forecasting clause, `API` and
`Purchase orders` are reasonable English answers and useless ontology.

**Two recent changes compound**, and we do not think either is wrong on its own:

1. **The aspect gate was removed** (0036/ADR-0101) because it dropped ~18% of properties, concentrated in
   carve-outs. Correct call — but it also means every thematic group now runs on every provision, including
   this one where the vocabulary does not apply.
2. **0037 retains novel values verbatim** instead of collapsing them to `OTHER` and discarding them. Also
   correct, and we asked for it — but it is what makes the noise *visible and served*. Before 0037 these
   354 values collapsed to `OTHER` and vanished.

**So 0037 did not create this; it revealed it.** The extraction was always this noisy — it was being thrown
away. We would rather know. But the dimension cannot be surfaced as it stands.

## What is NOT the problem

- **Not granularity.** This is measured on the post-0039 build at 68 provisions, not on fragments. The
  clauses are whole provisions and the closed-vocab dimensions are clean on the same data.
- **Not verbatim retention as a mechanism.** `carve_out` at 12:186 is defensible — that dimension is
  genuinely open, the tail is real carve-out language, and preserving it is the point of 0037.
  `damage_type` at 4:8 is fine.
- **Not a vocabulary gap.** Adding `skos:broader` synonyms cannot help: `API` and `HOVIONE` are not
  under-specified subjects, they are answers to a different question.

## What we are asking

Precision on this dimension, by whichever of these you judge right:

1. **Scope the question to where the vocabulary applies.** `covers` asked only of provisions that are
   plausibly indemnity/liability-shaped. This is the aspect gate's job, which was removed for good reasons
   — so perhaps a per-dimension applicability rule rather than a model-driven gate, or keying it off the
   clause's own function where one is present.
2. **Constrain the prompt to the conduct sense.** "Which of these conduct types does this provision carve
   out?" rather than "what does this provision cover?" — with an explicit "none" being the common answer.
3. **Make verbatim retention conditional on plausibility** for this dimension: retain a novel value only
   when it is in the conduct register, else drop as before. Narrower than (1) or (2) and preserves 0037
   everywhere it is working.

We have no opinion on which — (1) and (2) look like the real fixes and (3) like a containment. What we
cannot do is surface a dimension that is 99.7% noise.

## Reproducing

Post-0039 ingest of the INTERSECT/Hovione contract (CUAD
`full_contract_pdf/Part_III/Supply/INTERSECTENT,INC_05_11_2020-EX-10.1-SUPPLY AGREEMENT.PDF`), then:

```python
from collections import Counter
vals = Counter(p.value for c in contract_clause_index(store, contract_id)
               for p in c.properties if p.dimension.value == "covered_subject")
len(vals)            # 262 distinct across 68 provisions
sum(vals.values())   # 355 assertions, 354 of them AMBIGUOUS
```

**Caches matter.** A clean measurement needs the parse cache, the **chunk** cache and `clause_extract/`
all cleared — clearing only the parse re-parses and then reads chunks built before the fix. That cost us a
wrong report on 0039 before we caught it.
