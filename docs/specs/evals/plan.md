# Evals as an engine capability (EV-*)

Status ledger for the workstream that turns the `creating-evals` skill's method into code on `rag_wright.api`, with
Langfuse as an optional backend. Each task runs the working loop (contract, red, green, verify, approval gate, one
commit). Target release: **0.8.0** (the exports are `feat:`; nothing existing is removed).

**Status: plan approved (2026-10-11).** Scope: all eight scorer kinds, release 0.8.0, TexWright as the first consumer.
No task has started; EV-0 waits for the user's go-ahead.

**Why.** A product needs an "Evals" area behind its backend: domain experts' labelled data becomes gold sets,
developers run them against a capability, and everyone sees the result and how it moves between versions. Today the
engine exposes one eval function, `evaluate_ingestion` (synchronous, no progress, dataclass results whose `passed` is
a property). The rest of the method (gold-set design, a metric per capability kind, gate versus diagnostic,
repeatability) is prose in the skill, and the engine's own evals (`eval/`, `scripts/`) are scripts that do not ship
in the wheel. A product has to reimplement the method to use it.

**First consumer: TexWright.** Its domain experts already provide labelled data and can provide more, so gold sets
start from experts' own files rather than from a labelling UI. Its documents are client data and stay on the machine
(never committed, never sent to a hosted service). Where TexWright does not exercise a scorer kind, the engine's
reference pack evals (RuleWright's contract and compliance domain, public corpora) complete and verify the
implementation. RuleWright adopts the toolkit later.

**Target.** One toolkit on `rag_wright.api`:

| piece | what it is |
|---|---|
| gold-set contract | `GoldItem` / `GoldSet`: input, expected output, the grouping key that keeps a split leakage-safe, provenance (`hand` / `silver` / `generated`), a version, and `local_only` for data that must never leave the machine |
| gold import | load a gold set from the expert's own files (CSV / XLSX / JSONL) through a column mapping, so labelled spreadsheets become gold without retyping |
| scorers | pure functions, one per capability kind (below), each returning a gate and diagnostics |
| runner | `arun_eval(gold, task, scorers, *, repeats=1, concurrency=..., progress=None)`: runs the capability through the capability layer, counts a failed call as an error (never as an answer, ADR-0133), reports flips across repeats, returns an `EvalReport` |
| `EvalReport` | JSON-ready: the gate result, the diagnostics, per-item results, errors, flips, and what was measured (engine version, model ids, gold-set version) |
| Langfuse backend (optional) | when Langfuse is configured: the gold set becomes a versioned Langfuse dataset, the run goes through `run_experiment`, the scorers become item and run evaluators, and the report carries the dataset version and the run's link. Off, the same run works locally |

### The eight scorer kinds (from the `creating-evals` skill)

| # | kind | gate | diagnostics | engine eval it is checked against |
|---|---|---|---|---|
| 1 | classification / typed decision | per-class recall floor; top-1 accuracy when something acts on the main label | top-k recall vs tags per item, confusions | the SetFit / Laya classifier test sets |
| 2 | retrieval | recall@k (relevant = grade at or above a floor) | nDCG@k, never a gate | `eval/acord_retrieval.py` |
| 3 | extraction | recall and precision, apart | F1, extras, misses by input length | `scripts/eval_compliance_gold.py` (requirement extraction) |
| 4 | judgment / verdict | precision and recall of the actionable class | error-catch rate, false-refute rate, calibration, by provenance | `eval/contractnli_judge.py`, `eval/semantic_judge_gold.py` |
| 5 | generation / QA | citation recall, groundedness, correct abstention | an LLM judge, anchored by deterministic checks (a cited span exists) | `eval/harness.py` (golden QA) |
| 6 | graph / relational | recall over the answer set | | `eval/relational_eval.py` |
| 7 | ingestion structure and unit labelling | `evaluate_ingestion`'s checks | labelling accuracy, per-label results, confusions | `eval/segmenter_eval.py`, `eval/unit_grouper_eval.py` |
| 8 | candidate-then-choose | candidate coverage, value-level precision and recall | count of values emitted (never a win on its own) | `eval/residual_decision_gold.py` |

Every kind also gets, from the runner: repeatability (flips across identical runs, distance from the threshold) and
errors kept apart from the scored items.

## Design decisions (for review)

1. **Langfuse is a backend, not a dependency or a second path.** It activates on real configuration, as tracing does
   (`models/tracing.py`), never on importability. Every eval runs locally without it.
2. **Scoring runs in our process, not in Langfuse.** Langfuse's code evaluators run in a 2 s, standard-library-only
   sandbox with no network, so they cannot import the engine. Our scorers are handed to `run_experiment` as item /
   run evaluators; Langfuse stores and charts the scores.
3. **The task goes through the capability layer** (`ainvoke_subgraph` / `ainvoke_model`, or a product's own
   coroutine that does), never a hand-built copy, so the eval measures what production runs.
4. **Client data stays local.** `GoldSet.local_only` defaults to True; a local-only gold set is never pushed to
   Langfuse, and the docs say only a self-hosted Langfuse may receive client data at all. TexWright's gold is
   local-only.
5. **Failed calls are errors, not answers** (ADR-0133).
6. **Each scorer is verified against an engine eval's published numbers** (re-scoring that eval's saved outputs must
   give the same result), so the packaged scorers are the method, not an approximation of it.
7. **Out of scope:** Langfuse's managed LLM-as-a-judge and decision-model evaluators (they run inside Langfuse with
   its own LLM connection, and the Jev one is documented for Langfuse Cloud only), UI prompt experiments (they test a
   prompt, not our pipeline) and the CI GitHub Action (the engine's CI stays hermetic). A product may still use them.

## Tasks

| id | what | verify | status |
|---|---|---|---|
| EV-0 | Grounding spike, no engine code: the ContractNLI eval (150 pairs, our A100, greedy) through `run_experiment` against the local Langfuse, with a dataset, an item evaluator and a run evaluator; record what SDK 4.15.1 actually does (dataset versions, async task and concurrency, scores, run comparison, failures) | findings written into this plan; decisions adjusted if needed | todo |
| EV-1 | `evaluate_ingestion` ready for a web backend: `aevaluate_ingestion` with a `progress` callback (`i/N`) and a JSON-ready result that includes `passed`; the background-job pattern in the docs | hermetic tests; live on sample documents | todo |
| EV-2 | The contracts (`GoldItem`, `GoldSet`, `EvalReport`) and gold import from CSV / XLSX / JSONL through a column mapping | hermetic tests; round trip; a spreadsheet fixture with messy cells | todo |
| EV-3a | Scorers 1 (classification) and 8 (candidate-then-choose) | hermetic tests; parity with the engine evals named above | todo |
| EV-3b | Scorers 2 (retrieval) and 6 (graph / relational) | same | todo |
| EV-3c | Scorers 3 (extraction) and 4 (judgment) | same | todo |
| EV-3d | Scorer 5 (generation / QA) and 7 (ingestion, wrapping EV-1) | same | todo |
| EV-4 | The runner `arun_eval` (local): concurrency, `progress`, repeats and flips, errors apart, through the capability layer | hermetic tests; live: ContractNLI through the runner matches the script | todo |
| EV-5 | The Langfuse backend: gold set to a versioned dataset (schema from the contract), `run_experiment` with the scorers as evaluators, the report carrying the dataset version and run link; `local_only` respected | hermetic tests with a fake client; live against the local Langfuse | todo |
| EV-6 | Worked example, skills and docs. One evals skill, rewritten rather than a second one beside it (two copies of the method would drift): `creating-evals` section 2 names each kind's packaged scorer, section 4 becomes "call `arun_eval`" (a hand-built script only for a kind the toolkit does not cover), section 5 is replaced by what EV-0 and EV-5 verified (the backend switch, `local_only`, only a self-hosted Langfuse may receive client data, scorers run in our process); it gains a worked example on public data (ContractNLI) executed by a test, as the ingestion skill's is, and a section on building gold from experts' labelled files (the column mapping, kept local). The consumer skill `using-the-rag-wright-engine` gains an evals section: evals behind a product backend (a background job, progress to the UI, `EvalReport` as the response, where run history lives). `qwen-vllm-modal` (folded in 2026-10-12) leads with the product route, `ModelServerSpec` / `adeploy_model_server`, and keeps the raw `modal deploy` of its bundled script as the engine developer's path. `docs/` updated; a guide for a new product (TexWright) | doc-reference tests; the skill example runs under a test; the shipped-skills test | todo |
| EV-7 | (optional, later unless TexWright needs it) Human labels from a Langfuse annotation queue into a gold set, with an agreement report between annotators | hermetic tests; live with two labellers on a small queue | todo |

## Engine and product

- **Engine (this workstream):** the contracts, gold import, the eight scorers, the runner, `EvalReport`, the
  optional Langfuse backend, the ingestion eval's web-ready form, the docs, and the two skills (`creating-evals`,
  `using-the-rag-wright-engine`).
- **TexWright (its repo):** its gold sets from its experts' files (kept local), the tasks that call its capabilities,
  any backend routes and UI, and where run history is kept.
- **RuleWright (later):** the same, plus a migration guide when it adopts the toolkit.
