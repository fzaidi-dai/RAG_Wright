# Issue 0041 verification: the dense floor works — 7/7, and one follow-on observation

**From:** RuleWright (product) · **Date:** 2026-09-12 · **Engine:** `8013b40` (ADR-0104), `dense_floor_n`
default 3
**Corpus:** the post-0040 build you asked for — INTERSECT/Hovione + one small markdown contract,
**195 spans, 70 clauses**, unchanged since 0041 was filed (verified before re-testing)
**Caches:** none cleared, deliberately — the retrieval path is file-cache-free (checked), the corpus was not
re-ingested, and clearing the ingest caches would have cost a rebuild without changing anything measured.

---

## Both conditions hold

The exact 7-query probe from the issue. `k=8` is our production default; `k=30` matches the pre-fix study.

| question | before (k=30) | after, k=8 | after, k=30 | dense top-5 in top-8 |
|---|---|---|---|---|
| How is HOVIONE's total liability capped? | 11 | **8** | 11 | 5/5 |
| **What are the payment terms?** | **absent from top-30** | **6** | **28** | 4/5 |
| How long does the confidentiality obligation last? | 1 | 1 | 1 | 4/5 |
| Under what circumstances may either party terminate? | 4 | 4 | 4 | 4/5 |
| What insurance must the parties maintain? | 2 | 2 | 2 | 5/5 |
| Which law governs this agreement? | 2 | 2 | 2 | 5/5 |
| What warranties does HOVIONE give about the API? | 2 | 2 | 2 | 5/5 |

- **(a) dense #1 reaches the returned set on 7/7**, at both k.
- **(b) the five working queries are unchanged** — 1, 4, 2, 2, 2, identical before and after, at both k.

`dense_floor_n=3` looks right as the default on this corpus. The floor only engages where a span would
otherwise be cut: at k=30 the two problem queries sit at their natural fused ranks (11, 28) because 30 slots
are enough; at k=8 they are reserved in at 8 and 6.

**Retrieval is now deterministic**: 4 consecutive runs at production defaults (k=8, pool_k=30) return the
cap span every time, always at position 8.

## The follow-on: membership is fixed, position is the remaining cost

End to end through our workspace-ask operation, same question, three runs: **2 answered, 1 abstained** —
against **0 of 3 before the fix**. The two that answered gave exactly the right answer ("HOVIONE's total
liability per year is limited to the value of the revenues collected in the previous contractual year").

Since retrieval is 4/4 deterministic, the residual abstention is **downstream of retrieval**: the generator
reads eight spans of which the answer is the **last**, and sometimes abstains anyway. That is not a
retrieval bug and we are not asking you to fix it as one — but it is the practical consequence of
"fusion decides order, dense guarantees membership":

**A floor-reserved span currently lands at the END of the returned set**, which is the weakest position for
a generator reading evidence in order. If a floor-protected span could be placed at its DENSE rank rather
than appended after the fused ones, the evidence a generator sees first would be the strongest semantic
match. We do not know whether that is desirable in general — it would let dense override fused order, which
is more than membership — so we are reporting it rather than requesting it.

Worth knowing either way: with `dense_floor_n=3` the product now returns the correct cited answer most of
the time on a question that previously always said "no clause matched".

## Ask #3, closed

Thank you for the `clause_type` explanation — that it feeds only the relevance judge and never pool
membership or rerank order settles our null result (identical rank with no clause_type, with
`Cap On Liability`, and with the clause's own label). We have no need for a "route retrieval to this clause
type" input today; the typed `(dimension, value)` constraint is the right lever and we will use that where
we need biasing.
