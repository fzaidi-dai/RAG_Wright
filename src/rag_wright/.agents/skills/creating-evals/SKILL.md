---
name: creating-evals
description: >-
  Domain-agnostic recipe for creating EVALS for engine/product capabilities, eval-first (TDD): write the eval as
  soon as a capability is DEFINED (its contract + acceptance criterion), before it is implemented. Use it when
  starting a new domain (right after capabilities are defined), when adding or changing a capability, or when A/B-ing
  alternatives (e.g. a trained classifier vs a System-1 decision model). Covers gold-set design + reliability,
  per-capability-KIND metrics (extraction / classification / retrieval / graph / generation / judgment), the
  gate-vs-diagnostic split, building the gold cheaply, and running it with the engine's eval toolkit
  (`load_gold_set` from an expert's own files, one `score_*` per kind, `arun_eval` with repeats and errors kept apart,
  an `EvalReport` a backend can return, optional Langfuse datasets and runs). An eval is the executable acceptance
  criterion; training data IS an eval.
---

# Creating evals (eval-first / TDD)

An eval is the **executable acceptance criterion** for a capability. Write it **as soon as the capability is
DEFINED** — its contract (typed in/out) and its acceptance criterion exist — **before it is implemented**. This is
TDD at the capability level: the eval fails (red) on the unbuilt/weak capability, you implement to green, then you
can A/B alternatives and catch regressions forever. Corollary observed repeatedly in this engine: **the training
data you build for a classifier/decision model IS an eval** (a labeled gold set + a metric) — so building the eval
first also gives you the data design for free.

**The toolkit (on `rag_wright.api`).** `load_gold_set` turns an expert's CSV / XLSX / JSONL into a `GoldSet`;
one scorer per capability kind (section 2) turns predictions into a `ScorerResult` (the gate, its thresholds,
diagnostics that are never gated); `arun_eval` runs your capability over the gold set (concurrently, `[eval] i/N`
progress, repeats, a failed call kept as an error) and returns an `EvalReport` -- JSON-ready, every item's outputs
kept, so `rescore_eval` can score it again later without calling anything. Use it rather than hand-writing a
harness; section 6 is a complete example.

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
- **Gold from your experts' own files.** Domain experts label in spreadsheets: `load_gold_set(path_or_upload,
  GoldMapping(id=..., input=..., expected=..., group=...), name=..., version=...)` reads them as written -- the header
  found under a title, blank rows skipped, values kept exactly, each item's place recorded -- and refuses anything
  that would make the gold wrong (a missing column, a row without its id, a duplicate id) with where it is. Map
  `group` to the source document so splits stay leakage-safe. A workbook with several sheets: name one with
  `sheet=`. Say how the gold was made with `provenance=` (`hand`, `silver` or `generated`; or a per-row column through
  `GoldMapping(provenance=...)`), so silver is never conflated with gold. A gold set is `local_only` by default:
  client data never leaves the machine.
- **Gold stays local when the corpus is restrictively licensed** (CUAD/ACORD-derived gold lives under the
  gitignored `data/eval/`); commit the builder and the scorer, never the data.

## 2. Pick the metric by capability KIND, and split GATE vs DIAGNOSTIC
Always set ONE pass/fail **gate** (from the acceptance criterion) and report **diagnostics** alongside (never gate
on a diagnostic). Each kind has its scorer on `rag_wright.api`, checked against the engine evals named below:

| kind | scorer | gate |
|---|---|---|
| classification / typed decision | `score_classification` | the weakest class's recall@k (+ top-1 accuracy via `top1_floor`) |
| retrieval | `score_retrieval` | mean recall@k at a relevance grade; nDCG reported |
| extraction | `score_extraction` | recall and precision, apart |
| judgment / verdict | `score_judgment` | precision and recall of the actionable verdict |
| generation / QA | `score_generation` | citation recall, correct abstention, groundedness |
| graph / relational | `score_relational` | recall over the answer set |
| ingestion structure / unit labelling | `score_ingestion` | `evaluate_ingestion`'s checks (+ label accuracy) |
| candidate-then-choose | `score_candidate_choice` | candidate coverage, value-level recall and precision |

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
  separately, because it caps recall (ING-9b residual values: candidate coverage 0.93, 188/203 recomputed from the saved outputs; value-level recall 0.83 /
  precision 0.88 vs the LLM's 0.60 / 0.79. Pattern: `eval/residual_decision_gold.py`). **Count is not accuracy**:
  emitting more values is not a win without precision.
- **Ingestion structure**: `evaluate_ingestion` (in `rag_wright.api`) is the packaged structural eval of the
  ingestion hooks (tiling, table-row integrity, layout respect, coverage) on your own sample documents. Gate
  patterns: `eval/segmenter_eval.py`, `eval/unit_grouper_eval.py`.
- **Unit labelling** (when a unit's leading tag is a label something acts on): the same call with your
  `span_tagger`, any `unit_representative` and `unit_labels`, the gold `{<file name>: [{"text": <a snippet that
  occurs in exactly one unit>, "label": <expected, "" for none>}]}`. It needs no knowledge graph, so build it before
  the first ingest: label the units of a representative sample (headings, short fragments and units that should
  carry no label included, not only clean examples). `labels` reports accuracy, per-label recall and precision, and
  the confusion pairs, scored per unit. A snippet found in no unit or in several, and a unit whose snippets carry
  different labels, are failures, because that gold cannot be scored (if your units hold several labelled things,
  label the unit, not each thing in it).
  Compare candidate representatives (and the first-member baseline) on the same gold. Reference example, numbers
  only: the reference pack chose its rule this way on 510 documents with gold (ADR-0126): a vote over the non-heading
  members labelled 56.6% of units correctly, against 46.8% for the first member (usually the heading).

## 3. Build the gold cheaply (without faking it)
- **Silver bootstrapping**: a higher-capability teacher (LLM, or a decision model) labels candidate items; CURATE
  with reject-rules; mark silver, never conflate with gold. (Teacher labeling runs on the flat-GPU substrate per
  `qwen-vllm-modal`, not ad-hoc paid calls.)
- **Real public datasets** where they exist (e.g. labeled corpora for the task) — add to TRAIN; keep the TEST
  in-corpus + human-adjudicated so it stays an honest transfer test.
- **Generate hard cases** (near-boundary positives/negatives) to stress the exact confusions — TRAIN-ONLY; the
  gold test stays real. (Full data playbook: the `setfit` skill Phase 1/4 + `classifier-opportunity-analysis`.)

## 4. Run it: `arun_eval`
`arun_eval(gold, task, scorers, *, repeats, concurrency, progress)` is the harness. It runs `task` (your coroutine,
or a plain function, which runs off the event loop) on every item, `repeats` times, `concurrency` at a time, streams
`[eval] i/N` lines to `progress`, keeps a call that raises as an ERROR in that item's slot (never dropped, never an
answer), scores each repeat, and passes a scorer only if it passes on every repeat (its gate is the worst repeat;
`per_repeat_gate` lists each). `run_eval` is the synchronous form; inside a web backend, await `arun_eval` as a
background job. What is still yours:
- **Isolate the capability under test**: the task calls ONE capability, injected (a seam, such as the reference pack's
  `extract_override` or an injected `retrieve`), so the eval measures it, not the whole pipeline. Invoke production code THROUGH the
  capability layer (`ainvoke_subgraph`/`ainvoke_model`), never a hand-built copy. Score the **shipped request** (the
  exact prompt/question builder production uses, e.g. the reference pack's `judge_request`), not a copy of it re-typed in the eval.
- **Repeatability for non-deterministic models.** Use `repeats` and read `report.flips`; flips cluster near the
  threshold and usually mean the QUESTION is ambiguous: fix the question, do not vote it away (Jev flipped 5/181
  answers at scores 0.47-0.56; temperature/seed did not help, majority voting barely helped, rewriting the ambiguous
  question did. The ADR-0122 boundary residue prompt went from 94-96% with flips to 460/461, zero flips across 3
  calls). Pattern: `eval/boundary_residue_gold.py`.
- **Cache keys include the prompt and the method.** Any decision or extraction cache must key on the prompt text
  and the method (decision model vs LLM, and which model), so an eval never reuses outputs a different prompt or
  method produced (ADR-0122 ING-4d / ADR-0040 ING-9b: the residue decision cache and the clause cache).
- **Keep the report.** `report.model_dump_json()` holds every item's outputs; save it next to the gold version.
  `rescore_eval(report, gold, scorers)` re-scores it with new thresholds, a new scorer or a corrected gold, free. An
  eval kept only as aggregate numbers can never be checked again.
- **Pre-flight paid bulk** (>~50 paid calls): print the exact count + cost and wait for approval before launching.
- **Hermetic tests never touch the network**: `tests/conftest.py` fails a test that resolves a non-local host unless
  it carries a live marker (`model`, `store`, ...). Live evals run as scripts (outside pytest) or carry a live
  marker.

## 5. Langfuse -- optional: datasets, runs and scores next to the traces
`arun_eval(..., langfuse=True)` (or `RAG_EVAL_BACKEND=langfuse`; the keys alone send nothing) runs the same eval on
Langfuse: the gold set becomes a dataset (items upserted; items no longer in the gold set archived; the version is
the server's latest timestamp, recorded as `report.dataset_version`), each repeat a dataset run (`report.run_urls`),
and each scorer's gates and pass/fail are pushed as run scores. The report is the same, scored locally from the same
outputs. With engine tracing on (`RAG_TRACE_LEVEL`), every model call the task makes nests under its item's trace,
on both backends. What was verified against Langfuse 4.15.1 (self-hosted), and what to know:
- **`local_only` is refused before anything is sent.** Only a self-hosted Langfuse should ever receive client data;
  set `local_only=False` only for data that may leave the machine.
- **Scoring runs in your process**, not in Langfuse: its code evaluators are a 2-second, standard-library sandbox
  with no network, so they cannot run your scorers.
- **The SDK's own `run_experiment` drops an item whose task raises, and blocks a running event loop**; `arun_eval`
  handles both (an error is an error in its slot; the run is off the loop). Call `arun_eval`, not the SDK directly.
- **Reading observations back**: the v2 list endpoint is Cloud-only until Langfuse v4 ships in the open-source
  release, and the v1 endpoint times out on a trace-only query: bound every read by time.
- Langfuse's managed LLM-as-a-judge and decision-model evaluators, and its UI prompt experiments, test a prompt in
  Langfuse, not your pipeline: out of scope for this toolkit (a product may still use them).

## 6. A complete example
An expert's labelled workbook, your capability run through the capability layer, two repeats, the report kept and
scored again later. `my_judge` stands for your capability's call (e.g. through `ainvoke_model`); the rest runs as
written.

```python
import asyncio
import functools
from pathlib import Path

from rag_wright.api import (EvalReport, GoldMapping, arun_eval, load_gold_set, rescore_eval,
                            score_judgment)

GOLD_FILE = "gold/nli_expert_labels.xlsx"  # the expert's own file
REPORT = "eval_runs/nli_judge.json"

mapping = GoldMapping(id="ID", input=["Premise", "Hypothesis"], expected="Label", group="Contract")
gold = load_gold_set(GOLD_FILE, mapping, name="nli-judge", version="2026-10-12")


async def judge(item):
    return await my_judge(item.input["Premise"], item.input["Hypothesis"])


gate = functools.partial(score_judgment, actionable="contradiction", precision=0.8, recall=0.7)
report = asyncio.run(arun_eval(gold, judge, [gate], repeats=2, concurrency=8, progress=print))
Path(REPORT).parent.mkdir(parents=True, exist_ok=True)
Path(REPORT).write_text(report.model_dump_json(indent=1), encoding="utf-8")
print(report.passed, report.scores[0].gate, "errors", report.errors, "flips", report.flips)

stricter = functools.partial(score_judgment, actionable="contradiction", precision=0.9, recall=0.7)
saved = EvalReport.model_validate_json(Path(REPORT).read_text(encoding="utf-8"))
print("at 0.9 precision:", rescore_eval(saved, gold, [stricter]).passed)
```

## 7. The TDD loop
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
- **Scraping stdout** for metrics — keep and read the `EvalReport` JSON.
- **A failed call scored as an answer** (a timeout read as "neutral", an error read as "abstained") — it is an error;
  `arun_eval` keeps it apart (ADR-0133).
- **Only aggregates kept** — an eval whose per-item outputs were not saved can never be re-scored or checked; keep
  the report.
- **Faking a win by shrinking the sample / picking easy items** — real, representative, honest.

## Hand-off / where this sits
Chain: `classifier-opportunity-analysis` (identify the decision) → **`creating-evals` (THIS — write the eval FIRST)**
→ build it (a deterministic rule, or `setfit`/`laya`/Jev for a decision, or an LLM) → `authoring-a-capability`
(register). In the new-domain build sequence, this is the step right after capabilities are defined.
