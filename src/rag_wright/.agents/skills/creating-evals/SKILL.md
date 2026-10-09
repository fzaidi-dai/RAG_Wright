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

Repository paths in this skill (`docs/`, `eval/`, `scripts/`, `tests/`, `src/`) are in the engine repository: read them there or on GitHub, at the tag matching your installed engine. The `eval/` and `scripts/` files named as patterns are the engine's own evals: read them as
worked examples; your evals live in your repo.

## When to use
- **Starting a new domain**: the FIRST build step after capabilities are defined (step 5 of the domain-adaptation guide, `docs/domain-adaptation/README.md`) — write
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
- **Validate silver against a small BLIND hand-labelled sample before treating it as gold.** Label the sample
  without seeing any model output, then measure silver-vs-hand agreement. If it is low, the hand-labelled sample IS
  the gold. (ING-9: silver judge labels derived from classifier test sets agreed only 64% with hand labels; the
  decision rested on 192 blind hand-labelled cases. Pattern: `eval/semantic_judge_gold.py --score-blind`.)
- **Separate a tuning set from a held-out set.** Label the held-out set BEFORE any prompt runs on it, tune only on
  the tuning set, and report both numbers (ADR-0122 boundary residue prompt: 94.5% held-out vs 96.2% tuning). A
  number measured only on the set you tuned on is not a result.
- **Gold stays local when the corpus is restrictively licensed** (CUAD/ACORD-derived gold lives under the
  gitignored `data/eval/`); commit the builder and the scorer, never the data.

## 2. Pick the metric by capability KIND, and split GATE vs DIAGNOSTIC
Always set ONE pass/fail **gate** (from the acceptance criterion) and report **diagnostics** alongside (never gate
on a diagnostic).
- **Extraction** (section → records, clauses, requirements): recall / precision / F1 of extracted items vs gold,
  reported SEPARATELY (under- vs over-extraction are different failures). Watch over-extraction on non-operative
  input (definitions) and under-extraction on long input.
- **Classification / typed decision** (incl. SetFit, Laya, **Jev**): per-class recall + the per-class **floor**;
  symmetric eval; top-k recall for multi-label (reported against tags-per-item). Prefer a soft-tag/calibrated
  metric when the output routes rather than hard-gates.
  - **Evaluate it the way its consumers USE it (ADR-0126).** List every consumer of the output first. If anything acts
    on the PRIMARY label (a link, a record's type, a route), report top-1 accuracy as well as top-k recall: a top-k
    metric hides a confusable pair whose correct label is always rank 2. Measure on PIPELINE-PRODUCED inputs (what the
    segmenter really emits: headings, fragments, mixed sentences, the many no-label spans), not only on curated
    snippets. And before declaring a classifier swap has "no downstream impact", check each consumer of its primary
    label end to end. (ADR-0114's SetFit clause classifier passed >0.65 top-3 recall on gold snippets; in the pipeline
    its top-1 confused Cap / Uncapped Liability, which silently broke exception linking.)
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
  `scripts/eval_compliance_gold.py`. For a judge that accepts or refutes other outputs, report the **error-catch
  rate** (wrong outputs refuted), the **false-refute rate** (correct outputs refuted) and **calibration** (do its
  scores mean what they say), not one accuracy number. Pattern: `eval/semantic_judge_gold.py` (ING-9: decision
  model 95.3% vs LLM 90.1% on 192 blind hand-labelled cases).
- **Candidate-then-choose pipelines** (a generator proposes, a model picks): measure the generator's **coverage**
  separately, because it caps recall (ING-9b residual values: candidate coverage 95%; value-level recall 0.83 /
  precision 0.88 vs the LLM's 0.60 / 0.79. Pattern: `eval/residual_decision_gold.py`). **Count is not accuracy**:
  emitting more values is not a win without precision.
- **Ingestion structure**: `evaluate_ingestion` (in `rag_wright.api`) is the packaged structural eval of the
  ingestion hooks (tiling, table-row integrity, layout respect, coverage) on your own sample documents. Gate
  patterns: `eval/segmenter_eval.py`, `eval/unit_grouper_eval.py`.

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
  (`ainvoke_subgraph`/`ainvoke_model`), never a hand-built copy. Score the **shipped request** (the exact
  prompt/question builder production uses, e.g. `judge_request`), not a copy of it re-typed in the eval.
- **Repeatability for non-deterministic models.** Run each case several times on identical input; report how many
  answers flip and how far the scores sit from the threshold. Flips cluster near the threshold and usually mean
  the QUESTION is ambiguous: fix the question, do not vote it away (Jev flipped 5/181 answers at scores 0.47-0.56;
  temperature/seed did not help, majority voting barely helped, rewriting the ambiguous question did. The ADR-0122
  boundary residue prompt went from 94-96% with flips to 460/461, zero flips across 3 calls). Pattern:
  `eval/boundary_residue_gold.py`.
- **Cache keys include the prompt and the method.** Any decision or extraction cache must key on the prompt text
  and the method (decision model vs LLM, and which model), so an eval never reuses outputs a different prompt or
  method produced (ADR-0122 ING-4d / ADR-0040 ING-9b: the residue decision cache and the clause cache).
- **Score from a RESULT ARTIFACT (JSON), not stdout scraping** (scraping truncates and silently drops rows).
- **Parallelize** model/LLM calls (async + semaphore) — same cost, far less wall-clock; order-preserving so it
  stays deterministic.
- **Env-selected** so the SAME harness runs local (dev) or on Modal (full corpus + GPU).
- **Pre-flight paid bulk** (>~50 paid calls): print the exact count + cost and wait (`warn-before-bulk` rule).
- Stream `X/N` progress + actively monitor any run over ~30s (never launch-and-forget).
- **Hermetic tests never touch the network**: `tests/conftest.py` fails a test that resolves a non-local host unless
  it carries a live marker (`model`, `store`, ...). Live evals run as `uv run python -u eval/<name>.py` (outside
  pytest) or carry a live marker.

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
