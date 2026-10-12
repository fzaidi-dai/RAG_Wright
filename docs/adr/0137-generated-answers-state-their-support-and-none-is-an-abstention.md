# ADR-0137: A generated answer states the evidence's support every time, and "none" is an abstention

**Status:** accepted · **Date:** 2026-10-12 · **Related:** ADR-0045 (free text with light tags), PREC-1a
(`answer_kind`), EV-3d (`score_generation`), PS-28

## Context

The answer generator offered `<abstain/>` as an optional alternative output. Asked about something the evidence
does not contain, the model wrote "the provided evidence does not contain X" inside `<answer>`, cited evidence, and
the parser labelled it `answered` (EV-3d, live: 20 of 20 questions asked of contracts that lack the clause). A
product showed an answered, cited result for something that is not there.

## Decision

1. The tag instructions require `<support>full|partial|none</support>` on every response, judged by meaning rather
   than labels (text that serves the purpose asked about answers it even when the question's wording never
   appears). `none` is for when nothing in the evidence answers any part of the question, including a reply that
   the evidence has no such provision; it cites nothing.
2. The parser maps `none` to an abstention that keeps the model's sentence as its reason (scrubbed of ids like any
   prose the reader sees), `partial` to `partial` and `full` to `answered`. A response without the tag parses as
   before (`<partial/>`, else answered).
3. The `generation` skill says the same. No phrase matching on the answer text (English-only, and it would catch
   real answers such as "the agreement does not allow assignment").

Rejected: treating `none` with citations as `partial` (measured: false answers back to 8 of 20).

## Consequences

- Measured once on the EV-3d set (20 answerable, 20 not): false answers 20/20 -> 0/20; false abstentions 0/20 ->
  4/20, all hard cases (gold answers starting mid-sentence, a party named only in a header); citation recall
  0.946 -> 0.758, the old figure inflated by "does not contain" answers whose citations happened to hold the gold.
  Generation varies near the abstain boundary: one run, not a distribution.
- A product sees an abstention, with the model's one-sentence reason in `answer`, where it used to see an answered
  "not found".
