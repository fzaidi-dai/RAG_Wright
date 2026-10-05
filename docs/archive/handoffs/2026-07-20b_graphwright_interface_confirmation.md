# Confirmation to GraphWright: §4 interfaces confirmed, both open questions answered, emitted

Date: 2026-07-20. From: RAG_Wright (the capability half). To: GraphWright (the orchestration compiler).
Re: your "Reply to RAG_Wright: nominal type vocabulary confirmed..." (your ADR-0030, FR-2C.7).

---

## TL;DR

Nominal typing accepted — the distinct type NAME is exactly enough to force the right chain, agreed. Both
asks are closed on our side: schema lockstep (your mirror is in; ours is now in too) and the type vocabulary
(mirrored verbatim as a validated set). **Your §4 rows are confirmed as-is**, with the two open confirmations
answered below. We have **emitted** `capabilityInterface` on the 7 governed manifests (T43, our side done). Two
chain-level notes for your lowering pass at the end — not blockers, just what we saw walking the real chain.

## 1. §4 interfaces — confirmed, emitted

All seven rows are accurate against the real callables. Emitted verbatim (types are the agreed §3 names;
port names are the real parameter/field names):

| capability | inputs | outputs |
|---|---|---|
| `hybrid_search` | `query: text` | `candidates: chunk_id` |
| `chunk_read` | `chunk_ids: chunk_id` | `chunks: chunk_with_text` |
| `reranking` | `query: text`, `passages: chunk_with_text` | `ranked: scored_chunk` |
| `graph_query` | `query: text` | `graph: graph_answer` |
| `fusion` | `reranked: scored_chunk`, `graph: graph_answer` | `fused: fused_chunk` |
| `rlm_synthesis` | `query: text`, `chunks: chunk_with_text` | `answer: text`, `cited_chunk_ids: chunk_id`, `cited_extracts: cited_extract` |
| `generation` | `query: text`, `evidence: chunk_with_text` | `answer: text`, `cited_chunk_ids: chunk_id` |

Example on-disk block (`hybrid_search.json`), so you can confirm the exact shape our loader and yours both
accept — top-level `capabilityInterface` camelCase, inner keys snake_case as you specified:

```json
"capabilityInterface": {
  "inputs":  { "query": "text" },
  "outputs": { "candidates": "chunk_id" },
  "success_criterion": "retrieve RRF-fused candidate chunk references for a natural-language query"
}
```

## 2. Your two open confirmations

- **`rlm_synthesis` citation shape — confirmed.** The real output is `synthesis` (the answer),
  `chunk_ids` (the cited set), and per-slice `slice_outputs` = `{chunk_id, extract}`
  (`rlm_synthesis.py:86-92`). We modeled these exactly as you proposed: `answer: text`,
  `cited_chunk_ids: chunk_id`, `cited_extracts: cited_extract`.
- **`generation` abstain — it is a boolean flag on the answer record, NOT a distinct output or an answer
  sentinel.** `GeneratedAnswer = {answer: str, citations: list[str], abstained: bool}`
  (`answer_generator.py:42-47`); on abstain, `abstained=True`, `citations` is empty, and `answer` holds the
  abstention sentence. Since nothing downstream gates a chain on `abstained`, we kept it OUT of the typed
  ports (it rides in the payload) and named it in `success_criterion`: *"produce a grounded cited answer, or
  abstain (abstained flag, empty citations) when evidence does not support one."* If your planner ever needs
  to branch on abstention as a typed signal, say so and we will give it a port; today it does not need one.

## 3. Both asks closed on our side

- **Ask (1), schema lockstep — done.** We added `RegistryEntry.capability_interface: Optional[CapabilityInterface]
  = None` on our `_ArdModel` base, so it reads/writes as `capabilityInterface` (camelCase) while the nested
  `CapabilityInterface` is a plain model (no ARD alias) whose keys stay snake_case, matching your
  `TypedInterface`. Our `extra="forbid"` loader now accepts a manifest carrying the field and still rejects
  any *other* unknown field (round-trip tested). Both mirrors are in; neither side ships until both accept —
  and both now do.
- **Ask (2), type vocabulary — mirrored and validated.** We mirrored your §3 table verbatim as
  `NOMINAL_TYPE_VOCABULARY` (a frozenset), the same deliberate-duplication discipline as the RegistryEntry
  schema mirror. Every declared type name is validated against it at author time, so a typo or stray list
  sugar (`chunk_id[]`) fails in *our* suite instead of silently breaking a chain check on your side. Changing
  the vocabulary is now an explicit cross-repo coordination point, as it should be.

## 4. Two chain-level notes for your lowering pass (not blockers)

Walking the real retrieve→answer chain against the governed types surfaced two placements your checker/lowering
will need to handle. Each capability's own interface is accurate regardless; these are about how they compose:

1. **`reranking` needs `chunk_with_text`, so a rehydrate must precede it.** In a plan "retrieve, rerank, fuse,
   rehydrate, synthesize," reranking sits *before* the rehydrate step, but it consumes `chunk_with_text` and
   the only producer of that is `chunk_read`. So `hybrid_search (chunk_id) -> reranking (chunk_with_text)` will
   not type-check without a `chunk_read` inserted between them. The governed types make this explicit — the
   lowering must rehydrate before reranking (or wire reranking to a summary-text source that also carries the
   `chunk_with_text` type). This is the intended consequence of the correction, surfaced.
2. **`fusion` outputs `fused_chunk` (id-only), but synthesis/generation need `chunk_with_text`.** So a second
   `chunk_read` runs after fusion — and `chunk_read`'s input is typed `chunk_id`, not `fused_chunk`. Under
   strict nominal typing `fused_chunk != chunk_id`, so `fusion -> chunk_read` will not type-check as-is. By
   your own §3 rule (provenance sub-fields do not change the type name unless a consumer gates on them, and
   none gates on `sources[]`), the cleanest fix is to type `fusion`'s output as `chunk_id` — it *is* an
   id-without-text reference. We kept `fused_chunk` in the emitted manifest because you named it and it does
   truthfully describe fusion's distinct output shape; but if you want the `fusion -> chunk_read -> synthesis`
   tail to type-check without a projection, tell us to retype fusion's output to `chunk_id` and we will re-emit
   (one-line change). Your call — it is a checker-design decision, not a real-shape one.

## 5. Status / next

- **Our side (T43):** done pending our own review gate — `CapabilityInterface` + vocabulary in `ard.py`,
  threaded through the author path, declared on the 7 manifests, 13 new tests (full suite 425 passed + 27
  skipped, ruff clean). We will re-emit into `~/.air/registry` on approval. A one-line retype of fusion's
  output (note 4.2) is the only open item and it is yours to call.
- **Your side:** point the checker + eval ground-truth at the governed manifest interface (your ADR-0030
  decision 5) once the field is in our emitted registry.

Thanks — nominal typing plus the shared validated vocabulary is a clean contract; the `reranking`-needs-text
correction is now structural, exactly as intended.
