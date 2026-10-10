# The reference pack

The engine ships a **reference pack**: a contract/compliance worked example, built as ordinary capabilities, so the
open-core is runnable and demoable out of the box (it's what [`quickstart.md`](quickstart.md) uses). It is an
**example to read and copy, not the product** — your domain brings its own `.ttl` pack and capabilities
(see [Building a new domain](domain-adaptation/)).

## Loading it

The engine ships an **empty** ARD catalog (`MANIFEST_SPECS == {}`). Opt into the reference pack explicitly:

```python
from rag_wright.api import load_reference_pack, capability_index
load_reference_pack()          # registers the 36 reference-pack capabilities
capability_index()             # {slug: {kind, description}} — now populated
```

A product registers its **own** capabilities with `register_capability(manifest)` instead (or in addition). Nothing
is registered until you ask — a fresh install stays domain-neutral.

## What's in it

**36 capabilities**: 29 reference-pack capabilities across two reference domains, plus the 7 generic engine
capabilities the pack builds on (`rlm_method`, `rlm_chunking`, `rlm_synthesis`, `generation`, `vision_to_text`,
`span_relevance_judgment`, and the `jev_decision` decision model; listed by `engine_capabilities()` and registered
along with the pack):

| kind | count | examples |
|---|---|---|
| `subgraph` | 9 | `contract_ingestion_pipeline`, `intra_document_qa`, `typed_property_retrieval`, `relational_qa`, `typed_clause_extraction`, `query_constraint_extraction`, `requirement_extraction`, `compliance_ingestion`, `compliance_check` |
| `function` | 9 | `typed_value_normalization`, `extraction_grounding_judge`, `extraction_semantic_gate`, `clause_exception_linking`, `clause_disambiguation` |
| `model` | 3 | `clause_function_classification` (SetFit ensemble), `clause_property_classification` (a 29-dimension SetFit/Laya fleet), `jev_decision` (engine typed-decision model) |
| `agent_skill` | 10 | `rlm_method`, `rlm_chunking`, `rlm_synthesis`, `generation`, `vision_to_text`, `span_relevance_judgment`, `extraction_semantic_judge`, `claim_extraction`, `compliance_judgment` |
| `mcp_tool` | 4 | `compliance_check_mcp`, `intra_document_qa_mcp`, `relational_qa_mcp`, `typed_property_retrieval_mcp` |

Two reference domains over the shared store: **contract** (ingestion → the clause KG + hybrid index → scoped QA,
typed-property retrieval, relational QA) and **compliance** (FTC 16 CFR 255 endorsement rules → requirements →
checking a subject's claims against them), plus generic RLM / generation primitives and four MCP tool
surfaces.

The domain **knowledge** lives in the ontology bridges, not in code (ADR-0066): `packs/contracts/ontology/contract_bridge.ttl`,
`packs/compliance/ontology/compliance_bridge.ttl`, and the domain pack `packs/compliance/ontology/packs/ftc_16cfr255.ttl`. The reference pack is
two domain packs under `rag_wright.packs` (ING-8c): `packs.contracts` and `packs.compliance`, which builds on it. Each
has a `pack.py` holding its manifests (`CONTRACT_SPECS` / `COMPLIANCE_SPECS`), its canonical slugs and its
`register()`; `load_reference_pack()` is `load_pack("rag_wright.packs.compliance.pack")` (the compliance pack registers the contracts pack it builds on first). The thin
worked-example facades are `packs.compliance.invokers` and the product-seam example `packs.reference_seam`.

## Its schema is not in the default

The contract types (`Clause`, `Contract`, `PropertyValue`, the typed property edges, ...) and the compliance
`Requirement` type are **not** in the engine's default schema: a new workspace has only the neutral types. Each pack
keeps its domain store methods off the generic store, in a store extension that wraps it and ensures its schema on
construction: `ContractKGStore` (`rag_wright.packs.contracts.capabilities.contract_kg_store`) creates the schema
declared in `contract_bridge.ttl` plus the typed property edge types, and `ComplianceStore`
(`rag_wright.packs.compliance.capabilities.compliance_store`) creates the `Requirement` type declared in
`compliance_bridge.ttl`. The reference pipelines and readers construct them, so the schema appears the first time
they run. To read those node types directly (for example `kg_read(ws, "Clause", ...)`) in a
fresh process before anything has used the contract store, open the workspace with the pack's ontology as its pack,
which creates `Clause`, `Contract` and `PropertyValue` and their structural edges:

```python
from rag_wright.api import EngineConfig
from rag_wright.packs.contracts.ontology.loader import reference_pack_ttl   # path of the reference contract ontology
config = EngineConfig(store=..., pack=reference_pack_ttl())
```

## Invocable by name (9 of 36)

A capability with an `impl_ref` is invoked **by name** through the engine (the rest are composed by import, loaded
as agent-skill knowledge, or served by a deployed MCP server). The 9 invocable ones:

- **subgraphs** (`ainvoke_subgraph`): `contract_ingestion_pipeline`, `compliance_ingestion`, `intra_document_qa`,
  `typed_property_retrieval`, `relational_qa`, `compliance_check`
- **models** (`invoke_model` / `ainvoke_model`): `clause_function_classification`, `clause_property_classification`,
  `jev_decision`

## Reading it as a template

A new domain mirrors the pattern, swapping the vocabulary and document shape:

- the **ontology bridges** show how to declare closed value sets, the KG schema, SHACL constraints, and mappings —
  copy the shape, change the domain ([ontology authoring](domain-adaptation/ontology-authoring.md));
- `contract_ingestion_pipeline` shows the engine's shared ingestion stages driven with legal hooks: a legal
  segmenter, a clause-function span tagger, a provision grouper with a decision-model boundary decider, the clause
  extractor and its writer. Your domain does not build a graph for this: it calls `build_ingestion(extractor, ...)`
  with its own extractor and overrides only the hooks it needs (the defaults are a docling-layout segmenter and a
  structural unit grouper), then checks the result with `evaluate_ingestion`
  ([concepts](concepts.md#ingestion-the-engines-pipeline-the-domains-extractor-adr-0124),
  [KG construction](domain-adaptation/kg-construction.md));
- `clause_property_classification` / `jev_decision` show a trained classifier and a System-1 decision model wired as
  `model` capabilities ([classification & decision models](domain-adaptation/classification-and-decision-models.md));
- `intra_document_qa` / `typed_property_retrieval` / `relational_qa` show cited retrieval/QA legs to adapt.

## How a clause gets its function and its citation (a domain decision)

The contract pipeline groups spans into PROVISIONS (a numbered section with its body) and extracts one clause per
provision. Which span represents the provision is a domain decision, made through the engine's `unit_representative`
hook; the reference pack passes `provision_vote` (ADR-0126): the clause's `function` is the label with the highest
classifier probability summed over the provision's operative members (those that are not heading labels: a
heading/title span, or a line that only names the section, such as "Section 9. Uncapped Liability."), and its
citation `span_id` is the operative member most confident in that label (whose soft tags also scope the property
classifiers). Measured on all 510 CUAD contracts: 56.6% provision accuracy, against 46.8% for heading-first and 45.2%
for the operative span alone.

Why: a heading is the weakest text to classify and a poor citation. Measured: in "Section 9. Uncapped Liability.
Notwithstanding Section 8, ... there shall be no cap on ... liability", the heading was tagged Cap On Liability and
the operative sentence Uncapped Liability; with the heading as the representative the clause became a Cap clause and
lost its carve-out link (`clause_exception_linking` links Uncapped clauses to Cap clauses). Without classifier
probabilities it falls back to `operative_span` (the first tagged operative member, then a tagged heading, then the
first member). A product with other documents writes its own rule and passes it
to `build_ingestion(unit_representative=...)`.

## Its trained weights: fetched, not shipped

Two things to know about these weights (ADR-0129). Nine of the 15 Laya checkpoints carry a `choice` temperature
above the Laya runtime's accepted range, so Laya clamps it and warns on load. The fleet's labels are unaffected,
because it emits options by rank and never reads the probability, but those nine checkpoints' probabilities are
sharper than calibrated: do not gate on them. The SetFit heads were pickled with scikit-learn 1.9.1, the engine's
floor.

The pack's two `model` capabilities load trained weights that are not in the wheel or the repository (about 16 GB):
the clause-type SetFit ensemble and the 29-dimension SetFit/Laya property fleet. They load from the models root
(`RAG_MODELS_DIR`; default the engine checkout's `data/models` when it exists, else `./data/models`). Fetch them
with:

```sh
uv run python scripts/fetch_reference_models.py            # into the models root
uv run python scripts/fetch_reference_models.py --dest data/models
```

The script downloads one archive per model from a private GCS bucket (Google Cloud credentials with read access,
`GOOGLE_APPLICATION_CREDENTIALS`), checks each archive's sha256 against the manifest before extracting it, and skips
a model already fetched. A product that owns copies of these weights points `RAG_MODELS_DIR` at its own directory.

## Not shipped: the restrictively-licensed corpora

The reference pack does **not** include CUAD or ACORD (the contract evaluation corpora) — they are
restrictively licensed and have a separate acquisition path. The pack ships its ontology, pipelines, and small demo
fixtures only; the engine stays installable and demoable without them.
