# ADR-0030: Model-training standard — Modal, reusable registered training assets, mandatory checkpointing / best-model / versioning

Status: accepted (2026-07-26)
Relates to: T56/T60 (LegalBERT function classifier — the production trainer that already does best-model
retention + adopt-only-if-better + resume), the T-distill cross-encoder work (`scripts/distill/train_modal.py`,
Modal A10), the Modal grounding (modal SDK in the framework graph + official modal skill), memory
`reranker-operating-point-b`.

## Context

By this point the project trains several models — the LegalBERT function classifier (T56/T60) and the distilled
cross-encoders — and we have two exemplars that already do the right things: `scripts/train_legalbert_function.py`
(best-model retention via `load_best_model_at_end`+eval_loss, early stopping, resume-from-weights, adopt-only-
if-better guard, dry-run isolation) and `scripts/distill/train_modal.py` (Modal A10, per-fold GPU tasks, crash-
safe). These practices were established ad hoc, per task. They must become a **standing rule** so no future
training run silently drops them (a lost best-model, an unmonitored loss, a worse retrain clobbering a good
model, or an untracked local run are all real, expensive failure modes we have already paid for once).

## Decision

Whenever we train **any** model, the following are mandatory, not optional:

1. **Train on Modal.** Production/eval model training runs on Modal (grounded against the modal SDK in the
   framework graph + the official modal skill), not ad-hoc local training. Local runs are only for a quick
   code smoke on a tiny subset; anything that produces a model we keep or measure runs on Modal.

2. **Training scripts are categorized, reusable assets — destined for the registry.** Organize training code by
   **training category** (e.g. sequence-classification, cross-encoder/reranker, generative/LoRA fine-tune,
   embedding, dense retriever) as a **reusable asset**, not a one-off script. A new training need reuses or
   extends the matching category asset. These assets are intended to be **registered capabilities** (ARD /
   capability registry) eventually, the same way runtime capabilities are — so the training layer is a
   first-class, discoverable part of the system, not scratch code.

3. **Every training run MUST have, without exception:**
   - **Progress tracking + monitoring** — X/N, flushed/echoed to stdout and a progress file (our standing
     runner discipline), so a run is observable live.
   - **Loss monitoring** — train AND eval loss logged per epoch (or per logging interval), so regressions are
     visible.
   - **Regular checkpointing** — save every N steps or each epoch, so a killed run resumes and nothing is lost.
   - **Best-model retention (always)** — the checkpoint with the best eval loss is always kept, independent of
     the last checkpoint.
   - **Versioned adopt-only-if-better** — a newer version replaces the current production model only after the
     set update period AND only if its eval loss is better than the incumbent's. A worse retrain never
     overwrites a good model.

## Consequences

- Training is reproducible, observable, recoverable, and regression-safe by construction; the best model is
  never lost and never silently replaced by a worse one.
- Reuse across model types compounds: each new model type is an increment to a category asset, and the assets
  become registry-discoverable capabilities.
- The two existing exemplars are the reference implementations to **extend, not reinvent**:
  `scripts/train_legalbert_function.py` (best-model + adopt-only-if-better + resume) and
  `scripts/distill/train_modal.py` (the Modal harness: `modal.App`, `@app.function(gpu="A10")`,
  `Volume.batch_upload`, per-fold `starmap`).
- Cost is bounded and intentional: Modal A10 runs are cheap (single dollars), and the adopt-only-if-better +
  best-model guards mean a bad run costs a run, never a good model.

## References

- Exemplars: `scripts/train_legalbert_function.py`, `scripts/distill/train_modal.py`.
- Modal grounding: modal SDK in `graphify-out/framework/graph.json` (via `scripts/refresh_framework_graph.sh`),
  official modal skill at `~/.claude/skills/modal/`. Memory `reranker-operating-point-b`, `model-training-standard`.
