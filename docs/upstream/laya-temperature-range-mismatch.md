# DRAFT, not filed: Laya issue on the temperature range mismatch

A draft issue for the Laya repository (https://github.com/NandhaKishorM/laya). Not filed; filing it is a separate,
explicit step. It names no customer, product or dataset beyond what is public. Background: ADR-0129.

---

**Title:** `finetune_single_device.py` fits temperatures in [0.1, 10] but the runtime only serves [0.5, 5]

**Summary.** The research fine-tuning script fits each question type's temperature by NLL and clips it to
[0.1, 10]. The runtime accepts [0.5, 5] (`laya/common.py`, `TEMP_MIN = 0.5`, `TEMP_MAX = 5.0`) and clamps anything
outside, with a load-time `RuntimeWarning` ("this checkpoint ships invalid temperatures or values outside [0.5, 5]
... Treat confidence from the affected entries as uncalibrated"). So a checkpoint produced by the documented
training path can load with a temperature the runtime will not serve.

**Where.**

- Training: `research/scripts/finetune_single_device.py`, line 154 (at the 0.3.22 commit, `6d942c9`):
  `return float(torch.clamp(log_t.exp(), 0.1, 10.0).item())`
- Runtime: `laya/common.py`, `TEMP_MIN = 0.5`, `TEMP_MAX = 5.0`, applied by `clamp_temperature` when an `Agent` loads
  `rl_agent_config.json`. The runtime's own calibrator (`laya.calibrate.fit_one_temperature`) already clamps to the
  runtime range, so only the research script disagrees.

**What we saw.** 15 checkpoints fine-tuned from the English ModernBERT-large base with this script (laya 0.3.22,
single device). 9 of 15 have a `choice` temperature (`temperature[0]`) above 5: 10.0, 9.998, 10.0, 7.280, 7.129,
6.402, 6.202, 6.109, 5.637. The three at or near 10.0 sit on the script's clip, so their unconstrained fit was
higher still. The other six are within range.

**Effect.** For `choice` questions the clamp leaves the option ranking unchanged (one scale per question), so
argmax and top-k are stable: across 5,400 paired predictions (served 5.0 against the shipped value) no abstain
decision changed and every top-k difference was a tie between options equal at 4 decimals. The probabilities do
change, by up to 0.22 here, and in the direction of overconfidence (a sharper distribution than the fit chose).
Anyone gating on `probabilities` or `answer_confidence` for these checkpoints is gating on uncalibrated values,
and the only signal is a load-time warning.

**Suggested fixes (any one).**

1. Clip the research script's fit to the runtime range (`TEMP_MIN`, `TEMP_MAX` from `laya.common`), or reuse
   `laya.calibrate.fit_one_temperature`, so a trained checkpoint is always servable as fitted.
2. If temperatures above 5 are legitimate for small fine-tunes (the fit says the model is overconfident), widen the
   runtime range to match the training clip, so the shipped value is served and the warning is not raised for a
   valid fit.
3. At minimum, have the training script warn (or record `"temperature_bound_limited": true` in
   `rl_agent_config.json`) when the fit lands outside the runtime range, so the mismatch is caught at training time
   rather than at load.

**Workaround we use.** We rank by the answer and do not gate on its probability, and we document that these
checkpoints' probabilities are uncalibrated. A refit with the runtime calibrator (`records_from_labeled` +
`fit_temperature_map`, saved with `Agent.save_calibration`) would give in-range values.
