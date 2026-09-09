---
name: generation
description: >
  The grounded-answer method: answer a question using ONLY the supplied evidence, cite the bracketed chunk id
  that supports each claim, abstain rather than guess when the evidence does not support an answer, and hedge
  according to the certainty of graph-derived facts. A single grounded, cited LLM act (FR-C.9 / FR-Q.6). The applying
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
- **Abstain rather than guess.** If the evidence does not support an answer at all, abstain and do not
  fabricate one.
- **Hedge honestly instead of over-answering.** There are three outcomes, not two. When the evidence only
  *partially* or *tangentially* addresses the question -- it mentions related material but does not actually
  state what was asked -- do NOT present that mention as a confident answer. Give only what the evidence
  supports, cite it, and say plainly what the evidence does *not* establish: this is a **partial** answer, not a
  full one. Reserve a full, confident answer for when the evidence genuinely states it. (An unsupported
  confident answer is the one failure to avoid; an honest "the evidence mentions X but does not state Y" is
  correct behavior, not a miss.)
- **Judge each item by its actual text; verify the text really instantiates the concept the question asks
  about.** Do not assume an item answers the question just because it was retrieved. If the text does not match
  the concept asked -- e.g. the question asks for a monetary/maximum liability cap but the text is a
  force-majeure / excused-performance clause, or asks for minimum commitments but the text is research notes,
  definitions, or table fragments -- that item does **not** answer the question. Give only what the text
  genuinely supports and mark `<partial/>`, or abstain if nothing supports it. (A confident answer built from
  off-topic evidence is the exact failure to avoid.)
- **Hedge according to the certainty note, if one is given.** Some questions come with a short certainty note
  flagging that part of the evidence is uncertain or inferred rather than directly stated. When present, present
  the points that depend on such evidence tentatively (an inference, not a settled fact). Never mention certainty,
  confidence, or any internal label to the reader; let it shape only how tentatively you phrase the answer.
- **The answer is prose for a person; keep the engine's markers out of it.** Evidence items carry machine
  markers -- the citation id, `[dimension=value; ...]` typed-property groups, any `[Exception ... (inferred)]`
  framing, and the `<partial/>` marker itself -- that are **inputs to your judgement, not facts to relay**. Never
  repeat, name, quote, or describe them to the reader, and never narrate how certain or uncertain the engine is
  about a fact. In particular, `<partial/>` is a SIGNAL the engine reads to flag a partial answer, never words for
  the reader: emit it (per the abstention rule above) but keep it out of the answer prose -- write the partial
  answer in plain language, not the tag. Let the markers shape only *how confidently* you answer, then state the
  substance in plain language. Quote the clause's **real text** when it helps; never quote the markers. (Citation
  ids are recorded separately for the reader, so you never spell an id out in the answer prose.)
- **State a rule together with its inferred exceptions.** When an evidence item is framed as an exception or
  carve-out to another provision (e.g. "[Exception to the liability cap (inferred)] ..."), do not omit it or
  read it as a separate contradictory fact: answer with the rule AND its exceptions in one breath ("capped at
  X, EXCEPT ... for [the carve-outs]"), citing each, and present the exception as an inference -- so the reader
  sees both the limit and the conditions under which it lifts.

## What this skill does NOT own (the applying capability's job, enforced in CODE)

- **citation validity** -- a citation to an id not present in the evidence is dropped by the capability, not
  trusted from the model;
- **the no-claim-without-a-citation guarantee** (FR-Q.6) -- an answer that ends up with no valid citation is
  coerced to an abstention by the capability;
- **the empty-evidence short-circuit** (abstain without a model call) and the **output contract**
  (`GeneratedAnswer`). The model reads; the code guarantees.
