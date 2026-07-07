# ADR-0004: Entity disambiguation and canonicalization

Status: Accepted. Splits FR-C.7 into two stages (`entity_disambiguation` then `entity_resolution`)
and supersedes the earlier implicit assumption that clean mentions arrive at resolution.

## Context

FR-C.7 originally treated entity resolution as one step: match an extracted mention to an EDGAR
Central Index Key (CIK). Recognition (FR-C.6, task T23) produces mentions; resolution (task T24)
links them to the registry.

Constructing the relational evaluation set at T10 exposed that mentions are fragmented and noisy
before resolution ever runs:

- One entity appears as many surface forms: "Bank of America", "Bank of America, N.A.",
  "Bank of America, N. A" (stray space), and a bare "Bank" are one entity sitting as four nodes.
- Non-entities are captured as parties: template placeholders ("<<enter Company Name>>"), role
  artifacts ("(collectively the \"Company\")"), and bare common words ("Services", "Bank").
- Possessive, alias, and encoding variants split or pollute the graph: "Stremick's" versus
  "Stremicks", "formerly known as Tradeum, Inc. which d/b/a VerticalNet Solutions", diacritic and
  mojibake forms.

Resolution cannot fix any of this; it presupposes clean, canonical mentions. And if the pipeline
hands raw mentions to human name-to-CIK verification, the human becomes the clustering algorithm and
effort grows with mention count, not entity count, which does not scale past a toy subset.

A canonicalization stage is therefore required between recognition and resolution. FR-C.7 is now two
stages: `entity_disambiguation` (this capability, T23b) then `entity_resolution` (T24).

## Decision

Build `entity_disambiguation` as a deterministic three-stage capability, normalize then reject then
cluster, that emits canonical clusters as human-verifiable proposals. It does not auto-commit merges
and it does not link to CIKs (that is T24). Full coreference is deferred behind a seam.

### Normalization rules (collapse surface variants to one canonical comparison form)

- N1. Whitespace and punctuation: collapse repeated spaces, normalize stray internal spaces
  ("N. A" becomes "N.A."), trim surrounding punctuation.
- N2. Legal suffix: normalize corporate suffixes (Inc., LLC, Corp., Co., Ltd., N.A., L.P.) to a
  canonical form so suffix-only variants align.
- N3. Possessive and apostrophe: strip apostrophes and possessive markers ("Stremick's" becomes
  "Stremicks"). Corpus-derived, T10.
- N4. Case: case-fold for comparison.
- N5. Unicode: NFKC-normalize so diacritic and encoding variants align (for example the mojibake in
  "Informåtica").

### Reject rules (a mention is dropped and never reaches T24)

- R1. Template placeholders: fill-in artifacts, for example "<<enter Company Name>>", "[Company]".
- R2. Role artifacts: parenthetical role definitions and bare role words, for example
  "(collectively the \"Company\")", a standalone "Purchaser" or "Seller".
- R3. Bare generic-token or below-specificity fragments: a single generic business word or a mention
  under a specificity threshold, for example "Bank", "Services", "Group", "Holdings". Corpus-derived,
  T10.
- R4. Alias prefixes: strip "formerly known as", "f/k/a", "a/k/a", "d/b/a", "now known as", "n/k/a";
  recover the trailing specific name where present, else reject. Corpus-derived, T10.
- R5. Degenerate: whitespace-only, punctuation-only, or below a minimum length.

### Clustering

- C1. Blocking on a cheap key (a normalized significant token or a sorted-token key) to avoid
  all-pairs comparison.
- C2. Similarity within blocks (token-set or edit-distance, optionally embedding) above a threshold
  merges mentions into a candidate cluster.
- C3. Conservative-merge bias: when uncertain, do not merge. A false merge (two real entities
  collapsed into one node, producing a false edge) is worse than a false split, because the human
  catches a split at verification but cannot see a silent merge. This bias is why the human is shown
  flagged near-duplicates to decide.
- C4. Flag, do not merge, ambiguous near-duplicates: parent/subsidiary or shared-token pairs (for
  example "ScanSource" versus "ScanSource Latin America", "Armstrong Flooring" versus "Armstrong
  Hardwood Flooring") are flagged for a human decision made off the contract language, not string
  similarity.

### Output

Canonical clusters shaped as proposals, each carrying `chunk_id` provenance and confidence, for
human verification and then T24 linking. The human confirms, corrects, or splits proposals; the
capability never writes ground truth on its own.

### Deferred behind a seam

Full coreference (pronouns and definite descriptions such as "the Company" bound to the antecedent
party) is out of scope for this capability. It sits behind a stable seam that additional resolvers
can bind later, mirroring the OpenIE deferral in ADR-0001.

## Consequences

- Human verification scales with entity count, not mention count; the human verifies and splits
  proposals rather than generating clusters.
- The graph does not fragment across surface forms, so relational and multi-hop questions have
  coherent hubs.
- The conservative-merge bias is deliberate: the human is shown flagged near-duplicates to decide,
  which is the intended cost of never silently merging two real entities.
- The rules are deterministic and testable; each corpus-derived rule ships with a regression fixture
  (T23b acceptance).
- T24 is simplified: it links a clean cluster to a CIK rather than fighting surface-form variants.

## Provenance

The possessive-apostrophe rule (N3), the bare-generic-token reject (R3), and the alias-prefix reject
(R4) were each added in response to a specific miss found in the T10 verification set: Stremick's
versus Stremicks, a bare "Services", and "formerly known as Tradeum, Inc. which d/b/a VerticalNet
Solutions". Future corpus-derived rules are appended here with their triggering case, so this ADR
stays the single record of what the capability enforces and why.
