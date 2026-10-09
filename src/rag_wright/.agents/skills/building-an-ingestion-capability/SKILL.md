---
name: building-an-ingestion-capability
description: >-
  How to build a NEW domain's ingestion on the RAG_Wright engine with `build_ingestion`: decide what one extraction
  UNIT is in your documents, write the one required hook (the extractor) and only the optional hooks you need,
  declare the record types in your pack `.ttl`, tune the default hooks with `evaluate_ingestion` on your own sample
  documents, and handle spreadsheets, tables and embedded files. Use it when a product or domain pack needs to turn
  its documents into a cited knowledge graph, or when an ingestion result looks wrong (units too big, tables split,
  records uncited). Grounded in ADR-0124 and the generated API reference (`docs/api/`).
---

# Building an ingestion capability

The engine owns the ingestion MECHANISM; a domain supplies one function, the extractor, plus any optional hook whose
default does not fit. Everything named here is imported from `rag_wright.api` (the generated `docs/api/README.md` has
every signature). The decision record is ADR-0124 (`docs/adr/0124-generic-ingestion-builder-and-hooks.md`).
Repository paths in this skill (`docs/`, `eval/`, `scripts/`, `tests/`, `src/`) are in the engine repository: read them there or on GitHub, at the tag matching your installed engine.

## 1. Who owns which stage

| stage | owner | default (engine) | override with |
|---|---|---|---|
| parse (PDF, Office, spreadsheets incl. hidden sheets, HTML, Markdown; embedded files) | engine | docling, content-hash cached | (none) |
| chunk | engine default, domain may override | structural boundaries; a model refines only an over-cap section (domain-neutral prompt) | `chunk_model=`, `chunk_discoverer=` (e.g. `default_chunk_discoverer(guidance=...)` to say what a coherent unit is in your documents) |
| segment a chunk into spans | engine default, domain may override | layout-driven: a table row per span, sentences for prose, a heading joins what follows | `segmenter=` |
| tag spans (soft tags) | domain, optional | none | `span_tagger=` (to compare models behind it, see section 5 of `docs/domain-adaptation/classification-and-decision-models.md`) |
| index spans (embed + store, page/bbox provenance) | engine | the workspace's ingest embedder | `embedder=` |
| group spans into units | engine default, domain may override | a heading starts a unit, a table stays whole (a record table is one unit per row), page furniture dropped, units capped | `unit_grouper=`, `boundary_decider=` |
| choose each unit's representative span (its `anchor` + leading tag) | **domain decision**, optional | the grouper's choice: the first member, often a heading | `unit_representative=` |
| extract records from a unit | **domain (required)** | (none) | the `extractor` argument |
| write records | engine default | `kg_write` | `writer=` |
| per-document follow-up (e.g. an entity graph) | domain, optional | none | `document_hook=` |
| `Document` node, embedded children (`EmbeddedIn` / `AttachedTo`), progress, dead-lettering | engine | always on | (none) |

Every hook's output is checked by the engine, default or override alike: `check_tiling` (spans tile the chunk text,
ids `<chunk_id>#<index>`), `check_units` (known spans, each once, in order), `check_extraction` (provenance, below).
A unit whose extraction fails is recorded in the report and skipped; a document that fails is dead-lettered; the run
goes on.

## 2. Decide what ONE unit is (before writing code)

The unit is the text one extractor call reads. Get it right first; every other choice follows.

- Ask: "one record in my domain comes from ... ?" A section under a heading (a report, a policy) -> the default
  grouper already does this. One table row (a register, a test log) -> the default treats a DATABASE-style table
  (named, distinct header columns plus a serial first column or many columns) as one unit per row, and sets
  `Unit.table_row` with exact cell values; force it per document with `IngestSource(table_mode="record")`, or keep
  tables whole with `"block"`. A form (fields of ONE record) -> one unit (`auto` keeps a form grid whole).
- Units are capped at `IngestionTuning.max_unit_chars` (default 6000); a split table repeats its header row in each
  continuation unit.
- A source is a file path or uploaded bytes: `IngestSource(path=...)`, or `IngestSource(data=..., name=...)` (the
  name's extension picks the format), so an upload from object storage needs no temp file.
- If your documents mark units in a way layout does not show (a numbering scheme, a domain heading convention),
  override `unit_grouper=` or pass a `boundary_decider=` (candidate line texts -> "starts a new unit?" per text) to
  settle the lines the default grouper is unsure of. Domain conventions belong in your pack, never in the engine.
- **Decide which span REPRESENTS a unit** (`unit_representative=`, a `UnitRepresentative`: the unit's member spans,
  with their tags -> the one that represents it). The chosen span becomes `unit.anchor` (the citation your records
  carry, `span_id = unit.anchor.span_id`) and its primary tag leads `unit.tags` (the label your extractor reads).
  Without it a unit is represented by its first member, which is usually its heading: the weakest text to tag and a
  poor citation. The reference contracts pack passes `provision_vote` (ADR-0126): the label with the highest
  probability summed over the non-heading members, cited by the member most confident in it; on 510 CUAD contracts
  that beat heading-first by about 10 points. Build the gold BEFORE you choose a rule: for a sample of your documents,
  a short snippet from each unit you can label, with its expected label. Then compare rules with
  `evaluate_ingestion(..., span_tagger=..., unit_representative=..., unit_labels=...)` (leave out
  `unit_representative` for the first-member baseline) and keep the rule that labels best (the creating-evals skill).

## 3. Declare your record types in the pack `.ttl`

The store creates only the types a pack declares (ADR-0066: schema lives in the ontology, not in code). A record type
needs `span_id` and `confidence` properties to carry provenance:

```turtle
@prefix eng: <https://ragwright.local/ontology/engine#> .
@prefix my:  <https://example.org/my-domain#> .

my:SectionNode a eng:KgVertexType ; eng:vertexName "Section" ;
    eng:kgProperty "section_id:STRING", "title:STRING", "span_id:STRING", "confidence:STRING" ;
    eng:uniqueIndexOn "section_id" .
```

Point the workspace at it with `EngineConfig(pack="<path to your .ttl>")`; `open_workspace` then creates these types
on top of the neutral engine types. Writing a type the pack does not declare fails.

## 4. Write the extractor, tune, ingest

The extractor turns one `Unit` into a `UnitExtraction` of `KgNode` / `KgEdge` records. Provenance rule
(`check_extraction`): a fact node or edge carries `span_id` (a span of THIS unit, e.g. `unit.anchor.span_id`) and
`confidence` (`EXTRACTED`, `INFERRED` or `AMBIGUOUS`); nodes without `span_id` are shared vocabulary; a non-empty
extraction must cite at least once. Run `evaluate_ingestion` on your OWN sample documents before trusting the
defaults; it needs no store and makes no model calls.

```python
import asyncio
import os

from rag_wright.api import (
    EngineConfig, IngestionTuning, IngestSource, KgNode, StoreConfig, UnitExtraction, build_ingestion,
    evaluate_ingestion, open_workspace,
)

PACK_TTL = "my_domain/pack.ttl"
SAMPLES = ["samples/report.pdf"]
CORPUS = "my_domain"
CACHE = "data/cache/my_domain"


async def extract(unit, *, source_doc_id):
    """One unit -> its records, each citing a span of the unit."""
    title = unit.text.strip().splitlines()[0][:200]
    return UnitExtraction(nodes=[KgNode("Section", "section_id", {
        "section_id": f"{source_doc_id}:{unit.index}", "title": title,
        "span_id": unit.anchor.span_id, "confidence": "EXTRACTED"})])


tuning = IngestionTuning(max_unit_chars=6000)
evaluation = evaluate_ingestion(SAMPLES, cache_dir=CACHE, tuning=tuning)
print("evaluation passed:", evaluation.passed, evaluation.failures)

ws = open_workspace(EngineConfig(store=StoreConfig(
    host=os.environ["ARCADEDB_HOST"], port=os.environ["ARCADEDB_PORT"],
    user=os.environ["ARCADEDB_USER"], password=os.environ["ARCADEDB_PASSWORD"]), pack=PACK_TTL), corpus=CORPUS)
pipeline = build_ingestion(extract, tuning=tuning)
report = asyncio.run(pipeline.aingest(ws, [IngestSource(path=p) for p in SAMPLES], cache_dir=CACHE))
print(report.succeeded, "ingested,", report.failed, "dead-lettered")
for doc in report.documents:
    print(doc.doc_id, doc.units, "units,", doc.records, "records,", doc.extraction_failures)
```

Iterate on `evaluation.failures` (and the `DocumentEvaluation` measures: `tables_whole`, `headings_start_units`,
`coverage`, `cap_ok`, ...) by changing `IngestionTuning` before you override a hook. Keep `extract` async and
network-bound work inside it; the engine runs up to `IngestionTuning.extract_concurrency` units at once.

## 5. Spreadsheets, tables and embedded files

- **Hidden sheets** are ingested by default; `IngestSource(include_hidden_sheets=False)` skips them (listed in
  `DocumentReport.skipped_hidden_sheets`).
- **Exact cell values**: `table_rows(parse_document(...))` returns every data row as a `TableRow` (`columns`,
  `values`, `cell(name)`), read from the parse's cell grid, whole even when chunking split the table. A per-row unit
  carries its row in `Unit.table_row`, so the extractor reads cells, not re-parsed text.
- **Embedded files and PDF attachments** (an Office package's embedded workbook or PDF, a PDF's attached files) are
  ingested as CHILD documents through the same pipeline: each gets its own `Document` node and an `EmbeddedIn` edge
  to its parent, and an `AttachedTo` edge from the child to the table-row span it belongs to, with a confidence and
  the identifier evidence (`IngestionTuning.identifier` sets what counts as an identifier). The report lists them in
  `DocumentReport.children`, `links` and `unmapped_links`.

## 6. Verify (definition of done)

1. `evaluate_ingestion` passes on a representative sample of YOUR documents (not a hand-picked easy one). If your
   units carry labels, its `labels` report on your gold meets the bar you set, with the confusions understood.
2. A live ingest of that sample: no dead letters, `extraction_failures` explained, records cite spans
   (`kg_read(ws, "<your type>", fields=["span_id"])`), and `span_positions(ws, doc_id)` gives the citation positions.
3. A hermetic test of your extractor on fixed units (no model calls in the default suite; the engine's test network
   guard fails any unmarked test that reaches the network).
4. If the extractor calls a model, meter it: run the ingest inside `measure_usage()` and check the call count and
   cost per document before a bulk run.

## Anti-patterns

- Putting domain rules (section words, abbreviations, value lists) in Python: they belong in your pack `.ttl`.
- Overriding the segmenter or grouper before `evaluate_ingestion` shows the default fails on your documents.
- An extractor that returns records without `span_id`, or cites a span outside its unit (the run reports it as an
  extraction failure, and the records are not written).
- Re-parsing table text with regexes when `Unit.table_row` / `table_rows` already give the exact cells.
- Importing engine internals (`rag_wright.ingestion.*`, `rag_wright.store.*`): everything here is on `rag_wright.api`.
