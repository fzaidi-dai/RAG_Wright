# Evals as an engine capability (EV-*)

Status ledger for the workstream that turns the `creating-evals` skill's method into code on `rag_wright.api`, with
Langfuse as an optional backend. Each task runs the working loop (contract, red, green, verify, approval gate, one
commit). Target release: **0.8.0** (the exports are `feat:`; nothing existing is removed).

**Status: plan approved (2026-10-11).** Scope: all eight scorer kinds, release 0.8.0, TexWright as the first consumer.
EV-0 done 2026-10-12 (findings below). The event-loop fix (finding 5) is PS-24, done. EV-1 done; PS-25 and PS-26 done. EV-2 done. Next: EV-3a.

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

## EV-0 findings (2026-10-12, Langfuse 4.15.1, local server)

Phase 1 used a 6-item dataset and a fake task (no model). Phase 2 ran the ContractNLI slice (150 pairs, greedy, Qwen
on our A100, concurrency 16) twice through `DatasetClient.run_experiment`.

| # | what the SDK does | so the toolkit does this |
|---|---|---|
| 1 | Run evaluators gave the same numbers as the script's own formula: accuracy 0.667 and 0.660, 0 errors (earlier greedy runs: 0.693, 0.667, 0.680). Two greedy runs disagreed on 8 of 150 pairs, pair 88 among them (it took 228 s and still answered, no timeout) | the packaged scorers can be the evaluators; repeats and flips stay a runner feature (decision 6 holds) |
| 2 | A task that raises is dropped: missing from `item_results` and from what run evaluators see (4 of 6 reached them), only logged; the server still stores the run item | the runner's task wrapper never raises: a failed call becomes an error output, and rates divide by the gold-set size, not by the results returned (decision 5) |
| 3 | An item evaluator that raises leaves that item without a score, only logged | scorers are total functions; the backend wraps each one and records a scorer error explicitly |
| 4 | `run_experiment` is synchronous. Called inside a running event loop it runs the experiment on a new thread and joins it: the loop got 0 of ~24 heartbeats in 2.4 s. Through `asyncio.to_thread` it got 24 of 24 | `arun_eval` calls it through `asyncio.to_thread`; the backend docs say never to call it directly from a request handler |
| 5 | Each run gets a fresh event loop that is closed at the end. langchain_openai shares one cached async HTTP client per base URL and timeout across the process (`_cached_async_httpx_client`), so the next run's first calls reuse connections from the closed loop and fail at once with `APIConnectionError` (2 in run 2, recovered by the seam's retry) | see the finding below |
| 6 | Versions are timestamps: `get_dataset(name, version=t)` returns the items as they were at `t` (an item edited or archived later shows its old value), and a run on that client works. `run_experiment`'s own `_dataset_version` argument is private | the backend fetches the dataset at a timestamp and records it in the report; it never passes the private argument |
| 7 | A dataset's input and expected-output JSON schemas are enforced: a non-conforming item is refused with a 400 | `GoldSet` turns its contract into the two schemas (EV-5) |
| 8 | Run metadata, run-level scores (the run evaluators' values) and one item score per trace are stored; each item trace holds the task and evaluator spans. Whether the engine's own model-call spans nest inside the item trace was not checked (engine tracing was off, `RAG_TRACE_LEVEL=off`) | EV-5 checks the nesting with tracing on |
| 9 | Result order follows the dataset's item order, which is not insertion order | the runner keys results by item id, never by position |
| 10 | Running on local items (no dataset) works but still sends every input and output to Langfuse as traces | a `local_only` gold set never goes through `run_experiment` at all, not even as local items (decision 4) |
| 11 | A server deployed under a new app name built its snapshot first: ready in 348 s instead of ~13 s | eval runs reuse the usual server name |

**Finding (from 5): async model calls across event loops.** Any caller that runs async seam calls on more than one
event loop in a process (two `asyncio.run` calls, or a web backend plus a worker thread) has its first calls on a
new loop fail on dead connections. The seam's retry hides it today at the cost of a retry, and a burst at the start
of a run could exhaust the retries. **Owner:** engine. **Do:** before EV-4, a small fix task: the seam gives each
event loop its own async HTTP client (passed to `ChatOpenAI` as `http_async_client`), with a red test that makes
calls under two `asyncio.run`s and sees no connection error.

## Tasks

| id | what | verify | status |
|---|---|---|---|
| EV-0 | Grounding spike, no engine code: the ContractNLI eval (150 pairs, our A100, greedy) through `run_experiment` against the local Langfuse, with a dataset, an item evaluator and a run evaluator; record what SDK 4.15.1 actually does (dataset versions, async task and concurrency, scores, run comparison, failures) | findings written into this plan; decisions adjusted if needed | done (2026-10-12): the findings above; the decisions hold, with the runner rules in findings 2-6, 9 and 10 |
| EV-1 | `evaluate_ingestion` ready for a web backend: `aevaluate_ingestion` with a `progress` callback (`i/N`) and a JSON-ready result that includes `passed`; the background-job pattern in the docs | hermetic tests; live on sample documents | done (2026-10-12): `aevaluate_ingestion` (documents `document_concurrency` at a time, parse and segment off the loop, `[evaluate] i/N` progress), `evaluate_ingestion` a wrapper over it that names the async form inside a running loop, `IngestionEvaluation.to_dict()`; the job pattern in `docs/domain-adaptation/kg-construction.md`. Live: 9 PDFs as a polled background task, 24 s, the loop free (220 of ~242 heartbeats), identical to the sync result |
| EV-2 | The contracts (`GoldItem`, `GoldSet`, `EvalReport`) and gold import from CSV / XLSX / JSONL through a column mapping | hermetic tests; round trip; a spreadsheet fixture with messy cells | done (2026-10-12): `GoldItem` / `GoldSet` (versioned, `local_only` default, unique ids, JSON-ready, `groups()`), `EvalReport` with `ItemResult` / `ScorerResult` (`passed`, `errors`, `flips` derived); `load_gold_set` / `aload_gold_set` with `GoldMapping` (header found under a title, blank rows skipped, ids from a column or the row's place, provenance column, uploads by bytes; anything that would make the gold wrong refused with its place). XLSX read with openpyxl (declared; the docling path loses rows at blank lines, which became PS-26). Live: ContractNLI JSONL 150, CUAD CSV 510 (multi-line cells), the same as a messy workbook identical |
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
