# Evals

The engine's eval toolkit, on `rag_wright.api` (the evals workstream, `docs/specs/evals/plan.md`). The method -- what
a good gold set is, which metric gates which kind of capability, gate versus diagnostic -- is the `creating-evals`
skill; this page is the reference for the pieces. A product's guide to building its evals from its experts' files is
[Evals from your experts' files](domain-adaptation/evals-from-expert-files.md).

## The pieces

| piece | what it does |
|---|---|
| `GoldItem`, `GoldSet` | a gold set: items (input, expected, the grouping key that keeps a split leakage-safe, provenance), a name and a version; `local_only` (default True) keeps it off any hosted backend |
| `load_gold_set`, `aload_gold_set`, `GoldMapping`, `GoldImportError` | a gold set from an expert's CSV / XLSX / JSONL (a path or an upload's bytes), through a column mapping; anything that would make the gold wrong is refused with where it is |
| `score_classification`, `score_retrieval`, `score_extraction`, `score_judgment`, `score_generation`, `score_relational`, `score_ingestion`, `score_candidate_choice` | one scorer per capability kind: the gated measures against thresholds, and diagnostics that are never gated; a missing prediction is an error, counted wrong |
| `arun_eval`, `run_eval` | run your capability over the gold set: concurrently, `[eval] i/N` progress, repeats (a scorer passes only if it passes on every repeat), a failed call kept as an error |
| `EvalReport`, `ItemResult`, `ScorerResult` | the JSON-ready result: `passed`, each scorer's gate and diagnostics, `errors`, `flips`, every item's outputs, what was measured |
| `rescore_eval` | score a saved report again (new thresholds, a corrected gold) without calling anything |

Each scorer reproduces an engine eval's own numbers on that eval's saved outputs (`tests/evals/test_scorer_parity.py`).

## Run an eval

```python
import functools

from rag_wright.api import GoldMapping, arun_eval, load_gold_set, score_judgment

gold = load_gold_set("gold/labels.xlsx", GoldMapping(id="ID", input="Text", expected="Verdict", group="Document"),
                     name="verdicts", version="2026-10-12")


async def task(item):
    return await my_capability(item.input)  # your capability, through the capability layer


async def main():
    gate = functools.partial(score_judgment, actionable="violation", precision=0.8, recall=0.8)
    return await arun_eval(gold, task, [gate], repeats=2, progress=print)
```

In a web backend, start `arun_eval` as a background job (keep a reference to the task), return a job id, let the UI
poll the `progress` lines, and return `report.model_dump(mode="json")`. The `creating-evals` skill has a complete,
tested example.

## Langfuse (optional)

`arun_eval(..., langfuse=True)`, or `RAG_EVAL_BACKEND=langfuse` (see [Configuration](configuration.md)), runs the same
eval on Langfuse: the gold set becomes a dataset (items upserted, dropped items archived, the version recorded as
`report.dataset_version`), each repeat a dataset run (`report.run_urls`), each scorer's gates pushed as run scores.
With engine tracing on, every model call nests under its eval item. A `local_only` gold set is refused before
anything is sent. Scoring always runs in your process.
