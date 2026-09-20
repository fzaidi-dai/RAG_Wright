# Engine issue 0046: appending a natural follow-up to a question drops the clause type, silently degrading a typed sweep to a semantic one

**Raised by:** RuleWright (product), 2026-09-19, found when a golden recording changed under us (T-5a.12h).
**Severity:** silent quality loss on a common phrasing. No error, no warning; the user gets a worse search
and is told only that "no clause type was recognised".
**Path:** `capabilities/query_understanding.understand_query`, reached through our
`engine/seam.understand_question`.

---

## What we see

Four phrasings of one question, against the same corpus, same model, same settings:

| question | `clause_types` |
|---|---|
| *"Which of our contracts cap liability?"* | `['Cap On Liability']` |
| *"Which contracts have a limitation of liability clause?"* | `['Cap On Liability']` |
| *"Which contracts cap liability as a multiple of fees paid?"* | `['Cap On Liability']` |
| **"Which of our contracts cap liability, and at what amount?"** | **`[]`** — `in_taxonomy` false |

Deterministic: five consecutive attempts at the fourth phrasing, five fallbacks. It is not sampling noise.

Adding **", and at what amount?"** — the most natural follow-up a lawyer writes — removes the clause type
entirely. The same four words are what a user appends when they want *more* precision, and they get less.

## Why it matters to us

`AC-13` requires the reading to be shown before a sweep spends anything, and `in_taxonomy: False` means
the sweep degrades to a semantic search. That degradation is correct **as a mechanism** and it is well
signposted in the payload — our product surfaces it. The problem is that it fires on a question the
taxonomy plainly covers.

Concretely, in our harness the worker is told *"the corpus recognises no clause type in that question, so
nothing was searched. This is not an absence"* — so the run continues, semantically, across every
document, and the typed properties (`cap_basis`, and see issue 0045) never participate. A user who asked a
sharper question got a blunter search.

It also bit us in a way worth naming: an eval expectation derived from the typed spans became **empty**
when a re-recording caught this phrasing, and a scorer quietly began passing a blank answer. That is our
bug to guard (we have), but the trigger was this.

## What we think is happening

We have not read the extractor's prompt. From the outside it looks as though the trailing clause shifts
the parse from *"find clauses of type X"* toward *"answer a question about amounts"*, and the clause-type
slot is then left unfilled rather than filled and supplemented. `condition` is populated correctly in the
neighbouring phrasing (*"as a multiple of fees paid"* → `condition: "cap liability as a multiple of fees
paid"`), so the machinery for a qualifier exists; this phrasing just does not reach it.

## What would help

1. **Recognise the clause type and put the remainder in `condition`.** *"cap liability"* → `Cap On
   Liability`; *"and at what amount"* → a condition or simply dropped. This is the behaviour the other
   three phrasings already show.
2. Failing that, **do not let a trailing qualifier clear a clause type that the same sentence supports** —
   a parse that finds a type and then discards it is worse than one that never found it.
3. A regression case for this shape would be welcome: `<clause phrase> + ", and <attribute question>"` is
   an extremely common way to ask.

## Reproduction

```python
from rag_wright.capabilities.query_understanding import understand_query

for q in ("Which of our contracts cap liability?",
          "Which of our contracts cap liability, and at what amount?"):
    print(q, understand_query(q, model_id=<the configured query model>).clause_types)
```

Corpus-independent — this is question parsing, not retrieval. Engine at the commit RuleWright consumes as
of 2026-09-19.
