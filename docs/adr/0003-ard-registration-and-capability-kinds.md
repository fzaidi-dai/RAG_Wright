# ADR-0003: ARD registration — schema mirroring and capability kinds

- Status: Accepted
- Date: 2026-07-05
- Deciders: farhan.zaidi@dreamai.io, Claude Code
- Phase: 3 (Contracts), task T6 (capability registration seam)

## Context

Every built capability has two registrations from one key, its FR-C / FR-I / FR-Q name (tasks.md
"ARD registration"): an internal registration (by name, with its contract, for the MCP skill
surface, T31) and an Agentic Resource Discovery (ARD) registration (a manifest the GraphWright
compiler discovers and binds). The ARD manifest must conform to GraphWright's `RegistryEntry` schema
(GraphWright ADR-0005), which its `RegistryStore` loads and validates. RAG_Wright is the capability
half; GraphWright is the compiler half. This ADR records how RAG_Wright conforms to that schema and
how each capability's ARD `kind` is chosen.

## Decision

1. **Emit ARD manifests as JSON; do not import GraphWright.** RAG_Wright authors manifests as JSON
   conforming to the ADR-0005 schema; GraphWright's `RegistryStore` validates them on load. The
   capability half does not depend on the compiler half (that direction would be backwards, and a
   dependency is ask-first). Adding a `graphwright` dependency is rejected.

2. **The ADR-0005 schema is a shared wire-format contract, mirrored by deliberate duplication.**
   RAG_Wright re-declares the `RegistryEntry` schema (envelope, trust manifest, response bounds,
   governance, kind→media-type map, and all validators) in `src/rag_wright/capabilities/ard.py`,
   mirroring GraphWright's `src/graphwright/registry/entry.py` verbatim (ARD v0.9, camelCase wire,
   `extra="forbid"`). A RAG-side conformance test validates RAG's manifests against this mirror, so
   drift is caught in RAG_Wright's own suite, not only at GraphWright load. **A change to ADR-0005's
   schema is a cross-repo coordination point:** the mirror and GraphWright's `entry.py` must be
   updated together, or a manifest that validates on one side fails on the other.

3. **Kind is explicit per capability, no blanket default, by this binding rule:**
   - `mcp_tool` — the capability crosses the Model Context Protocol boundary (a query-side governed
     skill, FR-S.5): `hybrid_search`, `graph_query`.
   - `function` — an in-process graph-node call: `parsing`, `embedding`, `reranking`, `fusion`,
     `generation`.
   - `agent_skill` — loaded knowledge, not callable: `rlm_chunking`, `rlm_synthesis`, `rlm_method`.
   The kind follows from how a capability is bound and is assigned at the capability's own task; the
   ingestion-side capabilities not listed above (`graph_extraction`, `entity_resolution`,
   `ontology_registry_derivation`) take their kind by the same rule at their tasks. A capability
   that fits none of the six governed kinds (`agent_skill`, `mcp_tool`, `function`, `model`,
   `subgraph`, `dagster_asset`) is flagged, not forced into a wrong one. Blanket "all mcp_tool" and
   "all function" were both rejected: the first falsely exposes internal functions over MCP, the
   second erases the required MCP surface.

4. **Identity URN:** `urn:air:dreamai.io:rag_wright:<slug>`, where `<slug>` is the capability's
   **canonical slug** from SPEC.md section 5 ("FR-C canonical slugs") — the semantic name, never a
   requirement id (`hybrid_search`, never `fr-c-3`), so it survives spec renumbering. It is the
   cross-spec join key the Orchestration Spec binds against and the internal registry key. The
   `urn:air:` scheme, and the rule that `<publisher>` is a domain-anchored FQDN, are defined by the
   **ARD specification** (ARD v0.9 section 4.2.1, "Agent Identifier Format and Rationale":
   https://github.com/ards-project/ard-spec/blob/main/spec/ard.md) — ARD is **Agentic Resource
   Discovery**, and `air` is its registered URN Namespace Identifier (not an abbreviation to expand).
   Our publisher is the FQDN `dreamai.io`; the generic `urn:air:` schema validator mirrors
   GraphWright's (any publisher), while the exact `urn:air:dreamai.io:rag_wright:` prefix is enforced
   where we author and write our own manifests. The
   registry mirrors the canonical slug set (like the schema mirror, a cross-repo coordination point)
   and **rejects a non-canonical name**. `generation` (FR-C.9) is a single slug: reasoning,
   generation, and vision-to-text are one capability bound wherever needed, not two. `governance.owner`
   defaults to `dreamai.io`; callable kinds get a default `ResponseBounds` (max 25,000 tokens) the
   capability task may refine.

5. **The emitted skeleton is a draft, kept out of the loadable path.** Registration derives what it
   can (URN, kind, media-type, response bounds); the authored fields (representative queries 2-5,
   trust attestations) are filled at the capability's own task via `ManifestSkeleton.author(...)`,
   which returns the validated `RegistryEntry`. A `ManifestSkeleton` is never a `RegistryEntry` and
   cannot validate as one. Because `RegistryStore` globs `*.json` non-recursively at the registry
   root, drafts live in a `staging/` subdirectory (never globbed) until authored, so neither the
   store nor the compiler's glob ever loads a partial as if it were registered.

6. **One shared registry root, config-addressed.** `ard.write_manifest` resolves the write location
   from the `ARD_REGISTRY_ROOT` environment variable — the same variable GraphWright's `RegistryStore`
   reads, per GraphWright's `docs/authoring/registry-root.md` — defaulting to `~/.air/registry` when
   unset, creating the directory if absent, and writing each authored manifest to `<root>/<slug>.json`
   (the flat, non-recursive layout the store globs). We read that one shared root; we never create a
   second or project-local root and never hardcode a path.

## Consequences

- The two repos stay decoupled in the correct direction; RAG_Wright ships ARD manifests without a
  compiler dependency.
- Drift between the mirror and GraphWright's schema is the one maintenance cost, made explicit as a
  cross-repo coordination point and guarded by the RAG-side conformance test; a real drift surfaces
  as a located validation error, not a silent divergence.
- Each capability's `kind`, and therefore its media-type and callable-vs-loaded rules, follows from
  its binding, decided at its own task; the registration seam refuses an unknown kind.
- A partial manifest can never be discovered or bound: only an authored, validated `RegistryEntry`
  reaches the loadable path.
- The manifest `<name>` must match the shared-spec capability name exactly; if the Orchestration
  Spec's binding name for a capability differs from the name used here, that is a spec-level
  reconciliation, since the name is the join key across both documents.
