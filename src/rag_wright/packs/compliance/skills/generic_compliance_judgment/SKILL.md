---
name: generic_compliance_judgment
description: >
  The DOMAIN-AGNOSTIC compliance-judgment method: given ONE subject (a practice, document, statement, or
  scenario) and ONE applicable regulatory requirement, and seeing only the subject text (never the actor's
  internal records or evidence), decide whether the subject clearly VIOLATES the requirement, clearly SATISFIES
  it, or cannot be judged from the text and must be escalated for human review. Applies to ANY regulatory domain
  (safety, privacy, financial, environmental, advertising, ...). The advertising-specific method
  (`compliance_judgment`) is a SPECIALIZATION of this base method with FTC substantiation/disclosure doctrine;
  this base carries none of that domain doctrine. The applying capability owns the deterministic guarantees
  (verdict vocab, conservative default, both-sided citation) -- this skill teaches only the reading.
---

# Compliance judgment (generic): does this subject satisfy or violate this requirement?

This skill teaches a **method**, not a behavior, and it is **domain-agnostic** -- it works for any regulation,
not one vertical. It extends the grounding-judge idea (ADR-0028) from "is X supported by cue Y?" to "does the
subject X satisfy or violate requirement Y?". A capability applies it with its own contract
(`ComplianceFinding`) and its own guarantees (see "What this skill does NOT own").

## The one hard constraint: you see only the subject text

You are given the requirement and the subject. You **cannot** see the actor's internal records, evidence,
files, or context beyond the text in front of you. So you can only judge what the *subject text itself* shows.
This constraint is the whole reason the verdict is three-way, not two-way.

## The three verdicts

Judge the subject against **this one requirement**, reading the requirement's obligation/prohibition/permission
literally:

- **violation** — reserve this for what is **clearly wrong from the subject text itself**: the text shows the
  required act was **not done** (an obligation the subject plainly failed to meet), or a prohibited act **was
  done**, or a stated condition is plainly **breached**. The breach must be evident in the text, not inferred
  from missing evidence.

- **needs_review** — the requirement plausibly applies, but the subject text **does not show enough** to confirm
  either compliance or breach (the relevant record, evidence, or detail isn't in the text). You cannot verify it
  from the text alone, so **escalate** it: a human will check the actor's records/evidence. Do **not** call this
  a violation (you don't have the proof) and do **not** clear it as compliant (you can't confirm it either).

- **compliant** — the requirement is clearly met **from the text**, or the requirement **does not bite** on this
  subject at all (it governs a different situation than the one the text describes).

## The discipline

Only say **violation** when the text clearly shows the breach; only say **compliant** when the text clearly
clears it (or the requirement plainly does not apply); **otherwise `needs_review`**. Uncertainty is escalation,
never a silent pass. Give a one-sentence rationale grounded in the subject text and the requirement, and a
confidence in [0,1]. Refer to the material you are judging as "the subject" -- never assume a domain.

## What this skill does NOT own (the applying capability's job)

- the verdict **vocabulary** and the **conservative default** -- an unreadable or missing verdict maps to
  `needs_review` deterministically, in the capability, not here;
- the **both-sided citation** -- the exact subject span and the exact requirement clause are attached from the
  INPUTS by the capability, never authored by this skill (the model rules; it never fabricates a citation);
- any **roll-up** of findings and the **human gate**.
