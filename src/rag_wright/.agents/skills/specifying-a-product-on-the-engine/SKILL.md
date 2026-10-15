---
name: specifying-a-product-on-the-engine
description: Turn a product's high-level, technology-neutral spec into its low-level SPEC.md for a product built on the RAG_Wright engine - every requirement classified into the engine's shapes (pack, ingestion capability, query capability, operation, agent, screen, eval), mapped to the exact public API, skill and doc to ground against, with the stack the engine fixes separated from the choices still open, and the development process implied in the order of the requirements. Load at the start of a new product (after the high-level spec exists, before any plan or task) and whenever a new feature is added to the spec. Layered on spec-driven-development.
---

# Specifying a product on the engine

Repository paths in this skill (`docs/`, `src/`) are in the engine repository: read them there or on GitHub, at
the tag matching your installed engine.

> **What this adds to `spec-driven-development`.** That skill gives the shape of a good spec: objective, commands,
> structure, style, testing, boundaries, success criteria, open questions, and the gated Specify → Plan → Tasks →
> Implement flow. Follow it. This skill adds what a product on THIS engine needs on top: a high-level spec says
> *what* the product does; the low-level spec must also say, for every item, **which engine shape it takes and
> where the developer grounds it**, so planning and implementation do not rediscover the engine one task at a time.

## When to use

- A product has a high-level spec (features, screens, success criteria, data inventory), usually written without
  the engine in mind, and needs its `SPEC.md`.
- A feature is being added to an existing product's spec.

Not for engine work: the engine's own specs follow its own process.

## Inputs, read in this order

1. **The high-level spec and the data inventory** (what the customer gave you, and what each file is).
2. **The engine's mental model:** `docs/concepts.md`, `docs/architecture.md`, `docs/workspaces.md`.
3. **The domain-adaptation guide** `docs/domain-adaptation/README.md` (the build sequence) and its companions:
   ontology authoring, KG construction, entity resolution, authoring capabilities, classification and decision
   models, evals from experts' files. **And `docs/domain-adaptation/_engine-gaps.md`**: what the engine does not
   do yet, so the spec does not assume it.
4. **The public surface:** `docs/api/README.md` (`rag_wright.api`, the product's seam), `docs/api/pack_sdk.md`
   (`rag_wright.pack_sdk`, your pack's capability code), `docs/api/harness.md` (`rag_wright.harness`, your agent).
5. **The engine-shipped skills** linked in your `.claude/skills/`: `using-the-rag-wright-engine`,
   `authoring-a-capability`, `building-an-ingestion-capability`, `creating-evals`, `building-an-agent-harness`,
   `building-a-product-backend`, `building-a-product-frontend`, `classifier-opportunity-analysis`, `setfit`, `laya`.
6. **The reference pack** (`docs/reference-pack.md`): a worked example of every shape below, in a different domain.
   Read it as a pattern, never copy its vocabulary.

Read; do not skim. A spec that names an API the engine does not have costs a task to discover it.

## Step 1: number every item in the high-level spec

Walk the high-level spec and give every capability, screen, workflow, interaction rule, data reality, constraint
and success criterion an id (`PR-N` for requirements, `AC-N` for acceptance, `NFR-N` for non-functional, or the
product's own scheme). Keep a reconciliation table: high-level section → ids. **Nothing in the high-level spec may
be dropped silently**: an item the engine cannot serve becomes an open question or an engine request, never an
omission.

## Step 2: classify each item into the engine's shapes

Every product item lands in one or more of these. The route column is where the developer grounds the call
(against the `engine` grounding lane, then `inspect.signature` on the installed package).

| Shape | Typical high-level wording | Engine route (ground these) | Skill | Doc |
|---|---|---|---|---|
| **Domain knowledge**: vocabulary, schema, constraints, synonyms, decision criteria | "controlled vocabularies", "the ontology", "mandatory fields", "valid ranges" | `EngineConfig(pack=...)` (the store creates the declared types on `open_workspace`); your capabilities read value sets and SHACL shapes with `rdflib` / `pyshacl` | `authoring-a-capability` | ontology authoring |
| **Ingestion**: documents, spreadsheets, images, embedded files into the graph | "parse the forms", "load the lab reports", "idempotent re-runs" | `build_ingestion` (your extractor + optional hooks) → `aingest`; `IngestSource`, `IngestionTuning`, `table_rows` / `TableRow`, `evaluate_ingestion`, `check_extraction`; `KgNode` / `KgEdge` | `building-an-ingestion-capability` | KG construction |
| **Entity and registry resolution** | "resolve against the yarn register", "queue unresolved mentions" | your `document_hook`; the entity-resolution building blocks on `rag_wright.pack_sdk` | `building-an-ingestion-capability` | entity resolution |
| **A typed query or retrieval** | "find similar cases", "filter the table", "the dossier" | a query capability of your pack composing the primitives (`kg_read`, `kg_edges`, `NOT_NULL`, `Range`, hybrid span search with `span_search` from `rag_wright.api`), registered with `CapabilityManifest` + `register_capability` / `load_pack`, invoked with `ainvoke_subgraph` | `authoring-a-capability` | authoring capabilities |
| **A cited answer, or a refusal** | "every answer cites its source", "say when it is not in the records" | `agenerate_answer` over `EvidenceItem`s (an answer states its support; "none" abstains), `ajudge_spans` | `authoring-a-capability` | concepts (answers) |
| **A typed decision or classification** | "classify the structure", "which of these is it" | `classifier-opportunity-analysis` first; then rules, a trained classifier, or the decision model (`jev_decision` from `engine_capabilities()`, criteria in the pack) | `classifier-opportunity-analysis` → `setfit` / `laya` | classification and decision models |
| **A deterministic workflow behind a button** (mode **D**) | "upload, confirm, save", "export CSV" | a product **operation** (one function per user operation) calling your capabilities; the route calls the operation | `building-a-product-backend` | domain-adaptation step 8 |
| **The user-facing operations themselves** | "a menu of queries", "the same answers in chat and on screen" | a product **operations registry** (id, version, typed parameters, result shape, kind, whether it writes) that both the routes and the agent's tools call; a **parity test** that each returns the same result from both; write operations never on the agent's tool surface | `building-a-product-backend` | none |
| **The same workflow reached from chat** (mode **A**) | "ask about this case", "continue in chat" | the operation exposed as a tool on your `ToolSurface`; the agent is an `AgentDefinition` run with `start_run` / `resume_run` / `stream_run`; its model from `agent_chat_model` | `building-an-agent-harness` | the harness reference |
| **An open-ended question** (mode **N**) | "investigate", "ask anything about the records" | the agent's orchestrator composing the tools at run time, with citations | `building-an-agent-harness` | the harness reference |
| **The agent's scope and guardrails** | "only answer from the records", "decline off-topic", "never recommend" | the agent's `AgentPrompts` and rules, the refusal through `agenerate_answer` ("none" abstains), the wording kept as reviewable data, never code; behavior tests per request class | `building-an-agent-harness`, `creating-evals` | the harness reference |
| **A screen or interaction rule** | "the dossier screen", "the chat panel", "citations open the record" | the front end over the generated API client | `building-a-product-frontend` + `frontend-ui-engineering` | none |
| **A success criterion** | "95% of fields correct", "top-3 useful", "20-question set" | an eval written FIRST: `aload_gold_set`, `arun_eval`, `EvalReport`; `aevaluate_ingestion` for extraction | `creating-evals` | `docs/evals.md`, evals from experts' files |
| **Cost and usage** | "log cost per case and per query" | `measure_usage`, `traced_run`; every engine model call is metered | none | configuration (tracing) |
| **The model boundary** | "managed or self-hosted, swappable" | model ids in `EngineConfig.models`; `register_model_profile` for your own endpoint; `adeploy_model_server` for a self-hosted Qwen | `qwen-vllm-modal` | configuration |
| **The store, provenance and tenancy** | "one store", "provenance on every record", "single tenant" | the product's own ArcadeDB; `open_workspace` (`create=False` for a keeper), `temporary_workspace` for trials; every record carries `span_id` + a confidence tag; a `Document` node per source | `using-the-rag-wright-engine` | installation, workspaces |

**Classify every operation's mode (D / A / N) in the spec**, not later: it decides whether a model may be in its
path (never in an unambiguous button click) and whether it needs the harness.

## Step 3: two patterns a high-level spec rarely states but always needs

**An existing domain ontology becomes a bridge pack.** Customers often arrive with their own OWL/RDFS ontology.
The engine reads only its own declaration vocabulary (`eng:KgVertexType`, `eng:KgStructuralEdge`), so the domain
ontology stays the authority for meaning and a **bridge** `.ttl` declares the graph types the store creates, over
it. Prefer generating the bridge's declarations from the domain ontology with a CI drift check (regenerate, diff,
fail) to hand-maintaining a second copy; the reference pack's bridges over their source ontologies are the pattern.
Spec it as its own requirement, with the mapping decisions (which classes become vertex types, which object
properties become edges, which literals become typed properties, what is the unique key) as reviewable items.

**Existing instance data becomes a gold set, not an import.** Instance graphs or reference loaders that came with
the data are the best evidence of the intended mapping. Production ingestion re-derives the graph from the
sources through `build_ingestion`; the provided instances become the **ingestion eval's gold** (counts, keys, a
handful of hand-checked records), written before the extractor. The assumptions the provided loaders made are
requirements too: list each one and say whether the production extractor reproduces it, refines it, or drops it.

## Step 4: separate what the engine fixes from what is still open

A technology-neutral high-level spec leaves the stack open; building on the engine closes part of it. Put both in
the spec's stack section:

- **Fixed by the engine** (record, do not re-decide): Python with uv on the engine's pinned version range; the
  store (the product's own ArcadeDB, reached only through the engine); the model layer (engine model profiles,
  managed or self-hosted behind one id); the agent harness (`rag_wright.harness` on Deep Agents / LangGraph);
  ingestion and evals (the engine's).
- **The product's to choose** (propose with rationale, approved before building): the web framework and its
  relational store (sessions, jobs, the harness checkpointer), the front-end framework, authentication, deployment.

**Where the high-level spec assumed a technology the engine does not use, say so as a decision**, with options and
a recommendation, never by quietly substituting. A spec that asks for an RDF store with named graphs, for
example, is usually asking for per-source separation and provenance; state how the engine meets that (a
`Document` per source, provenance on every record, or one database per source) and let the reviewer approve the
reading.

**Name the models.** The engine fixes HOW models are reached, not WHICH: state the engine model id per role the
product uses (the engine's defaults, or registered profiles), whether a decision model (`jev_decision`) handles typed
choices, the self-hosted path if on-premises matters, and when trained classifiers would replace model calls
(`classifier-opportunity-analysis`). A model is configuration; the spec records the choice, the code never names it.

**Check `_engine-gaps.md` against every route you chose.** A requirement that lands on an open gap is marked with
the gap id and what the product does meanwhile (and an engine request if the gap is not listed). **And confirm every
primitive on a tier before designing around it**: a doc sentence saying a capability "composes X" is not proof that X
is public; check `docs/api/` (or `__all__` on the installed engine). An unlisted missing primitive is an engine
request the spec names.

## Step 5: write the SPEC

Use the `spec-driven-development` sections, and give every requirement this block:

```markdown
### PR-7: <the requirement, in the customer's words where possible>
- **Acceptance:** AC-7 (<how we know it works: measurable, against named data>)
- **Shape and mode:** query capability · D (button) and A (chat tool)
- **Engine route:** <public symbols to ground first, e.g. a capability over kg_read + agenerate_answer>
- **Skills:** authoring-a-capability, creating-evals
- **Docs:** domain-adaptation/authoring-capabilities.md
- **Eval:** <the gold set and the gate, written before the implementation>
- **Depends on:** PR-3 (the pack), PR-5 (ingestion)
- **Grounding note:** the route above is the starting point; grounding against the installed engine may find a
  better public route, and the implementer takes it and records why.
```

Add these sections beyond the generic ones:

- **Engine dependency and boundary**: the floor version, the dependency mode, `rag_wright.api` / `pack_sdk` /
  `harness` as the only doors, engine requests for gaps.
- **The domain pack**: the ontology files, the bridge, the value sets and constraints, and who may change them.
- **The capability catalog and its definition of done**: every capability the pack registers (slug, kind, what it
  does, which operations use it, the engine capabilities it builds on), and one requirement binding all of them to
  the `authoring-a-capability` definition of done: an `ainvoke` factory over the workspace handle, a
  `CapabilityManifest` registered by the pack's `register()` through `load_pack`, invocation by name only (never a
  direct import of the implementation), the ARD manifests published, a product-side conformance test, and the eval
  first. Deterministic product functions with no model (a rules layer, a filter) stay plain functions.
- **Models**: the model per role and the decision model (Step 4), and when classifiers come in.
- **Orchestration**: every operation with its D / A / N mode and the one implementation both callers share; the two
  registries kept apart (the engine's capability registry, the product's operations registry).
- **The agent's guardrails**: what is in scope, what is refused and how, what is declined, and any request class the
  product answers only with cited precedent; which operations the agent may call (read-only unless the spec says
  otherwise).
- **Settled data semantics**: every interpretation the data inventory or the provided loaders settled (how a value
  range, a unit, a code or a verdict is read) as its own tested requirement, so production encodes it rather than
  rediscovering it.
- **Evals**: one per success criterion and one per capability, with its gold set's source and size.
- **Data handling**: which files are client data, where they live, what may be committed, what may leave the
  machine (model endpoints only).
- **Reconciliation**: the Step 1 table, so a reviewer can see every high-level item landed.

## Step 6: let the order of the requirements carry the process

A developer builds a product on the engine in a fixed order, and the spec's dependency column should make it
visible:

1. **Phase 0**: the repo, its own ArcadeDB, the engine dependency, the grounding lanes, the linked skills, the
   guard tests, the engine seam registering capabilities at startup.
2. **The domain pack** (ontology, bridge, value sets, constraints), checked by loading it into a disposable
   workspace.
3. **Ingestion**, eval first: the gold from the provided instances or the experts' files, then the extractor and
   hooks until the eval passes on the real samples (`evaluate_ingestion` before a full run).
4. **Query capabilities**, each eval first, over the populated graph.
5. **Operations** (mode D) and their routes, each with a live engine-seam test.
6. **The agent** (modes A and N) on the harness, its tools being the same operations.
7. **The screens**, surface by surface, each shipped with its backend operation.
8. **The success-criteria evals** end to end, then the findings.

Tasks follow this order; the plan may interleave where a dependency allows.

**Keep the reconciliation live.** When the high-level spec changes (a fuller version arrives, a section is
renumbered), re-walk it and update the table before anything else: a requirement that silently lost its source, or
a new high-level item with no requirement, is how a spec drifts from what was agreed.

## Step 7: hand off to planning

`tasks.md` entries carry, from the spec: the requirement and acceptance ids, the engine route to ground first,
the skills to load, the eval to write first, and the grounding note. That is what lets an implementer start
grounding instead of searching.

## Anti-patterns

- **Naming an engine internal in the spec.** Only `rag_wright.api`, `rag_wright.pack_sdk` and
  `rag_wright.harness` symbols; anything else is an engine request.
- **Copying the reference pack's vocabulary or shapes into the domain.** It is a different domain.
- **Specifying a workaround for an engine gap in product code.** The spec names the gap and the request; the
  interim stays on the public surface.
- **A success criterion with no eval behind it**, or an eval written after the implementation it judges.
- **A model in the path of an unambiguous button click.** If the operation is D, the spec says so.
- **Dropping a high-level item because the engine does not cover it.** It becomes an open question or a request.
- **Restating the high-level spec without the mapping.** The value of the low-level spec is the route column.

## Verification checklist

- [ ] Every high-level item has an id and appears in the reconciliation table
- [ ] Every requirement has its shape, mode, engine route, skills, docs, eval and dependencies
- [ ] Every engine symbol named is on `rag_wright.api`, `rag_wright.pack_sdk` or `rag_wright.harness` in the
      installed engine (checked, not assumed)
- [ ] Every route was checked against `_engine-gaps.md`; gaps are named with what the product does meanwhile
- [ ] The stack section separates what the engine fixes from the product's open choices, each with a rationale
- [ ] Every mismatch between the high-level spec's assumptions and the engine is a decision with options
- [ ] The domain pack, the bridge and the ingestion gold are their own requirements
- [ ] The capability catalog is in the spec, with one requirement binding every capability to the definition of done
- [ ] The model per role and the decision-model choice are stated
- [ ] User-facing operations are one registry both tiers call, with a parity test; write operations are not agent tools
- [ ] The agent's guardrail policy is a requirement with behavior tests
- [ ] Every settled data semantic is a tested requirement
- [ ] Every success criterion and every capability has an eval, written first
- [ ] Client data handling is explicit: where it lives, what is committed, what leaves the machine
- [ ] The requirements' dependency order is the build process above
