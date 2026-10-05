---
name: creating-evals
description: >-
  Domain-agnostic recipe for creating EVALS for engine/product capabilities, eval-first (TDD): write the eval as
  soon as a capability is DEFINED (its contract + acceptance criterion), before it is implemented. Use it when
  starting a new domain (right after capabilities are defined), when adding or changing a capability, or when A/B-ing
  alternatives (e.g. a trained classifier vs a System-1 decision model). Covers gold-set design + reliability,
  per-capability-KIND metrics (extraction / classification / retrieval / graph / generation / judgment), the
  gate-vs-diagnostic split, building the gold cheaply, an isolated executable harness, and optional Langfuse
  Datasets/Experiments/Scores automation. An eval is the executable acceptance criterion; training data IS an eval.
---

# Creating evals (eval-first / TDD)

An eval is the **executable acceptance criterion** for a capability. Write it **as soon as the capability is
DEFINED** — its contract (typed in/out) and its acceptance criterion exist — **before it is implemented**. This is
TDD at the capability level: the eval fails (red) on the unbuilt/weak capability, you implement to green, then you
can A/B alternatives and catch regressions forever. Corollary observed repeatedly in this engine: **the training
data you build for a classifier/decision model IS an eval** (a labeled gold set + a metric) — so building the eval
first also gives you the data design for free.

## When to use
- **Starting a new domain**: the FIRST build step after capabilities are defined (build-sequence step 4.5) — write
  each capability's eval before/while you implement it.
- Adding or changing a capability, or tuning a threshold/prompt/model.
- **A/B-ing alternatives** on one capability (a deterministic rule vs a trained classifier vs a System-1 decision
  model vs an LLM) — the eval is the neutral judge; select on the metric.

## 1. Design the gold set (the foundation — get this right FIRST)
- **Real, in-domain items + expected outputs/labels.** Small but REPRESENTATIVE; never the easy cases only.
- **Reproducible + pinned + gitignored.** The gold is a generated artifact: pin its source snapshot + the selected
  ids so it rebuilds identically; gitignore the data, commit the BUILDER. (Pattern: `eval/build_golden.py`.)
- **Leakage-safe + balanced.** Split by the natural grouping unit (document / source / record), not by row.
  For classification, a SYMMETRIC per-class test and a per-class FLOOR (not overall accuracy — it hides dead
  classes). "k-shot" = k per class.
- **The gold is the CEILING — measure its RELIABILITY.** For subjective/ambiguous labels, get a second
  independent labeling and report inter-annotator (or inter-pass) agreement; adjudicate the disagreements and
  document the calls. A model cannot beat the gold's own consistency, and label ambiguity in the gold shows up as a
  classifier ceiling you cannot train past (CIC-1c: a two-pass gold agreed at 0.905 and a trained SetFit capped
  ~0.82 — diagnose the gold before blaming the model). If you have no human expert yet, a documented rubric + a
  two-pass consensus is
  the honest proxy — say so.

## 2. Pick the metric by capability KIND, and split GATE vs DIAGNOSTIC
Always set ONE pass/fail **gate** (from the acceptance criterion) and report **diagnostics** alongside (never gate
on a diagnostic).
- **Extraction** (section → records, clauses, requirements): recall / precision / F1 of extracted items vs gold,
  reported SEPARATELY (under- vs over-extraction are different failures). Watch over-extraction on non-operative
  input (definitions) and under-extraction on long input.
- **Classification / typed decision** (incl. SetFit, Laya, **Jev**): per-class recall + the per-class **floor**;
  symmetric eval; top-k recall for multi-label (reported against tags-per-item). Prefer a soft-tag/calibrated
  metric when the output routes rather than hard-gates.
- **Retrieval**: recall@k (binary, relevant = grade ≥ a floor) as the GATE; nDCG@k (graded, exp gain) as a
  DIAGNOSTIC (do NOT threshold nDCG). Isolate the retrieval legs (score corpus-ids before rehydration). Pattern:
  `eval/acord_retrieval.py`.
- **Graph / relational**: recall over the answer SET (reachability/traversal), k large enough to cover the set;
  node ids match gold by construction. Pattern: `eval/relational_eval.py`.
- **Generation / QA**: citation-recall / groundedness / correct-abstention; LLM-as-judge for free-text, but anchor
  with deterministic checks (a cited span must exist). Generation is non-deterministic near the abstain boundary —
  measure over several runs.
- **Judgment / compliance verdicts**: report PRECISION and RECALL of the actionable class (e.g. violation)
  SEPARATELY (alert-fatigue vs missed), and break out by provenance (real vs constructed). Pattern:
  `scripts/eval_compliance_gold.py`.

## 3. Build the gold cheaply (without faking it)
- **Silver bootstrapping**: a higher-capability teacher (LLM, or a decision model) labels candidate items; CURATE
  with reject-rules; mark silver, never conflate with gold. (Teacher labeling runs on the flat-GPU substrate per
  `qwen-vllm-modal`, not ad-hoc paid calls.)
- **Real public datasets** where they exist (e.g. labeled corpora for the task) — add to TRAIN; keep the TEST
  in-corpus + human-adjudicated so it stays an honest transfer test.
- **Generate hard cases** (near-boundary positives/negatives) to stress the exact confusions — TRAIN-ONLY; the
  gold test stays real. (Full data playbook: the `setfit` skill Phase 1/4 + `classifier-opportunity-analysis`.)

## 4. Make it an executable, isolated harness
- **Isolate the capability under test**: inject it (a seam / `extract_override` / an injected `retrieve`) so the
  eval measures ONE capability, not the whole pipeline. Invoke production code THROUGH the capability layer
  (`ainvoke_subgraph`/`ainvoke_model`), never a hand-built copy.
- **Score from a RESULT ARTIFACT (JSON), not stdout scraping** (scraping truncates and silently drops rows).
- **Parallelize** model/LLM calls (async + semaphore) — same cost, far less wall-clock; order-preserving so it
  stays deterministic.
- **Env-selected** so the SAME harness runs local (dev) or on Modal (full corpus + GPU).
- **Pre-flight paid bulk** (>~50 paid calls): print the exact count + cost and wait (`warn-before-bulk` rule).
- Stream `X/N` progress + actively monitor any run over ~30s (never launch-and-forget).

## 5. Langfuse — optional eval automation (we already use it for tracing)
Langfuse has a first-class eval stack we are NOT yet using (we use it only for spans/usage today): a **Dataset**
(items = input + optional expected output) → a **Task** (your capability) run over the dataset as an **Experiment
Run** → **Evaluators** (deterministic checks or LLM-as-judge) producing **Scores** (numeric/categorical/boolean),
all linked to traces. Reach for it when you want **tracked, re-runnable, dashboarded** evals + regression tracking
across capability versions; a local JSON harness is enough for a quick one-off gate. Keep the gold BUILDER + a
committed snapshot in the repo (reproducibility); push the items to the Langfuse dataset so runs and scores land
next to the traces you already collect. Ground the exact Dataset/Experiment API before wiring
(https://langfuse.com/docs/evaluation/concepts, https://langfuse.com/docs/datasets/overview); confirm the installed
SDK version's surface, not prose.

## 6. The TDD loop
1. Write the eval from the contract + a handful of real gold items → it fails (red) on the unbuilt/weak capability.
2. Implement the minimum to pass the gate (green).
3. A/B alternatives (rule / trained classifier / decision model / LLM) on the SAME gold; select on gate + diagnostics + cost + calibration.
4. Keep the eval; it is the regression guard (the Beyonce rule — if you shipped it, it has an eval).

## Anti-patterns (seen, do not repeat)
- **Overall accuracy** instead of a per-class floor — hides dead classes.
- **Testing on teacher/generated data as if it were gold** — measures mimicry, not accuracy; keep the test real + adjudicated.
- **Gating on a diagnostic** (e.g. nDCG) — report it, don't threshold it.
- **No reliability check on a subjective gold** — you can't read a number off a gold whose own labels disagree.
- **Eval not isolated** to the capability (measures the whole pipeline) — you can't attribute a regression.
- **Scraping stdout** for metrics — write + read a JSON artifact.
- **Faking a win by shrinking the sample / picking easy items** — real, representative, honest.

## Hand-off / where this sits
Chain: `classifier-opportunity-analysis` (identify the decision) → **`creating-evals` (THIS — write the eval FIRST)**
→ build it (a deterministic rule, or `setfit`/`laya`/Jev for a decision, or an LLM) → `authoring-a-capability`
(register). In the new-domain build sequence, this is the step right after capabilities are defined.
