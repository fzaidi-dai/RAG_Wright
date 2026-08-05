---
name: compliance_judgment
description: >
  The advertising-compliance judgment method: given ONE advertising claim and ONE applicable regulatory
  requirement, and seeing only the ad text (never the advertiser's evidence files), decide whether the claim
  clearly violates the requirement, clearly satisfies it, or cannot be judged from the text and must be
  escalated for human review. Applied by the compliance_check subgraph (query side). The applying capability
  owns the deterministic guarantees (verdict vocab, conservative default, both-sided citation) -- this skill
  teaches only the reading.
---

# Compliance judgment: does this claim satisfy or violate this requirement?

This skill teaches a **method**, not a behavior. It extends the grounding-judge idea (ADR-0028) from
"is X supported by cue Y?" to "does claim X satisfy or violate requirement Y?". A capability applies it with
its own contract (`ComplianceFinding`) and its own guarantees; those guarantees are the **applying
capability's** job, not the method's (see "What this skill does NOT own").

## The one hard constraint: you see only the ad text

You are given the requirement and the claim. You **cannot** see the advertiser's studies, substantiation
files, or evidence. So you can only judge what the *text itself* shows. This constraint is the whole reason the
verdict is three-way, not two-way.

## The three verdicts

- **violation** — reserve this for what is **clearly wrong from the text itself**:
  1. the claim **overclaims proof** — it asserts it is "clinically proven", "scientifically proven", "science
     backed", "doctor proven", or "guaranteed" **without pointing to an actual study or data** (the
     proof-language *is* the unsubstantiated claim, not evidence for it);
  2. an endorsement is **missing a required disclosure** — no "#ad" / "paid partnership" is present when a
     material connection would need disclosing;
  3. a review or testimonial is **fake or deceptive**.

- **needs_review** — an **objective** efficacy / health / performance / factual claim that may well be true, but
  the ad shows **no evidence** and makes **no overclaim**. You cannot verify its substantiation from the text
  alone, so **escalate** it: a human will check the advertiser's substantiation file. Do **not** call this a
  violation (you don't have the evidence), and do **not** clear it as compliant (you can't confirm it either).

- **compliant** — the requirement does not bite, because one of:
  - the claim is mere **subjective opinion or taste/experience puffery** ("smooth flavor", "relaxing", "I like
    it") — there is nothing objective to substantiate;
  - the required **disclosure is present** (see the claim's `disclosures_present`, e.g. "#ad");
  - the ad **actually points to real evidence** (a specific study / data / citation) for the objective claim;
  - there is **no objective claim** to substantiate.

## The discipline

Only say **violation** when the text clearly shows the breach; only say **compliant** when the text clearly
clears it; **otherwise `needs_review`**. Uncertainty is escalation, never a silent pass. Give a one-sentence
rationale and a confidence in [0,1].

## What this skill does NOT own (the applying capability's job)

- the verdict **vocabulary** and the **conservative default** — an unreadable or missing verdict maps to
  `needs_review` deterministically, in the capability, not here;
- the **both-sided citation** — the exact claim span and the exact requirement clause are attached from the
  INPUTS by the capability, never authored by this skill (the model rules; it never fabricates a citation);
- the **ad-level rollup** (how many findings make an ad a violation) and the **human gate**.
