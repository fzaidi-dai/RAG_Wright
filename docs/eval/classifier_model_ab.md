# Classifier base-model A/B (FUNCTION classifier) — 2026-07-27

Grounded-research follow-up to the rare-class classifier arc: are there better base models than LegalBERT for
the 44-way CUAD FUNCTION classifier? Four candidates, **one shared recipe**, **same leak-free split + holdout**.

## Protocol (recipe held constant, per the `model-training-recipe` skill)

- **Bases:** `nlpaueb/legal-bert-base-uncased` (current), `nlpaueb/bert-base-uncased-contracts` (Contracts-BERT),
  `microsoft/deberta-v3-base` (strong short-text encoder), `Qwen/Qwen2.5-0.5B` (small decoder LLM — tests the
  "use a small LLM" hypothesis).
- **Recipe:** AdamW, peak LR 2e-5, warmup 0.1, linear decay, weight_decay 0.01, max_grad_norm 1.0, **fp32**,
  inverse-frequency class-weighted CE, `metric_for_best_model=eval_macro_f1`, EarlyStopping(patience=3),
  10-epoch phase-1 then **continue-until-convergence** (load best, +5ep @ lr=1e-5 constant, adopt-only-if-better),
  Modal A10, resumable per-epoch checkpointing. Precision gotchas handled: DeBERTa forced `torch_dtype=float32`
  (its config carries fp16), dtype-agnostic weighted loss, Qwen pad-token setup.
- **Metric:** mean non-NONE **per-class recall** (the rare-class lever) + macro-F1, on the **SEED=0 20% holdout**
  (102 contracts, 2,495 non-NONE evaluable spans out of 27,087). Classifier trained on the disjoint 80%.
- Each model saved tagged on the Volume + downloaded to `data/models/<slug>/`; per-span holdout preds persisted
  to `data/models/ab_preds/<slug>.json` (re-scored against the on-disk weights via the `eval` mode, so preds
  match the saved model even where a non-adopted continuation diverged — Qwen's case).

## Result — single models

| Model | total epochs | holdout mean_recall | holdout macro-F1 |
|---|---|---|---|
| **deberta-v3** | 15 | **0.618** | 0.596 |
| **legal-bert** (current) | 15 | 0.614 | 0.596 |
| contracts-bert | 10 | 0.601 | 0.588 |
| qwen-0.5B | 15 (best @ phase-1 ep10) | 0.552 | 0.580 |

- **DeBERTa ≈ legal-bert** (+0.004 recall, identical macro-F1) — within noise, and DeBERTa is fp32-fragile.
- **Contracts-BERT** gives no edge over legal-bert.
- **Qwen-0.5B trails by ~0.06 recall** despite full convergence (its phase-2 continuation degraded and was
  correctly *not* adopted). The small-decoder-LLM hypothesis is empirically rejected for 44-way legal
  classification: a domain encoder wins. (Consistent with the grounded research + the Gemma zero-shot 0.52.)

## Ensemble / oracle analysis (`scripts/ab_model_overlap.py`)

| Ensemble | mean non-NONE recall |
|---|---|
| best single (deberta) | 0.618 |
| majority vote (all 4) | **0.600** (worse — Qwen is a weak voter, ties break badly) |
| **ORACLE (any-of-4 correct)** | **0.732** → **+0.114** headroom over best single |

- Pairwise error-overlap Jaccard 0.60–0.70; **only 43% of union-errors are irreducible** (all 4 wrong).
- The encoders miss **different** classes (per-class recall spread): DeBERTa wins MFN / Minimum-Commitment /
  Warranty-Duration / Liquidated-Damages; legal-bert wins Notice-Period / Joint-IP / ROFR / Exclusivity /
  Agreement-Date; Contracts-BERT wins Effective-Date / Change-of-Control.

## Decision (2026-07-27)

1. **Keep legal-bert as production** — no candidate beats it enough to justify switching (DeBERTa's +0.004 is
   noise, same macro-F1, and carries precision fragility).
2. **Drop the small-LLM-classifier idea** — Qwen is decisively worse.
3. **Ensemble deferred** — the +0.114 oracle gap is real (per-class complementarity, naive vote can't capture
   it), but a per-class-routed / confidence-weighted 2-encoder ensemble (legal-bert + deberta, drop qwen) is
   ~2× inference and is deferred until the current legal-bert CU-D1 baseline is (re)confirmed.

Reproduce: `uv run --no-sync modal run scripts/train_legalbert_modal.py --mode ab --bases "<...>" --lr 2e-5
--epochs 10 --patience 3` (train), `--mode eval --bases "<...>"` (re-score + download), then
`uv run --no-sync python -m scripts.ab_model_overlap` (analysis).
