# Reply to RuleWright: Laya temperatures and the scikit-learn floor (2026-10-10)

Answers RuleWright's `docs/engine-requests/2026-10-10-laya-temperatures-and-sklearn-floor.md`. The decision record is
ADR-0129. Nothing here is edited in the RuleWright repo.

## 1. The Laya temperatures: not a bug, and labels are unaffected; T-7.23 can proceed

**The values are fitted.** Laya's own fine-tuning script fits one temperature per question type by log-likelihood on
a held-out slice and clips it to [0.1, 10] (`research/scripts/finetune_single_device.py:154`); the Laya runtime
(0.3.22) accepts [0.5, 5] and clamps the rest. The three at (or within 0.002 of) 10.0 hit the training clip: the fit
wanted an even softer scale. So it is a range mismatch between Laya's training script and its runtime, not an
uninitialised value.

**The engine's classifier lane does not gate on the probability.** `_classifier_assertions`
(`rag_wright/packs/contracts/spans/property_extractor.py`) emits the top-k options by rank, abstains when `none` is
among them, and never reads the probability value. One temperature scales every option of a question alike, so it
cannot reorder them. Measured on 300 CUAD paragraphs, every affected checkpoint and dimension, served temperature
(5) against the shipped one, 5,400 paired predictions:

| | result |
|---|---|
| abstain decisions changed | 0 |
| emitted label sets changed | 45 (0.8%), every one between options equal to 4 decimals (Laya rounds probabilities; the sort breaks the tie) |
| primary label changed | 1, on a `damage_type` answer with all five options between 0.17 and 0.22 |
| the same temperature scored twice | identical |

So the clamp does not produce missing labels in the engine's pack, and measuring recall now gives the same answer
it would after any temperature change. **One thing to check on your side:** if your ported pack added a confidence
threshold to this lane, the clamp matters there, because those nine checkpoints' probabilities are up to 0.22
sharper than calibrated.

**What the engine did:** recorded the cause and the measurement (ADR-0129, the reference-pack docs, the `laya`
skill). It did not re-export the nine: a re-export at 5.0 would serve identically and only silence the warning.
Widening Laya's accepted range is Laya's decision; a report to Laya upstream is drafted in the engine repo
(`docs/upstream/laya-temperature-range-mismatch.md`, not yet filed). The `laya` skill now says what to check after
every training run.

## 2. The scikit-learn floor: done, and wider than the three heads

The engine now requires `scikit-learn>=1.9.1`. Eight shipped heads were pickled with 1.9.1, not three: the three
clause-function heads and five property-dimension heads (`cap_basis`, `carve_out`, `covered_parties`, `mutuality`,
`nonsolicit_target`). `tests/models/test_classifier_heads_load_clean.py` now loads every head inside the shipped
model directories with the version warning as an error, so a future skew fails the engine's suite. This ships in the
next engine release; RuleWright's own `>=1.9.1` already matches.
