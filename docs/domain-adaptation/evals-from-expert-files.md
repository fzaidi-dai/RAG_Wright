# Evals from your experts' files

For a new product (TexWright is the first): your domain experts already label documents in spreadsheets. This is how
those files become the gold sets your capabilities are measured against, without retyping them. The reference for the
pieces is [Evals](../evals.md); the method is the `creating-evals` skill.

## 1. Agree the file with the experts

One row per case, one column per field, a header row naming the columns. A title or notes above the header and blank
rows between blocks are fine; merged cells and labels written as colours are not (they cannot be read back as data).
Ask for:

- an **id** column with a stable id per row (it keys every run; without one the row's place is the id, which moves
  when rows are added),
- the **input** columns (the text or fields the capability sees) and the **expected** column(s),
- a **group** column naming the source document, so a split never puts one document on both sides,
- optionally a **provenance** column (`hand`, `silver`, `generated`) when some rows came from a model.

Several expected columns become one record (for an extraction: `{column: value}`).

## 2. Import it

```python
from rag_wright.api import GoldMapping, load_gold_set

mapping = GoldMapping(id="Test ID", input="Report text", expected=["Fabric", "GSM", "Result"], group="Report")
gold = load_gold_set("gold/lab_reports_labels.xlsx", mapping, name="lab-report-extraction", version="2026-10-12")
```

Header spelling is matched with spaces collapsed and case ignored. Every visible sheet whose rows hold the mapped
columns is read; name one with `sheet=` (hidden sheets are read only when named). A file that would make the gold
wrong -- a missing column (the error names the closest header it found), a row without its id, a duplicate id, an
unknown provenance -- raises `GoldImportError` with the sheet and row. In a backend, import an upload with
`aload_gold_set(IngestSource(data=..., name=...), mapping, ...)`.

## 3. Keep it local

A gold set is `local_only` by default and the Langfuse backend refuses one before sending anything. Client documents
and their labels stay on the machine: keep the files and the saved reports out of git, and version them with the gold
set's `version` instead.

## 4. Run, keep, compare

Pick the scorer for the capability's kind (the table in the `creating-evals` skill), run it with `arun_eval`, save the
`EvalReport` with the gold version, and compare runs. When the experts correct a label, bump the version and
`rescore_eval` the saved reports against the corrected gold before deciding anything was a model regression.
