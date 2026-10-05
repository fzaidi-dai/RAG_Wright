# ADR-0009: RLM semantic chunking is a retained, configurable capability — GATE-2 measures it, it does not delete it

> **Status: PARKED (ADR-0052).** A GraphWright / RLM-era decision, parked for now as part of the engine/product split; the referenced capability may still exist in code but is not treated as part of the current supported surface. Revisit or revive if a future need arises.


Date: 2026-07-11. Status: Accepted. Records the design intent behind RLM semantic chunking and the
Knowledge Graph, and constrains the GATE-2 / T22 "earns-its-cost" decision accordingly: the gate
measures the RLM chunker, but the decision space is keep-as-default vs. make-optional, never "drop."

## Context

GATE-1 (2026-07-09) A/B'd the RLM chunker against a fixed-window baseline on a dense-only proxy over 4
golden docs. It was mixed and confounded: RLM won boundary quality (0.920 vs 0.886) but lost recall@1
(0.200 vs 0.322), and the proxy could not see the sparse-over-full-text leg or the graph layer at all.
The recorded decision deferred the real "earns-its-cost" call to GATE-2 / T22, with the note "if
unproven there, make the RLM chunker optional, not dropped."

That note captured the operational conclusion but not the reasoning. The reasoning matters because the
naive reading of a gate — "if capability X doesn't beat the baseline on this corpus, remove X" — would
be wrong for this system, and a future session could apply it. This ADR records the why.

## Decision

**RLM semantic chunking is a first-class, retained capability of RAG_Wright, configurable per
deployment. GATE-2 (and any future corpus eval) measures it; no single corpus's eval result removes
it.** The only decision GATE-2 may reach on the RLM chunker is **keep-as-default vs. make-optional (a
seam/config toggle)** — never delete.

The rationale, as design intent:

1. **RLM chunking's job is efficient semantic boundary preservation.** It divides a document
   programmatically and recursively into portions that *mean something* — chapters, sections,
   sub-sections, or whatever the document's natural semantic units are — rather than fixed-window cuts
   that slice through the middle of a concept. Chunking that does not preserve meaningful boundaries
   degrades on complex questions that correlate multiple parts of a document (for example temporal
   reasoning over an event whose facts are spread across separate, seemingly unrelated passages).

2. **Meaningful chunks and the Knowledge Graph are two halves of one design, not alternatives.**
   Meaningful boundaries keep each concept intact *within* a chunk; the KG links facts *across* chunks
   (even unrelated ones) so cross-part and temporal reasoning is possible. This is precisely why
   ArcadeDB holds both the hybrid retrieval index and the KG in one store (FR-S.1, ADR-0007): there is
   no cross-store join to keep consistent, and meaningful chunks + KG together are the design for
   answering the most complex queries grounded and cited, without hallucinating or missing
   properly-grounded information.

3. **This is enterprise-grade RAG for any corpus, not eval-chasing on the current one.** The goal is a
   set of capabilities and tools that reliably insert and retrieve from *any* corpus. Every new corpus
   and deployment defines its own evals in the harness. A capability that does not win on the current
   CUAD/EDGAR corpus is not thereby proven useless: semantic chunking's advantage is corpus- and
   query-type-dependent, and the current corpus/query mix may be well served by regular chunking
   without proving that semantic chunking will not win elsewhere. The task, constraints, and evals
   defined for a given deployment determine what actually ships there.

## Consequences

- **GATE-2 measures, it does not delete.** At T22 the RLM chunker's outcome selects keep-as-default vs.
  make-optional-behind-a-config-toggle. If it is made optional, it stays available and testable, not
  removed. The choice is recorded (with the eval evidence) at GATE-2.
- **The GATE-2 eval must be able to see RLM's advantage or it proves nothing about it.** It must
  exercise the query types RLM is built for — cross-part, multi-hop, and temporal correlation (the T10
  relational/multi-hop set exists for exactly this) — and account for the KG synergy, on a full or
  genuinely representative sample (short and long docs, all archetypes), never downscaled for time.
  This is the in-depth-eval discipline the project already holds itself to; here it is load-bearing,
  because a dense-only or short-doc-only eval structurally cannot surface the semantic-boundary
  benefit. GATE-1's 4-shortest-docs run is explicitly not sufficient for this call.
- **Making it optional is cheap by construction.** The chunker already sits behind the RLM-skill and
  capability seams; an optional mode is a config toggle at the chunking capability, not a rearchitecture.
- **Scope guard.** This ADR does not pre-judge the GATE-2 result. It fixes the decision *space* and the
  *evaluation bar*, so the measured outcome is interpreted correctly rather than as a naive drop rule.
