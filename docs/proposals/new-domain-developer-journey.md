# Building a new-domain product on the RAG_Wright engine — a developer's journey

Status: **DRAFT / TARGET-STATE DESIGN for discussion** (2026-10-01). This describes how building a *new domain*
product on the engine **should** work once the engine/product boundary restructuring lands (the de-domaining in
ADR-0067 + the engine API layer + the capability runtime). It is written from the perspective of a developer who has
**never seen the contract/compliance domain** — because that is the test: if their path is smooth, the boundary is
right. A short "what exists today vs. what must be built" section at the end keeps it honest.

The thesis: **the engine is a domain-agnostic retrieval + knowledge-graph + ingestion + capability-execution
substrate. A new product = an ontology pack + domain capabilities (composed from engine capabilities) + a thin
product seam.** Get the boundary right and standing up a new domain is days, not months.

---

## 0. The mental model (what the engine is, and isn't)

The engine owns **mechanism**; you own **domain knowledge + composition + product**.

- **Engine (domain-agnostic):** parse, chunk, segment, embed, hybrid search, rerank, graph write/traverse, entity
  resolution, the `.ttl` ontology machinery, the LangGraph execution substrate (retry/dead-letter/progress), the
  model/LLM seam, usage/cost + tracing, and a stable **API layer** to invoke capabilities and open storage
  workspaces — **without the product ever knowing the store is ArcadeDB or the text embedder is BGE.**
- **You bring:** your **ontology** (entity/edge types, your closed-vocab tags, constraints) as a `.ttl` pack; your
  **domain capabilities** (ingestion/query graphs, classifiers) composed from engine capabilities; and a **product
  seam** (your app's logic: auth, tenancy, UI, use-case orchestration).

Two running examples used throughout:
- **BidWright** — an RFP / bid-response product: ingest a firm's past proposals + win/loss data, then "find similar
  past responses for this RFP section" and "draft a grounded response."
- **LoomMatch** (sidebar, to stress the non-text boundaries) — a textile firm's archive of fabric designs: "find
  past fabric patterns similar to this swatch," where the "embedding" is an **image** embedder, not BGE text.

---

## 1. The three layers you build within

| Layer | Who owns | What lives here | Reused across domains? |
|---|---|---|---|
| **Engine core API** | engine | invokers (`ainvoke_subgraph` / `invoke_model` / `invoke_function`), `open_workspace` (opaque handle), `kg_read`/`kg_write`, embed utility, model-by-alias, id/format accessors, usage/cost + progress + tracing, the mechanism capabilities (parse/embed/search/rerank/graph/resolve) | **yes — unchanged** |
| **Domain pack** | you (or a public source) | your `.ttl` (entity/edge types, closed-vocab dimensions, SHACL, synonyms), your Pydantic record contracts, your domain capabilities (ingestion/query subgraphs, classifiers), optional reference data | authored per domain |
| **Product seam** | you | tenancy/workspace routing, use-case orchestration, auth, UI/citation-preview types, scoping policy, observability wiring, optional MCP exposure | per product |

Everything below is "which of these three does it go in, and what engine API do I call."

---

## 2. Step 0 — Configure and set up the engine

You never write store or embedder code. You write a **typed `EngineConfig`** and hand it to the engine; the engine
resolves backends behind an **opaque workspace handle**.

```python
from rag_wright import engine                     # the engine API layer (proposed)

cfg = engine.EngineConfig(
    tenant="acme-textiles",                        # multi-tenancy is YOUR policy; the engine just keys on it
    store=engine.StoreConfig(backend="arcadedb",   # the ONLY place a backend is named; swap to neo4j here someday
                             host=..., port=..., user=..., password=...),
    # Everything below is OPTIONAL — the engine ships sensible defaults. Override only to trade quality/cost/latency.
    models={                                        # choose engine-supported models by ROLE/ALIAS, never a provider flag
        "reasoning":  "qwen3.8-27b-modal",
        "extraction": "qwen3.8-27b-modal",
        "generation": "gemma-4-…",
    },
    embeddings={"text": "bge-m3"},                  # pick an engine-SUPPORTED embedder (default: bge-m3)
    options=engine.Options(                         # the curated tradeoff knobs, each defaulted
        reranker="listwise-k25", retrieval_k=8, pool_k=30, dense_floor=3,
        chunking="rlm", extract_workers=8, judge=True,
    ),
)

ws = engine.open_workspace(cfg, corpus="bids")      # opaque WorkspaceHandle; resolves store+embedder+schema inside
```

**Three kinds of things live at this boundary — keep them distinct:**
1. **Hidden implementation** you never see or choose — ArcadeDB internals, vector math, connection plumbing, id
   formats. Behind the opaque handle, full stop. (The product never imports `ArcadeDBStore` or `query_embedder`; `ws`
   has no `.store`.)
2. **Engine-supported *options* (choices with defaults)** — a curated menu the engine exposes across
   **quality / cost / latency / data-sovereignty** tradeoffs: LLM by role/alias, embedder choice, reranker operating
   point, retrieval depth (`k`/`pool_k`/dense-floor), chunking strategy, ingestion concurrency, judge on/off,
   structured-output method. **Defaults just work**; you override only when budget/constraints demand it. (This
   *formalizes knobs that exist today* — model profiles, reranker operating point, retrieval args, and the six
   ingest env-vars the current seam leaks by name — into one defaulted `EngineConfig` surface.)
3. **Pluggable BYO extensions** — only when the engine's menu doesn't cover you. You register a capability the engine
   doesn't ship and refer to it by name.

So you **choose from #2 first**; you **plug #3 only for a genuine gap.**

**LoomMatch sidebar (#2 vs #3 for embeddings):** fabric similarity needs an *image* embedder, not BGE text. If the
engine already supports an image embedder, that's **#2** — `embeddings={"pattern": "<engine-image-embedder>"}`. If it
doesn't, that's **#3** — you register a small image-embedder **capability** and name it as the `"pattern"` profile.
Either way it's exposed to the rest of the engine as just "the `pattern` embedding profile"; you never touch
hybrid-search or the store. *(Boundary requirement for the restructuring: embedding is a config profile backed by an
engine-supported-or-pluggable embedder capability, so a new choice or modality is additive, never an engine edit.)*

---

## 3. Step 1 — Model your domain as an ontology pack (`.ttl`)

Your domain knowledge lives in a **`.ttl` pack**, never in engine Python (ADR-0066/0067). You author (or import a
public ontology, or adapt one) and drop it in `packs/<domain>.ttl`. It declares:

- **Entity types** and **relationship/edge types** (BidWright: `Firm`, `Project`, `RfpRequirement`; edges
  `SUBMITTED`, `WON`, `ADDRESSES`). *(These are the generic entity-KG types — the engine's graph write/traverse is
  parameterized on them; DD-5 makes them pack-declared, not hardcoded enums.)*
- **Your closed-vocab dimensions** — the "tags" you enrich records with (BidWright: `pricing_model ∈
  {fixed, t_and_m, milestone}`, `delivery_risk ∈ {low, med, high}`, `win_status ∈ {won, lost, pending}`). These are
  your version of what the contract pack calls clause dimensions.
- **KG node/edge storage mapping** — which record types become KG nodes, which dimension → which typed edge.
- **SHACL constraints** (applicability, cardinality) and **synonyms/surface forms** for normalization.

You never edit the engine to add these — `ensure_schema` builds the DDL from your pack; the vocab/template code is
generated from it; the normalizer is injectable.

**LoomMatch:** entity types `Design`, `Collection`, `Yarn`; dimensions `weave ∈ {plain, twill, satin, jacquard}`,
`motif ∈ {floral, geometric, abstract}`, `era`. Same story — a `.ttl`, no engine edit.

---

## 4. Step 2 — Decide your data enrichment (tags) and your classifiers

Enrichment = filling your closed-vocab dimensions on each record so queries can filter/rank on them. You have a clear
progression, each a first-class engine-supported path:

1. **Start with the LLM** (fast to stand up): the engine's residual-extraction mechanism fills your dimensions via one
   structured call per record. Good enough to launch and to generate training data.
2. **Distill to classifiers** when volume/latency matters: train a **model-kind capability** per dimension (or a
   grouped fleet) and register it. The engine ships the training **skills** (`setfit`, `laya`, `qwen-vllm-modal`) and
   the standard (checkpoint/registry/adopt-only-if-better). Your classifier becomes a registered `model` capability
   invoked in your ingestion graph and directly via `invoke_model("bid_dimension_classifier", text)`.
3. **Abstain + soft-scoping** carry over: add a NONE class so a classifier can say "not present," and scope the
   classifier lane to the record's likely functions — both are engine mechanisms, your pack supplies the vocab.

This is exactly the contract pack's Step-3a pattern (29-dim best-of-both fleet + residual LLM for numerics), reused
with *your* dimensions. You decide the tags; the engine provides the training, serving, abstain, and scoping
machinery.

**LoomMatch:** your "classifier" for `weave`/`motif` is likely a small **image** model — still a `model`-kind
capability, invoked the same way; the engine doesn't care whether the model reads text or pixels.

---

## 5. Step 3 — Ingestion: compose a domain ingestion capability

Ingestion is a **subgraph capability** you assemble from engine capabilities — or, for the common shape, you
configure the engine's generic ingestion pipeline with your pack + classifiers.

```
parse → chunk → segment → [ classify/enrich (your model capability) ‖ embed ‖ graph-extract ] → resolve → kg_write
```

- Every stage is an **engine capability** (parse, chunk, segment, embed, graph write, entity resolution) invoked by
  the graph's nodes (internal composition = direct import; decision D3). You supply the **enrichment** node (your
  classifier) and the **pack** (entity/edge/dimension types).
- You run it over your corpus via the engine's **ingest invoker + a corpus adapter**, using your workspace handle:

```python
ingest = engine.build_ingestion(cfg, corpus="bids", pack="bidwright", classifiers=["bid_dimension_classifier"])
await engine.ingest_corpus(my_documents, graph=ingest, resources=ws, on_progress=...)   # progress/cost automatic
```

- **Progress, usage/cost, retry/dead-letter are automatic** (the execution substrate + the API-layer cross-cutting
  utilities). You don't wire observability per node.

**LoomMatch:** "parse" is image ingest (the parser is a capability — a non-text parser is a pluggable parse
capability), "embed" uses your `pattern` image embedder profile. The ingestion *shape* is identical.

---

## 6. Step 4 — Define your query types / use cases

Map your use cases onto the engine's query capabilities; build domain query capabilities (subgraphs) only where you
add real logic. The engine gives you these shapes out of the box (each invoked via `ainvoke_subgraph` + `ws`):

| Use case shape | Engine capability | BidWright | LoomMatch |
|---|---|---|---|
| Single-document Q&A | `intra_document_qa` | "what did we propose for data migration in bid #42?" | "describe this design's construction" |
| Corpus-wide typed/similarity retrieval | `typed_property_retrieval` | "find past *won* fixed-price responses to security-audit RFP sections" | "find fabrics with a floral twill similar to this swatch" |
| Relational / graph | `relational_qa` | "which firms did we partner with on winning transport bids?" | "which collections used this yarn?" |
| Check against a requirement set | generic `compliance_check` (reference-pack generic mechanism) | "does our draft meet all mandatory RFP requirements?" | (n/a) |
| Grounded generation | `generation` + retrieval | "draft a response to this RFP section grounded in our past wins" | — |

Your **domain query capabilities** (if needed) are subgraphs that compose these + add your logic (e.g. BidWright's
"draft-a-response" = retrieve similar won bids → rank by win_status + recency → grounded generation). You register
them; they're invokable and MCP-exposable like any capability.

---

## 7. Step 5 — Build the product seam

Thin, and genuinely yours. It:
- opens workspaces per tenant (`open_workspace(cfg, corpus=…)`) — tenancy is your policy;
- invokes capabilities by name via the per-kind invokers with the handle + your domain inputs;
- adds product logic: auth, scoping policy, use-case orchestration, citation-preview/UI types, exception mapping,
  observability wiring (the engine emits usage/progress; you route it to your telemetry);
- optionally exposes any capability as an **MCP tool** for agents (generic adapter over the invoker) — so an agentic
  product consumes your capabilities as tools.

```python
# BidWright product seam (sketch)
ws = engine.open_workspace(cfg, corpus="bids")
hits = await engine.ainvoke_subgraph("typed_property_retrieval",
          {"query": rfp_section, "constraints": {"win_status": "won", "pricing_model": "fixed"}, "k": 10},
          resources=ws)
draft = await engine.ainvoke_subgraph("bid_response_draft", {"section": rfp_section, "evidence": hits}, resources=ws)
```

No `ArcadeDBStore`, no `query_embedder`, no model ids, no id-string parsing — those are engine API calls.

---

## 8. Where everything sits (master map)

| Concern | Lives in | Engine API you call |
|---|---|---|
| Store backend (ArcadeDB/Neo4j), connection, schema | **engine** (hidden) | `open_workspace(cfg)` |
| Embedding (text/image), vectors | **engine** (hidden) | embedding *profile* in `cfg`; pluggable embedder capability for new modality |
| Model choice / provider / structured-output | **engine** (hidden) | model **alias** in `cfg` |
| Parse / chunk / segment / search / rerank / graph / resolve | **engine** capabilities | invokers (internally direct-import) |
| Progress, usage/cost, retry/dead-letter, tracing | **engine** API layer | automatic + `usage`/`progress` utilities |
| Entity/edge types, your tags, SHACL, synonyms | **domain pack** `.ttl` | `ensure_schema`/vocab generated from it |
| Record contracts (your Pydantic types) | **domain pack** | — |
| Domain classifiers (enrichment) | **domain pack** (`model` capability) | `invoke_model(...)`, trained via skills |
| Ingestion/query **domain capabilities** (subgraphs) | **domain pack** | `ainvoke_subgraph(...)`; compose engine caps |
| KG reads scoped by your attrs | **engine** generic + pack helper | `kg_read(ws, node_type, where=…)` |
| Tenancy, auth, scoping policy, UI, use-case orchestration | **product seam** | — |
| MCP exposure of a capability | **engine** generic adapter | wrap the invoker |

---

## 9. What you DON'T build (the months saved) vs. what you DO build

**You don't build:** storage/graph management, embedding/vector handling, hybrid search + rerank, the ingestion and
retrieval orchestration substrate, retry/dead-letter/progress/cost/tracing, model-provider plumbing, classifier
training/serving infra, MCP plumbing, or any id/format parsing.

**You do build:** your `.ttl` ontology pack (entity/edge types + your tags + constraints), your record contracts,
your domain classifiers (using the engine's skills + standard), your domain ingestion/query capabilities (thin
subgraphs composing engine capabilities), and your product seam (tenancy + use cases + UI + auth).

That is the open-core payoff: the generic 70% is the engine; you write the domain 30%.

---

## 10. Honest status — target-state vs. today

This journey is the **target**. Today the engine does the hard parts well but doesn't yet present them as a clean API,
so a new-domain dev would hit friction. The restructuring that makes the above real:

- **De-domaining (ADR-0067 / DD-1..6):** lift domain methods off the store; `EntityResolver` seam + generic
  `EntityId`; entity/edge taxonomy → `.ttl` (DD-5); an import-linter guard. *Required so a new domain isn't inheriting
  contract/CIK assumptions.*
- **Engine API layer (new):** `open_workspace`/opaque handle + `EngineConfig`, the per-kind invokers, `kg_read`/
  id-format accessors, embedding-as-profile (+ pluggable embedder), model-by-alias, formalized usage/progress — this
  is what replaces the leaked store/embedder/model/id-format knowledge now living in the product seam.
- **Capability runtime (ADR-0068 sketch):** register every capability (incl. a lane-level classifier capability),
  per-kind invokers, generic capability→MCP.
- **Reference domain pack:** the existing contract + compliance domain becomes the worked *example* pack, proving the
  path (and keeping the engine runnable/demoable).

The sequence: **de-domain the core → publish the engine API layer (opaque handles + invokers + utilities) → the
capability runtime → then a new domain is "config + `.ttl` + capabilities + seam."**
