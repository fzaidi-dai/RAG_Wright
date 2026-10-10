# ADR-0129: The shipped Laya temperatures stay as fitted; scikit-learn floor at 1.9.1

**Status:** accepted · **Date:** 2026-10-10 · **Related:** ADR-0116 (Laya property classifiers), ADR-0119 (decision
models), PS-5 (reference weights on GCS)

## Context

RuleWright reported (its engine request of 2026-10-10) two findings in the shared reference weights:

1. Laya 0.3.22 warns on loading 9 of the 15 Laya checkpoints: their `choice` temperature (`temperature[0]`, from
   5.64 to 10.0) is outside the runtime's accepted [0.5, 5], so it is clamped to 5 and the probabilities are
   "uncalibrated".
2. Eight shipped SetFit heads (the three clause-function heads, five property-dimension heads) were pickled with
   scikit-learn 1.9.1 while the engine declared and ran 1.9.0, so each warns on load (`InconsistentVersionWarning`).

## Findings

- **Cause of the temperatures.** Laya's own fine-tuning script (`research/scripts/finetune_single_device.py:154`)
  fits one temperature per question type by log-likelihood on a held-out slice and clips it to [0.1, 10]; the
  runtime accepts [0.5, 5]. The values are fitted, not left uninitialised; the three at 10.0 hit the training clip.
  The mismatch is between Laya's training script and its runtime.
- **Effect on labels.** The reference pack's classifier lane (`_classifier_assertions`) emits the top-k options by
  rank and abstains when `none` is among them; it never reads the probability value. One temperature scales every
  option of a question alike, so it cannot reorder them. Measured on 300 CUAD paragraphs, every affected
  checkpoint and dimension, served (5) vs shipped temperature, 5,400 paired predictions: 0 abstain decisions
  changed; 45 emitted label sets (0.8%) differed, every one between options equal to 4 decimals (Laya rounds its
  probabilities, and the sort breaks the tie); 1 primary label changed, on a `damage_type` answer with all options
  between 0.17 and 0.22. Scoring twice at one temperature is identical.
- **Effect on probabilities.** For these 9 checkpoints the served probabilities are up to 0.22 sharper than the
  fitted calibration, so they are overconfident. Nothing in the engine reads them.

## Decision

1. The nine checkpoints are not re-exported; the cause and the measured effect are recorded here, in the reference
   pack's documentation and in the `laya` skill. A re-export at temperature 5.0 would serve identically and only
   silence the warning; widening the accepted range is Laya's decision, not the engine's. A report to Laya is
   drafted (`docs/upstream/laya-temperature-range-mismatch.md`, not filed).
2. A product that gates on these classifiers' probabilities must not treat them as calibrated (or must refit within
   the runtime's range).
3. The engine requires `scikit-learn>=1.9.1`, the version the shipped heads were pickled with, and
   `tests/models/test_classifier_heads_load_clean.py` fails when any shipped head warns on load.

## Consequences

- Labels from the reference fleet are as they were; the warning on loading those 9 checkpoints remains.
- A future training run checks `rl_agent_config.json` against the serving runtime's temperature range, and the
  training image's scikit-learn version is the engine's floor (or the floor moves with it).
