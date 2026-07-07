# ADR-0005: The relational + multi-hop golden set

Status: Accepted. Records how task T10 constructs the relational archetype, why the built set is
committed, and how a verified-PRIVATE entity is identified without loosening the `EntityId` contract.

## Context

CUAD (task T9) yields single-document clause questions. The ArcadeDB graph layer exists to answer
questions that traverse the entity graph, and nothing measures that leg unless a relational golden
set is built on purpose (T10; SPEC section 12 and section 8). Three forces shape it:

- Building the answer key by fuzzy name-to-CIK matching would be circular: linking parties to CIKs
  *is* the entity-resolution problem (FR-C.7). Only human-verified matches are ground truth. T10's
  verification produced `data/edgar/verification_set.json`, 45 entities resolved (28 CIK, 17
  PRIVATE); its `resolution` fields are ground truth and are never regenerated over.
- That verified file is gitignored (local triage artifact). If the golden set derived from it were
  also gitignored, the human-verified answer keys would exist in git nowhere and be lost on a fresh
  checkout.
- The `EntityId` contract (T1) is strict: canonical 10-digit CIK only. A verified-PRIVATE entity is
  a first-class answer but has no CIK, so it cannot be an `EntityId`.

## Decision

Build the set with `eval/multihop.py` (`build_relational`), keyed on the human `resolution`.

### Question shapes

Two set-valued shapes, both anchored on a hub (an entity signing >= 3 contracts), so recall@k is
measured exactly as for the CUAD archetypes:

- 1-hop: the hub's full set of direct co-parties.
- 2-hop: entities reachable through exactly one shared counterparty and not directly a co-party, a
  real chain of three distinct verified entities joined by two real contractual edges.

### Identity keys the whole build

Each entity's answer identity is its `resolution`: the CIK for a filer, and a namespaced
`PRIVATE:<entity_key>` sentinel for a verified-PRIVATE node. The co-party graph, variant collapse,
hub dedup, and answer sets are all keyed on this identity. Consequences fall out for free:

- Variant surface forms of one filer share a CIK and collapse to one answer entity ("a b watley" and
  "ab wately" become the single CIK `0001035632`).
- Distinct subsidiaries have distinct resolutions, so they keep separate answer keys and are never
  merged (ScanSource, a CIK, versus ScanSource Latin America, verified PRIVATE). One's counterparties
  are never listed under the other.
- A SKIP entity is excluded from the graph entirely.

The `PRIVATE:<key>` sentinel is the eval-set answer id, not an `EntityId`. The identifier contract
stays strict canonical-CIK-only; the private node is mapped to its assigned graph node id at eval
time. This keeps normalization at the boundary rather than weakening the contract.

### The axis is verified-vs-unverified, never public-vs-private

A CIK filer and a verified-PRIVATE entity are equally first-class answers. Folding private
counterparties out would under-measure the graph leg on exactly the entities that dominate this
corpus.

### Evidence path

Each answer carries an entity_id evidence chain (`[hub, answer]` at 1 hop, `[hub, bridge, answer]`
at 2 hops). The chunk_id evidence path is resolved at eval time once the corpus is ingested, the
same deferral CUAD `relevant_ids` use.

### The built set is committed

`eval/golden/relational/set.json` lives in the source tree (not `data/`) and is committed. It is a
verified fixture, not a rebuildable cache: it embeds human-verified answer keys that exist nowhere
else in git once the local triage file is set aside. This is the one deliberate exception to "never
commit generated data", justified because the alternative loses the human verification from history.
The gitignored triage file stays out; its distilled ground truth lands in git through this set.

## Consequences

- The relational split runs through the existing harness: `RelationalQuestion.to_golden()` projects
  onto a `GoldenQuestion` with `Archetype.RELATIONAL`, so `evaluate()` reports `relational/text` and
  `relational/graph` alongside the CUAD archetypes, each leg measured separately.
- Honest eval property, recorded in the set metadata as `public-filer-centric, multi-hop-modest`:
  the corpus is star-shaped, 16 one-hop questions but only 3 hubs with genuine 2-hop neighborhoods
  (the dairy cluster Stremick's/Premier/Fonterra and the Sina/Leju/Baidu cluster). Multi-hop is
  modest because of the corpus, surfaced by measurement, not because the set is under-built.
- Rebuild anytime with `uv run python -m eval.multihop` from the local verified set; the committed
  `resolution` fields, not the rebuild, are the ground truth.
- Feeds GATE-2 (the recall bar) and the T32 per-source ablation, which is the entire reason the
  graph layer exists.

## Provenance

Related: ADR-0002 (CUAD + EDGAR corpus), ADR-0004 (entity disambiguation, the T23b canonicalizer
T10 also produced). The `EntityId` strictness this ADR preserves is the T1 contract decision.
