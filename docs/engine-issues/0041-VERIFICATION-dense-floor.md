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

## The follow-on, and a correction to it

End to end through our workspace-ask operation, the liability-cap question at our production `k=8` now
answers **7 times in 10** (10 runs, three sittings) against **0 of 3 before the fix**. The answers are
exactly right and cited: *"HOVIONE's total liability per year is limited to the value of the revenues
collected in the previous contractual year."*

Retrieval itself is **deterministic** -- 4 consecutive runs at k=8 return the cap span every time, always at
position 8 of 8 -- so the residual 3-in-10 is downstream of retrieval, in generation.

**Our first reading of that was that POSITION is the cost** (a floor-reserved span is appended after the
fused ones, the weakest place for a generator reading evidence in order). **We then measured `k` and the
evidence does not support it:**

| k | answer rate, same question | where the cap span sits |
|---|---|---|
| **8** (our default) | **3/3** | position 8 of 8 -- LAST |
| 12 | 3/3 | last |
| 30 | **1/3** | position 11 of 30 -- earlier in the set |

At k=30 the span is placed *earlier* relative to the set and the answer rate *falls*. So the dominant
factor looks like the VOLUME of competing evidence, not the answer's position in it. **Please do not
re-order floor-protected spans on our account** -- we would have asked for the wrong thing.

**It is also question-specific, not a general property of the floor.** At the same k=8 with the floor
active, on the same corpus:

| question | answered | with a citation |
|---|---|---|
| How is HOVIONE's total liability capped? | 7/10 | 7/10 |
| Which law governs this agreement? | **4/4** | **4/4** |
| What insurance must the parties maintain? | **4/4** | **4/4** |

So the floor is not causing abstention in general. Something about this particular question and its evidence
set is harder, and we have not isolated it. We are reporting it so the number is on record, not asking for a
fix -- if it turns out to matter to you, the corpus and the probe are reproducible.

**`dense_floor_n=3` is right for us as the default on this evidence.** Raising `k` is not a workaround we
should reach for: it makes this worse.

## Ask #3, closed

Thank you for the `clause_type` explanation — that it feeds only the relevance judge and never pool
membership or rerank order settles our null result (identical rank with no clause_type, with
`Cap On Liability`, and with the clause's own label). We have no need for a "route retrieval to this clause
type" input today; the typed `(dimension, value)` constraint is the right lever and we will use that where
we need biasing.
