# RuleWright: moving to rag-wright 0.8 (in progress)

What the release after 0.7.0 changes for RuleWright. Do the 0.3 to 0.7 guides first. Nothing here is edited from
the engine repo. This guide grows with the release; each section names its task.

## 1. A "not in the evidence" answer is now an abstention (PS-28, ADR-0137)

**What changed.** `agenerate_answer` used to return `answer_kind="answered"` (or `"partial"`), with citations, when
the model's answer was that the evidence does not contain what was asked. It now returns `answer_kind="abstained"`
(`abstained=True`, no citations) and keeps the model's one-sentence reason in `answer`, e.g. "The provided evidence
does not contain any provisions regarding a minimum commitment."

**What to do in RuleWright.**
- Where the UI shows an abstention, show `answer` as the reason when it is not the generic abstention text, instead
  of a fixed "no answer" message.
- Any count of answered questions, or any check that treats "answered" as "found", now excludes these: re-baseline
  such numbers after upgrading.
