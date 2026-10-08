# Ontology induction from a domain corpus: OntoCast evaluation + a home-grown design

Status: PROPOSAL (2026-10-08), DEFERRED. Nothing here is built or run yet. Two pieces of work are parked: (1) a live
evaluation of OntoCast as a baseline, and (2) building our own ontology-induction capability. Scope: an engine
capability (domain-neutral); the review UI belongs to a product.

> One-line: for domains with no good public ontology, or to enrich one we have, induce an ontology from the domain's
> corpus with an agentic propose / ground / consolidate / judge / critique loop, and emit a full pack `.ttl` in which
> every term carries evidence and a confidence, low-confidence terms going to a human review queue.

---

## 1. Why

Our engine keeps domain knowledge in a pack `.ttl` (ADR-0066). Authoring one by hand is the slowest step of adapting
the engine to a new domain (`docs/domain-adaptation/ontology-authoring.md`). A corpus-driven inducer would draft that
pack, and the same loop could enrich an existing pack (e.g. the contracts pack) with what a new corpus shows.

## 2. OntoCast: desk review (code-level, 2026-10-08)

[OntoCast](https://github.com/growgraph/ontocast) v0.6.7, Apache-2.0, "agentic ontology and knowledge graph
co-generation". Read from the code (not installed or run); file references are to its repository at v0.6.7.

**Design (what it gets right):**
- A LangGraph pipeline: convert, chunk (sections, then semantic or naive chunks of 3,000 to 12,000 characters), an
  ontology phase, then a facts phase, then serialize. Units are processed in parallel (`asyncio.gather` + semaphore).
- Per unit: render once, then critic passes. Each pass runs **deterministic findings first**, then one critic LLM
  call, then applies each proposed fix **one at a time, rolling it back** if the unit gets worse.
- The model emits **patches** (insert/delete), never a regenerated ontology; patches are merged (insert wins),
  partitioned by namespace ownership, and applied to a versioned catalog (graph-hash lineage, `prov:wasDerivedFrom`,
  semantic version bumps).
- Client-side parsing (JSON envelope with Turtle inside, three repair levels, retry with the error fed back): works
  with OpenRouter and open models without tool calling.
- A strong offline test suite (about 1,900 tests, model calls stubbed), an LLM response cache, an embeddable
  LangGraph node.

**Cost:** about 2 to 4 model calls per unit with defaults (render, plus a critic pass, plus a selection call when a
catalog exists), up to 3x on parse failures. No hard call or token budget.

**Gaps against our needs:**

| Gap | Detail |
|---|---|
| Fragmentation | Induced from scratch, each unit mints its own ontology (its own IRI); same-IRI results are unioned, different ones only pairwise de-duplicated. No corpus-wide consolidation. |
| No confidence | Facts and terms carry no confidence. |
| Coarse provenance | Facts cite a chunk (3,000 to 12,000 characters), not a span; the ontology dump carries no provenance. |
| Incomplete schema | The prompt does not require `rdfs:domain` / `rdfs:range`; no SKOS concept schemes; no SHACL generated for the ontology. |
| No extend-only guarantee | Enrichment can propagate a `delete_graph` onto the supplied ontology in some modes. |
| No quality evidence | No ontology-quality metric or benchmark in the repository (results live in a separate repository). |
| Churn | Seven releases in two months, several breaking (wire format, defaults). |
| Open-model handling | No `<think>` stripping or `extra_body` passthrough. |

## 3. Proposed design: our own inducer

Built on what the engine already has: `build_ingestion` units with span provenance, Qwen via tag-parse
(ADR-0045), Jev typed decisions (ADR-0119), local BGE-M3 embeddings, entity resolution and disambiguation, the
property-grounding judge (ADR-0028), the pack `.ttl` format with `eng:` declarations, SKOS and SHACL, and our eval
practice.

**Stages:**

1. **Propose (Qwen, per unit).** Candidate classes, properties and value sets, each with a verbatim evidence quote
   from the unit. Tag-parse output.
2. **Ground (local).** Drop any proposal whose quote is not in the unit text; keep the span id as provenance.
3. **Consolidate across the corpus (local).** Embed, cluster, entity-resolve and disambiguate the candidates;
   count support (how many documents and units propose each).
4. **Judge (Jev, batched per cluster).** Typed decisions: is it a domain concept; class, property or value; which
   parent; merge these near-duplicates or not; closed value set or open text.
5. **Critique.** Deterministic checks first (cycles, label collisions, orphans, missing domain/range as a blocking
   finding), then a Qwen critic proposing patches, each applied alone and rolled back if it makes things worse
   (OntoCast's pattern).
6. **Emit.** A full pack `.ttl`: OWL classes and properties with domain/range, SKOS concept schemes for value sets,
   SHACL shapes, `eng:KgVertexType` declarations; every term annotated with its evidence spans and confidence.
7. **Review.** Terms below a confidence threshold go to a review queue (export API); accepted/rejected decisions come
   back (import API) and update the pack. The decisions are also labelled data for training a classifier or Laya to
   replace Jev on common judgments later (ADR-0115/0116/0119 pattern).

**Confidence per term:** a combination of the Jev decision score, corpus support, grounding, and critic agreement
(the exact model is a design task).

**Enrichment mode:** the seed pack is read-only; the inducer emits only additions under its own namespace
(extend-only by construction), each reviewed like an induced term.

**Boundaries:** the inducer is a domain-neutral engine capability, registered like any other (its own ADR and
workstream under `docs/specs/ontology-induction/`); the review UI is product work; domain seeds and naming
conventions stay in packs.

## 4. Evaluation plan

**Corpus:** 8 substantive CUAD contracts, stratified across agreement types (license, distribution, supply,
development, franchise, services, maintenance, endorsement), 15,000 to 60,000 characters each (about 300,000
characters, roughly 40 to 60 units). CUAD is restrictively licensed: outputs stay local, never committed.

**Reference:** the curated contracts pack (`contract_bridge.ttl`): 52 clause types, 29 property dimensions with closed
value sets, entity and relationship types.

**Metrics:** recall of reference concepts (label and BGE-M3 similarity matching, no model calls); precision from a
manual review of a sample of induced terms; structure (labels, domain/range coverage, hierarchy); fragmentation and
the per-document growth curve; deletions (enrichment); repeatability across runs; calls, tokens and cost per document.

**Experiments (both systems where they apply):**
- **E1a** induce from scratch (OntoCast default);
- **E1b** induce into a single header-only seed (OntoCast fixed mode; the fragmentation fix to test);
- **E2** enrich `contract_bridge.ttl` with about a third of the clause types and property dimensions held out, and
  score their rediscovery plus deletions and the quality of additions;
- **E3** (optional) facts on 2 contracts, to test re-grounding chunk-level provenance to span ids.

**A second domain** without a curated ontology, to test real induction rather than rediscovery: textile (TexWright;
client data stays local) or a public domain.

## 5. OntoCast baseline: run recipe and cost

Isolated install (never into the engine project); Python 3.12:

```
uv run --python 3.12 --with "ontocast[server,openai]==0.6.7" --with sentence-transformers \
  ontocast --env-file oc.env process --input-path ./docs --output-dir ./out
```

`oc.env`: `LLM_PROVIDER=openai`, `LLM_BASE_URL=https://openrouter.ai/api/v1`, `LLM_API_KEY`,
`LLM_MODEL_NAME=qwen/qwen3.8-27b`, `LLM_TEMPERATURE=0`, `CURRENT_DOMAIN`, `ONTOCAST_CACHE_DIR`,
`RENDER_MODE=ontology`, `ONTOLOGY_CRITIC_PASSES=1`, `FACTS_CRITIC_PASSES=0`, `MAX_VISITS_PER_NODE=1`,
`PARALLEL_WORKERS=8`, `LLM_MAX_INFLIGHT=8`, one shared local embedding model for chunking and aggregation. Induce
from scratch with `--ontology-dir ''`; enrich with `--ontology-dir ./seed`, `ONTOLOGY_CONTEXT_MODE=fixed_single_ontology`
and `ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=<pack IRI>`. Outputs: `<stem>.ontology.ttl`, `<stem>.run.json` (calls,
budget, metrics); the in-memory store is lost on exit, so the last document's ontology dump is the cumulative result.

**Estimated cost** at OpenRouter's 2026-10-08 Qwen3.8-27B price ($0.425 / M input, $2.55 / M output): E1a + E1b about
240 calls, about $3; E2 about 120 calls with large prompts (the seed is context), about $3; E3 about 40 calls, about
$1. About $7 in total, $20 worst case (every call retried 3 times). Over the paid-call threshold, so it needs a
pre-flight and approval when it runs. The cache makes re-scoring free.

## 6. Effort (estimate)

In the one-task-at-a-time loop: MVP (stages 1 to 4, emit, eval harness, contracts rediscovery test) about 2 weeks;
enrichment mode and the critic loop about 1 week; the confidence model and the review export/import APIs about 1 week.
The plumbing is predictable; induced-ontology quality (parent selection, class / property / value typing, naming) is
the uncertain part, so the MVP's eval results are a go/no-go gate before the rest.

## 7. Open questions

- Does Qwen (via OpenRouter) produce clean proposals at scale, and what is OntoCast's real fragmentation and quality
  on it (the baseline answers both)?
- The confidence model: how to weigh Jev score, support, grounding and critic agreement; calibrated against review
  decisions.
- IRI minting and stability across runs and across enrichment rounds.
- Which second domain to use, and how to build a small gold set for it.
- The review loop's product UX (which product hosts it first).

## 8. When resumed

1. Run the OntoCast baseline (E1b + E2 first, about $4) after a pre-flight and approval.
2. Write the workstream spec + ADR (`docs/specs/ontology-induction/`): stages, the confidence model, the review loop,
   the eval gate, the task breakdown; review before any build.
