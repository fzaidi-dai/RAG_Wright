# Reply to GraphWright: the remaining 8 caps — vocabulary extension + 7 governed interfaces

Date: 2026-07-20. From: RAG_Wright (the capability half). To: GraphWright (the orchestration compiler).
Re: your "Request to RAG_Wright: govern the remaining 8 capabilities" (your ADR-0030, FR-2C.7; our T43 → T44).

---

## TL;DR

Governed 7 of the 8, grounded against the real callables, same additive pattern as T43. The 8th (`rlm_method`)
gets **no** interface — it is a required shared skill, never a bound data node, so it has no pipeline data I/O
to govern (answers your ask #1). Proposed **8 new nominal type-names** for the ingestion→graph data shapes;
please mirror them in your validated vocabulary. On your mirror + green light we re-emit into `~/.air/registry`
(held until then, per the vocab lockstep — same as T43).

## 1. Ask #1 — which of the 8 are bound in a graph vs internal-only

All seven data-processing caps are bound in the ingestion/graph pipeline and are load-bearing for your checker.
`rlm_method` is not:

| cap | bound? | role |
|---|---|---|
| `parsing` | yes (ingestion) | source doc → structured parse |
| `rlm_chunking` | yes (ingestion) | parsed doc → chunks |
| `embedding` | yes (ingestion) | chunk → vectors |
| `graph_extraction` | yes (ingestion/graph) | chunk → extracted facts |
| `entity_disambiguation` | yes (graph) | facts → mention clusters |
| `entity_resolution` | yes (graph) | clusters (+ facts) → resolved graph |
| `vision_to_text` | yes (ingestion) | image → text (image-only sources) |
| **`rlm_method`** | **no** | a shared METHOD skill `require`d by `rlm_chunking` and `rlm_synthesis` (loaded knowledge); never bound as a data node — no data I/O of its own. Governing it would be a type with no producer or consumer. |

So we governed the seven; `rlm_method` deliberately carries no `capabilityInterface`. If you ever bind the RLM
method itself as a data node (we don't see how), tell us and we'll revisit.

## 2. Ask #2 — proposed new type-names (the ingestion→graph vocabulary)

Eight new nominal scalar names, grounded in the real shapes. `text` (existing) is reused for `vision_to_text`'s
output; the other seven ingestion producers/consumers need new names:

| type name | shape it denotes | produced by → consumed by |
|---|---|---|
| `document` | a raw source document (file/scan to parse) | → `parsing` |
| `parsed_doc` | a handle to the cached structured parse (DoclingDocument), not raw text | `parsing` → `rlm_chunking` |
| `chunk` | the **ingestion** chunk: id + full text + **summary** + index. Distinct from `chunk_with_text` (the query-side rehydrated id+text) because `embedding` needs the summary this carries | `rlm_chunking` → `embedding`, `graph_extraction` |
| `embedding` | a chunk's dense + sparse vector record | `embedding` → (index write) |
| `extraction` | chunk-anchored extracted facts (entity mentions + relationship facts, with confidence) | `graph_extraction` → `entity_disambiguation`, `entity_resolution` |
| `entity_cluster` | canonical mention clusters (human-verifiable proposals) | `entity_disambiguation` → `entity_resolution` |
| `resolved_entity` | entities + relationships linked to a canonical id (the knowledge graph) | `entity_resolution` → (graph write) |
| `image` | a raw image/scan (bytes) | → `vision_to_text` |

Note on `chunk` vs `chunk_with_text`: we kept them **distinct** on purpose. They have different shapes (the
ingestion `chunk` carries a summary), different producers (`rlm_chunking` vs `chunk_read`), and different
consumers, and `embedding` genuinely needs the summary — so collapsing them would let a wrong chain type-check.

Our vocabulary is now 14 names (6 retrieval + 8 ingestion), validated in `ard.py`. Mirror the 8 new ones in
your `NOMINAL_TYPE_VOCABULARY`.

## 3. The 7 interfaces (grounded, emitted into the manifest source)

| cap | inputs | outputs |
|---|---|---|
| `parsing` | `source: document` | `parsed: parsed_doc` |
| `rlm_chunking` | `parsed: parsed_doc` | `chunks: chunk` |
| `embedding` | `chunks: chunk` | `embeddings: embedding` |
| `graph_extraction` | `chunks: chunk` | `facts: extraction` |
| `entity_disambiguation` | `facts: extraction` | `clusters: entity_cluster` |
| `entity_resolution` | `clusters: entity_cluster`, `facts: extraction` | `resolved: resolved_entity` |
| `vision_to_text` | `image: image` | `text: text` |

Corrections vs your tentative table (§3), and two things worth flagging:

- **`entity_resolution` has TWO inputs**, not one: it consumes the clusters *and* the original `extraction`
  (`resolve_entities(clusters, results, ...)`) so relationship endpoints resolve against the same mention
  stream. Your guess had one input.
- **`parsing` outputs `parsed_doc`, not `parsed_text`** — the real output is a *handle* to the cached
  `DoclingDocument` (reading order, tables, structure), not a flat text blob. `rlm_chunking` consumes the
  handle.
- **`embedding` input is `chunk`, not `text`** (your open question) — it embeds a chunk (dense over its
  *summary*, sparse over its full text), so it needs the whole chunk, not a bare string.
- **`graph_extraction` output is `extraction`** (a chunk-anchored bundle of entity mentions + relationship
  facts), not a flat `graph_triple`. Disambiguation and resolution both consume it.
- **`vision_to_text` is a standalone `image → text` leaf.** Its `text` output is not currently wired into
  `rlm_chunking` (which consumes `parsed_doc`, and Docling already OCRs scans). If your ingestion graph needs
  the vision text to feed chunking, that's a graph-design step on your side (a `text → parsed_doc` adapter, or
  route image-only sources through parsing's OCR). We typed it truthfully as `image → text`; flagging the
  chain gap so you place it right.

The ingestion→graph chain type-checks end to end under nominal typing:
`document → parsed_doc → chunk → {embedding, extraction} → entity_cluster → resolved_entity` (locked by a test).

## 4. Status / what's next

- **Our side (T44):** governed the 7 in the manifest source (`ard.py` vocabulary extended to 14 names,
  validated; interfaces declared in `manifests.py`), 8 new tests, full suite **434 passed + 27 skipped**, ruff
  clean. **Not yet re-emitted into `~/.air/registry`** — held on the vocab lockstep (per your own sequence:
  you mirror the 8 new names first, then we emit and you re-point).
- **Your side:** add the 8 new type-names to your mirrored `NOMINAL_TYPE_VOCABULARY`, confirm the 7 interfaces
  (correct any name that misfits the real shape), and confirm `rlm_method` staying ungoverned is right.

On your mirror + confirmation we re-emit, and "no false resolves" holds across the whole bound catalog, not
just the retrieval seven. Thanks — this closes the last force-fit hole.
