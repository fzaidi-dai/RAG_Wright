# GP-1(B) plan — real entity extraction via docling-graph (2026-07-28)

**Goal.** Replace the gold-anchored GP-1(A) graph with a graph **extracted from contract text**, so the
relational/graph recall (measured by GP-2's `eval/relational_eval.py`) becomes a *real* signal — how well
extraction+resolution recovers the golden graph, vs the 1.000 gold-anchored upper bound. Extraction engine:
**docling-graph** (IBM/docling-project, v1.9.1), a schema-driven LLM KG extractor, chosen over a hand-rolled
extractor (well-tested, docling-integrated, reusable across domains, Granite-capable). Grounded via the cloned
repo + a dedicated AST index (`graphify-out/docling-graph/graph.json`).

## 1. What docling-graph does / doesn't (grounded)
- **Engine, bring-your-own schema.** You pass a Pydantic template (entities via `model_config={"is_entity":
  True,"graph_id_fields":[...]}`, relationships via an `edge("LABEL")` helper). The `core/extractors/contracts/`
  dir is the *extraction strategy* (`direct` = one call + gleaning; `dense` = skeleton-then-fill for big docs;
  `auto`), NOT a legal-contract schema. No bundled legal ontology.
- **API:** `run_pipeline(config, mode="api") -> PipelineContext`; read `context.extracted_models`
  (`list[BaseModel]`) and `context.knowledge_graph` (`networkx.DiGraph`, per-node `__provenance__`). API mode
  writes nothing to disk.
- **Inputs:** raw text string (API mode), `.md`/`.txt`, PDF, or a serialized `DoclingDocument` JSON (conversion
  skipped). We feed our **already-parsed** contract text — no re-parse.
- **No external entity linking** (internal blake2b dedup only) → we resolve to EDGAR CIK ourselves.
- **Deps:** compatible (our docling 2.109 ∈ `>=2.105,<3.0`; docling-core 2.86 ✓); only new dep `litellm`.

## 2. Integration architecture (reuses our resolution + store + GP-1(A) primitives)
```
cached contract text/DoclingDocument (from ingest_cuad parse cache)
  -> docling-graph run_pipeline(template=ContractParties, model=<A/B model>, mode="api")   EXTRACT (LLM)
       -> context.extracted_models : per-contract parties (+ relationships)
  -> adapter: parties per contract -> parties_to_extraction()      [GP-1(A) primitive, no-LLM facts]
  -> disambiguate() -> resolve_entities(registry)                  [T23b/T24: -> EDGAR CIK]
  -> to_graph() -> store.write_graph()                            [T25: Entity + CONTRACTS_WITH -> ragwright_cuad]
  -> eval/relational_eval.graph_leg_recall()                      [GP-2: REAL recall vs golden set]
```
- **docling-graph replaces only the LLM extraction step**; our resolution + ArcadeDB sink + eval are unchanged.
  `parties_to_extraction` (built in GP-1(A)) is the bridge from extracted parties → our fact pipeline.
- **Resolution registry (design point, GP-1B.2):** build `EntityRegistry` from the verified set's entities +
  their `variants` as aliases, so resolution recall is high and the measured recall isolates **extraction**
  quality. Private parties resolve to `None` today (golden id is `PRIVATE:<key>`) — a known alignment gap;
  measure CIK-entity recall primarily, treat `PRIVATE` 1-hop coverage as a stretch.
- **Primary target = 1-hop CONTRACTS_WITH** (parties per contract). 2-hop (`AFFILIATE_OF`/subsidiary) is a
  stretch: the golden 2-hop comes from human-verified subsidiary flags that may not be stated in the text.

## 3. The contract template
A small hand-written Pydantic template (no templategen needed):
```python
class Party(BaseModel):
    model_config = {"is_entity": True, "graph_id_fields": ["name"]}
    name: str = Field(description="Exact legal name of a signing party to the agreement")

class ContractParties(BaseModel):
    model_config = {"is_entity": True, "graph_id_fields": ["title"]}
    title: str = Field(description="Contract/agreement title or document name")
    parties: list[Party] = edge("PARTY_TO", description="The organizations that are signing parties")
```
`extracted_models` → each `ContractParties.parties` → party names → `parties_to_extraction`. (Lint via
`docling-graph template lint`.) A later iteration can add clause/relationship fields if we want richer edges.

## 4. Model config + the A/B contenders
All route through LiteLLM; selection = `provider_override` + `model_override` (+ `llm_overrides.connection`
for base_url/api_key). `structured_output=True` uses LiteLLM `response_format` json_schema; `drop_params=True`
strips unsupported params (handles DeepSeek/Gemma quirks).

| Contender | How configured | Home | Notes |
|---|---|---|---|
| **Granite** | `provider_override="ollama"`, `model_override="granite... "` (local) or `provider_override="watsonx"`, `model_override="ibm/granite-4-h-small"` | local (Ollama on Mac; `granite-4.0-1b`~2GB / `-3b`) or IBM watsonx | native default; advisory bench: 1B local **88%**. Privacy/cost story. watsonx needs IBM creds. |
| **Gemma** | `provider_override="openrouter"`, `model_override="google/gemma-4-..."` (key via `connection`) | OpenRouter (our seam provider) | matches our current Gemma usage |
| **DeepSeek** | `provider_override="openrouter"`, `model_override="deepseek/deepseek-v4-pro"` | OpenRouter | our STRUCTURED_REASONING default |

- **Granite runtime (decided 2026-07-28):** **local Ollama first, with live memory monitoring.** Small Granite
  models (`granite-4.0-1b`~2GB, `-3b`) *should* fit on the Mac, but with other apps running the system could go
  unstable — so while Granite runs locally, **monitor system/RSS memory; if it exceeds ~85%, fall back to a
  Modal-hosted Ollama Granite** (keep that deployment path ready — a Modal function running `ollama serve` +
  the Granite pull, reachable via `OLLAMA_BASE_URL`). watsonx is the alternative IBM-cloud path (needs creds);
  if Granite is on OpenRouter, routing all three uniformly through OpenRouter is the fairest same-infra A/B
  (confirm availability at GP-1B.3).

## 5. Model-seam reconciliation (ADR)
docling-graph uses its **own LiteLLM config seam** (provider/model/base_url/api_key in `PipelineConfig`), not
our `models/profiles` seam. Our standing rule bars a hardcoded provider flag *in node/agent code* — docling-
graph's config **is** a config seam (not code), so it's compatible in spirit. Plan: drive docling-graph via
config from our env (OpenRouter key, base_url) through a thin adapter `extraction_model_config(role)`; record
the chosen model + provider flags in a dated **ADR** (as with ADR-0023/0027/0032), keeping empiricism in config
+ ADR, not code.

## 6. A/B design (benchmark-then-adopt, per ADR-0023/0032 discipline)
- **Dev slice first:** ~10–15 contracts with ≥2 verified parties. For each model {Granite, Gemma, DeepSeek}:
  extract → resolve → build graph → `graph_leg_recall` (1-hop) + party-extraction precision/recall vs the
  verified parties, plus **cost + latency + local-vs-API**. Score, then pick.
- **Adopt winner**, run full-corpus extraction → populate → re-run `eval/relational_eval` → the **real** recall
  vs the 1.000 gold upper bound. Record in the ADR + ledger.
- Never conclude from the dev slice alone if models are close — expand the slice.

## 7. Task breakdown (contract-first TDD, one task + gate each)
- **GP-1B.0** — `uv add docling-graph` (**ask-first**: dependency change) + a smoke: `run_pipeline` on ONE
  cached contract with `ContractParties` + a cheap model → non-empty `extracted_models`. Verify deps resolve
  (docling unchanged at 2.109), litellm added.
- **GP-1B.1** — the `ContractParties` template + `template lint`; hermetic test (template shape/markers).
- **GP-1B.2** — adapter `extracted_models -> parties -> parties_to_extraction -> disambiguate -> resolve ->
  to_graph`; the verified-variants registry; hermetic test (fixture extracted model → CIK-keyed nodes/edges).
- **GP-1B.3** — model-config wiring `extraction_model_config(role)` (OpenRouter for Gemma/DeepSeek, Ollama/
  watsonx/Modal for Granite); confirm each model returns structured output on 1 contract.
- **GP-1B.4** — dev-slice A/B (Gemma/Granite/DeepSeek): recall + precision + cost/latency → pick. ADR draft.
- **GP-1B.5** — full-corpus extraction with the winner → populate → re-run relational eval → real recall; ADR
  + ledger; update `graph-layer` memory (gold-anchored → text-extracted).
- **GP-1B.6** — **author a comprehensive, reusable agent Skill** for *schema-driven LLM KG extraction from a
  text corpus* (once the recipe works). It codifies: bring-your-own Pydantic template design (entities/edges
  markers), docling-graph `run_pipeline` usage (API mode, feeding pre-parsed text), the model-config seam
  (Granite local/Modal-Ollama/watsonx + Gemma/DeepSeek via OpenRouter, with the >85%-memory Modal fallback),
  bolt-on external resolution (e.g. EDGAR CIK) + store sink, and the benchmark-then-adopt A/B discipline —
  so this becomes a reusable asset for any domain with entities + a schema (like the `model-training-recipe`
  skill is for training).

## 8. Risks / open decisions
1. **Dependency add** (`uv add docling-graph` + litellm) — ask-first; low risk (versions compatible).
2. **Granite runtime** — local Ollama first + **memory monitoring, fall back to Modal-hosted Ollama at >85%
   system memory** (decided 2026-07-28); watsonx/OpenRouter as alternatives (confirm at GP-1B.3).
3. **Resolution recall** — raw extracted names → CIK; mitigate with verified-variant aliases; `PRIVATE` 1-hop
   alignment is a known gap (measure CIK recall primarily).
4. **2-hop** — likely low from text (subsidiary structure is human-verified, not always stated); 1-hop is the
   headline.
5. **Model seam** — config seam + ADR (section 5).
6. **docling-graph internal chunking** (dense mode) is its own concern, independent of our retrieval chunker.
