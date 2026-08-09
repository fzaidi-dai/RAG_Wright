# Function-gate ON/OFF graded recall (the ADR-0047 evidence)

**Question:** does the precomputed clause `function` pre-filter (Leg B `span_hybrid_search(function=f)`) buy recall,
or is it redundant given BGE + property rerank?

**Setup:** 57 ACORD attorney-graded queries against the **unified production KG** `ragwright_cuad_full`
(ADR-0046), qrels keyed on canonical `parent_chunk_id`s (`data/eval/acord_unify/acord_prod_qrels.json`, relevant
coverage 100%). Recall unit = clause. Two legs, identical except the function filter:
- **ON** = `span_hybrid_search(function=oracle_fn)` — oracle picks the function covering the most gold clauses, so
  this is the gate's **best case** (its recall ceiling with a perfect query→function classifier).
- **OFF** = `span_hybrid_search(function=None)` — whole-index BGE pool, no function pre-filter.

Harness: `scripts/legb_function_gate_recall.py {raw|rerank}`, POOL_K=400.

## Result

**Raw BGE-hybrid pool recall (no reranker — isolates the gate's pure effect):**

| recall@ | ON (oracle-fn gate) | OFF (whole-index) | Δ (OFF−ON) |
|--------:|--------------------:|------------------:|-----------:|
| 10 | 0.043 | 0.060 | +0.018 |
| 20 | 0.076 | 0.093 | +0.016 |
| 50 | 0.178 | 0.169 | −0.009 |

**Function-reachability CEILING (oracle): 0.969** — even a perfect classifier can only reach 96.9% of relevant
clauses via the single best function bucket; 3.1% of relevant clauses have their spans labeled a *different*
function and are unreachable once you commit to one. Whole-index (OFF) has no such ceiling.

**With BGE cross-encoder rerank (bar context: ACORD recall@50 bar 0.667), running r@50 by checkpoint:**

| queries | ON r@50 | OFF r@50 |
|--------:|--------:|---------:|
| 10 | 0.187 | 0.203 |
| 20 | 0.256 | 0.269 |
| 30 | 0.271 | 0.272 |
| 40 | 0.252 | 0.258 |

(Absolute reranked recall sits below the recipe's full-function-bucket bar because this two-stage harness caps the
first-stage pool at POOL_K spans; the ON/OFF **delta** is unaffected since both legs share that pool.)

## Conclusion

**The precomputed function gate does not improve recall** — ON ≈ OFF within ±0.02 at every K in both modes, with
OFF (whole-index) marginally *ahead*, and the gate imposes a 0.969 recall ceiling that whole-index does not.
Combined with the earlier static audit (only Leg B hard-depends on the label) and the top-8 cited-span probe
(OFF reproduces ON at 7-8/8), this is direct graded-recall evidence to **retire the precomputed clause function**
as a retrieval pre-filter (ADR-0047 / Option B). Dropping the gate also shrinks a mislabel's query-side blast
radius (a mislabeled clause simply doesn't rank for the wrong query, rather than polluting a filtered pool).
