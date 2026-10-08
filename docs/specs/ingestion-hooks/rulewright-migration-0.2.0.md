# RuleWright: migrating to rag-wright 0.2.0

**Audience:** the RuleWright repo (and its coding agent). **Engine release:** `rag-wright` 0.2.0 on PyPI, tag `0.2.0`
(ingestion hooks, ADR-0124). **Source of truth for every break:** `ing8-breaking-changes.md` (beside this file). This
document is that record applied to RuleWright as it is today (commit `53f4894`): every item below was found by scanning
RuleWright's code against the 0.2.0 engine, not assumed. Dependency direction is unchanged: Product -> Engine.

> ## The core of this migration: RuleWright owns its capabilities, and its seam is where it owns them
>
> 1. **The engine starts with an EMPTY capability catalog.** Installing or importing `rag-wright` registers nothing.
>    Every capability RuleWright invokes must be registered BY RULEWRIGHT, once, at startup. Nothing the engine does
>    will register them on its behalf.
> 2. **The contract and compliance capabilities are not the engine's.** All 30 of them
>    (`contract_ingestion_pipeline`, `intra_document_qa`, `typed_property_retrieval`, `relational_qa`,
>    `compliance_ingestion`, `compliance_check`, ...) belong to the **reference pack** (`rag_wright.packs.contracts`
>    and `rag_wright.packs.compliance`): the engine's worked example, which RuleWright builds on today. RuleWright
>    registers them by loading that pack: `load_reference_pack()`. If RuleWright later replaces them with its own
>    capabilities, it registers its own pack instead (`load_pack("<its module>")`).
> 3. **The engine's generic capabilities are offered, never pre-registered.** `engine_capabilities()` returns the
>    definitions of the 7 domain-neutral ones (`jev_decision`, `generation`, `vision_to_text`,
>    `span_relevance_judgment`, the RLM skills); a product registers the ones it uses (the reference pack already
>    registers those it needs).
> 4. **The seam is the only place RuleWright touches the engine**, and registration is the seam's first job, at
>    startup, before any ingest or query. The engine ships a worked reference seam to model it on:
>    `src/rag_wright/packs/reference_seam.py` (`ContractComplianceSeam`; it registers the reference pack in its
>    constructor). Section 1 below is the minimum to get working now; "Later" at the end is the refactor of
>    RuleWright's seam toward that shape.
>
> **What happens if this is missed:** `ainvoke_subgraph(...)` raises `KeyError` (unknown capability); the compliance
> ingest dead-letters every policy section; the contract ingest silently falls back from the decision model to the
> LLM (on the measured contract, 277 calls and $0.277 instead of 215 decision-model calls, 0 LLM calls, $0.013).

## 0. Where RuleWright stands

- RuleWright depends on the engine as an **editable path dependency** (`[tool.uv.sources]
  rag-wright = { path = "../RAG_Wright", editable = true }`), so it already runs against the 0.2.0 code: until it
  migrates, its engine imports fail.
- It uses **52 distinct engine symbols, none through `rag_wright.api`**. 22 still resolve unchanged; **28 moved** (the
  reference pack now lives in `rag_wright.packs`); **2 were replaced**. One ingest call passes **2 removed
  arguments**. **4 store calls** moved to the packs' store extensions. All of RuleWright's databases need a **one-time
  field migration**. And it must **register the engine capabilities at startup**, or the compliance ingest fails and the
  contract ingest silently falls back to many more LLM calls.
- Scan method (re-run it after migrating, section 8): every `rag_wright` import and dotted string in the 323 Python
  files, resolved against 0.2.0; every keyword argument passed to an imported engine callable, checked against its 0.2.0
  signature.

Optional: switch from the path dependency to the release (`rag-wright>=0.2.0`) once this migration is done.

## 1. Own the capabilities: register them in the seam at startup (required)

The capability catalog ships empty (ADR-0118; see "The core of this migration" above). Beyond `ainvoke_subgraph`,
two 0.2.0 paths RuleWright calls directly look `jev_decision` (the decision model) up in it:

- **Compliance ingest** (`run_compliance_document_ingestion`, default `extraction_backend="jev"`) resolves
  `jev_decision` directly: unregistered, it raises `KeyError` and every policy section dead-letters.
- **Contract ingest** uses the decision model for provision boundaries, the extraction judge and the residual property
  values only when `OPENROUTER_API_KEY` is set **and** `jev_decision` is registered. Unregistered, it falls back
  silently to the LLM: on the measured contract (Aimmune, 131 provisions) that is 277 calls ($0.277) instead of 215
  decision-model calls and 0 LLM calls ($0.013).

Do this once, in the seam, at process start (before any ingest or query), exactly as the reference seam does in its
constructor:

```python
from rag_wright.api import load_reference_pack

load_reference_pack()  # the contracts + compliance packs and the engine capabilities they use (incl. jev_decision)
```

It is idempotent (registering again replaces entries with identical ones). Check it at startup: after registration,
`capability_index()` (from `rag_wright.api`) lists the capabilities RuleWright invokes; RuleWright's `engine_info()`
reports `len(canonical_capability_slugs())` (section 3), which is 41 with the reference pack registered and 11 without.
When RuleWright replaces the reference pack with its own capabilities, its seam calls `load_pack("<its pack module>")`
(a module whose `register()` registers its manifests) and registers the engine capabilities it relies on
(`for m in engine_capabilities(): register_capability(m)`, all from `rag_wright.api`).

## 2. Imports: 28 moved

Module paths moved (no shims). Apply this mapping to imports **and** to dotted strings (e.g. `monkeypatch.setattr`
targets) in `src/rulewright/engine/seam.py` and the tests `test_answer_model.py`, `test_corpus_retrieval.py`,
`test_engine_async_contract.py`, `test_ingest_models.py`, `test_named_conditions.py`, `test_reingest_idempotent.py`,
`test_scanned_and_table_pdfs.py`, `test_stage_progress.py`:

| 0.1 module | 0.2.0 module |
|---|---|
| `rag_wright.subgraphs.contract_ingestion_pipeline` | `rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline` |
| `rag_wright.subgraphs.intra_document_qa` | `rag_wright.packs.contracts.subgraphs.intra_document_qa` |
| `rag_wright.subgraphs.typed_property_retrieval` | `rag_wright.packs.contracts.subgraphs.typed_property_retrieval` |
| `rag_wright.subgraphs.compliance_check` | `rag_wright.packs.compliance.subgraphs.compliance_check` |
| `rag_wright.subgraphs.compliance_ingestion` | `rag_wright.packs.compliance.subgraphs.compliance_ingestion` |
| `rag_wright.capabilities.contract_kg_serve` | `rag_wright.packs.contracts.capabilities.contract_kg_serve` |
| `rag_wright.capabilities.dg_extraction` | `rag_wright.packs.contracts.capabilities.dg_extraction` |
| `rag_wright.capabilities.property_boosted_retrieval` | `rag_wright.packs.contracts.capabilities.property_boosted_retrieval` |
| `rag_wright.capabilities.query_understanding` | `rag_wright.packs.contracts.capabilities.query_understanding` |
| `rag_wright.contracts.function` | `rag_wright.packs.contracts.schemas.function` |
| `rag_wright.contracts.property` | `rag_wright.packs.contracts.schemas.property` |
| `rag_wright.spans.clause_function_classifier` | `rag_wright.packs.contracts.spans.clause_function_classifier` |

The symbols themselves keep their names. A one-shot rewrite (run from the RuleWright root, then review the diff):

```python
import pathlib, re, subprocess
MOVES = {
    "rag_wright.subgraphs.contract_ingestion_pipeline": "rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline",
    "rag_wright.subgraphs.intra_document_qa": "rag_wright.packs.contracts.subgraphs.intra_document_qa",
    "rag_wright.subgraphs.typed_property_retrieval": "rag_wright.packs.contracts.subgraphs.typed_property_retrieval",
    "rag_wright.subgraphs.compliance_check": "rag_wright.packs.compliance.subgraphs.compliance_check",
    "rag_wright.subgraphs.compliance_ingestion": "rag_wright.packs.compliance.subgraphs.compliance_ingestion",
    "rag_wright.capabilities.contract_kg_serve": "rag_wright.packs.contracts.capabilities.contract_kg_serve",
    "rag_wright.capabilities.dg_extraction": "rag_wright.packs.contracts.capabilities.dg_extraction",
    "rag_wright.capabilities.property_boosted_retrieval": "rag_wright.packs.contracts.capabilities.property_boosted_retrieval",
    "rag_wright.capabilities.query_understanding": "rag_wright.packs.contracts.capabilities.query_understanding",
    "rag_wright.contracts.function": "rag_wright.packs.contracts.schemas.function",
    "rag_wright.contracts.property": "rag_wright.packs.contracts.schemas.property",
    "rag_wright.spans.clause_function_classifier": "rag_wright.packs.contracts.spans.clause_function_classifier",
}
rx = re.compile("|".join(rf"(?<![\w.]){re.escape(k)}(?!\w)" for k in sorted(MOVES, key=len, reverse=True)))
# `from rag_wright.subgraphs import contract_ingestion_pipeline` (a moved module imported from its old package)
from_pkg = re.compile(r"from (rag_wright(?:\.\w+)*) import (\w+)")


def _from_pkg(m):
    new = MOVES.get(f"{m.group(1)}.{m.group(2)}")
    return f"from {new.rsplit('.', 1)[0]} import {m.group(2)}" if new else m.group(0)


for f in subprocess.run(["git", "ls-files", "*.py"], capture_output=True, text=True).stdout.split():
    p = pathlib.Path(f); t = p.read_text(); n = rx.sub(lambda m: MOVES[m.group(0)], from_pkg.sub(_from_pkg, t))
    if n != t: p.write_text(n); print("rewrote", f)
```

Run it with `uv run python <file>`.

## 3. Two replaced symbols (`src/rulewright/engine/seam.py`)

- **`CANONICAL_CAPABILITY_SLUGS`** (a frozenset) is gone: use the function `canonical_capability_slugs()` (from
  `rag_wright.api`). It returns the engine's 11 generic slugs plus those of every loaded pack, so call it after
  `load_reference_pack()` (section 1). In `engine_info()`:
  `slugs = sorted(canonical_capability_slugs())`. `capability_urn` is unchanged (`rag_wright.capabilities.registry`).
  The test docstring in `tests/test_engine_seam.py` ("a frozenset of 52 entries") needs updating too; with the
  reference pack loaded the set is 41 (11 engine + 30 pack).
- **`rag_wright.contracts.ontology.RelationshipType`** no longer exists (it was already removed before 0.1.0, so
  this line was broken then too). `graph_query` takes the relationship as a string; the contract pack names it:

  ```python
  from rag_wright.packs.contracts.ontology.contract_taxonomy import AFFILIATE_OF  # "Affiliate Of"

  answer = graph_query(entity_id, store=store, relationship_type=AFFILIATE_OF, documents=documents)
  ```

## 4. The ingest call: drop two arguments (`seam.py`, the `aproduction_document_ingest(...)` call)

`list_model=` and `samples=` were removed (they were accepted and never used: clause extraction is classifier-first),
so passing them now raises `TypeError`. Delete both arguments. The other arguments RuleWright passes (`cache_dir`,
`registry`, `party_seed_path`, `extract_model`, `graph_extract_model`, `judge_model`, `chunk_model`, `classify_fn`)
are unchanged. `tests/test_ingest_models.py::test_the_list_model_is_turned_OFF_explicitly` asserts the old argument and
should be deleted.

Dead settings to retire with it: `rulewright_ingest_samples`, `rulewright_engine_list_model`,
`rulewright_engine_clause_samples` and the `RAG_INGEST_LIST_MODEL` / `RAG_INGEST_CLAUSE_SAMPLES` /
`RAG_INGEST_CLAUSE_EXTRACTOR` exports in `export_engine_env` (in 0.2.0 only the pack's unused legacy tag-parse
extractor reads those variables; `tests/test_engine_knobs.py` lists them). `CLASSIFY_CONCURRENCY`,
`CLAUSE_CONCURRENCY` and `RAG_EXTRACT_WORKERS` are still read; keep them (or set the first two through
`EngineConfig(options=EngineOptions(packs={"contracts": ContractIngestOptions(...)}))` if RuleWright moves to the API
path).

## 5. Store methods that moved (`seam.py`)

The generic store no longer carries contract or compliance methods (ING-8e). Wrap the store in the pack's store
extension (constructing it also ensures that pack's schema):

| RuleWright today | 0.2.0 |
|---|---|
| `store.contract_by_id(document_id)` (resume check) | `ContractKGStore(store).contract_by_id(document_id)` |
| `store.all_spans_by_contract(contract_id)` | `store.all_spans_by_document(contract_id)` (generic store) |
| `store.clauses_in_contract(contract_id)` | `ContractKGStore(store).clauses_in_contract(contract_id)` |
| `store.all_requirements(...)` (3 call sites) | `ComplianceStore(store).all_requirements(...)` |

Imports: `from rag_wright.packs.contracts.capabilities.contract_kg_store import ContractKGStore` and
`from rag_wright.packs.compliance.capabilities.compliance_store import ComplianceStore`.

RuleWright's span-locations function reads only `span_id`, `pages`, `bbox`, `doc_start`, `doc_end` and `text` from the
span rows, none of which were renamed; it can also be replaced outright by `ContractKGStore(store).span_locations(
contract_id)`, which does the same join (map the engine's `SpanLocation` to RuleWright's own type). Elsewhere, if
RuleWright reads span rows, the renamed keys are `contract_id` -> `document_id`, `function` -> `primary_tag`,
`functions` -> `tags`; `parent_okf_path` is gone. (The scan found no RuleWright code reading the renamed keys; the
`contract_id` keys in its tests are its own API and graph-state fields.)

## 6. Migrate every database once (required)

0.2.0 renamed the stored span fields, and `ensure_schema()` (called by RuleWright's seam on every store it opens)
**refuses** a database whose `Span` type still has the old fields, with a `RuntimeError` naming the fix. Migrate each
RuleWright database once: `rulewright_contracts`, `rulewright_compliance`, `rulewright_dev`, and any per-tenant
`rw_<id>_contract` / `rw_<id>_compliance` database still in use (drop the test leftovers instead). Back up first
(`scripts/backup_kg_to_gcs.sh <db>` in the engine repo). It is idempotent, batched, prints `X/N` progress, and a
database with no `Span` type (or already migrated) is a no-op.

While RuleWright uses the engine checkout (the path dependency), use the engine's script:

```
cd ../RAG_Wright && uv run python -u scripts/migrate_span_fields.py rulewright_contracts
```

From an installed 0.2.0 (the script is not in the wheel), the same thing through the package:

```python
from rag_wright.store.arcadedb import ArcadeDBStore

for db in ["rulewright_contracts", "rulewright_compliance", "rulewright_dev"]:
    store = ArcadeDBStore.from_env(database=db)
    moved = store.migrate_span_fields(progress=lambda done, total: print(f"[migrate] {db} {done}/{total}", flush=True))
    store.ensure_schema()  # now accepted
    store.close()
    print(db, "spans moved:", moved)
```

The compliance `Requirement` data needs no migration (its type and properties are unchanged; it is now declared in
the compliance pack's `.ttl`).

## 7. Behaviour changes to expect (no code change needed)

- **Neutral default schema.** `ensure_schema()` on a NEW database creates only the engine types (Chunk, Entity,
  Relationship, Mentions, Span, Document, EmbeddedIn, AttachedTo). The contract types (Clause, PropertyValue, Contract,
  the typed edges) appear when `ContractKGStore` is first constructed on that store (the contract pipeline does this),
  and `Requirement` when `ComplianceStore` is. Existing databases already have them.
- **Decision model on ingest** (with section 1 done): provision boundaries, the extraction judge and the residual
  property values run on Jev; `RAG_SEMANTIC_JUDGE=llm` / `RAG_RESIDUAL_EXTRACTOR=llm` move a step back to the LLM.
- **Write-conflict retries.** Concurrent writes that ArcadeDB rejects with a concurrent-modification conflict are now
  retried; before 0.2.0 they were lost (documents dead-lettered, spans dropped) under concurrent ingestion.
- **A `Document` node per ingested document**, and embedded files / PDF attachments are ingested as linked child
  documents (`EmbeddedIn` / `AttachedTo`).
- **Stricter span contracts.** `SpanRecord` / `Span` reject unknown fields (an old field name raises instead of being
  dropped), and `CapabilityManifest` rejects `response_bounds` on an `agent_skill`.

## 8. Verify

1. Every engine import resolves: `uv run python -c "import rulewright.engine.seam"`, then the full test suite.
2. Re-run the scan from section 0 (the engine's `tests/arch/test_doc_references.py` has the snippet checker it is
   built on): no unresolved `rag_wright` import or dotted string, no keyword argument outside a 0.2.0 signature.
3. Databases: every database the seam opens passes `ensure_schema()` (no `RuntimeError`).
4. Live: ingest one contract and one policy; the contract run should show Jev calls and no per-provision LLM calls
   (`measure_usage` / the run report), the policy run no dead-lettered sections.

## 9. Dependency setup: local co-development now, released product later

RuleWright is in the **local co-development** mode (the product-starter playbook's section 5): it commits an editable
path dependency on the engine checkout, so every engine change is visible to it (and its coding agent) the moment it
is saved, with no build, publish or reinstall. A wheel would be a snapshot to rebuild and reinstall on every change,
so it is only worth it where there is no checkout (a sealed deploy image, an offline machine). Make the mode safe as
well as fast:

1. **Declare a version floor** in `pyproject.toml` (`dependencies`): `"rag-wright"` becomes `"rag-wright>=0.2.0"`. The
   editable source still satisfies it locally; an install without the source can no longer resolve an older release.

   ```toml
   [project]
   dependencies = ["rag-wright>=0.2.0", ...]

   [tool.uv.sources]  # local co-development
   rag-wright = { path = "../RAG_Wright", editable = true }
   ```

2. **Check against the published release where it matters** (CI, before a deploy): `uv sync --no-sources` ignores
   the path source and installs `rag-wright` from PyPI under the floor (verified on 0.2.0), then run the suite. Two
   rules: never with `--locked` (the committed `uv.lock` records the local path, so `--no-sources` must re-resolve),
   and never commit the `uv.lock` it leaves behind (it rewrites the lock to the PyPI source in that checkout; a plain
   `uv lock` flips it back). Locally, the plain `uv sync` keeps the editable engine.
3. **Expect the trade-off.** In this mode a breaking engine change breaks RuleWright at once (as 0.2.0 did). That is
   useful feedback when deliberate: before the engine commits a change to a public symbol or store method,
   RuleWright's suite is run against the engine working tree.
4. **Switch to the released-product mode** when RuleWright gets CI, deploys or other developers: drop the path source,
   `uv lock` against PyPI, commit, add the starter's `dependabot.yml` (engine releases then arrive as CI-gated PRs),
   and adopt an unreleased engine fix with a temporary git pin
   (`rag-wright = { git = "https://github.com/fzaidi-dai/RAG_Wright", rev = "<sha>" }`) until its release lands.

## Later (recommended): rebuild the seam on the reference seam's shape

RuleWright's seam (`src/rulewright/engine/seam.py`, about 1,500 lines, 49 functions) reaches into engine internals
everywhere: none of its 52 engine symbols comes from `rag_wright.api`. That is why every engine release becomes a
migration. The target shape is the engine's worked reference seam, **`src/rag_wright/packs/reference_seam.py`**
(`ContractComplianceSeam`, about 130 lines):

- **Registration first:** its constructor registers the capabilities it invokes (`load_reference_pack()`).
- **Every method is one engine call** through `rag_wright.api` (`open_workspace`, `ainvoke_subgraph`,
  `aparse_document`, `entities_by_name`) or a pack store (`ContractKGStore`, `ComplianceStore`): ingest a contract, ask
  a contract, search the corpus, find a party, counterparties, affiliates, contract terms, span locations, the clause
  vocabulary, ingest a policy, check a subject, requirements and their locations.
- **No engine internals:** no `ArcadeDBStore`, embedder, model id or id-string parsing in the seam.
- **Product concerns stay in RuleWright**, around those calls, where the reference seam marks `# PRODUCT OWNS:`:
  tenancy and auth, scoping, the advertising-compliance variants, presentation and citation types, caching, telemetry
  routing, settings. (`docs/product/seam-adaptation-guide.md` in the engine repo walks through the adaptation.)

Rebuild RuleWright's seam by mapping each of its engine-facing functions onto the matching reference-seam method (or
the `rag_wright.api` call behind it) and keeping its product concerns around that call. The public surface is listed
in `docs/api/README.md`; the product-starter templates and the `using-the-rag-wright-engine` skill describe the same
layout.
