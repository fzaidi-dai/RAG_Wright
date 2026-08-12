# ADR-0052: Engine / Product split (open-core); GraphWright parked

Status: Accepted (foundational; reshapes the project boundary)
Date: 2026-08-12
Supersedes/updates: the three-piece "two-halves" framing (RAG_Wright capability half / GraphWright compiler
half / ARD registry). Updates CLAUDE.md (§0 Spec row + the boundary callout + a new Product section),
`docs/playbook.md`, and the `rag-wright-graphwright-two-halves` memory.
Related: ADR-0049 (generic-customer lens), ADR-0051 (living ontology), the product roadmap
(`docs/product/contracts_product_roadmap.md`).

## Context

RAG_Wright is now production-shaped as a reusable RAG/KG engine (ingestion + query pipelines, capabilities,
capability-named MCP servers, the store/model-profile seams, the ontology machinery). The product surface (the
"Contract Portfolio X-ray" + compliance app: the §7 `contract-xray` tools, the risk checklist, renewal wiring,
UI/UX, data-source integrations, guardrails, user-feedback loops) is a strategy draft and **barely exists in
code** — verified: none of `ask_contract` / `filter_contracts` / `scan_portfolio_risk` / `renewal_calendar` /
the `sme_default` checklist exist by name.

Two decisions drive this ADR:

1. **Split the engine from the product** (open-core). The engine becomes an open-source-able platform, reusable
   and retargetable to other domains; the product (the polished contract-management + compliance-checking
   application) is a separate, closed-source repo that depends on the engine. Rationale: adoption via open
   engine, differentiation held in the application layer, and — crucially — **timing**: because the product
   layer is not built yet, splitting now means product code is *born* in the product repo and never entangles
   the engine. This is the cheapest the split will ever be; building the product *into* this repo first would
   incur a real disentangling cost later.

2. **Park GraphWright** (the orchestration compiler). It is larger-scoped than first anticipated. Instead of
   compiling orchestration up front, the product will build production orchestration **by hand**; that
   hand-built orchestration becomes the concrete corpus of real workflows that a future GraphWright would learn
   to compress. You cannot design a good compiler before you know its target; hand-building first de-risks both.

## Decision

1. **Two artifacts, one seam.**
   - **Engine / Platform** = *this* repo (RAG_Wright), the open-core candidate: ARD registry, ingestion pipeline
     (parse / chunk / embed / KG-extract / resolve), query pipeline (retrieval legs, rerank, reason / generate),
     the capability-named MCP servers (`intra_document_qa`, `typed_property_retrieval`, `relational_qa`,
     `compliance_check`), the store seam, the model-profile seam, and the ontology *mechanism* (synced-artifacts
     machinery, the gap channel, the deontic backbone, and — when built — the ONT-EVOLVE-1 bootstrap/evolution
     engine).
   - **Product** = a *new, separate* repo (closed-source): UI/UX surfaces (MCP Apps, Slack / Teams, thin web),
     the product-named tool surface (`contract-xray`: `scan_portfolio_risk`, `renewal_calendar`, …), the
     hand-built orchestration / agent harness, data-source *integrations*, guardrails + human-gate policy, and
     the user-feedback loop *surfaces* (ONT-EVOLVE-1's interaction layer; the engine provides the mechanism).
   - The product depends on the engine; it gets its own CLAUDE.md, SPEC, plan, and tasks (its own working-loop
     instance).

2. **GraphWright parked.** Removed as an *active* assumption from the engine's governing docs. The engine no
   longer defers ingestion/query "graphs" to a compiler — those pipelines are ordinary engine software (they
   already are), and the product orchestrates capabilities by hand. GraphWright is revisited only after the
   hand-built product orchestration reveals what is worth compiling (its future spec). No GraphWright code,
   dependency, or `AC-N` scheme is load-bearing meanwhile.

3. **ARD is a first-class, standing commitment — independent of GraphWright.** ARD (Agentic Resource Discovery,
   `urn:air`) is an emerging open standard for discovering agent capabilities (Google-originated), expected to
   be broadly supported. We support it **because it is the standard**, not because of any compiler: ARD is not
   native to, nor bound to, GraphWright in any way, so parking GraphWright does **not** reduce ARD's role at all.
   Every engine capability registers in ARD as part of its definition of done. We currently run a **local
   instantiation** (local addresses / paths); the payoff is that if/when we want our capabilities discoverable by
   a global agent marketplace, an ARD-based registration makes that graduation trivial. ARD is also the natural
   engine↔product discovery seam (and, later, the GraphWright seam), but its primary rationale is
   standards-alignment and global discoverability, standalone. The product discovers and binds engine
   capabilities through ARD; it may additionally import them directly for convenience, but ARD registration is
   never optional on the engine side.

4. **Domain assets stay as a REFERENCE DOMAIN PACK.** Assets that are domain-specific but currently live in the
   engine — the contract ontology (`ontology/contract_bridge.ttl` + the compliance bridge), the compliance /
   requirement extraction+judge SKILLs (`skills/requirement_extraction`, `skills/generic_compliance_judgment`),
   the demo fixtures — remain in the open engine **as its worked reference example**, so the open-core is
   genuinely runnable and demoable. The product's moat is the *application layer* + curated/additional domain
   packs + UI, NOT the reference ontology. (Eval corpora with restrictive licenses — CUAD / ACORD — are the
   exception: they get a separate acquisition path, not shipped in the open repo; see hygiene checklist.)

5. **Strict dependency direction, enforced.** Product → Engine only; never Engine → Product. Enforced in CI with
   an import-linter rule (`import-linter` or `tach`) so an accidental reverse import fails the build. This is
   what keeps the two from fusing back into a distributed monolith.

6. **Repo mechanics.** This repo stays the engine and its CLAUDE.md / SPEC / `tasks.md` continue to govern the
   engine. The product repo is created separately (and may be cloned alongside for co-development) with its own
   governing docs. The product pins an engine version; the engine's public API becomes a stability contract.

7. **Open-sourcing hygiene checklist (gates the first public cut, not this ADR):**
   - License audit: CC-BY fixture attributions; CUAD/ACORD corpus licensing → separate acquisition path (not in
     the open repo); vendor docs under `docs/vendor/` (ADR-0008) reviewed for redistribution rights.
   - Secrets sweep (`.env`, keys, endpoints) — already gitignored; re-verify history.
   - A clean, documented public API surface + README/quickstart; `LICENSE` + `CONTRIBUTING`.
   - Confirm no customer-specific or proprietary data in the tree.

## Consequences

Positive:
- The boundary is drawn at the cheapest possible moment (product not yet built → nothing to disentangle).
- Open-core adoption path for the engine; differentiation retained in the closed product.
- The product moves fast with hand-built orchestration and simultaneously *produces GraphWright's requirements*.
- Leverages an existing seam (ARD) rather than inventing one.

Negative / costs (accepted):
- A two-repo versioning tax; the engine's public API is now a contract you cannot casually break.
- Open-sourcing hygiene is a real mini-project (above), gating the first public release.
- Every engine module must be classifiable as engine-vs-domain-pack; a few domain assets may later move to the
  product if they prove to be moat, not reference.

Deferred / not decided here:
- The product SPEC scope, surfaces, and phases (belong in the product repo).
- Which domain packs ship open vs closed beyond the contract/compliance reference.
- GraphWright resumption criteria (revisit once hand-built orchestration is real).

## Alternatives rejected
- **Build the product into this repo, split later.** Rejected: pays a disentangling cost precisely because the
  product would interleave with engine code; the current empty-product-surface timing makes a clean split free.
- **Keep GraphWright active and compile orchestration now.** Rejected: larger-scoped than anticipated, and
  designing the compiler before hand-building the target orchestration is backwards.
- **Treat ARD as GraphWright-coupled (and therefore optional now that GraphWright is parked).** Rejected on a
  factual correction: ARD is an independent emerging standard for agent-capability discovery, not a GraphWright
  feature. Supporting it is a standing commitment for its own sake (global discoverability); parking the compiler
  is irrelevant to it.
