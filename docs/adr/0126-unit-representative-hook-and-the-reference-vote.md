# ADR-0126: Which span represents a unit is a domain hook; the reference pack votes over operative spans

**Status:** accepted · **Date:** 2026-10-09 · **Related:** ADR-0124 (ingestion builder and hooks), ADR-0114 (SetFit
clause-function classifier), ADR-0044 (cap / carve-out exception links)

## Context

A unit (a contract provision) is labelled and cited by one span: its `anchor` (the citation its records carry) and
its leading tag (the label the extractor reads). The engine's grouper and the reference provision grouper both used
the first member, usually the section heading. In a live ingest (PS-8a) "Section 9. Uncapped Liability." was tagged
Cap On Liability while its operative sentence was tagged Uncapped Liability, so the clause became a Cap clause and
lost its carve-out link. The right choice depends on a domain's documents, so it is the product's decision.

## Decision

1. **Engine:** a `UnitRepresentative` hook (`build_ingestion(unit_representative=...)`, `IngestionStages`) applied
   after any grouper: the hook returns the member span that represents the unit (or a copy of one with its
   `primary` set, for a label decided across members); it becomes the unit's `anchor`, and its primary tag leads the
   unit's `tags`. Without the hook, behaviour is unchanged (the first member). The engine checks membership.
2. **Reference contracts pack:** `provision_vote` (rule D): the label is the function with the highest SetFit
   probability summed over the provision's operative (non-heading) members; the clause cites the operative member
   most confident in that label. Probabilities reach the spans through `clause_function_classification`
   (`with_probabilities`) and `TaggedSpan.scores` (not persisted). Without probabilities it falls back to
   `operative_span` (the first tagged non-heading member).

## Evidence (all 510 CUAD contracts, 6,656 provisions with gold, deterministic provision grouping, CUAD outputs local)

| rule | provision accuracy | Cap provisions | Uncapped provisions | Cap+Uncapped contracts with both clauses (111) | Cap recall (275) | false Uncapped (399) |
|---|---|---|---|---|---|---|
| A heading-first (before) | 46.8% | 17.1% | 28.5% | 18.9% | 43.6% | 9.5% |
| B operative span | 45.2% | 16.1% | 36.9% | 23.4% | 43.3% | 8.3% |
| C vote, all members | 57.8% | 23.6% | 31.5% | 14.4% | 43.6% | 6.0% |
| **D vote, operative members** | **56.6%** | **23.8%** | **33.1%** | **18.9%** | **44.4%** | **7.0%** |

D is within 1.2 points of the best overall, best on Cap provisions and Cap recall, and better than C on carve-out
sections and linking.

## Consequences

- Clauses cite operative text, not headings; the reference pack's clause function is about 10 points more accurate.
- The ceiling is the classifier: every rule tops out near 57%, more than half of the Cap contracts get no Cap
  clause, and Cap / Uncapped stay confused. That is classifier work (ADR-0114 / ADR-0115), with these numbers as the
  baseline. ADR-0114's evaluation measured top-3 recall on gold snippets; the primary label in pipeline context was
  never measured, which is how this went unseen (lessons recorded in the `setfit` and `creating-evals` skills, PS-9).
- The clause-extraction cache key includes the anchor span, so a re-ingest re-extracts the clauses whose anchor moved.
- A product measures its own rule with a gold set (PS-R4 adds `evaluate_ingestion` support).
