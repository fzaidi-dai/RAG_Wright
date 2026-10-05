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

**36 capabilities**, across two reference domains plus generic primitives:

| kind | count | examples |
|---|---|---|
| `subgraph` | 9 | `contract_ingestion_pipeline`, `intra_document_qa`, `typed_property_retrieval`, `relational_qa`, `compliance_ingestion`, `compliance_check` |
| `function` | 9 | `typed_clause_extraction`, `typed_value_normalization`, `extraction_grounding_judge`, `clause_exception_linking`, `query_constraint_extraction` |
| `model` | 3 | `clause_function_classification`, `clause_property_classification` (SetFit fleet), `jev_decision` (typed-decision model) |
| `agent_skill` | 11 | `rlm_method`, `rlm_chunking`, `rlm_synthesis`, `generation`, `vision_to_text`, `requirement_extraction`, `claim_extraction`, `compliance_judgment` |
| `mcp_tool` | 4 | `compliance_check_mcp`, `intra_document_qa_mcp`, `relational_qa_mcp`, `typed_property_retrieval_mcp` |

Two reference domains over the shared store: **contract** (ingestion → the clause KG + hybrid index → scoped QA,
typed-property retrieval, relational QA) and **compliance** (FTC 16 CFR 255 endorsement rules → requirements →
checking a subject's claims against them), plus generic RLM / generation / OKF primitives and four MCP tool
surfaces.

The domain **knowledge** lives in the ontology bridges, not in code (ADR-0066): `ontology/contract_bridge.ttl`,
`ontology/compliance_bridge.ttl`, and the domain pack `ontology/packs/ftc_16cfr255.ttl`. Thin worked-example
facades live under `rag_wright/reference/`.

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
- `contract_ingestion_pipeline` shows an ingestion graph composing the generic primitives (parse → chunk → segment
  → classify → extract → embed → write); your domain supplies its own graph over the same primitives
  ([KG construction](domain-adaptation/kg-construction.md));
- `clause_property_classification` / `jev_decision` show a trained classifier and a System-1 decision model wired as
  `model` capabilities ([classification & decision models](domain-adaptation/classification-and-decision-models.md));
- `intra_document_qa` / `typed_property_retrieval` / `relational_qa` show cited retrieval/QA legs to adapt.

## Not shipped: the restrictively-licensed corpora

The reference pack does **not** include CUAD or ACORD (the contract evaluation corpora) — they are
restrictively licensed and have a separate acquisition path. The pack ships its ontology, pipelines, and small demo
fixtures only; the engine stays installable and demoable without them.
