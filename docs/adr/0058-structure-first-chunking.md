# ADR-0058: Structure-first chunking (deterministic default, tag-parse model fallback, dynamic-agent seam)

Status: Accepted (2026-08-19; approved in principle — the Tier-1 default switch is gated on the CHUNK-5 A/B)
Date: 2026-08-19
Component: the chunking capability — `capabilities/rlm_chunking.py` (the `BoundaryDiscoverer` seam),
`capabilities/parsing.py` / `corpus/document_parser.py` (the docling structure), the contract-ingestion chunk
stage, and `models/tag_structured.py`. Raised by: RuleWright (product), engine issue 0004.
Related: ADR-0057 (async wall-clock deadline — what *caught* this), ADR-0045 (client-side XML tag-parse vs
server-side guided decoding), ADR-0031 (single-call boundary discovery, CU-B4), ADR-0039 (self-hosted Granite
substrate), ADR-0052 (engine/product split — heavy orchestration is product-side).

## Context

Engine issue 0004 (follows resolved 0003). On a ~15-page / 24-clause contract, one model call exceeds the
ADR-0057 180s wall-clock deadline every time (3/3 reps) and is cancelled; a ~5-page / 8-clause doc never trips
it. The deadline worked exactly as designed — bounded, visible, logged — so this is **performance, not
correctness**. Code-confirmed root cause: `SingleCallBoundaryDiscoverer.adiscover` makes **one structured model
call over the whole document** to find clause boundaries (`build_structured(GENERAL, _BoundaryList).ainvoke`,
`rlm_chunking.py:283`), wired as the production contract discoverer. Its cost scales with document length
(the boundary-list output grows with clause count), and it uses **server-side guided decoding** — the exact
mechanism ADR-0045 abandoned on the query side because it "runs away to `max_model_len`" (no clean EOS) on
self-hosted models like Granite. The two compound into the 180s runaway; then chunking falls back to a
deterministic partition anyway, so the 180s is pure waste.

Two facts reframe the fix:

1. **Docling already extracts a generic structural tree for every document** — a `DoclingDocument` of items,
   each with a domain-neutral `DocItemLabel` (`SECTION_HEADER`, `TITLE`, `LIST_ITEM`, `TABLE`, `TEXT`, …) and a
   hierarchy `level`. The engine already **parses and caches the full structure** (`load_document` =
   `DoclingDocument.load_from_json(manifest_path)`) and already sections it generically, no LLM, via
   `document_to_sections` (used on the compliance side today). The boundary items even *carry* `label`/`level`
   (`_document_items` reads them) — but the single-call prompt discards them and asks the model to re-derive
   boundaries the labels already encode.
2. **ADR-0045's client-side tag-parse is the portable fix for the runaway** — free-text `<field>` tags parsed
   deterministically, no server-side grammar. Its current scope is flat schemas; the boundary output reshapes
   to a flat `list[int]` of cut-points, so it needs no nested-schema work.

## Decision

Make chunking **structure-first**: use the deterministic structural signal docling already computed, and call
the model only where structure is genuinely absent — a two-tier design behind the existing `BoundaryDiscoverer`
seam.

- **Tier 1 (default): a deterministic `StructuralBoundaryDiscoverer`.** Derive clause boundaries from the cached
  `DoclingDocument` labels/levels (heading/section labels start a chunk; list/table items and hierarchy levels
  respected). **Zero model calls, generic** (any docling-parseable document), latency independent of length. The
  default for the contract-ingestion path.
- **Tier 2 fallback (b1): bounded per-section model calls, via tag-parse.** For a section the structural pass
  marks ambiguous (sparse/unreliable labels — e.g. scanned wall-of-text), a **bounded per-section** boundary
  call, run concurrently (gather + semaphore), using client-side **tag-parse** (ADR-0045) over a **flat
  cut-index contract** so it terminates fast on Granite and never scales with whole-document length.
- **Tier 2 escalation (b2): held as an opt-in seam, not built now.** A Deep-Agents dynamic-subagent +
  programmatic-tool-calling discoverer (agent writes interpreter code to probe structure and dispatch `task()`
  subagents over slices — the RLM pattern) is the right instrument for *heterogeneous* corpora (mixed
  tables-as-structure / forms / prose). It stays a documented plug-point on the `BoundaryDiscoverer` seam and is
  **product-side / opt-in** (it is beta `langchain-quickjs`, adds an interpreter runtime + agent loop, and heavy
  orchestration belongs to the product per ADR-0052) — not the engine's default ingest.
- **Side-fix (regardless of cause):** name the **stage/call-site** in the deadline warning (`seam.py:235`
  currently logs only `model_id`), so a future timeout is a statement, not a hypothesis.
- **The single-call/agentic discoverers are kept** (seam-swappable) for unstructured documents and as A/B
  baselines; the RLM chunker is not dropped (GATE-2 posture).

## Consequences

- The 180s cancellation disappears for structured documents (the workload in scope): boundaries come from
  docling's already-computed labels at ~zero model cost; only genuinely ambiguous sections touch the model, and
  those calls are bounded and tag-parsed (no runaway).
- Generic by construction — the structural signal is docling's domain-neutral labels, so a new corpus (a
  regulation, a policy, tomorrow's arbitrary document) benefits with no per-domain code.
- A boundary **contract change** (nested span-list → flat cut-index list) — a data-model change, hence
  ask-first; it is internal to chunking (not a stored identifier), and the manifest/`Chunk` output is unchanged.
- Adds a fallback path and a new discoverer, but removes the whole-document model call — net simpler on the hot
  path. The seam keeps the dynamic-agent (b2) door open without baking a beta runtime into the engine.
- Quality must be **measured, not assumed**: a clause-integrity A/B (structural vs single-call on a CUAD sample)
  gates the default switch, so we do not trade correctness for latency blind.
