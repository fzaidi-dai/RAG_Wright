# RAG_Wright: Foundations & Building on Top

A reference for teams building products on the engine (RuleWright and others). Read this before designing a
product surface or adding a domain. It states the foundation concepts, then the method for extending the engine.

---

## 1. What the engine is

RAG_Wright is a domain-neutral RAG + knowledge-graph engine. It is the open-core.

It ships one **reference domain pack** — contracts + compliance — as a worked example. Everything domain-specific
lives in that pack, not in the engine.

Products sit on top. The dependency is one-way: **Product → Engine, never Engine → Product.** This is what keeps
the engine retargetable to any domain.

## 2. The core principle: neuro-symbolic

This is the axiom the whole design serves.

- The knowledge graph **grounds, constrains, and asserts** facts, and supplies structured context.
- The LLM **reasons over that structure**. It does not brute-force.
- A query is one **bounded** LLM call over a symbolic gate plus retrieved evidence. Never the whole document in a
  prompt.
- **Every claim carries a citation.** No citation, no claim.
- Deterministic work stays deterministic and testable. Generative work is used only where it earns its place.

Why: precision, auditability, and cost. The graph does the exact work; the model does the reasoning.

## 3. Ontology is the source of truth

Domain **knowledge** lives in a `.ttl` ontology pack: vocabularies (closed value sets), schema (classes,
properties, KG edge types), constraints (applicability, cardinality, deontic polarity — authored as SHACL), and
synonyms.

Code holds **mechanism** only: the pipeline, the router, the extractor, the judge, the prompt overlay.

Two rules follow:

- **A new domain is a `.ttl` pack, not an engine edit.** The vast majority of a new domain is knowledge, not code.
- **Staleness is solved by generation + CI diff**, never by moving truth into code. When code needs a generated
  view of the ontology, it is generated and checked for drift. The source stays in the `.ttl`.

## 4. Capabilities and how callers reach them

Every capability is registered and ARD-discoverable (`urn:air:...`). There are six kinds:

`function` · `model` · `agent_skill` (a Skill) · `subgraph` (a LangGraph composite) · `mcp_tool` · `dagster_asset`

- Callers use the Python entrypoints. Agents also use the `mcp_tool` surfaces (discoverable via ARD).
- The engine gives you **composable capabilities, some composite subgraphs, and MCP tools.**
- It does **not** give you the top-level agent workflow. The **product hand-builds the orchestration** that strings
  capabilities into a user operation. (GraphWright, the orchestration compiler, is parked; hand-built orchestration
  is the interim, and the future spec.)

So the highest abstraction the engine exposes is a composite subgraph or an MCP tool — not a finished domain
workflow.

**MCP tools never take a model-supplied tenant.** A tool never accepts a tenant, database, or scope as an
argument — those are filled by the model, so a tenant argument is a cross-tenant read one token away. The store
is bound per session by the **caller**, out-of-band (a `store_resolver` on the server factory, resolved from the
MCP request context), never from a model argument or a per-process env var in a multi-tenant deployment. The
engine enforces this (a build-failing guard) and provides the binding seam; tenancy itself is product policy.
(ADR-0099.)

## 5. Adding a domain — ontology first

Order matters. Most of the effort is steps 1–2, not new capabilities.

1. **Author the ontology pack** (`ontology/packs/<domain>.ttl` + a bridge): vocabularies, schema, KG edges, SHACL
   constraints, synonyms. This is the primary lever.
2. **Bootstrap the extraction template** from the ontology, then hand-maintain it. Extraction is domain-shaped.
3. **Add a classifier / entity registry / resolver only if the domain needs one.** The SEC/EDGAR resolver is the
   plug-in slot; a new domain supplies its own.
4. **Reuse the generic pipeline and the domain-general query legs** on the new KG. Do not re-implement ingest. The
   query capabilities (`typed_property_retrieval`, `intra_document_qa`, `relational_qa`, `graph_query`) work on any
   KG built to the shared identifiers.
5. **Add new `function` / `subgraph` / `mcp_tool` capabilities only for genuinely new use-case shapes.**
6. **Eval and harden** each capability and each exposed surface.
7. **Expose** the composite subgraphs + MCP tools. The product hand-builds the orchestration on top.

**Compliance-shaped domains reuse the compliance module.** If a domain is "requirements/criteria vs a subject
document" (e.g. RFP/bid: criteria vs a proposal), the compliance pipeline applies directly. It is domain-general —
the same `compliance_check` ran across advertising, workplace safety, and a customer policy with **zero per-domain
code**, differing only in ingested requirements and an optional applicability ontology. The new code is the parts
genuinely unique to the domain (e.g. scoring, weighting, award logic).

## 6. What the product (caller) owns

- UI/UX, and the agent / chat harness.
- The product-named tool surface, and the orchestration of engine capabilities into user operations.
- **Config:** the store, ArcadeDB, models, and environment. Models can be passed as arguments or set by env; the
  engine defines the seams (the `Store` protocol, the model-profile seam), the caller supplies the concrete config.
- Guardrails, human-gate policy, and the user-feedback loop.
- Any proprietary domain packs it chooses not to open-source.

## 7. Open vs closed

- **Open-core:** the engine mechanism + the reference domain pack.
- **Closed (product):** the app, UI, orchestration, and any proprietary domain packs.
- A new domain pack can be either — decide per pack.
- **Exception:** restrictively-licensed eval corpora (CUAD/ACORD) never ship in the open repo.

---

## References (ADRs)

- Engine/product split; GraphWright parked; ARD standing — **ADR-0052**
- Ontology is the single source of truth; solve staleness by sync, not by demoting the source — **ADR-0066**
- The extraction template is authoritative code, bootstrapped from the ontology then hand-maintained — **ADR-0037**
- Deontic applicability gates (query-side neuro-symbolic routing) — **ADR-0065**
- Neuro-symbolic judge cascade (grounding + SHACL + semantic judge) — **ADR-0040 / ADR-0028**
- Function gate not load-bearing; whole-index pool + property boost + rerank — **ADR-0047**
- The generic ingestion pipeline + the one-adapter rule — the `corpus_ingest` SKILL
