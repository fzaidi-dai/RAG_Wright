---
name: generation
description: >
  The grounded-answer method: answer a question using ONLY the supplied evidence, cite the bracketed chunk id
  that supports each claim, abstain rather than guess when the evidence does not support an answer, and respect
  the confidence tag on any graph-derived fact. A single grounded, cited LLM act (FR-C.9 / FR-Q.6). The applying
  capability enforces the hard guarantees in code (dropping fabricated citations, coercing an uncited answer to
  an abstention) -- this skill teaches only the reading.
---

# Answer generation: grounded, cited, willing to abstain

This skill teaches a **method** for turning retrieved evidence into a final answer that never makes a claim
without a citation. It is a single grounded reading, not a workflow.

## The method

- **Answer using ONLY the evidence below.** Do not use outside knowledge; if a fact is not in the evidence,
  it is not available to you.
- **Cite the bracketed chunk id that supports each claim** in `citations`. Every claim in the answer must be
  traceable to an evidence item by its `[chunk_id]`.
- **Abstain rather than guess.** If the evidence does not support an answer, set `abstained=true` and do not
  fabricate one. A supported partial answer is fine; an unsupported confident answer is not.
- **Respect the confidence tag on a graph-derived fact.** When an evidence item carries a `[confidence: ...]`
  tag (EXTRACTED / INFERRED / AMBIGUOUS), weight it accordingly -- do not assert an AMBIGUOUS fact as settled.
- **State a rule together with its inferred exceptions.** When an evidence item is framed as an exception or
  carve-out to another provision (e.g. "[Exception to the liability cap (inferred)] ..."), do not omit it or
  read it as a separate contradictory fact: answer with the rule AND its exceptions in one breath ("capped at
  X, EXCEPT ... for [the carve-outs]"), citing each, and present the exception as inferred (per its
  `[confidence: INFERRED]` tag) -- so the reader sees both the limit and the conditions under which it lifts.

## What this skill does NOT own (the applying capability's job, enforced in CODE)

- **citation validity** -- a citation to an id not present in the evidence is dropped by the capability, not
  trusted from the model;
- **the no-claim-without-a-citation guarantee** (FR-Q.6) -- an answer that ends up with no valid citation is
  coerced to an abstention by the capability;
- **the empty-evidence short-circuit** (abstain without a model call) and the **output contract**
  (`GeneratedAnswer`). The model reads; the code guarantees.
