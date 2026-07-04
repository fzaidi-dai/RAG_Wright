# ADR-0002: Validation corpus — CUAD plus SEC EDGAR

- Status: Accepted
- Date: 2026-07-04
- Deciders: farhan.zaidi@dreamai.io, Claude Code
- Phase: 0 (foundation) / 1 (plan)

## Context

SPEC.md leaves the validation corpus, the ontology, and the entity registry abstract
(assumption 3, open questions §16.2 and §16.3). The whole phased build is "measured against"
the Phase 0 golden evaluation set (§13), so the corpus is load-bearing: it determines whether
the ontology, the registry, and — critically — the relational and multi-hop archetype that the
ArcadeDB graph layer exists to serve are measurable at all. A concrete corpus is needed before
the ontology-derivation, entity-resolution, and graph capabilities can be built or tested.

## Decision

Use the **Contract Understanding Atticus Dataset (CUAD)** as the primary validation corpus,
together with **U.S. Securities and Exchange Commission (SEC) EDGAR** entity data:

- **Corpus:** a subset of roughly 100–150 CUAD contracts (~100MB), deliberately including some
  scanned filings so the Docling parse and vision-to-text path is exercised. Confirm the
  **Creative Commons Attribution 4.0 (CC BY 4.0)** license at download.
- **Ontology (resolves §16.2):** the 41 CUAD clause categories plus party and entity types,
  expressed as Pydantic models.
- **Entity registry (resolves §16.3):** EDGAR Central Index Key (CIK) identifiers as the
  canonical `entity_id`s; entity resolution is closed-world against this registry.
- **Golden eval set by archetype:** CUAD's expert clause annotations are ground truth for the
  exact/lexical and semantic archetypes and for clause-finding answer-and-citation questions;
  the **EDGAR party-and-entity graph is used to construct the relational and multi-hop
  questions**. This last part is not optional — CUAD alone is single-document clause extraction
  and under-tests the relational/multi-hop archetype, which is the entire reason the graph
  layer exists.

## Alternatives considered

- **Synthetic questions** — rejected: no expert ground truth; would not credibly measure
  faithfulness or citation correctness.
- **Other legal/document datasets considered** — set aside in favor of CUAD's expert
  annotations, commercial-clean license, and EDGAR's ready-made canonical entity registry.

## Consequences

- Real expert ground truth for retrieval, clause-finding, and citation checkpoints (§12).
- EDGAR supplies the canonical entity registry the graph layer needs, and the party-and-entity
  graph makes the relational/multi-hop archetype and the per-source ablation measurable.
- The scanned-filing subset exercises Docling OCR and the vision-to-text ingestion path (FR-C.9).
- **Known limitation:** this is single-domain legal, so the generic-RAG generality claim
  (SPEC.md §1, §15) needs validation on a second domain later. This is a property of the eval,
  not a blocker.
- SPEC.md is amended accordingly (assumption 3, §16.2, §16.3, §12, §13 Phase 0).
