# tasks.md: RAG_Wright task ledger

Phase 2 output. The persistent, cross-session task ledger and shared memory of progress. Derived
from `plan.md` (Phase 1) and `SPEC.md` v0.1, honoring ADR-0001 (stack) and ADR-0002 (corpus).

> **RESUME / NEXT UP (2026-08-09): ACORD UNIFIED INTO THE ONE PRODUCTION KG (ADR-0046) — done; NEXT = the
> function-gate ON/OFF GRADED RECALL on the unified KG.** Context: PREC-1b (Leak-A ingestion function-mislabel)
> led to a design question — do the query legs even need the precomputed LegalBERT clause `function`? Static
> audit: only `typed_property_retrieval` (Leg B) HARD-depends on it (a `span_hybrid_search(function=f)` pool
> pre-filter); relational/intra-doc/compliance don't. Empirical top-8 probe (7 typed queries, local KG +
> Cerebras/Gemma-4 @conc≤2, `scripts/…legb_gate_ab` in scratchpad): function-gate OFF (whole-index BGE + property
> rerank) reproduces ON at 7-8/8 — the gate only trims one tail semantic-neighbor, and DROPPING it shrinks a
> mislabel's blast radius. To BANK this with graded recall we needed ACORD on the CURRENT KG — which exposed that
> ACORD lived in a stale separate `ragwright_acord_pivot` (14/23 property edges), violating ADR-0033 (one KG).
> **UNIFICATION DONE (ADR-0046, `scripts/acord_unify.py {map|ingest|qrels}`):** (1) MAP — 3,221/3,931 (82%) ACORD
> clauses already verbatim in `ragwright_cuad_full` → existing clause; (2) INGEST — the 710 remainder through the
> ENHANCED clause layer (segment→LegalBERT→granite 23-dim+ADR-0040 judge→typed edges+BGE spans), granite@conc8,
> monitored; verified in-KG: Clause 42,314→45,404 (+3,090), Span 136,460→139,955 (+3,495), 710 distinct clauses;
> (3) QRELS — ACORD qrels on the unified KG's canonical `parent_chunk_id`s, **relevant coverage 620/620 = 100%**
> (`data/eval/acord_unify/acord_prod_qrels.json`). OKF is dropped: the ingest uses canonical ids only
> (`source_doc_id=acord-{aid}`), `parent_okf_path` cleared on all 3,495 ACORD spans (matches CUAD). Pre-ingest
> backup taken; ACORD nodes are `acord-`-prefixed (reversible). `ragwright_acord_pivot` backed up +
> superseded/unreferenced — physical `drop database` pending (classifier-blocked; user runs it). **NEXT:** adapt
> the graded-recall recipe (`eval/function_property_rerank.py` style: oracle-function pool vs whole-index pool →
> property-boost + BGE-rerank → recall@50 vs ACORD bar 0.667) into a unified-KG ON/OFF harness (query LLM on
> Cerebras/Gemma-4 @conc≤2), run it, and decide Option A (keep+fix the label) vs Option B (retire precomputed
> function, derive at query time). Local Docker ArcadeDB UP.
>
> **(prior) RESUME / NEXT UP (2026-08-08):** **ADR-0045 (client-side XML-tag structured output) — QUERY SIDE DONE.**
> All query-side structured-output callers now use `models/tag_structured.py::build_tag_structured` (free-text +
> client-side tag-parse, a drop-in for `build_structured`), so structured output is LLM-agnostic and no longer
> depends on server-side guided decoding (which runs away on self-hosted Gemma 4 / vLLM and costs ~60s/call on
> Cerebras). Routed: query function classifier, query understanding (step-2 emit), highlight field-extract, OKF
> reader judge, and generation (`answer_model_for` is now UNIVERSALLY the tag path; the `client_side_structured`
> profile flag + `RAG_CLIENT_SIDE_STRUCTURED` env force are no longer consulted). Each re-tested via
> OpenRouter/Cerebras (0.5–3s/call, correct). Commits 91fd240..37a3e46. **NOT YET ROUTED (deliberate):**
> `compliance_judgment.py` (Granite compliance arc, own gold-eval parity — confirm before touching) and
> ingestion (`rlm_chunking.py` etc. — a later task: Gemma 4 + tag-parse vs DeepSeek A/B, needs the
> `list[BaseModel]` nested extension).
>
> **PROVIDERS FINALIZED (2026-08-08, `docs/eval/silver_provider_routing.md`, commit `e198f0c`).** Silver eval on
> Gemma-4-31b + tag-parse across three OpenRouter routings: **Cerebras pinned + no fallback + low concurrency
> wins** (100% recall, **0 flips**, 1.74s median) vs cross-provider fallback (lands on slow deepinfra AND
> reintroduces flips → 93% recall / 2 flips). `_provider_pin` (commit `5c91daf`) now supports an ordered list +
> `OPENROUTER_ALLOW_FALLBACKS`; `measure_silver.py` defaults the OpenRouter backend to Cerebras-pinned. The
> **generation non-determinism blocker is RESOLVED** (0 flips on Gemma-4+tag-parse, both self-hosted 31B-W4A16
> and Cerebras — it was a Granite-8B property). Residual, NON-blocking: abstention precision 50% (near-miss
> over-answer on `Insurance`/`Minimum Commitment`) — a model-strength lever, its own task.
>
> **MCP-PROTO Phase B COMPLETE — all 4 Tier-1 capabilities are FastMCP tools, Deep-Agent-validated.** Each leg
> mirrors the `compliance_server.py` reference (injectable fn + production/demo + ARD `mcp_tool` + hermetic
> in-memory-Client tests) AND is exercised end-to-end by a Deep Agent over stdio (parity with the compliance
> demo): **MCP-B1 (`78c32a4`)** `intra_document_qa` → `answer_contract_question` → cited `GeneratedAnswer`
> (`intra_document_qa_mcp`); **MCP-B2 (`ca37364`)** `relational_qa` → `answer_relational_question`
> (query, start_entity_id, max_hops) → cited `GeneratedAnswer`, graph-structural (`relational_qa_mcp`); **MCP-B3**
> `typed_property_retrieval` → `retrieve_typed_property_spans` → `TypedPropertyRetrieval` (ranked cited spans, NOT
> `GeneratedAnswer` — corpus-wide RETRIEVAL) (`typed_property_retrieval_mcp`). **Deep-Agent parity demo:**
> `scripts/mcp_query_legs_agent_demo.py` spawns all 3 servers over stdio, the agent discovered + called all 3
> tools and cited their returned ids (run with `RAG_SERVING=openrouter`, NO provider pin — the driver is DeepSeek
> STRUCTURED_REASONING, not Cerebras). MCP Tier-1 = `compliance_check` + the 3 query legs, all wrapped.
> **REAL-INFRA SMOKE DONE (`scripts/mcp_intra_document_qa_smoke.py`):** spawned the PRODUCTION
> `intra_document_qa` MCP server over stdio (no demo fn) against the LOCAL Docker ArcadeDB KG
> (`ragwright_cuad_full`, 42,314 clauses) + Gemma-4-31b/Cerebras; `answer_contract_question` on a real contract
> returned a real cited answer (`abstained=False`, real chunk_id). Proves the wrapped capability works as a tool
> on live infra. CAVEAT (feeds PREC-1): on `LIMEENERGYCO…DISTRIBUTOR_AGREEMENT` the served/answered clause was a
> force-majeure limitation, NOT a monetary cap — the classify→serve step surfaced the wrong clause. Local Docker
> ArcadeDB is UP.
>
> **NEXT = PREC-1 (retrieval/abstention precision lever) — IN PROGRESS.** Its own task. DIAGNOSIS DONE: the two
> precision signals localize to DIFFERENT pipeline stages, neither a query classifier/serve bug.
> **Leak A (ingestion function-mislabel):** CONFIRMED — `LIMEENERGYCO…` idx 75 is a FORCE-MAJEURE clause labeled
> `Cap On Liability` in the KG; classifier + serve were correct, the KG label is wrong. Corpus: 374 contracts /
> 2,099 cap-labeled clauses; mislabel RATE not yet measured (regex too crude — "other" bucket was mostly regex
> misses on real limitation clauses). → needs an LLM-judge quantification over enough contracts to justify
> replacing regex with an LLM function-classifier in ingestion, then the fix.
> **Leak B (generation abstention discipline):** silver frozen-evidence over-answer. RE-EXAMINED: the 50% is not
> flat — of 4 negatives, 2 clean abstains + 1 HONEST HEDGE (`Insurance`: answer explicitly says "the evidence
> does not specify… although it mentions…", cited — asserts no unsupported answer) + 1 REAL over-answer
> (`Minimum Commitment`: confident list, no caveat). On "did it assert an unsupported answer?" that's ~75%, 1 true
> failure. The binary `abstained` flag is too strict for the honest hedge.
>
> **PREC-1a (Leak-B subtask) — DONE (awaiting approval/commit).** Made the hedge first-class + re-scored silver
> honestly, THEN added a generation-layer mitigation for mislabeled evidence:
> - **Structured sufficiency signal:** `GeneratedAnswer.answer_kind ∈ {answered, partial, abstained}` (`abstained`
>   kept + derived, backward-compat — no `abstained=`-only caller changed) + a `<partial/>` tag in the
>   tag-protocol + `parse_tagged_answer`/`_finalize` propagation + SKILL three-outcome guidance. Silver 3-way:
>   `Insurance`→partial (honest hedge, credited); binary precision 50% was too strict → honest 75%.
> - **(a) label de-assertion:** `_clause_to_evidence` now frames the KG function as `[auto-tag: X]` (a guess to
>   verify), not an asserted prefix `"X: ..."`. **(b) SKILL self-check:** treat `[auto-tag: X]` as possibly-wrong,
>   verify the text instantiates the concept, hedge/abstain on mismatch. Frozen fixture reformatted to match
>   (`scripts/migrate_silver_evidence_autotag.py`, 233 items, same content + key).
> - **Result:** `Minimum Commitment` answered→**partial** (honest: "does not explicitly state the formal minimum
>   commitments"); silver honest precision **75%→100%** (no confident over-answer left), recall **100%**, 0 flips.
>   Also validated on REAL infra: the LIMEENERGYCO force-majeure→Cap mislabel now returns **partial** ("the
>   evidence does not establish a general liability cap") instead of confidently mislabeling force-majeure as the
>   cap. (a)+(b) = an HONESTY fix on both mislabeled cases, NOT a correctness fix (right answer needs the label
>   fixed at ingestion). Minor nit: occasional tag-leak in parse fallback (non-deterministic model formatting).
> **NEXT = PREC-1b (Leak-A: ingestion function-mislabel) — NOT STARTED. Discuss options after compaction.**
> Goal: (1) QUANTIFY the mislabel rate honestly, then (2) FIX it. Generation-side (a)+(b) already makes the
> system HEDGE honestly on mislabels; PREC-1b is about CORRECTNESS (serve the RIGHT clause).
>
> **OPEN QUESTION 0 (establish FIRST — the fix depends on it):** what actually assigned each clause's `function`
> in `ragwright_cuad_full`? The label is PASSED INTO the extractor (`spans/clause_kg_extractor.py` `__call__(...,
> function=...)`), computed UPSTREAM by the ingestion driver — NOT by an LLM at extraction time. Candidates:
> (i) CUAD gold-overlap labels (`spans/cuad_labels.py`: segment each contract, label each operative span by the
> CUAD clause-type whose answer span it overlaps MOST, type regex-parsed from the CUAD question, else NONE);
> (ii) the trained LegalBERT function classifier (`spans/legalbert_classifier.py`, kind=model); (iii) other. Find
> the actual KG-build driver and confirm. The user framed it as "replace regex by LLM" — but the force-majeure→
> `Cap On Liability` mislabel is most likely a CUAD-gold **overlap-heuristic** artifact (a span overlapping a
> broad CUAD answer region inherits the wrong type), which is a different fix than "regex."
>
> **PART 1 — QUANTIFY (do first; user: "run on enough contracts/clauses to conclude we need to replace the
> labeler"):** an LLM-judge over a stratified sample of served clauses per function — "does this clause TEXT
> actually instantiate function X, per its definition?" → correct / mislabel / ambiguous, with cited examples +
> a per-function mislabel-rate table. Design to DISCUSS: sample size + stratification (corpus = 374 contracts /
> 2,099 `Cap On Liability` clauses; sample across all 41 functions vs. focus on confirmed-bad `Cap On Liability`
> + `Minimum Commitment` + a random control); judge model = Gemma-4-31b/Cerebras via tag-parse, CONCURRENT
> (async+semaphore, standing rule); judge needs per-function DEFINITIONS (CUAD category defs exist). NB regex
> was proven too crude for this (the earlier 40-contract scan: "other" bucket was mostly regex misses on real
> limitation clauses) — MUST be an LLM judge.
>
> **PART 2 — FIX OPTIONS (discuss after Part 1):**
> - **A. LLM function-classifier at ingestion (user's leaning).** Replace the labeler with a tag-parse LLM
>   classifier over clause TEXT (the query-side `classify_query_functions` already works in-distribution on
>   clause-like text). Pros: reads content, not overlap geometry. Cons: cost at scale (one-time, batchable,
>   concurrent) + needs a RELABEL pass (cheaper: run the new labeler over EXISTING clause texts, update
>   `function` + re-run `symbolic_validate`, NO full re-extraction) or a full re-ingest (GCP bulk box, ADR-0038).
>   Tension: do we trust an LLM over CUAD GOLD where gold is clean? (Only override where the label is wrong.)
> - **B. Neuro-symbolic function-label gate (extend ADR-0040 cascade).** We already have `symbolic_validate`
>   (function→dimension applicability, SHACL) + `reground` (ADR-0028 lexical property gate). ADD a check that the
>   FUNCTION label's defining cue is present in the text (force-majeure clause labeled `Cap On Liability` has no
>   cap/limit cue → downgrade/relabel/flag). Ties to [[ontology-lever-vs-extraction-lever]]: is a given mislabel
>   a "schema-checkable rule" (cue present?) or a "better reading" (needs the model)? Cap-vs-force-majeure looks
>   cue-checkable.
> - **C. Fix the label SOURCE, not re-classify.** If mislabels come from the CUAD overlap heuristic, tighten it
>   (higher overlap threshold, better single-label disambiguation, low-overlap → NONE). Cheapest if the root
>   cause is the heuristic; no LLM at ingestion.
> - **D. Combine (neuro-symbolic, ADR-0040 spirit):** LLM classifier as labeler + symbolic/lexical gate as the
>   check + CUAD gold as validation. Likely the durable answer.
> - Note: property tags were ALSO garbage on the mislabeled clause (`nonsolicit_target=employees` on plant
>   trials) — but ADR-0040 already targets property fidelity; the FUNCTION label is the new, separate gap.
> **VALIDATION after the fix:** re-run the real-infra Cap smoke (should serve a REAL cap clause or correctly
> abstain), re-quantify the mislabel rate (Part 1 judge) to show improvement, and re-check silver.
>
> **INFRA/CONFIG STATE (for resume):** local Docker ArcadeDB `arcadedb-ragwright` is UP (`ragwright_cuad_full`,
> 42,314 clauses / 510 contracts, `.env` → localhost:2480). Modal apps + A100 STOPPED. Silver default =
> Cerebras-pinned Gemma-4 (`measure_silver.py` defaults, commit `e198f0c`); the Deep-Agent driver = DeepSeek
> (STRUCTURED_REASONING) with NO provider pin (Cerebras doesn't serve DeepSeek — a pin 404s). Silver answers dump
> = scratchpad `silver_answers.txt`. Real-infra smoke = `scripts/mcp_intra_document_qa_smoke.py` (needs local KG up).
>
> **RESUME / NEXT UP (2026-08-07):** **MCP-PROTO + Phase-A quality arc in flight.** Prototyped the "capability
> as an MCP tool" pattern: `compliance_check` wrapped via FastMCP (`src/rag_wright/mcp/compliance_server.py`),
> a Deep Agent called it end-to-end, and it's registered as the first ARD `mcp_tool` (`compliance_check_mcp`).
> FastMCP grounded durably (clone-staged into the framework graph; recipe in CLAUDE.md/playbook/ADR-0008).
> **Phase B (wrapping the query legs as MCP tools) is SUSPENDED** to fix quality nuances first.
> **Phase A (validate the 3 unproven query legs on Modal, no MCP):** A1 `intra_document_qa` — clause-level
> span_id persisted + backfilled on BOTH KGs (ADR-0042, 100% match, in-place); A2 `relational_qa` — rebuilt to
> GRAPH-STRUCTURAL evidence (no text_store), validated clean on Modal; A3 — see the retirement below.
> **>>> RETIRED `cross_corpus_retrieval` (ADR-0043). DO NOT recreate it, re-register it, or wrap it as an MCP
> tool. It was redundant with `typed_property_retrieval` (Leg B), which uses the correct BGE+property pool
> (`property_boosted_retrieval`) and is re-validated on Modal (fee-multiple-cap query → top-4 [MATCH]). The
> corpus-wide function+property retrieval leg is `typed_property_retrieval`.** Along the way fixed a latent
> query-constraint bug (function="" → empty constraints; now the `NO_FUNCTION` sentinel, used by Leg B too).
> **MCP Tier-1 candidate list (for when Phase B resumes) = `compliance_check`, `intra_document_qa`,
> `relational_qa`, `typed_property_retrieval` (NOT cross_corpus_retrieval).**
> **A1 quality — RESOLVED + re-scoped (ADR-0044, 2026-08-07).** Built the cap↔carve-out `IsExceptionTo`
> relationship (`clause_exception_linking` capability + `intra_document_qa` consumption + generation SKILL
> voices "capped, EXCEPT ..."); linking pass run co-located on the Modal KG (409 edges / 390 contracts, 214
> distant/cap-less left unlinked; `scripts/modal_exception_linking.py`). Re-validating A1 revealed the abstain
> was mostly a **crash I introduced**: the new clause→clause edges (no `dimension`) polluted
> `contract_clause_kg`'s outE traversal → null-dimension ValidationError → serve degraded to empty → abstain;
> FIXED (`898571b`, `dimension IS NOT NULL` guard + skip non-property rows). A1 now answers with citations.
> **NEW open Leg-A item (supersedes "over-classification abstain"): vLLM-Granite-8B generation is
> NON-DETERMINISTIC near its abstain boundary** — the same answerable query abstained ~2/3 of runs at temp 0
> (greedy vLLM isn't bitwise-reproducible; borderline query flips). Generation-robustness / model-strength
> issue (Pro-on-hard-queries lever), its own task. **ADR-0044 linking pass now run on BOTH KGs** — Modal
> (409 edges / 390 contracts) and local canonical 510 (409 edges / 391 contracts, `exceptions_of_clause` +
> outE-fix verified; the deterministic proximity derivation matches). Both KGs backfilled; local Docker DOWN;
> A100 DOWN; `rw-arcadedb` UP (cheap).
>
> ---
>
> **RESUME / NEXT UP (2026-08-05):** **SKILL-SPLIT arc CLOSED (ADR-0041), pushed to origin/main.** Every
> LLM-bearing capability is now correctly kinded per the rubric (function=deterministic/no-model ·
> agent_skill=a single LLM act authored as a SKILL.md folder · subgraph=a multi-step workflow · model=inference):
> split `compliance_judgment`/`claim_extraction`/`requirement_extraction`(→subgraph)/`extraction_semantic_judge`
> into skill+function; `vision_to_text`→agent_skill; `generation` prompt→`skills/generation/SKILL.md`
> (prompt-parity — every agent_skill's prompt now lives in its SKILL.md); kinds are CI-pinned by the manifest
> tests. **Naming audit:** relocated `CuadAdapter`/`run_cuad_ingestion` out of the GENERIC
> `subgraphs/contract_ingestion_pipeline.py` → `corpus/cuad_ingestion.py` (generic pipeline stays corpus-agnostic).
> Runtime-tiers decision recorded (single-shot=seam+SKILL.md; heavy=create_agent/Deep-Agent, already built +
> tested: `rlm`/`okf_navigate`; SkillsMiddleware progressive-disclosure deferred to real need). **993 green,
> whole-src ruff 100% clean.** NOTE: the whole LG arc (LG-0..LG-3, incl. the 4 composite subgraphs) shipped
> 2026-07-31 — any "LG-3 next" note is STALE.
>
> **NEXT (open):** rung-2 = **EXPERT-GRADE the compliance gold** (legal SME reviews the 19 labels + grow the real
> negative class into a benchmark). The residual precision limit (~0.73) is the external-substantiation reality +
> small/weak gold labels, NOT tuning or code. Contract-side **EC-5** (enterprise packaging) deferred, fully in-code.
>
> **MODAL-COMPLIANCE DONE (2026-08-05): the compliance module now runs fully on Modal, like the contract side.**
> The compliance Requirement KG (`ragwright_compliance`, 96 reqs) was `BACKUP DATABASE`'d → GCS
> (`gs://dreamai-pocs-ragwright-ingest/kg-backups/`, ADR-0038 convention) → `modal volume put` onto the
> `rw-arcadedb-data` Volume; `scripts/modal_arcadedb.py serve()` generalized to restore BOTH DBs (per-DB guard,
> one server serves every DB in the mount) → Modal KG verified 81/14/1=96 = local. Local Docker STOPPED after
> the backup+restore+basic-test (nothing downstream needs it). Reused BOTH existing Modal apps (no new GPU/DB
> server): judge + claim/requirement extraction route to A100 vLLM-Granite via `RAG_SERVING=vllm`; CC-8 BGE
> narrowing routes to the A100 `/embed` via `remote_encoders.query_embedder()` + `STACK_URL` (the one code swap
> in `eval_compliance_gold.py`; `compliance_engine_smoke.py` gained an i/N counter). **Component test:** smoke
> 4/4 on Modal KG + A100 Granite. **Full E2E:** rung-2 gold eval on Modal (A100 Granite extraction + A100 BGE +
> A100 Granite judge + Modal KG) = clearance-safety 1.00 / hard-viol-precision 0.91 / hard-FP-rate 0.12 =
> MATCHES the local/OpenRouter baseline (substrate parity proven). Modal apps STOPPED post-run (no idle credits).
>
> ---
>
> **RESUME / NEXT UP (2026-08-04):** ENTERPRISE-CONTAINER deployable system done; **RECOVER-4-DOCS DONE**
> (local canonical KG now uniform at **510**: Contract 510 / Span 136,460 / clauses 42,314 / entities 1,181 /
> PARTY_TO 1,287). The 4 dead-letters were TRANSIENT (the doc-~387 heap-OOM cascade), not a chunker defect —
> reproduced all 4, they chunk cleanly, so NO chunker fallback was needed; recovery = a `RESET=0` delta re-ingest.
> **PARTY-LINK-DEFAULT-MANY DONE**: `run_cuad_ingestion` now defaults the corpus link step to the many-to-many
> mention-cache derivation (`_corpus_party_link_fn`), so a re-ingest never reverts PARTY_TO to 1-to-1. **All 3
> Modal apps STOPPED** (rw-arcadedb / rw-query / rw-stack-a100). 910 green.
>
> **COMPLIANCE RUNG 1 COMPLETE (2026-08-05): the ad-compliance engine (CC-0..CC-7), 966 green.** Two-sided
> retrieval + entailment over the FTC 16 CFR 255 KG (`ragwright_compliance`, separate DB, 155 Requirement nodes).
> Capabilities/subgraphs: requirement_extraction, claim_extraction, compliance_judgment (the new judgment node),
> compliance_ingestion (subgraph), compliance_check (subgraph) + Requirement/Claim/Verdict contracts +
> compliance_bridge.ttl. **Track-1 GATE CLEARED** (`docs/eval/compliance_gate_cc7.md`): product judge Granite acc
> 0.773 ≈ ceiling, safest on the liability FN; engine 4/4 on the labeled ads. **NEXT (superseded — see the
> 2026-08-05 banner above): CC-6/CC-8 semantic narrowing are DONE; rung-2 gold increments (RG-5/RG-6/NEG-GOLD/
> EXTRACT-TUNE) DONE; the open item is now EXPERT-GRADE the gold.** Two findings recorded
> in [[ontology-lever-vs-extraction-lever]] + [[docling-graph-extraction-contract]].
>
> **RESUME / NEXT UP (2026-07-30):** The ARD-registration + LangGraph-hardening arc is in flight.
> **DONE & committed:** CAP-REG-1/1b/2 (reclassify + register contract-KG capabilities), LG-0 (langgraph dep +
> `subgraphs/scaffold.py` + vendor-neutral OTel `subgraphs/observability.py` per GraphWright's
> `temp/observability-contract.md`), and **LG-1 + LG-2 COMPLETE** — the 4 hardened subgraphs
> `typed_clause_extraction` (reference), `query_constraint_extraction`, `semantic_chunking`, `graph_extraction`
> in `src/rag_wright/subgraphs/`, all registered, 783 hermetic green.
> **LG-3 COMPLETE (2026-07-31):** all 4 composites shipped (`relational_qa`, `intra_document_qa`,
> `cross_corpus_retrieval`, `contract_ingestion_pipeline`) + CAP-REG-3 (retrieval core) + CAP-REG-4
> (graph_extraction re-backed with GP-1B, ADR-0035) + the KG-hygiene arc (HYG-1 canonical slug / HYG-2 entity
> graph reconciled / KG-7 Party<->Contract link, 219 PARTY_TO edges live, graphs connected).
> **NEXT:** INGEST-REFACTOR (migrate the per-corpus scripts onto the LG-3d generic pipeline + adapters) and
> SKILL-corpus-ingest (the recipe); then enhancements PARTY-TO-MANY-TO-MANY + CUAD-FULL-COVERAGE, and the
> compliance subgraphs (roadmap §13). Follow the LG-1/LG-2 pattern: build on `subgraphs/scaffold.py`, harden with
> retry→dead-letter via `runtime.execution_info.node_attempt` (langgraph 1.2.9's error_handler is called
> node-style), observability via `subgraphs/observability.py` (raw_llm_span only for raw-SDK/docling-graph
> calls; seam/`ChatOpenAI` calls auto-capture), inject seams for hermetic tests. Real `uv add`/`lock`/`sync`
> need the Bash sandbox DISABLED (see [[uv-network-ops-need-sandbox-disabled]]).

This is the **capability half** only. Each task builds and registers one FR-C / FR-I / FR-Q
capability, or a foundation seam, as ordinary tested software. Registration is twofold and is part
of every capability's definition of done: internal registration (T6, which the Model Context
Protocol surface exposes) and Agentic Resource Discovery (ARD) registration (the manifest the
GraphWright compiler discovers and binds against); see "ARD registration" below. The ingestion and
query **graphs** are compiled separately from the Orchestration Spec by the GraphWright compiler
and are not built here. Any task that looks like "wire the pipeline into a graph" is compiler work,
not this repo's.

> Conventions: acronyms expanded on first use, no em dashes, plain phrasing.

> **HYGIENE (recurring, tracked 2026-07-17):** external-agent scratch has twice appeared in `src/`
> (turn 1: a broken `skills/rlm/__init__.py` + an external `_MAX_DEPTH` cap; turn 2: a dead
> `handle_leaf_depth_3` Python mirror of the JS depth-cap branch + its export + ~13 untracked test
> runners). The invariant at risk is **tested-equals-shipped** — a second copy of real logic (a Python
> mirror of `RLM_WORKFLOW_JS`) is not guarded by the drift test and can be mistaken for, or drift from,
> the truth. Standing rule: **any external edit to the RLM files (`agent.py`, `SKILL.md`,
> `RLM_WORKFLOW_JS`, `skills/rlm/__init__.py`) is suspect until verified** — those are where a silent
> mirror or broken import does real damage. Verify (drift test + suite + no unguarded logic copy) before
> trusting an external RLM edit.

> **LATENT HARDENING ITEM — RLM worker query/slice threading (GraphWright grounding 2026-07-18; CONFIRMED
> accurate; DRY-RUN VERDICT: did NOT bite → parked, not urgent).** The RLM leaf dispatch is **query-less AND
> slice-less**:
> `task({description: "handle leaf depth " + depth, subagentType: "rlm_slice_worker"})`
> (`agent.py:129/137/154`) passes no query and no slice items. The worker system prompt
> `_EXTRACT_WORKER_PROMPT` (`rlm_synthesis.py:59`) says *"extract the facts in this slice that help answer
> the question"* but the workflow provides NEITHER the slice text NOR the question; the query lives only in
> the orchestrator's `_request` (*"Question: {query}"*, `:231`). So whether a worker extracts
> query-relevantly depends on the **orchestrator model choosing** to thread the query (and slice) into the
> dispatch — model-driven, not enforced. Same silent-gap class as the working-set findings (SKILL.md
> under-specifies; a capable model papers over it until it doesn't). **Affects standalone synthesis too, not
> just the joint run.** My tests do NOT catch it — the sub-agent responders are stubs that return canned
> output regardless of dispatch content. **HELD, do NOT fix now:** GraphWright's dry-run is instrumented to
> check whether extractions are query-relevant (not just whether citations resolve). Fixing now would treat
> an unconfirmed symptom AND destroy the dry-run's evidence of the current, unmodified behavior. Sequence:
> dry-run verdict → if it BITES, thread the query (+ slice) into the worker dispatch (a real fix that also
> improves standalone synthesis, and add a non-stub test that asserts the worker receives the query); if it
> does NOT, record as a latent hardening item (enforce-what-works-by-luck).
> **VERDICT (GraphWright dry-run, 2026-07-18): did NOT bite.** On real "Audit Rights" data the extractions
> were query-relevant (worker got the query) AND extract-matched-chunk (worker got the slice) — the
> orchestrator model threaded both. So it works, but by model choice, not enforcement → **parked as a latent
> hardening item, not urgent (integration-first).** When picked up: thread query+slice explicitly into the
> dispatch so it's enforced, and add a non-stub test asserting the worker receives them.

---

## Last approved / next up

- **>>> RESUME POINT (2026-07-29): KG-0/1/2/3/4 DONE. TWO typed KGs populated: `ragwright_acord_pivot` (ACORD,
  3,887 clauses, Leg B corpus) + `ragwright_cuad` (CUAD, 7,026 clauses / 100 contracts, Leg A corpus). Property
  schema extended to 30 dims for full CUAD coverage. Leg A serving = `capabilities/contract_kg_serve.py` (scoped
  disambiguation/aggregation/cited QnA; live-demoed). NEXT = KG-5 (Leg B: typed-edge constraint match as a
  filter/rerank feature over the BGE base pool + pointwise-Gemma reranker, into `eval/function_property_rerank.py`),
  then KG-6 (eval on harder queries). Open follow-ups: wire `disambiguate` into `highlight_serve`; Modal
  concurrency (single-A10 NUM_PARALLEL=1 too slow — used OpenRouter); party-role + cross-clause-ref layers.**
- **(prior) RESUME POINT (2026-07-28): KG-0/1/2/3 DONE and the ACORD typed KG POPULATED (Leg B corpus).** The typed
  unified KG is live: `write_clause_kg` populated it via `scripts/populate_clause_kg.py`; flat `HasProperty`
  retired; spans preserved; grounding gate applied. **KG-4 = Leg A intra-contract scoped-query serving —
  structured/relational/cited QnA scoped to one `contract_id` (disambiguate same-type clauses by property,
  party<->clause role Qs, aggregation, cross-clause refs); upgrade the `highlight_serve` backend.** Then KG-5
  (Leg B typed-edge rerank feature) + KG-6 (eval on harder queries).
- **(prior) RESUME POINT: KG-0/1/2 DONE; NEXT = KG-3 (write the TYPED unified KG to ArcadeDB).**
  KG-2 shipped the per-clause typed extractor (`spans/clause_kg_extractor.py`: `clause_to_record` adapter +
  `DGClausePropertyExtractor` + `granite_clause_extractor()`; granite-4.1-8b, grounding-judge gate; live smoke
  accurate). KG-3 = resolve/ground the extracted `ClausePropertyRecord`s (values/predicates -> FOLIO/ODRL IRIs;
  parties -> CIK/PRIVATE) and WRITE the typed unified KG to ArcadeDB — new typed edge types (`HAS_*`/`EXCEPTS`/
  `BOUNDED_BY`/`COVERS`/`CAPS`/`GRANTS`/`PROHIBITS`/`REQUIRES`/`GOVERNED_BY`/`REFERENCES`), DROP the flat
  `HasProperty`; PRESERVE node identities (`clause_id`/`entity_id`/`value_key`) + the provenance schema +
  content-hash gate (extend `store/arcadedb.py::write_property_graph`, ask-first store DDL change).
  NOTE (unrelated, pre-existing): `tests/models/test_profile_seam.py::test_no_extra_body_kwarg_when_profile_has_none`
  was red since CU-D2 (c40d35c) — being fixed separately right after KG-2.
- **(prior) RESUME POINT: KG-0 + KG-1 DONE; NEXT = KG-2.**
  KG-1 shipped the clause extraction template (`src/rag_wright/ontology/`: `contract_bridge.ttl` +
  `contract_bridge.spec.yaml` + compiled `clause_template.py`; lint clean; 9 tests; `docling-graph[templategen]`
  added). Reuse the GP-1B recipe (`kg-extraction-recipe` Skill, `dg_extraction.py` seam, Granite-on-Modal).
- **(prior) RESUME POINT (2026-07-28): KG-0 DONE (schema-review gate passed).** Building the UNIFIED
  CONTRACT KG (`docs/unified_contract_kg_plan.md` + **ADR-0033**): ONE KG (Contract/Party/Clause/value nodes +
  typed edges) served as 3 scoped queries (A = one contract_id; B = clauses cross-corpus as a filter/rerank
  feature; C = done, real recall 0.991). **KG-0 DONE:** ontology bridge designed + approved —
  `docs/unified_contract_kg_ontology_bridge.md` (FOLIO subClassOf + ODRL full-depth incl. `odrl:constraint` +
  small custom OWL bridge promoting the 19 PropertyDimensions to typed `HAS_*`/`EXCEPTS`/`BOUNDED_BY`/`COVERS`/
  `CAPS`/`GRANTS`/`PROHIBITS`/`REQUIRES`/`GOVERNED_BY`/`REFERENCES` edges + `owl:oneOf` value classes + PROV-O).
  Gate decisions: build TYPED / retire the flat graph; KG-2 model = **granite-4.1-8b, NO A/B** (DeepSeek =
  below-par contingency only). `docling_graph` now in the framework grounding graph (2402 nodes). **NEXT STEP =
  KG-1** (compile the bridge OWL -> Pydantic template via `docling-graph template from-ontology`; adds
  `rdflib`/`linkml-runtime` via the `templategen` extra — ask-first dep — and authors+validates the `.ttl`).
- **2026-07-26: Classifier rare-class improvement (in progress).** (1) DIAGNOSIS: failures split into SCARCITY
  (3 classes <35 train spans at 0.00 recall: Unlimited-License 15, Irrevocable/Perpetual-License 28,
  Notice-Period-To-Terminate-Renewal 32) and CONFUSABILITY (well-resourced siblings: Uncapped↔Cap-on-Liability,
  Change-of-Control↔Anti-Assignment, the license family). Class-weighting + per-type cap ALREADY in training.
  (2) Migrated training to Modal per ADR-0030: `scripts/train_legalbert_modal.py` (A10 train + bulk logits,
  best-model download to staging, adopt-if-better) + `train_legalbert_function.py` refactor (`build_split`,
  `build_holdout_spans`). Validated end-to-end. (3) Step-2 logit-bias calibration = NEGATIVE (NONE-downweight
  b*=0 no help; logit-adjust +0.03 macroF1 only by collapsing NONE precision 0.26->1.00 leak = unacceptable).
  (4) STEP 4 (mine silver) = NEGATIVE, not adopted. Reused the T60 CUAD-NONE pattern:
  `spans/scarce_function_labels.py` + `scripts/mine_scarce_functions.py` (keyword pre-filter -> GEMMA confirm;
  A/B: Gemma 0.57 recall / 0.98 precision ~= DeepSeek 0.58/0.98, so Gemma kept). Mined 139 silver spans
  (Third-Party +81, Notice-Period +23, Non-Disparagement +15, Unlimited/Irrevocable +9, MFN +2), folded
  train-only (leak-safe). Modal retrain: mean non-NONE recall 0.631->0.648 (+0.017 DIFFUSE, within run-variance)
  but the TARGET scarce classes did NOT move (Notice-Period 0->0, Irrevocable 0->0, Third-Party 0.71->0.71) --
  the 2 zeros are CONFUSABILITY (->Renewal-Term/->License-Grant), not scarcity, so data can't fix them. Kept
  the current production model (staging not promoted).
  (5) STEP 5 (sibling-margin) = NO EFFECT. Derived confusable FAMILIES from the holdout confusion; added a
  sibling-margin hinge to the Modal WeightedTrainer. Identical to step-4 (0.648) -- the margin acts on TRAIN
  examples the model already separates, so it can't fix HELD-OUT generalization. Left as a tried lever,
  defaulted OFF (`sib_lambda=0`).
  (5b) STEP 5b (LLM hybrid) = ADOPTED (targeted). `spans/hybrid_classifier.py` HybridFunctionClassifier =
  LegalBERT + Gemma fallback only when a RARE_TARGET (`spans/function_families.py`) is in the top-2 AND the
  top-2 are confusable siblings in a dev-validated ROUTE family (Gemma beats LegalBERT there; A/B). Dev-
  validated +0.014 mean non-NONE recall (leak-free test half). Wired into `ingest_cuad.py`. CU-D1 (applied to
  the store, 221/27074=0.8% re-labeled): the two 0.00 classes become highlightable -- **Notice-Period 0->0.24,
  Irrevocable 0->0.12**, No-Solicit 0.50 -- AGGREGATE holds (0.704->0.700). A capability gain (rare types now
  work) at ~few-hundred Gemma calls/ingest, no query-time cost, zero risk to common classes. Classifier arc
  (steps 1-5b) COMPLETE. **Next: await direction** (CU-D1 aggregate bounded by common classes + chunking, not
  the rare tail; other levers = chunking quality, serve/retrieval; or GATE-2).
- **2026-07-27: Classifier base-model A/B DONE (grounded-research follow-up).** 4 bases, one recipe, same
  SEED=0 holdout, on Modal per the new `model-training-recipe` skill (resumable checkpointing +
  continue-until-convergence + tagged always-save + adopt-only-if-better; `scripts/train_legalbert_modal.py`
  gained `eval` mode + `eval_fn` to re-score saved weights, function timeout 90m->4h for the slow Qwen). Result
  (holdout mean non-NONE recall): **deberta-v3 0.618 ~ legal-bert 0.614** (identical macro-F1 0.596) >
  contracts-bert 0.601 >> **qwen-0.5B 0.552** (small-decoder-LLM hypothesis empirically rejected). Ensemble
  math (`scripts/ab_model_overlap.py`): majority-vote 0.600 (< best single), **ORACLE any-of-4 = 0.732
  (+0.114)** -- encoders miss DIFFERENT classes (per-class complementarity), naive vote can't capture it.
  **DECISIONS:** keep **legal-bert** as production (DeBERTa's +0.004 = noise + fp32-fragile); drop the small-LLM
  idea; **defer** the 2-encoder ensemble until the legal-bert CU-D1 baseline is reconfirmed. All 4 saved to
  `data/models/<slug>/` + preds in `data/models/ab_preds/`. Results: **`docs/eval/classifier_model_ab.md`**.
  (SKILL is installed at `~/.claude/skills/model-training-recipe/`, outside the repo.) **Next: reconfirm CU-D1.**
- **2026-07-26: CU-D2 DONE (approved) -- CUAD highlighting workstream COMPLETE (CU-A1..CU-D2).** NL->type eval
  drove a benchmarked model switch: `understand_query` is now TWO-STEP reason->emit on **Gemma** (ADR-0032;
  single-call Gemma failed like Qwen -> split + profile thinking-disable). Gemma-two-step ties/beats Pro at 3x
  less cost. Results: **`docs/eval/nl_to_type_cu-d2.md`**. **Next up: none pending -- await direction** (open
  levers: classifier per-class recall for rare CUAD types [CU-D1 ceiling], out-of-taxonomy leak tightening,
  the discriminator/(b) hook for value-condition queries, CU-B5 page/bbox overlay).
- **2026-07-26: CU-D1 DONE (approved).** CUAD highlighting eval, full SEED=0 holdout, given-type.
  **Coverage 0.704**, presence R=0.864/P=0.601/F1=0.709. Results at **`docs/eval/cuad_highlighting_cu-d1.md`**.
  Caught+fixed an ingest label-casing bug (Ip->IP). Ceiling = classifier per-class recall.
- **2026-07-26: CU-C2 DONE (approved).** Serve + citation (`capabilities/highlight_serve.py`): typed set /
  extract / discriminate-stub / out-of-taxonomy fallback; every span cites its doc offsets. Live end-to-end
  citation loop verified. Phase C complete.
- **2026-07-26: CU-C1 DONE (approved).** NL->type front door (`capabilities/query_understanding.py`): one
  structured call -> normalize-at-boundary -> `QueryIntent`. 14 hermetic + live smoke. NOT canonical-slug
  registered (NL->type is not an FR-C/FR-Q spec capability; built as query-graph glue).
- **2026-07-26: CU-B4 DONE (approved).** CUAD ingestion driver over the full SEED=0 holdout: single-call
  chunker + deterministic repair (ADR-0031), two-phase concurrent-chunk/sequential-store. 102 contracts,
  27,074 spans, offset round-trip clean on all 27,074.
- **2026-07-26: NEW WORKSTREAM — CUAD clause-highlighting pipeline (plan approved).** Within-doc
  retrieval-as-classification for a real MVP: NL query about a KNOWN contract -> highlighted, cited spans.
  NL->type is a first-class front door (not deferred); citation = exact doc location (offsets, dropped at 3
  hops today). Plan: `~/.claude/plans/precious-enchanting-kahan.md`. Decisions: out-of-taxonomy = semantic
  fallback + low-confidence flag; multi-type allowed; value-as-discriminator (case b) detected-now/stage-
  stubbed; NL->type eval = LLM-generated + spot-check; eval only on the SEED=0 20% CUAD holdout (leak-free).
  Tasks (contract-first, one-at-a-time gate):
  | CU-A1 | Contracts: extend SpanRecord (contract_id/doc_start/doc_end/page/bbox) + new ContractRecord, QueryIntent, HighlightResult | 3 Contracts | FR-C | **done** — `contracts/{span(extended),contract_meta,query_intent,highlight}.py`; QueryIntent canonicalizes clause_types to FUNCTION_LABELS; SpanRecord CUAD fields optional (ACORD leg untouched); parent_chunk_id serves as clause_id (no redundant field); 13 tests, 57 span/store/chunk regressions green |
  | CU-B1 | Offset-preserving parse->chunk: Chunk.doc_start/doc_end + canonical_document_text() | 4 Build | FR-I | **done** — canonical text = _SEP-join of finalized chunk texts (chunker strips/merges/splits, so NOT raw Docling text); byte-faithful round-trip test; bbox source retained in the DoclingDocument (CU-B5). RLM boundary discovery untouched |
  | CU-B5 | (DEFERRED) page/bbox overlay: map canonical char-offset -> DoclingDocument item -> page+bbox for PDF-overlay highlight | 5 Integrate | FR-Q | todo (deferred) |
  | CU-B2 | Document-absolute span offsets + persist (segment->SpanRecord->Span schema->upsert_span) | 4 Build | FR-I | **done** — `to_span_record()` composes doc_start=chunk.doc_start+op.start (RAW text so canonical[doc_start:doc_end]==text); Span schema + upsert_span persist contract_id/doc_start/doc_end (null on the ACORD leg). Hermetic round-trip + LIVE ArcadeDB round-trip (4 -m store pass, ACORD leg backward-compat) |
  | CU-B3 | Contract vertex + upsert_contract + typed filter spans_by_contract(contract_id, functions) | 4 Build | FR-S/FR-Q | **done** — Contract vertex+schema+unique index; upsert_contract (parties JSON, page_count nullable) + contract_by_id lookup; spans_by_contract = WHERE contract_id AND function IN [...] ORDER BY doc_start. Live tests: round-trip, multi-type, cross-contract isolation, absent->empty; 16 -m store green |
  | CU-B4 | CUAD ingestion driver `scripts/ingest_cuad.py` (parse->chunk->segment->classify->embed->store, holdout) | 5 Integrate | FR-I | **done** (ADR-0031) — SingleCallBoundaryDiscoverer + deterministic repair_partition (plugs into chunk() via the discoverer seam; agentic RLM unchanged); two-phase driver (concurrent single-call chunk / sequential local classify+embed+store). Chunk-model comparison: agentic RLM 5-9min ALL models vs single-call 3.4s integrity 1.0 -> single-call for structured contracts, async not process-pool (no interpreter lock). Repair tested thoroughly first: 17 tests incl 3000-iter property test (any garbage -> valid partition). Full SEED=0 holdout: 102 contracts, 27,074 spans (31% typed), **offset round-trip clean 27074/27074** in-memory + fresh store read; phase1 156s @conc6. 35 hermetic tests green |
  | CU-C1 | NL->type query-understanding capability (build_structured -> QueryIntent; multi-type; out-of-taxonomy) | 4 Build | FR-Q | **done** — `capabilities/query_understanding.py` `understand_query()`: one STRUCTURED_REASONING call -> LOOSE schema -> normalize at boundary into strict QueryIntent (canonicalize case-insensitively, drop unmappable, dedupe, DERIVE in_taxonomy from what maps -> mapping miss degrades to fallback not error). 14 hermetic tests + live smoke (colloquial "walk away"->Termination For Convenience; force majeure->out-of-taxonomy fallback; multi-type; extract-value routing). NOT registered under a canonical slug: NL->type is not an FR-C/FR-Q capability in the closed spec catalog (registering would invent a requirement); built as query-graph glue. Flagged at gate; user approved |
  | CU-C2 | Serve + citation (typed filter -> set; field-extract; discriminator stub; fallback+flag; HighlightResult) | 5 Integrate | FR-Q | **done** — `capabilities/highlight_serve.py` `serve_highlight()`: 4 branches (highlight=typed set; extract=concurrent per-span field-extract; discriminate=passthrough stub/hook for (b); out-of-taxonomy=contract-scoped cosine fallback + low_confidence). `store.all_spans_by_contract` (vectors, for fallback; global ANN would miss a ~1%-of-corpus contract). 6 hermetic tests + live end-to-end smoke: NL->understand->serve->HighlightResult, every span's [doc_start:doc_end] slices canonical back (bad=0); extract pinpoints values (California, party names); force-majeure->fallback+flag. 50 CUAD tests green |
  | CU-D1 | CUAD highlighting eval (held-out, given-type): presence acc + macro span-overlap F1 | 5 Integrate | FR-Q | **done** — `eval/cuad_highlight.py`, full SEED=0 holdout (102 contracts x 41 cats = 4182 cells, no subset). **RESULTS: `docs/eval/cuad_highlighting_cu-d1.md`** (regenerated by the eval). Coverage 0.704 (gold answer inside a returned span); presence R=0.864 P=0.601 F1=0.709; token R=0.752 F1=0.489 (F1 low by design=whole-clause highlight). Metric = normalized-text overlap NOT raw offsets (canonical!=raw CUAD coords). Ceiling = LegalBERT per-class recall (macro-F1 0.544); zeros = rare-class blind spots. Caught+fixed an ingest casing bug (Ip->IP function labels stored raw; canonicalize at boundary; 494 labels corrected in place). 7 hermetic metric tests |
  | CU-D2 | NL->type eval (LLM-generated NL queries + spot-check; type accuracy + out-of-taxonomy) | 5 Integrate | FR-Q | **done** (ADR-0032) — `eval/nl_to_type.py`, 208 LLM-generated (Gemma) + spot-checked queries (44 labels x4 + 8 out-of-tax + 8 multi), predicted cross-model. **RESULTS: `docs/eval/nl_to_type_cu-d2.md`**. Drove a benchmarked model switch: single-call Gemma returned None (thinking-mode tool rejection like Qwen) -> refactored `understand_query` to TWO-STEP reason->emit (CLAUDE.md rule/ADR-0006) + Gemma profile thinking-disable (verified chunking unaffected). A/B: Gemma-two-step (any 0.886/exact 0.773/multi 1.0/oot 0.75, 141s) ties/beats Pro-single (0.881/0.619/1.0/0.833, 409s) at 3x less latency+cost -> **adopted Gemma default** (benchmarked exception, like ADR-0031/0023). 62 CUAD tests green |

  **Demo / reusable API (2026-07-28, planned — `docs/demo_plan.md`).** Common **FastAPI backend** = three
  capability surfaces, **endpoints named by CAPABILITY not corpus** (reusable in a real app / as future
  orchestration agents; NOT `/cuad/ask`): (1) locate-in-document `POST /documents/{id}/ask` [ready], (2)
  retrieve-across-corpus `POST /retrieve` [text leg, on `ragwright_acord_pivot`, GATE-R pending], (3)
  traverse-relationships `POST /graph/query` [needs ER graph populated]. Shared `GET /documents`,
  `/documents/{id}`, `/health`. **CLI + Web frontends built separately, both call the API.** New deps
  `fastapi`/`uvicorn`/`httpx` ask-first. **Sequencing (per direction): ER-graph population precedes the full
  demo**; surface (1) is demoable now. FastAPI = hand-wired glue (not a registered capability); the compiler
  binds the *registered* capabilities later. Tasks:
  | GRAPH-POP | ER-graph population driver: per contract graph_extraction->disambiguation->entity_resolution(EDGAR CIK)->write_graph into ArcadeDB; unwire `_EMPTY_GRAPH`; run relational archetype. Corpus = EDGAR/CUAD entity graph (NOT the ACORD clause-relation sketch). Code exists+tested; driver does NOT. Payoff bounded by thin golden set (16x1-hop+3x2-hop). **GP-1 = cheap/no-LLM first** (per direction 2026-07-28): registry-gazetteer nodes (`EntityRegistry.resolve` closed-world to EDGAR CIK; `Contract.parties` is empty in the store) + edges pending edge-source decision (CUAD Parties annotation vs LLM ContractExtractor); AFFILIATE_OF/2-hop deferred to a later LLM phase. **SPEC-vs-BUILD NOTE:** FR-C.6 names **docling-graph** (Pydantic-contract extraction) for the schema-entity leg + a NER+dependency+OpenIE bulk path; the build substituted a hand-rolled `ContractExtractor` + NER-only. docling-graph adoption **DEFERRED** (user, 2026-07-28; it is LLM/LiteLLM-based so not "cheap", has no registry linking, sinks to NetworkX/Cypher not ArcadeDB, and bypasses the model-profile seam) -- revisit as a schema-driven extraction engine for new/richer corpora, reconciling seam + resolution + ArcadeDB sink. **GP-1(A) DONE (2026-07-28, approved):** `scripts/populate_entity_graph.py` materializes the verified coparty adjacency -> `ragwright_cuad` entity KG **44 Entity nodes / 40 CONTRACTS_WITH edges** (was `entities: 0`); node ids = CIK / `PRIVATE:<key>` aligned to `eval.multihop._identity`; 1-hop traversal verified vs golden hubs (BofA->2, VerticalNet->6, Excite->3, Federated->1). `parties_to_extraction` (graph_extraction.py, no-LLM primitive, kept for a GP-1(B) auto-resolution coverage diagnostic) + 6 hermetic tests. Gold-anchored (plumbing/alignment verify, NOT extraction quality); synthetic edge provenance (`verified:` marker, not chunk-cited). **NEXT: GP-2** (unwire `_EMPTY_GRAPH` / wire the graph leg into the relational eval path), then **GP-3** (run relational archetype -> numbers), then **GP-1(B)** (real LLM text extraction; model = benchmark Gemma vs DeepSeek per 2026-07-28). **GP-2 DONE (2026-07-28, approved):** `eval/relational_eval.py` `graph_leg_recall()` wires the graph leg into the relational archetype (graph_query(anchor.identity, max_hops=hop_count) over the KG -> recall_at_k vs golden identities) + 3 hermetic tests. Live: **relational/graph recall@50 = 1.000** (1hop 16Q, 2hop 3Q, 0 misses). **This 1.000 is PLUMBING/ALIGNMENT verification, NOT extraction quality** -- tautological by gold-anchoring (graph built from the same verified adjacency the golden set uses); the real recall comes after GP-1(B). Correction: `_EMPTY_GRAPH` in `run_acord_retrieval.py` is the ACORD clause path (correctly empty), NOT the relational leg -- nothing to unwire. This also is GP-3's numbers (meaningful only post-GP-1(B)). **GP-1(B) = docling-graph adoption (2026-07-28, plan `docs/gp1b_docling_graph_plan.md`):** real extraction via **docling-graph** (IBM/docling-project schema-driven LLM KG extractor) instead of the hand-rolled ContractExtractor (reverses the earlier defer -- user directive; well-tested, docling-integrated, Granite-capable, reusable). Design: docling-graph extracts parties -> `parties_to_extraction` -> our resolve(EDGAR CIK) -> write_graph -> relational_eval (real recall vs the 1.000 gold bound). A/B **Gemma vs Granite vs DeepSeek** within docling-graph. Granite = local Ollama first + memory-monitor, Modal-Ollama fallback at >85%. Final deliverable GP-1B.6 = a reusable **agent Skill** for schema-driven KG extraction. **GP-1B.0 DONE:** `uv pip install docling-graph` (EVAL install -- not in pyproject yet, formal `uv add` at adoption GP-1B.5; docling unchanged 2.109, +litellm 1.93, tiktoken 0.13->0.12; run_pipeline API verified; 18 tests green). AST index at `graphify-out/docling-graph/`. **GP-1B.1 DONE (approved):** `capabilities/dg_extraction.py` = `ContractParties`/`Party` template + local `edge()` helper (edge() is NOT importable -- shipped templates define it locally); 5 hermetic tests incl. a docling-graph GraphConverter lint. Live smoke: docling-graph -> OpenRouter/DeepSeek extracted title + 2 parties (ACCURAY INCORPORATED, SIEMENS AKTIENGESELLSCHAFT) from a real CUAD contract. **3 integration gotchas (for GP-1B.3 + the Skill):** (1) source must be a FILE PATH not a raw string (docling-graph stat()s it); (2) unknown "openrouter" provider => generic 8192-token context window + 8192 output reservation => it SKIPS the LLM on any real doc ("produced no models") -- fix: cap output via `GenerationOverrides(max_tokens=...)` and/or `extraction_contract="dense"`/correct context window for FULL contracts; (3) its extractor swallows real errors into [] (`except Exception: return [], None`) -- debug via the backend logger. litellm->OpenRouter verified working directly (DeepSeek + Gemini). **GP-1B.2 DONE (approved):** adapter in `dg_extraction.py` -- `build_verified_registry(vset)` (verified filers + `variants` as aliases -> CIK; PRIVATE/unknown -> unlinked, isolating extraction quality) + `resolve_extracted(items, registry)` (extracted parties -> parties_to_extraction -> disambiguate -> resolve(CIK) -> to_graph); 3 hermetic tests (8 total green). **KEY FINDING (structured-output, direct model tests):** the docling-graph "produced no models"/empty-parties issue is NOT the model or context size -- it's `structured_output=True`'s STRICT nested json_schema: DeepSeek returns no parties under strict schema, gemini mis-formats -> swallowed to []; BOTH DeepSeek + Gemma extract correctly in `json_object` mode; **Gemma handles the strict schema, DeepSeek does NOT** (early A/B point). **Fix for GP-1B.3: `structured_output=False` (json_object) + max_tokens cap.** **GP-1B.3 DONE (approved):** extraction-model seam in `dg_extraction.py` -- `ExtractionModel` + `openrouter_model`/`ollama_model` + `build_pipeline_config` + `extract_parties`, with the reliability fixes baked in (`structured_output=False` + max_tokens cap); 3 hermetic tests (11 dg-tests green). Infra stood up: **Ollama installed + `granite4:micro` (Granite 4, 2.1GB) pulled + server running**. **All 3 A/B models confirmed extracting a real contract's parties:** Gemma + DeepSeek via OpenRouter, **Granite 4 micro LOCALLY via Ollama (14s, memory 40%->50%, 25.8GB free -- comfortably under the 85% threshold, so NO Modal fallback needed on this machine)**. **GP-1B.4 A/B RESULTS (12-contract dev slice, `scripts/dg_model_ab.py`, party recall/prec vs name-filtered CUAD gold + latency):** DeepSeek 0.787/0.917/33s (quality leader, best precision) ~ **Granite4:micro (local) 0.764/0.830/5.9s (value leader: near-DeepSeek recall, 5-6x faster, FREE+LOCAL+PRIVATE)** >> Gemma 0.648/0.599/25s (out). 5 hermetic scorer tests. **Winner decision DEFERRED -> GP-1B.4b** (user: Granite nearly matches DeepSeek, so explore MORE Granite before finalizing). **GP-1B.4b = Granite-variant A/B (todo):** micro (baseline) vs `granite4:tiny-h` (7B MoE, local) vs `ibm-granite/granite-4.1-8b` (OpenRouter, the only 4.1), DeepSeek as reference; Modal-vLLM follow-on for `granite4:small-h` (32B, uses credits). Grounded avail: OpenRouter = 4.0-h-micro + 4.1-8b only; Ollama granite4 = micro/3b/tiny-h(7B)/small-h(32B), no 4.1. Note: "4.0 tiny"=7B (not 3B); no 4.1-3B exists. **GP-1B.4b RESULTS (12-contract slice):** deepseek 0.829/0.889/41s >= **granite-4.1-8b 0.815/0.861/5.7s (IBM-open, ~=DeepSeek at 7x speed -- the standout)** > granite4:tiny-h(7B,local) 0.773/0.875 > granite4:micro(3B,local) 0.764/0.830. Granite thesis validated: 4.1-8b reaches DeepSeek-class extraction. **GP-1B.4c DONE (Modal Granite 32B + Modal-ready):** Modal **Endpoints** (managed product) does NOT support Granite (catalog = Qwen/Gemma-4/DeepSeek-V4/GLM/Nemotron/gpt-oss/Kimi; no Granite; DeepSeek-V4-Pro + Gemma-4-31b ARE there) -> self-hosted via `@app.server()` (`scripts/modal_granite_server.py`: Ollama on **A10**, model in a Volume via CPU `prepull`, unauthenticated; our `ollama_model(base_url=)` seam unchanged). Verified end-to-end (32B loaded on A10G, 200s). **32B result: 0.745/0.819 -- WORST Granite, does NOT beat 4.1-8b (0.815) or DeepSeek (0.829): GENERATION (4.1) > SIZE (32B 4.0).** **WINNER = `granite-4.1-8b`** (best Granite ~= DeepSeek, 7x faster, IBM-open) -> GP-1B.5. Full A/B: **`docs/eval/dg_model_ab.md`**. Gotchas: A100 needs a payment method (A10 works); ollama installer needs zstd; large pull digest-mismatch -> retry. Server stopped (billing halted; redeploy = `modal deploy scripts/modal_granite_server.py`). **GP-1B.5 DONE (approved): REAL relational recall.** `scripts/populate_entity_graph_extracted.py`: full-corpus extraction (509/510 CUAD contracts, docling-graph+granite-4.1-8b, 335s, conc 8) -> resolve(verified-variant registry) -> graph **1172 nodes (27 CIK-linked, ~27/28 verified CIK entities recovered) + 2086 CONTRACTS_WITH edges** into ragwright_cuad (replaced the gold-anchored graph). `eval/relational_eval`: **real recall@50 = 0.416 (1hop 0.494, 2hop 0.000)** vs the 1.000 gold-anchored bound. Extraction strong; recall capped by (1) **PRIVATE-entity alignment gap** (17/45 verified are PRIVATE -> our resolution yields UNLINKED:<key> not golden PRIVATE:<key> -> 0 recall on private anchors/answers; DOMINANT + fixable), (2) name-resolution recall, (3) 2-hop compounding -> 0. **GP-1B.5a DONE: PRIVATE-alignment fix -> real recall 0.416 -> 0.991.** `dg_extraction.build_private_map` (verified-PRIVATE surfaces -> golden `PRIVATE:<key>`) + `resolve_extracted(private_map=)` + driver extraction cache (`data/cache/dg_extracted_parties.json`) + 3 hermetic tests. Rebuilt graph: 1180 nodes (43 identified = 27 CIK + 16 PRIVATE) + 2179 edges. **eval/relational_eval: recall@50 = 0.991 (1hop 0.990, 2hop 1.000)** vs the 1.000 gold-anchored bound -- i.e. REAL granite-4.1-8b+docling-graph extraction recovers the golden relational graph essentially as well as the gold-anchored one (1 miss: verticalnet 5/6, a name-normalization edge case). Resolution anchored on verified variants (isolates extraction quality by design), so 0.991 = granite's extraction recall. **GP-1(B) CORE COMPLETE.** **GP-1B.6 DONE:** reusable agent Skill `kg-extraction-recipe` authored at `~/.claude/skills/kg-extraction-recipe/SKILL.md` (schema-driven LLM KG extraction: use docling-graph + BYO Pydantic template; the file-path/structured_output=False/max_tokens gotchas; the model seam OpenRouter/Ollama/Modal-@app.server + the Modal-Endpoints-no-Granite + A100-payment-method notes; bolt-on resolution + SENTINEL/PRIVATE alignment as the dominant recall lever 0.416->0.991; benchmark-then-adopt, generation>size; gold-anchor-first eval). **GP-1(B) FULLY COMPLETE (GP-1B.0-.6).** GRAPH-POP: GP-1(A) gold-anchored + GP-2 leg-wired + GP-1(B) real extraction (docling-graph+granite-4.1-8b, recall 0.991) + Modal-ready all done. | 5 Integrate | FR-C | **GP-1(A)+GP-2 done; GP-1(B) DONE (GP-1B.0-.6; real recall 0.991; Skill authored)** |

  **Unified Contract KG (2026-07-28, PLANNED — `docs/unified_contract_kg_plan.md`, ADR-0033; build NOT started,
  RESUME POINT).** ONE KG (Contract/Party/Clause/value nodes + typed edges) served as 3 scoped queries: Leg A =
  intra-contract QnA (one contract_id), Leg B = clauses cross-corpus (filter/rerank feature over BGE base pool
  + pointwise-Gemma reranker, NOT standalone), Leg C = entity traversal (DONE). Grounding: FOLIO (types,
  aligned) + ODRL (rights spine) + small custom OWL bridge (promote PropertyDimensions -> typed edges).
  Construct via the GP-1B recipe (docling-graph `template from-ontology` -> Pydantic -> per-span extract
  [**granite-4.1-8b, NO A/B**; DeepSeek = below-par contingency only; grounding-judge ADR-0028 gate] ->
  resolve/ground CIK+PRIVATE+FOLIO/ODRL IRIs -> write). Replaces the flat property graph (ADR-0025/0026) with
  typed ER (flat retired, not kept). Tasks:
  | KG-0 | Ontology bridge design (FOLIO align audit + ODRL + small custom OWL: clause-type classes, property-dimensions->typed edges, value nodes, PROV-O). **Ask-first data-model change -> schema-review gate** | 5 Integrate | FR-Q/FR-S | **DONE (2026-07-28, approved) — `docs/unified_contract_kg_ontology_bridge.md`. Gate resolutions: distinct `HAS_*` edges (Q1); `HAS_*`/`EXCEPTS`/`BOUNDED_BY` naming (Q2); build typed, RETIRE the flat `HasProperty` graph, not a fallback (Q3); `.ttl` authored+validated at KG-1 (Q4); ODRL at FULL depth = deontic core + `odrl:constraint` for cap/temporal bounds (Q5); KG-2 model = granite-4.1-8b, NO A/B. Also: `docling_graph` added to the framework grounding graph (2402 nodes; `refresh_framework_graph.sh`)** |
  | KG-1 | Compile bridge OWL -> Pydantic clause template (`docling-graph template from-ontology`); hermetic lint | 5 Integrate | FR-C | **DONE (2026-07-28, approved) — `src/rag_wright/ontology/`: `contract_bridge.ttl` (335 triples, rdflib-clean; FOLIO exactMatch + ODRL subPropertyOf + `odrl:Constraint` cap/temporal models + 15 `owl:oneOf` vocabs + PROV-O), `contract_bridge.spec.yaml` (editable SPEC source), `clause_template.py` (compiled root `Clause`, 18 fields: 10 enum props + 3 list props covers/excepts/prohibits_damage + 3 nested constraint models caps/bounded_by/governed_by). `docling-graph template lint` = exit 0 / 0 gaps. 9 hermetic tests (`tests/ontology/test_clause_template.py`) incl. the invariant: every enum == `property.py::CLOSED_VOCAB` (one shared vocab); `CapBasis.cap_other`->canonical `other` at KG-3 (documented). Dep: `docling-graph[templategen]==1.9.1` (brings rdflib 7.6.0 + linkml-runtime 1.11.1; also formalizes docling-graph in pyproject). 164 tests green** |
  | KG-2 | Per-clause typed extraction from spans with **granite-4.1-8b (no A/B; DeepSeek = KG-6 below-par contingency only)**; grounding-judge (ADR-0028) gate; hermetic tests + live smoke | 5 Integrate | FR-C | **DONE (2026-07-28, approved) — reuses the GP-1B `dg_extraction.py` seam (added `extract_clause`: same docling-graph API-mode + reliability fixes, template=`clause_template.Clause`). New `spans/clause_kg_extractor.py`: `clause_to_record` (PURE adapter Clause->`ClausePropertyRecord`; OTHER=not-asserted dropped; `cap_other`->canonical `other`; open dims cap_quantum/jurisdiction/temporal via _clean; temporal_kind routes NOTICE_PERIOD vs TEMPORAL_BOUND) + `DGClausePropertyExtractor` (extract->adapt->`reground` ADR-0028 gate; matches T57b PropertyExtractor shape) + `granite_clause_extractor()` (OpenRouter `ibm-granite/granite-4.1-8b`, no A/B). 12 hermetic tests + LIVE granite smoke (`-m model`, 10s): a mutual cap clause -> carve_out{fraud,gross_negligence} + damage_type{indirect,consequential,punitive} + cap_basis=multiple_of_fees + cap_quantum=12_months, FOLIO IRI attached, all grounded. 656 hermetic pass** |
  | KG-3 | Resolution/grounding (parties->CIK/PRIVATE; values/predicates->FOLIO/ODRL IRIs) -> write the TYPED unified KG to ArcadeDB (new typed edge types, drop flat `HasProperty`; node identities + provenance schema preserved) | 5 Integrate | FR-C/FR-S | **DONE (schema + write path + driver; 2026-07-28, approved) — `store/arcadedb.py`: `_TYPED_DIMENSION_EDGE` (all 19 dims -> 14 sanctioned typed edges `HAS_*`/`EXCEPTS`/`COVERS`/`PROHIBITS`/`REQUIRES`/`CAPS`/`BOUNDED_BY`/`GOVERNED_BY`), predicate-IRI grounding (ODRL on deontic, bridge IRI else) + FOLIO on value nodes, `write_clause_kg` (content-hash gated, 1 txn), `clause_kg_counts`/`clause_typed_edges`/`clear_clause_kg`; identities (`clause_id`/`value_key`)+provenance+gate preserved; legacy `HasProperty`/`write_property_graph` kept non-breaking. 6 hermetic + 5 live `-m store` (typed write+readback+predicate-IRI+FOLIO+provenance, idempotent, shared-value dedup, clear); 29 store / 663 hermetic green. Built in `ragwright_acord_pivot`; physical Leg-C merge deferred to KG-4/5. Population DRIVER `scripts/populate_clause_kg.py` built. **FULL-CORPUS TYPED POPULATION RUN DONE (2026-07-28): granite-4.1-8b over 3,863 clauses (FRESH=1 retired the flat graph, spans kept), ~56min @1.15/s, 0 errors -> typed KG in `ragwright_acord_pivot` = 3,887 clauses / 1,220 value nodes / 12,481 typed edges; legacy `HasProperty`=0; spans=14,553 preserved; grounding gate downgraded 2,761/12,481 to AMBIGUOUS (9,720 EXTRACTED). Edge dist: PROHIBITS 2569, BOUNDED_BY 2178, COVERS 1318, CAPS 1003, EXCEPTS 919, HAS_MUTUALITY 769, HAS_IP_OWNERSHIP 694, HAS_CLAIM_SCOPE 606, GOVERNED_BY 547, REQUIRES 442, HAS_FAVORABILITY 431, HAS_ASYMMETRY 418, HAS_WARRANTY_SCOPE 415, HAS_RENEWAL 172. Run offline (HF_HUB_OFFLINE=1) after a transient huggingface.co DNS blip amplified docling's MiniLM-tokenizer HEAD checks.**** |
  | KG-4 | Leg A: intra-contract scoped-query serving (structured, relational, cited QnA); upgrade `highlight_serve` backend | 5 Integrate | FR-Q | **DONE (2026-07-29, approved). CUAD typed KG POPULATED in `ragwright_cuad` (granite-4.1-8b via OpenRouter, 7,026 clauses / 1,916 value nodes / 11,598 typed edges across 100 contracts, median 54 clauses/contract max 503 — real intra-contract structure; 26% AMBIGUOUS ~ ACORD's 22%; ~1% clauses persistently unextractable, kept as spans). Cache `scripts/build_cuad_clause_cache.py` (7,097 substantive clauses, metadata tags dropped). Leg A capability `capabilities/contract_kg_serve.py`: `contract_clause_index`/`clauses_of_function`/`disambiguate`/`aggregate_by_property`/`grounded_only` over 3 new store methods (`clauses_in_contract`/`contract_clause_kg`/`clauses_with_property`; exact clause_id key-range scoping, LIKE-safe). Cited (clause_id + span_id + confidence). 6 hermetic + 1 live `-m store`; 672 hermetic green. LIVE DEMO: CERES collaboration contract 503 clauses/340 typed; disambiguated 9 Covenant-Not-To-Sue clauses -> 1 by temporal_bound=unbounded. DEFERRED: party-role Qs (Leg C layer), cross-clause REFERENCES, wiring `disambiguate` into `highlight_serve` discriminate branch. Modal path built (granite4.1:8b-bf16 container) but single-A10 too slow (NUM_PARALLEL=1) -> used OpenRouter; Modal concurrency deferred.** ORIGINAL: the typed KG was ACORD clause-level (1 clause/doc, no intra-contract structure); Leg A needs multi-clause CONTRACTS. CUAD is already segmented (`ragwright_cuad`: 102 contracts / 27,074 spans, contract_id + function tags + doc offsets, ~260 spans/contract). Tags 100% in-taxonomy (0 out-of-taxonomy; 1 label `Unlimited/All-You-Can-Eat-License` has 0 spans). PROPERTY-SCHEMA EXTENDED for full CUAD coverage (approved): +11 dims (exclusivity_type/right_of_first_type/restriction_scope/coc_consent/assignment_consent/escrow_release_trigger/mfn_scope/termination_right + open audit_frequency/commitment_quantum/ld_trigger) through property.py + contract_bridge.ttl + clause_template.py (regen, lint clean) + `_TYPED_DIMENSION_EDGE` (deontic->GRANTS/PROHIBITS, rest->HAS_*; edges 14->23) + adapter + grounding cues + tests (666 hermetic/29 store green; live granite fills the new dims). NEXT: build CUAD extraction cache (filter substantive functions) -> Modal granite4.1:8b-bf16 population -> then serve Leg A.** |
  | KG-5 | Leg B: typed-KG constraint match as the retrieval/rerank signal. **REDESIGN (2026-07-29, in progress): KG-PRIMARY / neuro-symbolic** — the KG (symbolic, schema-typed, confidence-tagged) LEADS retrieval; the LLM reranker is demoted to a cheap final touch. Rejected the pointwise-Gemma base (N calls/query = production bottleneck; the eval cache hides it). Variants to measure (recall@10/@20 + nDCG@10 + per-query LLM-call count; condensed/pool-ranking metrics only — full-pool r@50 is confounded on partially-judged ACORD): V1 hard-filter (AND), V2 graded-rank (#constraints, recall-safe), V3 graded->one listwise Gemma call, V4 graded->embedding. First BGE-based attempt (eval/kg_property_rerank.py) was a stopgap that understated the KG; being replaced. | 5 Integrate | FR-Q | **in-progress** |
  | KG-5a | KG value normalization (canonicalization + subsumption) — fix 4b match brittleness | 5 Integrate | FR-Q | **DONE (2026-07-29). Diagnostic: exact set-intersection match lost gold to (1) jurisdiction surface variants (England/England and Wales/English law) and (2) granularity (licensor_affiliates vs affiliates). `contracts/jurisdiction.py` (gazetteer+normalize+containment; 65/92 ACORD, 75/170 CUAD surfaces canonicalized, additive `canonical_value` on value nodes via `store.patch_canonical_jurisdictions`) + `contracts/value_match.py` (jurisdiction canon + 3 subsumption rollups: affiliates/restriction_scope/mfn_scope; `covered_subject`->ip_infringement noted FUTURE) + `skos:broader` in contract_bridge.ttl + eval match wired. 57 hermetic green. RE-DIAGNOSTIC: England gold match 4/6->10/10, affiliates 0/6->8/12 (multiple-gov-laws unchanged = extraction disagreement not match). NOT committed (holding).** |
  | KG-5b | Query front door = `extract_clause` (granite+clause_template) on the query — same extractor/schema both sides (replaces the Flash 4a decomposer) | 5 Integrate | FR-Q | **VALIDATED (2026-07-29, diagnostic; build pending). granite+clause_template extracts the right typed constraints from queries in BOTH raw-descriptor and question-rephrasing form (10/10 core constraints correct); jurisdiction now aligns across sides (query gives `England` like the clauses). => query-side symbolic, NO separate question-template needed. CAVEATS: (1) granite `clause_type` UNRELIABLE for function routing (often empty / non-taxonomy free-text) -> route function elsewhere (KG-5c); (2) some NOISE (spurious constraints, question-style a bit more) -> mitigation under investigation (grounding-judge on the query extraction).** |
  | KG-5c | Function routing A/B/C — how the candidate set is built in production (replaces the ORACLE function; the V4 numbers rely on the oracle pool, so this measures the real cost): **(A)** LegalBERT `classify_topk(k=2)` on the query -> union-top-2 function pool (~0.99 reach), KG ranks within [today's V4 with a real, imperfect classifier instead of oracle]; **(B)** KG-primary / `MODE=corpus` — NO function pool, the whole clause corpus ranked by the query's granite-extracted typed constraints (function is a soft signal only) — tests whether property-match alone survives without type-scoping (risk: a `mutuality=mutual` query pulls mutual clauses of every type); **(C)** HYBRID — LegalBERT top-2 as a SOFT type-boost (not a hard filter), so a strongly-property-matching clause of a mis-classified type is not lost; hedges classifier error AND B's type-pollution. Report all three vs the V4-oracle number (0.569/0.843/0.615). granite `clause_type` is UNRELIABLE (KG-5b) so it does NOT replace LegalBERT for function; A/C keep LegalBERT, B drops the function step entirely (not by replacement — by a different retrieval mechanism). | 5 Integrate | FR-Q | **MEASURED (2026-07-29, in `eval/kg_primary.py` MODE=classifier/corpus/hybrid, VARIANT=v4, cached BGE emb `data/models/kg_v4_emb_*.json`). recall@10 / @20 / nDCG@10: Oracle-ref 0.569/0.843/0.615 · A classifier 0.437/0.623/0.497 · B corpus 0.399/0.577/0.456 · C hybrid 0.395/0.586/0.458. FINDINGS: (1) the ORACLE flattered V4 massively — every real router is −0.22 to −0.26 recall@20 below it; honest production today ≈ mode A (0.62 r@20 / 0.50 nDCG). (2) hard function pool > no-pool (A > B≈C): function scoping helps, B's type-pollution confirmed; the soft type-boost (C) adds ~nothing (reuses the same unreliable query classifier, only a tiebreak). (3) THE BOTTLENECK IS QUERY-SIDE FUNCTION ROUTING, NOT THE RERANKER — the oracle→real gap (−0.22 r@20) dwarfs every reranker lever (listwise +0.01, pointwise +0.08). Root cause: LegalBERT was trained on CLAUSE spans; queries are a different distribution. NEXT (function router = the dominant lever; see KG-5e): (d) derive function from the granite-extracted DIMENSIONS via a dimension->function map [free, uses the reliable extraction — the user's "typed extraction builds the pool" intuition]; (c) union-top-3; (b) taxonomy-constrained LLM function classifier folded into the existing granite call; (a) fine-tune classifier on query text. Awaiting direction.** |
  | KG-5d | Noise-handling lever (promoted from the KG-5b caveat — was load-bearing but invisible): the query-side granite extraction emits some SPURIOUS typed constraints (question-style a bit more so) that can mis-rank in the KG graded match. (1) MEASURE the noise impact first (per-query: which ranked constraints have no gold basis; does dropping them change recall@10/nDCG); (2) only if harmful, author a TIGHT query template (precision over coverage — the reverse of the clause template) and/or a query-side grounding-judge variant — but NOT the naive grounding-judge-on-query (KG-5 rejected it: it false-flags real constraints like `control_of_defense` where the query wording != the clause cue). Also folds the deferred KG-primary "close-the-gap" lever: IDF-weight the constraint match so non-discriminative closed values (e.g. a near-universal enum) count less than rare ones — a finer KG score than integer match counts, aimed at the ~0.08 nDCG gap to the pointwise ceiling. | 5 Integrate | FR-Q | **NOISE MEASURED -> NO FIX NEEDED (2026-07-29, on adopted llm_union v4; eval `CLEAN=oracle`). 31% of query constraints are candidate noise (31/99 spurious, avg 0.54/query), BUT an ORACLE noise filter (gold-informed, an UPPER BOUND) lifts only +0.007 recall@20 / +0.015 nDCG@10 (0.490/0.713/0.553 -> 0.499/0.720/0.568). The KG graded-rank + embedding tiebreak is robust to spurious constraints (a constraint matching nothing adds 0; relevants still sort by real matches + embedding). => DO NOT build the query template (the task's "fix only if harmful" gate: measured, not harmful). IDF-WEIGHT MEASURED -> ALSO NO HELP (eval `MATCH=idf`, df over N=3931 clauses, idf=log(N/df)): 0.487/0.704/0.550 vs count 0.490/0.713/0.553 — slightly WORSE (-0.009 r@20). Queries carry few constraints (~1.7) and a "common" value is often the KEY signal (mutuality=mutual for a mutual-clause query); down-weighting non-discriminative values discards real signal (discriminativeness != relevance). KG-5d VERDICT: no change warranted — neither a query template (noise) nor IDF-weighting helps; the count-match + embedding-tiebreak ranking is already robust. Adopted pipeline stands as-is. Levers left in eval as CLEAN=oracle / MATCH=idf diagnostics (defaults none/count).** |
  | KG-5e | Query-side FUNCTION ROUTER — the dominant retrieval lever surfaced by KG-5c (oracle→real gap −0.22 recall@20 dwarfs every reranker lever). Root cause: LegalBERT is trained on CLAUSE spans, queries are OOD. Build + A/B the query→function router against the KG-5c mode-A baseline (0.437/0.623/0.497) and the oracle ceiling (0.569/0.843/0.615): **(d)** deterministic `dimension -> function` map over the granite-extracted query dimensions (FREE, reuses the reliable extraction — the "typed extraction builds the pool" path); **(c)** union-top-3 pool; **(b)** taxonomy-constrained LLM function classifier folded into the existing granite call (~1 call/query); **(a)** fine-tune LegalBERT on query-style text (needs query-labeled data — derive from ACORD gold clause->function, guard against train/test leakage). Pick the best by recall@20/nDCG@10 vs pool size (precision). | 5 Integrate | FR-Q | **MEASURED d+c (2026-07-29). recall@10 / @20 / nDCG@10 vs oracle-ceiling 0.569/0.843/0.615 and classifier-top2 0.437/0.623/0.497: (c) CLASSIFIER TOP-3 = 0.472/0.693/0.528 <- WINNER (+0.070 r@20 over top-2, ~1/3 of the oracle gap closed, no new dependency — just widen the pool we already build). (d) DIMENSION ROUTING is a DEAD END: conditional K2 0.292/0.433/0.336, K3 0.333/0.465/0.370; lift K2 0.289/0.390/0.310, K3 0.307/0.432/0.336, K2 min_support=3 0.299/0.399/0.315; pmi K2 0.277/0.378/0.306 — EVERY scoring lands <=0.47 r@20, below the classifier. The lift/PMI fix corrected the isolated frequency-bias example (unit test) but in aggregate traded frequency bias for RARITY bias (dividing by tiny P(f) boosts niche functions sharing a dimension); min_support didn't rescue. UNION (c-top3 U d-lift-K2) = 0.470/0.669/0.523 — slightly WORSE than (c) alone: (d) dilutes, adds noise not signal. ROOT CAUSE (d fails): the query's typed dimensions are too weakly function-discriminative (mutuality/jurisdiction/caps recur across many function types) and too sparse per query. VERDICT: the "typed extraction builds the pool" intuition was worth testing but the extraction's DIMENSIONS don't route as well as the clause classifier; ADOPT (c) classifier top-3. Residual oracle gap (0.693->0.843) = classifier's query-time OOD error -> needs lever (a) fine-tune-on-queries or (b) LLM function classifier. Built: `contracts/function_routing.py` (build_cooccurrence/route_functions, conditional|lift|pmi + min_support; 6 hermetic tests), `scripts/build_function_routing_map.py` (held-out CUAD prior -> `data/models/dimension_function_map.json`), eval MODE=classifier(TOPK)/route(SCORE)/union. LEVER (b) DONE (2026-07-29): taxonomy-constrained LLM query->function classifier, built as TWO SEPARATE granite calls (constraints + function; `capabilities/query_function_classifier.py` classify_query_functions + route_query wrapper, 3 hermetic tests). Needed a granite structured-output fix: granite silently returns [] under function_calling, json_schema fixes it -> new granite profile in profiles.py (structured_method=json_schema; empirical, KG-5e, ADR-0034 pending). Results (r@10/@20/nDCG@10): (b) LLM alone K=3 = 0.434/0.620/0.503 (routes NARROW, avg 1.5 fns/q -> ~= classifier top-2, BELOW top-3); **(b∪c) LLM-UNION-classifier-top3 = 0.490/0.713/0.553 = NEW BEST real router**, beats (c)-top3 (0.472/0.693/0.528) on ALL THREE (+0.020 r@20, +0.025 nDCG) -- complementary routers (LLM in-distribution + LegalBERT broad) fail on different queries, so union RECOVERS (unlike d∪c which diluted). Cost = 2 granite calls/query (constraints+function) + local LegalBERT, NO reranker. VERDICT: ADOPT (b∪c) llm_union as the production router (0.713 r@20, ~40% of the oracle gap closed). Residual oracle gap 0.713->0.843 -> lever (a) fine-tune-on-queries / widen (b) K, diminishing returns. Awaiting go-ahead for KG-5d (noise).** |
  | KG-6 | Eval: extraction recall vs gold clause-KG; A/B on HARDER queries (intra-contract multi-constraint/role/aggregation; cross-corpus conjunctive), grade>=2 floor | 5 Integrate | §12 | **DONE (2026-07-29). THESIS CONFIRMED: the typed-KG advantage SCALES WITH QUERY HARDNESS. A/B = KG-primary (adopted llm_union v4 router, KG count-match + BGE tiebreak) vs DENSE-ONLY (same pool, `RANK=dense`, embedding rank only), split by #constraints (grade>=2, the ACORD default floor). Deltas KG-dense: [0-constraint n=11 KG-INERT: 0/0/0 -- perfect control, identical 0.505/0.640/0.560]; [1 single n=22: r@10 +0.034, r@20 -0.001, nDCG +0.053]; [>=2 MULTI n=24: r@10 +0.075, r@20 +0.050, nDCG +0.072 -- LARGER on ALL THREE]. Multi-constraint KG-primary = 0.562/0.814/0.600 (vs dense 0.487/0.764/0.528). Symbolic constraint-counting helps most where queries AND multiple constraints (dense blurs them). eval/kg_primary.py RANK=kg|dense + per-hardness buckets. CONSOLIDATED (existing measurements): Leg C relational/role -- EDGAR 1-hop/2-hop gold, real relational recall 0.991 vs 1.000 bound (eval/multihop.py + relational_eval.py, GP-1B); Leg A intra-contract multi-constraint -- KG-4 disambiguation demo (CERES 9 Covenant-Not-To-Sue -> 1 by temporal_bound). EXTRACTION FIDELITY: grounding-judge gate on the typed KG (~22% AMBIGUOUS flagged / ~78% grounded EXTRACTED, ADR-0028) + function tags 100% in-taxonomy (KG-4). HONEST LIMITATION (recorded): no hand-annotated property-VALUE gold-KG exists, so property extraction recall is a grounding-judge PROXY, not a gold-anchored number (would need hand annotation); the RELATIONAL leg IS gold-anchored (0.991).** |
  | KG-7 | The Party<->Contract unifying link over the ONE contract KG: add `PARTY_TO` edges (Entity -> Contract) connecting the populated party graph to the typed Clause KG, WITHOUT re-ingest. Schema change (ask-first, approved). | 5 Integrate | FR-S.1 | **DONE (2026-07-31; user-approved schema change). ADR-0036.** `capabilities/party_clause_linking.py`: `derive_party_contract_links` (PURE — match each Contract.parties_json name to an Entity node by `normalize_entity_name`, first-wins, dedup (entity,contract), COUNT unmatched private/unlinked parties not drop) + `party_clause_linking(store)` (read all_contracts/all_entities -> derive -> write -> result). Store: new `PartyTo` edge type in ensure_schema + `all_contracts`/`all_entities` readbacks + idempotent `write_party_contract_links` (clears ONLY the PARTY_TO layer first). Edge not id-field because Party<->Contract is many-to-MANY (one CIK node, many contracts); Contract->Clause stays edge-free (clause_id key-range). Twofold reg: +slug +manifest +register_party_clause_linking (function, contract PartyClauseLinkResult). NO node/identifier scheme change. 6 hermetic tests; 821 green. LIVE RUN over ragwright_cuad = separate op (smoke DRY -> full write of PARTY_TO edges; non-destructive, adds edges only). NEXT = LG-3d.** |
  | HYG-1 | KG-data-hygiene root-cause fix: ONE canonical `source_doc_id` slug used by ALL ingestion paths (the `_`-vs-`-` divergence broke cross-graph joins). Forward fix; existing data reconciled by HYG-2. | 5 Integrate | FR-S.2 | **DONE (2026-07-31).** Root cause: FIVE roll-your-own slugifiers (ingest_cuad `_`, dg_extraction `-`, populate_property_store `-`+prefix, parsing `_`, corpus/cuad raw stem); parse_cuad passes the raw CUAD title verbatim; ChunkId/ParsedDocument validators accept `_`/`-`/`.` interchangeably so nothing reconciled them. FIX: `contracts/identifiers.canonical_source_doc_id(raw)` (non-safe run -> single `_`, strip, idempotent, raises on empty) — the ONE derivation all 5 sites now call (dg_extraction is THE fix: `-`->`_`). Regression test `test_regression_the_two_paths_now_agree` guards it. 7 new tests + 1 parsing assertion updated (old un-stripped `Some_Contract_v2_` -> canonical `Some_Contract_v2`). 828 green; touched files ruff-clean. FORWARD-ONLY (existing ragwright_cuad Entity ids still `-` until HYG-2). |
  | HYG-2 | Reconcile the live CUAD Entity graph: re-run the entity-graph WRITE from the GP-1B cache with the HYG-1 slug -> re-key the `ragwright_cuad` Entity `chunk_id`s from `-` to `_` (matches Contract/Clause). NO re-extraction (cache reuse); entity_id (CIK/name) unchanged so Leg-C recall 0.991 preserved. | 5 Integrate | FR-S.2 | **DONE (2026-07-31; live-data op, no repo code — the fix was HYG-1's dg_extraction). Re-ran `populate_entity_graph_extracted` DRY then real (LIMIT=0, from cache 509 contracts, NO LLM). Result: Entity chunk_ids re-keyed to `_` (no `-AGREEMENT` remains); Leg-C PRESERVED (1180 entities / 2179 CONTRACTS_WITH — identical, entity_id unchanged); the provenance join (`Entity.chunk_id` source_doc == `Contract.contract_id`) went 0 -> 219 available PARTY_TO edges across 96/102 contracts. Non-destructive to Contract/Clause/Span. Verified live.** |
  | PARTY-TO-MANY-TO-MANY | Complete the `PARTY_TO` link to true many-to-many (limitation found in KG-7-revised, recorded in ADR-0036 + `party_clause_linking.py` docstring): the provenance join links each party to only its LAST-written extraction contract, because GP-1B upserts one `Entity` per resolved party. A multi-contract party (esp. the CIK-linked ones) should link to EVERY contract it signed. Fix: derive links from the per-(contract, party) mention data (the GP-1B cache `data/cache/dg_extracted_parties.json`, keyed by contract) rather than the single stored `chunk_id`, resolving names -> entity_ids. Ties to the party-role layer. | 5 Integrate | FR-S.1 | **DONE (2026-08-04).** `party_clause_linking.py`: `derive_party_contract_links_from_mentions(contracts, entities, mentions)` (pure many-to-many: contract keys canonicalized HYG-1 → contract_id, party names resolved by `normalize_entity_name` first-wins → one PARTY_TO per (entity, contract), deduped; unmatched name counted, node-less contract skipped) + `party_clause_linking(store, *, mentions=None)` (mention cache → many-to-many, else KG-7 provenance). 6 hermetic tests (a party in 2 contracts → 2 links; canonical key+name; unmatched; skip; dedup; capability). `scripts/link_party_clause.py` updated (MANY=1 default from `data/cache/dg_extracted_parties.json`, DB→ragwright_cuad_full). **LIVE WRITE over `ragwright_cuad_full`**: 1174 single-provenance → **1278 many-to-many PARTY_TO edges** (75 multi-contract parties gained links); VERIFIED CIK 0001043946 → 5 DISTINCT contracts, 0000070858 (BlackRock) → 5 distinct (BlackRock JOINT_FILING + Cardlytics Maintenance_Agreement 1-4), no dup edges. 908 green; ruff clean (also fixed 4 pre-existing E741 in the test file). |
  | PARTY-LINK-DEFAULT-MANY | Durability follow-up to PARTY-TO-MANY-TO-MANY (found during RECOVER-4-DOCS): `run_cuad_ingestion`'s corpus-level link step called `party_clause_linking(store)` with NO `mentions=`, so every re-ingest silently reverted PARTY_TO to the 1-to-1 provenance join (observed live: 1278 → 1181). Make the many-to-many mention-cache derivation the default. | 5 Integrate | FR-S.1 | **DONE (2026-08-04).** Extracted `_corpus_party_link_fn(store, mentions_path)` in `contract_ingestion_pipeline.py` — defaults to the mention-cache many-to-many derivation (loads `data/cache/dg_extracted_parties.json` lazily at link time, same path as `link_party_clause.py MANY=1`), graceful fallback to the KG-7 provenance join when the cache is absent; `run_cuad_ingestion` now wires it. 2 hermetic tests (many-to-many default + no-cache fallback); 910 green; ruff clean. A future `ingest_cuad_full` re-run now keeps PARTY_TO many-to-many automatically (no manual `MANY=1` re-run). |
  | CUAD-FULL-COVERAGE | Corpus ingested at INCONSISTENT scales per stage (found during HYG-2): party/entity graph ~482 of 510 CUAD contracts (GP-1B, for Leg-C), but clause KG ~100, Contract nodes 102, **Spans only 77** (retrieval index even smaller than clauses — the clause side isn't internally consistent). 386 party-graph contracts have no Contract node. To fully connect the KG over the whole corpus, ingest all 510 UNIFORMLY across every stage via the LG-3d generic pipeline + CUAD adapter. Optional / when-needed (bigger extraction job). | 5 Integrate | FR-S | **DONE (2026-08-03, on a GCP GPU VM; adoption deferred). Ran all 510 through the generic pipeline into a FRESH db `ragwright_cuad_full` (reset on THAT db; live `ragwright_cuad` untouched until verify+adopt) via `scripts/ingest_cuad_full.py`. Enabled by INGEST-REFACTOR (a) cache adoption. TWO perf fixes found+made en route: (1) the REAL bottleneck was BGE-M3 span embedding on CPU (~1000ms/span under ingest load, contending with LegalBERT) -> BGEM3Embedder now auto-uses Metal `mps` (device/`EMBED_DEVICE`-aware, batched); verified bit-identical vectors (CPU-vs-MPS cosine 1.0), so a pure speed change; (2) clause-extraction concurrency now env-tunable (`CLAUSE_CONCURRENCY`, running at 24). Steady rate ~0.85 clause/s (the inherent docling-graph+granite-4.1-8b throughput, matches prior runs; concurrency-capped by per-clause pipeline overhead, not the API) => ~11hr for ~36k clauses. RESUMABLE (per-doc clause cache persists) + monitored (`[ingest] i/510`). Models: granite-4.1-8b (clause + party, party mostly cache-seeded) + gemma-4-31b-it (chunking, ~408 uncached docs); LegalBERT + BGE-M3 local. ROBUSTNESS (2 interruptions handled): (i) harness REAPS detached bg processes after a few hours -> relaunch via `nohup ... & disown` + a `RESET` env so resume doesn't wipe; (ii) an ArcadeDB `Error during read lock` TransactionException in the WRITE stage CRASHED the run (the write node was the one stage NOT wrapped in the retry/dead-letter `_guard`) -> fixed: write node now guarded + retried (a transient DB write error dead-letters the doc, never kills the corpus ingest), the Contract node is written LAST (a true fully-written marker), and `run_corpus_ingestion` gained an `is_done` RESUME-SKIP (skip docs whose Contract already exists -> a resume re-does only what remains, no re-embed/re-write). 2 more hermetic tests (843 green). (iii) THE ACTUAL ROOT CAUSE of both the read-lock crash AND a later hung `reset` drop: the ArcadeDB Docker container's JVM heap was capped at 2GB (`ARCADEDB_OPTS_MEMORY=-Xms2G -Xmx2G`) and the full KG (graph + clause KG + BGE vector index) OOM'd it (~doc 387); OOM surfaced as read-lock TransactionExceptions + GC thrash, not a clean OOM. FIXED (2026-08-01): recreated the container with `-Xmx6G` (old kept as `arcadedb-ragwright-old` rollback; live `ragwright_cuad` verified intact = 7096 clauses/1180 entities; partial full-build db deleted for a clean rebuild). See memory [[arcadedb-container-heap]]. Rebuild relaunched on the 6GB heap (clause cache free-replays docs 1-387). On completion: verify uniform coverage (Contract/clauseKG/entity/Span/PARTY_TO all ~510) then adopt (swap in for the live KG) -- awaits approval. **(2026-08-02) MOVED to a GCP GPU VM (arcadedb-gpu, L4/g2-standard-8): local ArcadeDB OOM'd on the vector-index build even at 6GB heap; co-located the whole ingest on the VM (BGE + LegalBERT on the L4) -> replay ~190 docs/30min, fresh tail OpenRouter-bound ~11 docs/30min; see memory [[gcp-bulk-ingestion-box]].** **GRANITE VRAM measured (2026-08-02, `scripts/modal_granite_server.py::measure_gpu_memory`): granite-4.1-8b BF16 = ~17GB VRAM on an A10 (Ollama size_vram 16,728 MiB = weights+KV@ctx4096; +~0.3GB CUDA overhead) = 74% of a 23GB A10; fits any 24GB L4/A10-class card with ~6GB spare (room for KV / a small 2nd model, NOT a 2nd granite). Q4 on Mac ~10GB by contrast. See [[local-vs-hosted-granite-throughput]].** **✅ COMPLETE (2026-08-03): 506/510 contracts ingested UNIFORMLY (4 short one-page filings chunk-dead-lettered). Verified counts: Contract 506 · Clause 42,269 · PropertyValue 9,013 · Span 136,292 (WITH embeddings) · Entity 1,174 · PartyTo 1,174 · 72,664 typed clause edges across 25 edge types (GOVERNED_BY 6468 / PROHIBITS 15659 / REQUIRES 7809 / GRANTS 1442 / HAS_TERMINATION_RIGHT 3886 / ...). The HYG-2 inconsistency (Contract 102 / clause KG ~100 / Span 77 / entity ~482) is FIXED — every stage now consistent at 506. BACKED UP to GCS (consistent online `BACKUP DATABASE` -> `docker cp` -> `gcloud storage cp`): `gs://dreamai-pocs-ragwright-ingest/kg-backups/ragwright_cuad_full-backup-20260802-202058017.zip` (627 MB, self-contained; restore anywhere via `RESTORE DATABASE`). See ADR-0038 + memories [[gcp-bulk-ingestion-box]] / [[arcadedb-container-heap]]. GPU VM STOPPED post-backup (billing). REMAINING: (1) ADOPTION — point the query pipeline at the KG (remote SSH tunnel to the VM, OR restore the GCS backup into a local ArcadeDB for fast eval loops) — DEFERRED to after-a-break (user); (2) recover the 4 short docs via a too-short->single-chunk chunker fallback + `RESET=0` re-ingest (skips the 506 done). **DONE (2026-08-04): NO chunker fix needed** — reproduced `chunk()` on all 4 via the exact pipeline path (`SingleCallBoundaryDiscoverer` + `_parsed_from_text`), all chunk cleanly (1/1/5/3 chunks); the dead-letters were TRANSIENT (the doc-~387 heap-OOM read-lock cascade documented in [[arcadedb-container-heap]]). Recovery = `RESET=0 RAG_SERVING=openrouter` delta re-ingest into the LOCAL canonical `ragwright_cuad_full` (is_done skipped the 506, ingested only the 4; OpenRouter granite + local BGE/LegalBERT, no A100 spin). All 4 fully present (Pcquote/MACY'S/TALCOTT/SCOUTCAM spans 8/8/116/36). **CAUGHT + FIXED a regression:** the ingest's built-in `party_clause_linking(store)` reverted PARTY_TO 1278->1181 (1-to-1); re-ran `link_party_clause MANY=1` -> **1287** (many-to-many restored + extended to the 4 new contracts, 509/510 reached), then made it the pipeline default (see PARTY-LINK-DEFAULT-MANY). Local canonical KG uniform at 510. Modal KG still 506 (will re-sync from a fresh local backup when it next comes up). **THROUGHPUT RESEARCH (2026-08-03, `scripts/modal_granite_throughput.py`): benchmarked granite-4.1-8b bulk extraction -- self-hosted vLLM vs the single OpenRouter provider. OpenRouter scales cleanly (C=8 5.3 -> C=64 23.5 req/s / 2813 tok/s, ZERO 429s, still climbing) => real headroom; a RAW granite call is ~1.2s p50 vs docling-graph-wrapped ~10s, so the fresh-tail bottleneck was our conc=24 + docling-graph overhead, NOT OpenRouter. vLLM (continuous batching, 1 GPU): **A100-40GB 33.1 req/s (4623 tok/s) BEATS OpenRouter ~1.4x**; A10 12.5, L4 7.3 both below. So a single A100 self-host wins on throughput; mid-tier GPUs don't. Caveats: OpenRouter still climbing at C=64 (ceiling untested); A100 ~$2.5-4/hr+idle+ops vs OpenRouter pay-per-token+zero-ops => one-off bulk favors OpenRouter (raise CLAUSE_CONCURRENCY to 48-64, free), sustained/private favors A100 vLLM. Cheap ingest speedup regardless = higher concurrency + trim docling-graph. Full detail in memory [[local-vs-hosted-granite-throughput]].** |
  | MODAL-STACK-2 | Validate vLLM-Granite **structured-output** clause extraction matches OpenRouter QUALITY (not just speed; the throughput benchmark measured raw generation only). Serve granite-4.1-8b on a Modal A100 via vLLM with guided decoding (xgrammar/outlines) over the `Clause` template; run the real DGClausePropertyExtractor path against it on a clause sample; compare extracted fields + AMBIGUOUS rate vs OpenRouter granite. | 5 Integrate | ADR-0039 | **todo (DOING FIRST)** |
  | CHUNKER-OPEN | **RESHAPED (2026-08-03, user): granite-only assessment, NO Gemma/OpenRouter comparison** (comparison deferred — user decides later if needed). Assess **granite-4.1-8b** as the `SingleCallBoundaryDiscoverer` ON ITS OWN, to eventually drop Gemma and stay single-model on the A100. Chunking is a SEPARATE step that consumes docling's parse (`document.texts` structural items) — the discoverer sends `[index] text[:140]` lines + ONE structured `_BoundaryList` (list of spans) call → `repair_partition` to a valid partition. `_BoundaryList` is a LIST-of-objects = exactly the shape granite failed under `function_calling`, so use **json_schema** (the granite structured-output fix, KG-5e/ADR-0034). Run on a few docling-parsed CUAD docs at **temperature=0** and measure: (1) structured output NON-degenerate (real span list, not []), (2) RAW span validity (how much `repair_partition` had to fix), (3) CONSISTENCY across repeated runs at temp=0, (4) boundary sensibility (chunk count/sizes/coherence). Live on vLLM-Granite (A100, spin up→run→stop). | 5 Integrate | ADR-0039 | **DONE (2026-08-03) — granite is a viable single-call chunker.** `scripts/granite_chunker_assess.py` (loads cached docling parses `data/gate2_cache/parsed/*.json`, injects a vLLM json_schema factory into the `SingleCallBoundaryDiscoverer` prompt+schema+`repair_partition`; OFFSET/DOCS/RUNS knobs). Live on vLLM-Granite A100 (spin-up→run→STOP, all apps verified stopped; NO OpenRouter/Gemma). RESULTS (4 CUAD docs, temp=0, 3 runs each): items→chunks 98→15 / 158→23 / 14→14 / **316→36**; **structured output NON-degenerate** (json_schema, real span lists, never []); **FULLY DETERMINISTIC** (IDENTICAL boundaries ×3 on every doc); **3/4 raw partitions already perfectly valid** (no repair), 1 needed a single minor gap/overlap fix (`repair_partition` absorbs it); sensible sizes (~1-2k char mean). **CONFIG FIX made:** `scripts/modal_granite_vllm_server.py` `--max-model-len 8192→16384` — the 8192 default blocked whole-doc chunking of large contracts (the 316-item doc's prompt ≈8.2k tokens > 8192; extraction was unaffected = per-clause small prompts). At 16K the large doc chunked cleanly (36 chunks, raw-valid, deterministic). **LARGE-DOC CAVEAT (recorded, for MODAL-STACK-1):** 16K covers the assessed docs (largest ~8.2k tok / 316 items) but the very largest CUAD contracts (500+ items) may exceed 16K → need 32K or a windowed/hierarchical chunking fallback. NO src change (assessment); the granite-chunker wiring (vLLM json_schema factory into `SingleCallBoundaryDiscoverer`) rides with MODAL-STACK-1 alongside the JUDGE-SEMANTIC inline wiring. Gemma/OpenRouter comparison deferred (user decides later). |
  | MODAL-STACK-1 | ADOPT via the self-hosted stack (ADR-0039): point the seam at vLLM-Granite, roles→granite, wire the deferred gates, make the full KG queryable. **BROKEN DOWN (2026-08-03, reviewed plan) into MS1-1..6.** Decisions: **D1** KG LOCAL (restore GCS backup into local ArcadeDB; DELETE old `ragwright_cuad` 102-contract testing KG to save space; PRESERVE `ragwright_acord_pivot`); **D2** co-locate BGE+LegalBERT WITH vLLM-Granite on ONE A100 (validate LLM+non-LLM GPU coexistence — pulls the GPU-box build earlier); **D3** single granite for ALL roles (drop Gemma/DeepSeek from the product default, env-overridable). Full Modal-Volume ArcadeDB + production app packaging = ENTERPRISE-CONTAINER. | 5 Integrate | ADR-0039 | **DONE (2026-08-03) — MS1-1..6 all complete.** The product runs end-to-end on the self-hosted open-model substrate: seam→vLLM-Granite + roles→single-granite (MS1-1/2), chunker+judge+extraction all vLLM-routable (MS1-3), full KG adopted locally (MS1-4), co-located A100 (vLLM+BGE+LegalBERT, FIT + correctness + concurrent coexistence, BGE cosine 1.0 vs stored, 34.8/42.4GB no OOM; MS1-5a/5b), and all 3 legs answer on the stack with NO OpenRouter / NO local GPU (MS1-6). OPEN FOLLOW-UPS: (a) clause↔span join for the CUAD typed-rerank composite (MS1-6 finding); (b) full Modal-Volume ArcadeDB + app packaging = ENTERPRISE-CONTAINER. |
  | MS1-1 | **Seam → vLLM serving path.** `models/seam.py`: config-driven serving (`RAG_SERVING=openrouter|vllm`; vLLM `base_url`/key from env), NEVER hardcoded; OpenRouter stays default+fallback. `build_model`/`build_structured` read the serving config for base_url/api_key (keep per-model profile `structured_method`/retries). AC: `RAG_SERVING=vllm` routes to the vLLM base_url, unset→OpenRouter. Verify: hermetic seam tests (config selection + routing); live smoke deferred to MS1-6. Files: `models/seam.py`, tests. | 5 Integrate | ADR-0039 | **DONE (2026-08-03).** `models/seam.py`: new `_serving_config()` selects backend by `RAG_SERVING` (openrouter default/dev+fallback | vllm product), vLLM reads `VLLM_BASE_URL`+`VLLM_API_KEY`; `build_model` uses it instead of hardwired `_openrouter_config`; everything else (profile structured_method/extra_body/retries/timeout) backend-agnostic so ALL seam callers flip to vLLM with no call-site change. Fail-loud: invalid RAG_SERVING → ValueError, vllm without VLLM_BASE_URL → KeyError. 9 hermetic tests (`tests/models/test_serving_seam.py`; offline — ChatOpenAI construction makes no network call, asserts `openai_api_base` routing); live smoke rides with MS1-6. 877 green; ruff clean. |
  | MS1-2 | **Roles → single granite (drop Gemma/DeepSeek default).** `models/profiles.py`: STRUCTURED_REASONING / GENERAL / SUMMARIZATION defaults → `ibm-granite/granite-4.1-8b` (granite profile already `json_schema`); env-overridable via the existing `RAG_MODEL_*`. Foundation models stay env-selectable for dev, dropped from the product default. AC: `model_for(role)`→granite by default for those roles; env override still works. Verify: hermetic profile tests. Files: `models/profiles.py`, tests. | 5 Integrate | ADR-0039 | **DONE (2026-08-03).** ALL 5 roles default to `ibm-granite/granite-4.1-8b` via `_PRODUCT_LLM` in `_ROLE_ENV` (incl. OKF_ENRICHMENT — user-corrected: OKF is NOT in the ingestion/query pipeline, only `okf/enrich.py`, so ADR-0023's Gemma is vestigial; zero Gemma in defaults). Foundation-model profiles (deepseek/qwen/gemma×2/kimi) stay REGISTERED in `PROFILES` (dev-override selectable, not deregistered). `model_for` precedence (all config): role-specific `RAG_MODEL_<ROLE>` > all-roles `RAG_MODEL_ALL` (NEW — point every role at one model for a cross-model test) > default. So any single role OR all roles are swappable purely by env, and `RAG_SERVING` (MS1-1) picks the backend. 6 profile tests updated/added (every-role→granite, all-roles override, per-role-wins, foundation-models-stay-registered); 15 profile / 879 total green; ruff clean. |
  | MS1-3 | **Wire the deferred inline gates.** (a) granite chunker: `SingleCallBoundaryDiscoverer` uses GENERAL→granite via the seam (16K already set); drop the Gemma default. (b) `semantic_judge` INLINE into ingestion (`clause_kg_extractor` / `typed_clause_extraction`) after reground+symbolic_validate, via `build_semantic_judge_fn` through the seam. AC: chunker + judge run through the seam; ingestion applies semantic_judge on surviving semantic assertions. Verify: hermetic wiring tests (fake seam). Files: `rlm_chunking.py`, `clause_kg_extractor.py`/`typed_clause_extraction.py`, tests. | 5 Integrate | ADR-0039 | **DONE (2026-08-03).** (a) chunker: `SingleCallBoundaryDiscoverer` defaults to `model_for(GENERAL)`=granite via the seam (verified — 16K max-model-len already set MS-CHUNKER). (b) semantic judge INLINE: `DGClausePropertyExtractor` gained an injected `semantic_judge_fn` (default None → deterministic-only, so existing callers/hermetic tests unaffected); `production_document_ingest` wires the granite judge via `build_semantic_judge_fn(model_for(STRUCTURED_REASONING))` (lazy → no network at construction; hermetic pipeline tests use `build_document_ingest` with fakes so nothing fires). **(c) SCOPE ADDITION (breakdown missed it): clause EXTRACTION is a separate model surface (docling-graph `ExtractionModel`), NOT the seam** — added `dg_extraction.default_extraction_model()` + `vllm_model()` that read the SAME `RAG_SERVING` switch (via new `seam.serving_backend()`, the single source), so `granite_clause_extractor` routes extraction to vLLM-Granite (product) or OpenRouter (dev). => ALL THREE LLM surfaces (chunk + extract + judge) flip to vLLM-Granite together with `RAG_SERVING=vllm`. 5 hermetic tests (`tests/models/test_serving_wiring.py`); 884 green; ruff clean. Live exercise rides with MS1-6. Files: `models/seam.py`, `capabilities/dg_extraction.py`, `spans/clause_kg_extractor.py`, `subgraphs/contract_ingestion_pipeline.py`. |
  | MS1-4 | **Restore full KG locally + delete old.** (a) DELETE ArcadeDB db `ragwright_cuad` (102-contract testing KG) to save space — PRESERVE `ragwright_acord_pivot` (ACORD/Leg-B, different corpus); (b) `RESTORE DATABASE` `ragwright_cuad_full` from the GCS backup (`gs://dreamai-pocs-ragwright-ingest/kg-backups/ragwright_cuad_full-backup-*.zip`) into local ArcadeDB; (c) repoint the default live-KG name → `ragwright_cuad_full`. AC: full KG live locally, counts match (Contract 506 / Clause 42269 / PropertyValue 9013 / Span 136292 / Entity 1174 / PartyTo 1174), old KG gone, ACORD intact. Verify: count queries. No GPU. | 5 Integrate | FR-S / ADR-0039 | **DONE (2026-08-03).** Started Docker + `arcadedb-ragwright` container (user had killed it for memory). INSPECTED first: the local `ragwright_cuad_full` (817M) was a STALE PARTIAL (Contract 385 / PartyTo 0 — the local build that OOM'd ~doc 387 before the GCP move), NOT authoritative → GCS restore needed. (a) DROPPED `ragwright_cuad` (102/7096, old testing KG) + 7 stale scratch dbs (ragwright_acord, ingest_smoke×2, pivot_dryrun, clause_kg_dryrun, ping, rag_wright) via `DatabaseDao.delete`; PRESERVED `ragwright_acord_pivot`. (b) Downloaded the GCS backup (627M zip = file-level ArcadeDB snapshot, 65 files) → stop container → replace the partial dir → unzip (1.0G) → restart → VERIFIED counts EXACT: Contract 506 / Clause 42269 / PropertyValue 9013 / Span 136292 / Entity 1174 / PartyTo 1174. (c) Repointed `.env` `ARCADEDB_DATABASE` rag_wright→`ragwright_cuad_full` (the deleted scratch was the old default); `from_env()` now hits the full KG (Contract 506). Only `ragwright_cuad_full` + `ragwright_acord_pivot` remain on disk. NOTE (minor, retiring scripts): the old population scripts (populate_entity_graph / link_party_clause) still default `DB=ragwright_cuad` (now deleted) — INGEST-REFACTOR is retiring them; not the query path. `.env` is gitignored (not committed). |
  | MS1-5a | **Co-located A100 container + FIT.** ONE Modal A100 container hosting vLLM-Granite (subprocess) + BGE-M3 + LegalBERT (in-process), exposing embed + classify endpoints beside the vLLM OpenAI server (one ASGI/web app). Tune `--gpu-memory-utilization` DOWN (~0.75-0.80) so BGE (~2-4GB) + LegalBERT (~0.5-1GB) LOAD alongside granite (~17GB weights+KV) on the 40GB card. AC: all three LOAD and serve a trivial request on one A100 with NO OOM; memory headroom reported. Verify: live smoke of each endpoint + a memory report (`nvidia-smi`/torch). GPU (warm session, `scaledown_window=300`). Files: new/extended `scripts/modal_*` app. Failure mode addressed = OOM-at-load / physical fit. | 5 Integrate | ADR-0039 | **DONE (2026-08-03).** `scripts/modal_stack_a100.py`: a Modal A100 `@app.cls` — `@modal.enter` launches vLLM-Granite as a subprocess (`--gpu-memory-utilization 0.75`, 16K) + loads BGE-M3 (`BGEM3FlagModel`, fp16, cuda) + LegalBERT (transformers, from the new `rw-models` Volume, uploaded 419M) in-process; `@modal.asgi_app` exposes `/health` (memory) + `/embed` + `/classify` + a `/v1/{path}` proxy to the local vLLM OpenAI server. FIT PASSED: all three load + serve on ONE A100-40GB with **34.8GB used / 42.4GB total → 7.6GB free, NO OOM**; `/embed`→dense-1024+sparse, `/classify`→"Governing Law" (correct), `/v1`→granite "OK". URL `https://farhan-zaidi--rw-stack-a100-stack-web.modal.run` (seam base_url `.../v1`; `scaledown_window=300`). |
  | MS1-5b | **Service correctness + coexistence.** On the MS1-5a container: verify each service's OUTPUT is correct — **BGE embeddings land in the SAME space as the KG's stored span vectors** (cosine ~1.0 vs a known span's stored vector — the silent-retrieval-drift risk, KG vectors were built with BGE-M3 on the GCP L4), LegalBERT classify correct on known clauses, granite generate correct — AND all three stay clean under CONCURRENT load (no OOM when hit together). AC: each endpoint correct + coexists under concurrent load. Verify: consistency check vs stored vectors + concurrent smoke. GPU (same warm session). Failure mode addressed = silent wrongness / OOM-under-load (distinct from MS1-5a's fit). | 5 Integrate | ADR-0039 | **DONE (2026-08-03).** `scripts/stack_correctness_validate.py` (fetches Span text+stored-dense+function from `ragwright_cuad_full`, checks each service vs ground truth + a concurrent mixed-load burst). RESULTS: **(1) BGE consistency = min cosine 1.0000 across 10 distinct-function spans** — A100-BGE lands EXACTLY in the KG's stored vector space (no silent retrieval drift; stored vectors were GCP-L4-BGE). **(2) LegalBERT 10/10 match the stored function labels.** **(3) concurrent coexistence (24 mixed reqs): embed 8/8, classify 8/8, generate 8/8, 34.8GB/42.4GB, NO OOM.** (First run showed generate 0/8 = cold vLLM: the warm container had SCALED DOWN during a local Docker-daemon hang >5min idle; re-ran after `until vllm_up` → all green. Lesson: keep the A100 warm across 5a/5b/6, don't let long LOCAL ops idle it out.) |
  | MS1-6 | **End-to-end adoption query validation.** Local query pipeline (reads LOCAL `ragwright_cuad_full`) with its seams pointed at the MS1-5 co-located A100 (LegalBERT classify + BGE embed + granite rerank/generate via vLLM; `RAG_SERVING=vllm`). Run representative real queries — Leg A intra-contract, Leg B typed retrieval, Leg C relational — and confirm CITED answers. AC: representative queries answered with citations against the fully self-hosted stack; substrate switch proven end-to-end → MODAL-STACK-1 done. Verify: query transcript + eyeball. GPU (warm, same session as MS1-5). | 5 Integrate | FR-Q / ADR-0039 | **DONE (2026-08-03).** Built the query-encoder adapters `capabilities/remote_encoders.py` (`RemoteBGEEmbedder`/`RemoteLegalBertClassifier` → the A100 /embed + /classify; `query_embedder`/`query_classifier` factories select remote when `STACK_URL` set, mirroring `RAG_SERVING`; 6 hermetic tests). Added top-k to the A100 /classify. `scripts/adoption_query_validate.py` ran all 3 legs on the fully self-hosted stack (local `ragwright_cuad_full` + A100: granite via `RAG_SERVING=vllm`, BGE+LegalBERT via `STACK_URL`; NO OpenRouter, NO local GPU): **Leg B** typed retrieval — LegalBERT top-3 + granite router → correct function, `span_hybrid_search` (A100 BGE + Span index) → spot-on cited spans ("liability shall not exceed the fees…"); **Leg A** — cited Cap-On-Liability clause (props) + granite grounded CITED answer via A100; **Leg C** — PARTY_TO traversal (Electric City Corp. → contract). 890 green; ruff clean. **FINDING (follow-up, not adoption-blocking): the CUAD full KG's Clause layer (props) and Span layer (vectors+text) DON'T JOIN by a shared id**, so the full typed-constraint-RERANK composite (`cross_corpus_retrieval`, needs props+vector per candidate) can't bind to CUAD-full yet — Leg B used pure `span_hybrid_search` (function-filtered) instead. The property-boost rerank over CUAD-full needs the clause↔span join built (relates to deferred Leg-A/B enrichment + INGEST-REFACTOR consistency). Store hybrid also has a Chunk-vs-Span index split (`hybrid_search`→Chunk index [empty in this KG], `span_hybrid_search`→Span [correct]). |
  | SPAN-CLAUSE-RERANK | **Typed-constraint property-boost rerank over CUAD-full (the MS1-6 follow-up).** CORRECTION to the MS1-6 finding: the clause↔span join is NOT missing — it is persisted on the TYPED PROPERTY EDGES: `_clause_kg_statements` writes `span_id` on every Clause→PropertyValue edge (ADR-0025 join key = the operative span's id), and `to_span_record` stores that same value as `Span.span_id`. So `edge.span_id == Span.span_id` joins a span to its clause's typed props (my MS1-6 compare of `clause_id`==`span_id` used the WRONG key). BUILD the KG-5-adopted retrieval over CUAD-full: route (LegalBERT top-k + granite) → constraints (granite) → **BGE base pool** (`span_hybrid_search`, bounded) → join each span to its props via `edge.span_id` → `typed_constraint_match_rank` (primary) + dense tiebreak → cited spans. AC: property-boosted ranking measurably re-orders vs pure hybrid (a constraint-matching span outranks a non-matching one of the same function); a store `span_properties(span_ids)` join method + hermetic tests; live validation on the A100 stack. Verify: pytest + a live query transcript showing the boost. | 5 Integrate | FR-Q / ADR-0033 | **(a) FOUNDATION DONE (2026-08-04); (b) capability + A100 validation NEXT.** (a): `ArcadeDBStore.span_properties(span_ids)` — the join, `{span_id: {(dim,value)}}` via the 23 typed edge types on `edge.span_id` (`inV().value` for the value); +1 hermetic test. `scripts/typed_rerank_validate.py` PROVED the boost over CUAD-full (local BGE, hand-constraints): Anti-Assignment/`assignment_consent=free` → 3 scattered-by-BGE matches promoted to TOP-3 (first-match rank 2→1); No-Solicit/`nonsolicit_target=customers` → matches stay top (boost stable). Corrected the MS1-6 finding (join was on the EDGES, not missing; I compared the wrong key clause_id==span_id). 891 green. **(b) DONE (2026-08-04).** Packaged `capabilities/property_boosted_retrieval.py` (`property_boosted_retrieval` + `RankedSpan`): BGE pool (`span_hybrid_search` over routed functions, deduped) → `span_properties` join → `typed_constraint_match_rank` (stable: match primary, BGE tiebreak) → top-k cited spans with the matched constraints; registered twofold (slug + manifest); `store.span_texts` for citations; 4 hermetic tests (boost lifts a BGE-last match to rank 1, BGE-tiebreak stable, pool dedup, empty). **FULL A100-INTEGRATED validation** (`scripts/property_boosted_a100_validate.py`, local KG + A100: granite constraint-extraction via `route_query` + A100 BGE/LegalBERT; NO OpenRouter/local GPU): "freely assign without consent" → granite `assignment_consent=free` → rank-1 MATCH ("Permitted Assignments… either Party may assign") above the consent-required clauses; "cap at a multiple of fees" → `cap_basis=multiple_of_fees` → ranks 1-4 MATCH; "governing law of more than one jurisdiction" → `law_multiplicity=multiple` → rank-1 MATCH. 896 green; ruff clean. **SPAN-CLAUSE-RERANK COMPLETE.** |
  | LEGB-ADOPT | **Adopt `property_boosted_retrieval` into the MS1-6 Leg B path proper.** The canonical adoption validation `scripts/adoption_query_validate.py` Leg B still used pure `span_hybrid_search`; upgrade it to the full route→granite-constraints→`property_boosted_retrieval` flow (the SPAN-CLAUSE-RERANK capability), so the canonical MS1-6 adoption path IS the property-boosted one. Consolidate the redundant `property_boosted_a100_validate.py` into it. AC: adoption Leg B shows granite constraints + boosted cited spans with matched constraints; re-validated on the A100 stack (all 3 legs green). | 5 Integrate | FR-Q / ADR-0033 | **DONE (2026-08-04).** `scripts/adoption_query_validate.py` Leg B upgraded: `span_hybrid_search` → `route_query` (granite constraints + granite functions) + LegalBERT top-k → `property_boosted_retrieval`; removed the redundant `property_boosted_a100_validate.py`. RE-VALIDATED on the A100 stack (all 3 legs green): Leg B "freely assign without consent" → `assignment_consent=free` → rank-1 MATCH above consent-required clauses; "cap at multiple of fees" → `cap_basis=multiple_of_fees` → ranks 1-4 MATCH; Leg A cited answer; Leg C traversal. 896 green; ruff clean. **NOTE (architecture gap, → new task LEGB-SUBGRAPH): this Leg B WORKFLOW is composed IMPERATIVELY in the validation script, NOT as a registered LangGraph subgraph (the LG-3 declared pattern). `property_boosted_retrieval` IS a registered function capability, but the composite flow must become a registered `kind="subgraph"` (upgrade `cross_corpus_retrieval`'s CUAD binding, user-chosen) to follow the ARD pattern.** |
  | LEGB-SUBGRAPH | **Make the property-boosted Leg B a REGISTERED LangGraph subgraph (the LG-3/ARD declared pattern) — closes the gap that the Leg B workflow was an imperative script.** DESIGN (user-chosen): a NEW hardened subgraph reusing `property_boosted_retrieval` (the registered function capability) as a single retrieve node, rather than restructuring `cross_corpus_retrieval` (which stays the general/ACORD composite). | 5 Integrate | FR-Q / ADR-0033 | **DONE (2026-08-04).** `subgraphs/typed_property_retrieval.py`: `build_typed_property_retrieval(constraints_fn, functions_fn, retrieve_fn)` on `scaffold.py` — START → {extract_constraints, classify_functions} PARALLEL → retrieve (joins both, runs `property_boosted_retrieval`) → assemble → END; each IO node degrades-to-empty on exhausted retries (never crashes, query-side posture). `production_typed_property_retrieval` wires granite constraint-extraction + granite/LegalBERT routing + the capability over the store+encoders. Registered twofold (`typed_property_retrieval`, kind=subgraph: slug + manifest + register fn). 5 hermetic tests (happy path, retrieve joins both, each seam degrades-to-empty) + a LOCAL smoke of the COMPILED graph running the REAL retrieval over the live KG (rank-1 `assignment_consent=free` match). 902 green; ruff clean. Production granite front-door = the same capabilities already A100-validated in LEGB-ADOPT. `cross_corpus_retrieval` untouched (coexists; different pool strategy, shares retrieval_core rerank). **(2) adopted into the demo: `scripts/adoption_query_validate.py` Leg B now INVOKES the subgraph (`production_typed_property_retrieval(...).invoke({query})`) instead of the imperative loop. (1) FULL PRODUCTION SUBGRAPH RUN ON THE A100 (user-required): the compiled subgraph ran end-to-end on the self-hosted stack — granite constraints + routing (parallel) → property_boosted_retrieval; `cap_basis=multiple_of_fees` → ranks 1-4 MATCH, anti-assignment `free` surfaced; all 3 legs green. NO OpenRouter / NO local GPU.**** |
  | JUDGE-ONTOLOGY-1 | **Neuro-symbolic Layer 2, part 1 — function→dimension applicability (the biggest deterministic win; DO FIRST).** ADR-0040. Add a NEW symbolic-validation pass to the extraction-fidelity cascade AFTER the lexical `reground` and BEFORE the KG write. **Ontology addition:** formalize the `function → applicable-dimensions` map in the ontology (today `rdfs:domain` points at constraint classes like `cbr:CapConstraint`, NOT at FOLIO clause functions) — author it as SHACL node shapes (one `sh:NodeShape` per clause function, `sh:property` listing its permitted dimensions) in a NEW `contract_bridge.shapes.ttl` derived alongside `contract_bridge.ttl`. **Code:** a `symbolic_validate(record, function)` that serializes the ClausePropertyRecord's assertions to RDF (clause instance + FOLIO function + property triples) and runs `pyshacl` against the shapes; any assertion whose dimension is not applicable to the clause's function → downgrade to AMBIGUOUS with the SHACL violation as provenance (kills the observed `nonsolicit_target`-on-Anti-Assignment class = error class 1). New dep `pyshacl`. Ground the applicability map against the `Clause` template's function-specific field structure. TDD: messy fixtures (a wrong-dimension assertion must flag; a correct one must pass). Wire into `typed_clause_extraction` (ingestion) next to `reground`. | 5 Integrate | ADR-0040 | **DONE (2026-08-03).** `spans/symbolic_validation.py`: `FUNCTION_APPLICABLE_DIMS` (all 44 FUNCTION_LABELS → applicable PropertyDimension sets; metadata fns → ∅; tier-1 cross-cutting kept permissive to avoid false AMBIGUOUS) compiled to `sh:closed` SHACL NodeShapes (one per function, lru-cached/built once) + `_record_to_rdf` + `nonapplicable_dimensions` (pyshacl) + `symbolic_validate` (downgrade non-applicable assertions to AMBIGUOUS, confidence-independent, leaves AMBIGUOUS alone, unmodeled function = permissive). Wired beside `reground` at all 3 INGESTION sites (`clause_kg_extractor.py`, `typed_clause_extraction.py` ×2); query-side untouched (KG-5d). **DECISION:** map authored in CODE (references PropertyDimension/FUNCTION_LABELS enum members → typos are compile/test failures; no standalone `.ttl` to drift), shapes built in-memory — the harness (record→RDF / shapes-builder / validate / downgrade) is DOMAIN-GENERIC; a new domain re-authors only the map data (user-confirmed). Grounded pyshacl.validate + sh:closed/sh:resultPath readback against installed source first; rdflib+pyshacl added to the framework graph (34,279 nodes). New dep pyshacl 0.40.1. 9 hermetic tests; 852 green; ruff clean. NEXT = JUDGE-ONTOLOGY-2.** |
  | JUDGE-ONTOLOGY-2 | **Layer 2, part 2 — cardinality + cross-dimension consistency SHACL.** ADR-0040. Extend `contract_bridge.shapes.ttl`: (a) **cardinality** `sh:maxCount 1` shapes generated from the ×33 `owl:FunctionalProperty` (a scalar dim asserted twice → violation); (b) **value ∈ vocab** `sh:in` shapes generated from the ×25 `owl:oneOf` (formalizes the existing in-code `CLOSED_VOCAB` check as an ontology shape so the rule lives in ONE place); (c) **cross-dimension rules** as `sh:sparql` constraints (e.g. `cap_basis=uncapped ⇒ no cap_quantum`; `temporal_bound=unbounded ⇒ no notice_period` — enumerate the real contradictory pairs from the 30 dims). Each violation → downgrade the offending assertion. Reuses JUDGE-ONTOLOGY-1's RDF-serialize + pyshacl harness. TDD per shape class. | 5 Integrate | ADR-0040 | **DONE (2026-08-03) — scope narrowed after grounding, user-approved.** Implemented **(a) cardinality**: `MULTI_VALUED_DIMENSIONS` = {carve_out, covered_subject, damage_type} (the adapter's `_LIST_ENUM_DIMS`); every other dim is scalar → `sh:maxCount 1` added to its `sh:property` in `_shapes_graph`. A scalar dim asserted with 2 conflicting values (granite hedging, e.g. cap_basis fixed_fee+multiple_of_fees) → ALL its assertions downgraded to AMBIGUOUS. Reused JUDGE-ONTOLOGY-1's harness (a maxCount violation reports the same `sh:resultPath` as a closed violation) → renamed `nonapplicable_dimensions`→`flagged_dimensions` (covers applicability + cardinality). **(b) sh:in SKIPPED — redundant:** value-in-vocab is already enforced at the Pydantic boundary (`property.PropertyAssertion._value_in_vocab_or_ambiguous`), a SHACL shape would duplicate a working validator. **(c) cross-dim sh:sparql SKIPPED — no defensible hard rule in this schema:** the dimensions are orthogonal facets (mutual-but-asymmetric is legitimate), so the only genuine intra-clause "contradiction" is same-dimension/two-values = (a); forcing weak cross-dim rules would false-flag real clauses. **DEFERRED (not dropped):** revisit cross-dim rules post-MVP / during beta-customer trials, driven by real production data + customer domain expertise (they know the domain better) — that's the right source for genuine cross-dim contradictions. The one real cross-signal rule (deontic polarity vs rule type) is JUDGE-ONTOLOGY-3. 2 new tests (11 total); 854 green; ruff clean. NEXT = JUDGE-ONTOLOGY-3 (deontic).** |
  | JUDGE-ONTOLOGY-3 | **Layer 2, part 3 — deontic consistency (ODRL).** ADR-0040. Kills the observed `assignment_consent=free`-on-a-prohibition class (error class 2 = deontic inversion). **Ontology additions:** (a) tag each deontic value with its polarity (`odrl:Permission` / `odrl:Prohibition` / `odrl:Duty`) in `contract_bridge.ttl`; (b) establish the clause's own deontic rule-type signal — DESIGN DECISION to settle in-task: derive it from the function (some FOLIO functions are inherently prohibitions), from a lexical deontic-cue detector (`shall not`→prohibition, `may`→permission, `shall`→duty), or add a `deontic_type` field to the extraction — pick the most reliable/cheapest. **Shape:** a `sh:sparql` constraint that flags a value whose polarity contradicts the clause's rule type. Depends on JUDGE-ONTOLOGY-1's harness. | 5 Integrate | ADR-0040 | **DONE (2026-08-03).** DESIGN DECISION = **function-derived** deontic stance (user-approved; deterministic, non-circular, reuses the classifier output, no fuzzy text parsing / no self-report extraction field). `PERMISSION_POLARITY_VALUES` = {assignment_consent→{free}, coc_consent→{unrestricted}} (the "may freely" endpoints, grounded in `contract_bridge.ttl` cbr:free/cbr:unrestricted + the odrl:permission/prohibition property tagging); `RESTRICTIVE_FUNCTIONS` = {Anti-Assignment, Non-Transferable License, Change Of Control}. Realized as `sh:in` (allowed = CLOSED_VOCAB[dim] − permission values) on the scoped property shape in `_shapes_graph`; a violation reports the same `sh:resultPath` → folds into `flagged_dimensions` → downgrade to AMBIGUOUS. `consent_required` on Anti-Assignment passes. KNOWN aggressiveness (accepted, tune later): free/unrestricted are applicable ONLY on restrictive functions, so EVERY occurrence is flagged — intended (on Anti-Assignment `free` = misread or misclassification; downgrade is non-destructive). Loosen if false positives / below-par cases appear in real data. sh:in grounded against installed pyshacl first. 3 new tests (14 total); 857 green; ruff clean. **Layer 2 COMPLETE (applicability + cardinality + deontic).** NEXT = JUDGE-SEMANTIC (Layer 3, narrowed LLM-judge on GRANITE) + GROUNDING-OPENVALUED (independent).** |
  | JUDGE-SEMANTIC | **Layer 3 — narrowed LLM-semantic-judge for the irreducible residual.** ADR-0040. ONLY the dims neither lexical nor symbolic can reach (mutuality, favorability, party_asymmetry, cap interpretation — pure text→value *reading*). A cheap, targeted verify-or-refute LLM call per semantic assertion that re-cites the span; refuted → AMBIGUOUS. Because Layers 1–2 already cleared the type/consistency/deontic errors this call class is small + focused; it is the ONLY place an LLM is spent on judging. Parallelize with the async+semaphore pattern ([[concurrent-llm-in-evals-and-tests]]). Runs on the same granite substrate (no OpenRouter dependency — [[product-substrate-open-model-modal-a100]]). Ingestion-side gate; NOT on queries (KG-5d: reground false-flags queries). | 5 Integrate | ADR-0040 | **DONE (2026-08-03) — built, hermetically tested, LIVE-validated on Granite.** `spans/semantic_judge.py`: `SEMANTIC_DIMENSIONS` = the 12 closed dims with no lexical cue (in CLOSED_VOCAB, not in GROUNDING_CUES — mutuality/favorability/party_asymmetry/cap_basis/law_multiplicity/ip_ownership/nonsolicit_target/renewal_mechanism/coc_consent/assignment_consent/mfn_scope/termination_right); `SemanticVerdict(supported,reason)`; `semantic_judge(record,text,judge_fn)` judges ONLY surviving (non-AMBIGUOUS) semantic assertions concurrently (`map_concurrent`), downgrades refuted→AMBIGUOUS, None-verdict=untouched (never downgrade on judge failure); `build_semantic_judge_fn(model_id, structured_factory=build_structured)` = verify-or-refute through the MODEL SEAM (model-neutral; product = Granite, factory injected for hermetic tests) with a per-dim gloss in the prompt. Registered twofold (`extraction_semantic_judge`: registry slug + ARD manifest + register fn). 6 hermetic tests; 868 green; ruff clean. **LIVE (vLLM-Granite A100, redeployed then STOPPED; NO OpenRouter): discrimination diagnostic `scripts/semantic_judge_probe.py` = 6/6 correct-supported + 4/6 wrong-refuted = 10/12 (83%); structured json_schema NON-degenerate.** Residual weakness = narrow-value-on-broad-clause (accepts unilateral on an "each party" clause) — granite reasoning gap, tune later. Full-pipeline run `scripts/semantic_judge_live_validate.py` found semantic assertions are SPARSE post-extraction (~1 surviving / 12 clauses) → inline LLM cost at ingest is LOW. **NOT wired inline into ingestion yet (deliberate):** the inline production path uses the seam which TODAY points at OpenRouter (ruled out for L3); wire it inline at MODAL-STACK-1 when the seam → vLLM-Granite. **ADR-0040 cascade COMPLETE (L1 lexical + open-valued, L2 symbolic ×3, L3 semantic).** |
  | GROUNDING-OPENVALUED | **Grounding-coverage fix (NOT ontology-semantics) — the open-valued-inference gap.** ADR-0040. The lexical `reground` SKIPS open-valued dims (no cue map), so `jurisdiction=United States` inferred with no textual basis passes as grounded (error class 3). Extend the lexical judge to open-valued dims: require the extracted value's surface token (or a normalized form) to appear in the span, else AMBIGUOUS. Distinct from the SHACL work (this is text-presence, not type/consistency) — tracked separately so the neuro-symbolic boundary stays honest. | 5 Integrate | ADR-0040 | **DONE (2026-08-03).** `spans/property_grounding.py`: `OPEN_VALUED_DIMENSIONS` = the 7 dims not in CLOSED_VOCAB (jurisdiction, cap_quantum, temporal_bound, notice_period, audit_frequency, commitment_quantum, ld_trigger); `_open_value_grounded` = TOKEN OVERLAP (grounded if ANY significant token, len≥2, split on non-alphanumeric, appears in the clause; lenient so normalized forms survive — `12_months` grounded by "months" in "twelve (12) months"); `is_grounded` branches: closed-lexical → cue check, open-valued → token overlap, closed-SEMANTIC (mutuality/favorability/party_asymmetry/cap_basis/consent regimes) → still pass (Layer-3's job, guarded by a test). Flows through `reground` (downgrade) + `needs_escalation`; ingestion-only (query untouched, KG-5d). Catches the observed `jurisdiction`-with-no-basis fabrication → AMBIGUOUS; deliberately lenient (any-token) to minimize false positives. 4 new tests (9 total); 861 green; ruff clean. **Closes the 3rd/last MODAL-STACK-2 error class.** Remaining in ADR-0040 = JUDGE-SEMANTIC (Layer 3, Granite). |
  | QUERY-SPECDEC | Query-side vLLM speedups experiment (speculative decoding etc.) to keep query latency + cost low on the A100. Separate experiment. | 5 Integrate | ADR-0039 | **todo** |
  | ENTERPRISE-CONTAINER | Application structuring + packaging (ADR-0039): the full deployable system on Modal — scale-to-zero CPU query app + ArcadeDB on a Modal Volume + the co-located A100 (MS1-5). **PLAN (2026-08-04, reviewed): EC-1..EC-5.** Decisions: KG on Modal = ALWAYS-WARM (cheap CPU, no per-query DB cold start); query↔KG via Modal INTERNAL networking, A100 via existing public HTTPS; port the CURRENT local KG (fresh backup, incl. the 1278 many-to-many PARTY_TO edges), NOT the stale GCS backup; EC-5 (customer-install + cost model) DEFERRED. | 5 Integrate | ADR-0039 | **DEPLOYABLE SYSTEM DONE (2026-08-04): EC-1..EC-4 complete — the product runs end-to-end on Modal (query app + Modal ArcadeDB KG + A100 models). Only EC-5 (enterprise packaging + cost model) deferred.** |
  | EC-1 | **Port the KG to a Modal Volume (always-warm ArcadeDB service).** Fresh `BACKUP DATABASE` of the CURRENT local `ragwright_cuad_full` (with the 1278 many-to-many PARTY_TO edges) → upload to a Modal Volume → a Modal ArcadeDB app serves it (always-warm). AC: Modal ArcadeDB serves `ragwright_cuad_full` with counts matching local (Contract 506 / Clause 42269 / Span 136292 / Entity 1174 / PartyTo 1278). Verify: count queries against the Modal DB. No GPU (needs local Docker up for the backup). | 5 Integrate | FR-S / ADR-0039 | **DONE (2026-08-04).** Fresh online `BACKUP DATABASE` of the CURRENT local `ragwright_cuad_full` (657MB, incl. the 1278 many-to-many edges) → uploaded the single zip to the `rw-arcadedb-data` Modal Volume (single-file put; the 65-file dir put kept failing on SSL/connection). `scripts/modal_arcadedb.py`: Modal app serving ArcadeDB — base `eclipse-temurin:21-jre` + `add_python` (ArcadeDB 26.7.1 needs Java 21 = class v65; the stock arcadedb image is Alpine/musl which Modal can't run on), ArcadeDB tarball downloaded at build + EXTRACTED at RUNTIME (build sandbox FS can't recreate its hardlinks), backup unzipped once into the Volume on first boot, `min_containers=1` ALWAYS-WARM, HTTP API 2480, basic-auth via rootPassword. URL `https://farhan-zaidi--rw-arcadedb-serve.modal.run`. VERIFIED counts on the Modal KG EXACTLY match local (Contract 506 / Clause 42269 / PropertyValue 9013 / Span 136292 / Entity 1174 / PartyTo 1278) + PARTY-TO-MANY-TO-MANY preserved (CIK 0001043946 → 5 contracts). **NOTE (decision-2 deviation): exposed as a PUBLIC auth-protected web endpoint (rootPassword basic-auth, same posture as the vLLM app), NOT Modal-internal-only — Modal has no trivial private-HTTP; true internal networking is a later refinement.** |
  | EC-2 | **Query-service app + endpoint (Modal, scale-to-zero, CPU).** A Modal app running the registered query subgraphs (`typed_property_retrieval` Leg B, `intra_document_qa` Leg A, `relational_qa` Leg C), seams wired to the Modal ArcadeDB (internal) + the A100 adapters (`RAG_SERVING=vllm`, `STACK_URL`); exposes a `/query` endpoint (question → route to a leg → cited answer). AC: `/query` returns a cited answer for a representative question. Verify: hit the deployed endpoint. No GPU (calls the A100 app). | 5 Integrate | FR-Q / ADR-0039 | **DONE (2026-08-04).** Store-seam adaptation: `ArcadeDBStore.from_env` gained `ARCADEDB_PROTOCOL` (default http; `https` for the Modal KG). `scripts/modal_query_app.py`: Modal ASGI app (scale-to-zero, CPU, lean image — langgraph + langchain-openai + arcadedb-python + docling-graph + pyshacl + httpx + `add_local_python_source(rag_wright)`; NO FlagEmbedding/torch/spacy — the query path uses the A100 adapters, those imports stay lazy). Env wires the KG (Modal ArcadeDB https+basic-auth) + the A100 (`RAG_SERVING=vllm` + `STACK_URL`). `/health` (KG-only) + `/query` (runs the `typed_property_retrieval` Leg B subgraph → cited spans). DEPLOYED `https://farhan-zaidi--rw-query-query.modal.run`; `/health` GREEN reaching the Modal KG (`{"kg":"ok","contracts":506}`). The full `/query` cited-answer path needs the A100 warm (EC-3 warm-up + EC-4 validate). 908 green; ruff clean. |
  | EC-3 | **Warm-pool lifecycle (automatic A100 warm-on-request).** Bake the A100 warm-up INTO the query app: on a query, if the A100 is cold, trigger it + wait for `vllm_up`, then serve (first-query warm-up tolerated, ADR-0039); `scaledown_window` tears it down after idle. Premium tiers (proactive/longer keep-warm) noted, not built. AC: a cold-start query transparently warms + answers; a follow-up within the window is fast. GPU (uses A100). | 5 Integrate | ADR-0039 | **DONE (2026-08-04).** Design: a synchronous ~4-min warm-in-request EXCEEDS Modal's web-request timeout (the request is cancelled), so `/query` uses the serverless WARM-AND-RETRY pattern: `_a100_ready()` does ONE quick `/health` check which ALSO triggers the scale-to-zero A100 to spin up; if cold → return `{status: warming, retry_after_s: 60}` FAST (never exceeds the request limit) while the A100 warms autonomously (vLLM keeps loading, scaledown_window holds it); the client retries; once `vllm_up`, `/query` answers. Verified: a cold `/query` triggered the A100 → it warmed to `vllm_up:true` on its own → the retry answered. |
  | EC-4 | **End-to-end deployed validation.** Hit the deployed `/query` endpoint with real questions (Leg A/B/C) → CITED answers, FULLY on Modal (KG on Modal Volume + A100 models; NO local anything). AC: representative questions answered with citations against the deployed system → ENTERPRISE-CONTAINER deployable-system milestone. Verify: query transcript. GPU (warm session). | 5 Integrate | FR-Q / ADR-0039 | **DONE (2026-08-04). DEPLOYABLE SYSTEM PROVEN END-TO-END on Modal (NO OpenRouter, NO local anything).** POST `/query "anti-assignment clauses that allow free assignment without consent"` on the deployed `rw-query` app → HTTP 200 (44.6s): granite constraints `assignment_consent=free` (A100 vLLM) + routed functions Anti-Assignment (A100 LegalBERT+granite) → property-boosted retrieval over the Modal KG (A100 BGE + `span_hybrid_search` + `span_properties` edge.span_id join) → rank-1 MATCH "Permitted Assignments… either Party may freely assign" above the consent-required clauses. Full path: query app (Modal CPU) → A100 models → Modal ArcadeDB KG → cited answer. **ENTERPRISE-CONTAINER deployable system (EC-1..EC-4) COMPLETE; only EC-5 (packaging + cost model) deferred.** |
  | EC-5 | **(DEFERRED follow-up) Enterprise packaging + cost model.** The "run our tested container on the customer's own A100 private cloud" deliverable (data never leaves their premises) + the cost/tier model (warm-pool economics, premium tiers). Mostly packaging + a short doc. | 5 Integrate | ADR-0039 | **deferred (after the deployable system)** |
  | KG-7-revised | Switch the Party<->Contract join from the DEAD `parties_json` name-match (empty on all contracts) to the provenance id join (`Entity.chunk_id` source_doc == `Contract.contract_id`), now a clean exact match post-HYG-1/2. Update `derive_party_contract_links` + tests + ADR-0036; finalize `scripts/link_party_clause.py`; then the live PARTY_TO write. | 5 Integrate | FR-S.1 | **DONE (2026-07-31). ADR-0036 updated.** `derive_party_contract_links` rewritten to the PROVENANCE join (Entity.chunk_id source_doc == Contract.contract_id; party_name from Entity.name; dedup (entity,contract); unmatched = coverage-gap entities). Store `all_entities` now returns chunk_id; `all_contracts` returns just contract_id. 7 tests rewritten; 829 green; ruff clean. **LIVE WRITE done**: 219 PARTY_TO edges across 96/102 contracts written to ragwright_cuad; verified end-to-end traversal Party -PARTY_TO-> Contract -> 55 clauses. Known limitation (ADR-0036): GP-1B upserts 1 Entity/party so a multi-contract party links only to its last-written extraction contract (full many-to-many needs the per-(contract,party) mention data — later). NEXT = LG-3d.** |
  | LG-3d | `contract_ingestion_pipeline` = the GENERIC ingestion pipeline (parse -> chunk -> segment -> clause+graph extraction -> entity resolution -> write clause KG + entity graph -> party_clause_linking) + a `CorpusAdapter` seam + a CUAD adapter as the REFERENCE. **DESIGN DECISION (user-confirmed 2026-07-31): NO per-corpus ingestion functions.** Adding a corpus = writing ONE small `CorpusAdapter` (documents() -> SourceDocument{canonical id, text, optional corpus metadata: CUAD parties / ACORD pre-segmentation}), NEVER re-implementing the flow (`run_ingestion(XYZAdapter())`, not `ingest_xyz()`). Test on a FEW docs, NO full re-ingest. | 5 Integrate | ARD | **after KG-7-revised.** |
  | INGEST-REFACTOR | Migrate the existing per-corpus scripts (ingest_cuad, ingest_acord, populate_clause_kg, populate_entity_graph_extracted, populate_property_store) onto the LG-3d generic pipeline + thin `CorpusAdapter`s; retire the duplicated pipeline logic. Do AFTER LG-3d proves the generic path on the CUAD adapter (don't retire working scripts first). | 5 Integrate | ARD | **PHASE-1 DONE (2026-07-31): wired + proven the generic pipeline end-to-end on real CUAD (CuadAdapter, scratch db, non-destructive). `production_document_ingest` + `run_cuad_ingestion` in `subgraphs/contract_ingestion_pipeline.py` compose the real capabilities: text->_parsed_from_text->semantic_chunking->segment->LegalBERT function-classify->granite clause extraction (docling-graph) [concurrent map_concurrent + per-item skip-tolerance + span-extraction CACHE keyed by clause_id+template-schema-version so a template change auto-invalidates] || GP-1B graph_extraction [concurrent] -> entity_resolution -> write_clause_kg + write_graph -> party_clause_linking. Earlier completed run: 230 clauses / 8 entities / 22 rels / 8 PARTY_TO, 0 dead-lettered. FIXED 3 real defects in my glue vs the tested path: sequential->concurrent extraction; missing per-item tolerance; AND a CLAUSE-TEMPLATE runaway (the `document_reference` graph-id -- a field WE DISCARD -- was a required free-text sink where granite dumped 400-char verbatim quotes, ballooning JSON past max_tokens and truncating). Fix (a) max_length + brevity on all free-text template fields (backstop; granite ignores maxLength at generation), (b) made `document_reference` Optional/null-permitted -> validated 0 truncations on a 14-span sample, 5 vs 18 in-run, clause yield 101 vs 99 (recovered previously-lost clauses). Reverted the false-lead max_tokens 4000->2000. `scripts/ingest_smoke.py` runner. 834 green. **PHASE 2a DONE (2026-07-31): wired the dense/sparse Span RETRIEVAL INDEX (the real gap).** RESTRUCTURED the graph: pulled segmentation+LegalBERT-classification into a SHARED `segment` node feeding three parallel branches -- `extract_clauses` | `index_spans` (BGE-M3 embed -> `to_span_record` -> `upsert_span`) | `extract_graph`. `index_spans` is BEST-EFFORT (a failed index degrades to 0, never dead-letters the doc's KG). `build_document_ingest` now takes 7 seams; `production_document_ingest` adds `segment_fn`/`index_fn` + a BGE-M3 embedder; `clauses_fn` consumes segments. Smoke on 2 CUAD docs (scratch db): 236 clauses / 8 entities / 22 rels / 8 PARTY_TO / **660 Span index records**; `hybrid_search('...liability...')` -> 3 hits (top function=Uncapped Liability) -- retrieval PROVEN over the new index. 5 hermetic tests (incl. index-best-effort); 835 green. **PHASE 2a-followup / (a) CACHE-ADOPTION + graph_fn fidelity DONE (2026-07-31), the CUAD-FULL-COVERAGE cost-down.** Grounding the caches revealed the pipeline's `graph_fn` ran GP-1B PER-CHUNK (the extractor bounds to an 8000-char preamble, so non-header chunks extract from party-less text) -- both a fidelity mismatch vs the proven per-CONTRACT GP-1B (the design chosen for ingest) AND the single largest LLM cost (~10x a per-contract call). FIX: `graph_fn` now runs GP-1B ONCE PER CONTRACT (`per_contract_graph_extraction`), reusing party names cached per doc. Two clean cache seams adopted (idempotent, testable, module-level): `seed_party_cache` pre-populates the per-contract party cache from GP-1B's `dg_extracted_parties.json` (canonical-id keyed, HYG-1) -- reuses ~482/510 extractions; `seed_chunk_cache` copies prior content-hash-keyed `chunk()` manifests (102 CUAD, verified all-match) so re-chunking is skipped where text is unchanged. `parties_to_extraction(cid, cached_names)` is byte-identical to a fresh extraction (the live extractor's own body), so the cache is lossless. `run_cuad_ingestion` wires both (CUAD-specific seed paths); `production_document_ingest` gained `party_seed_path`. Smoke PROVED it: seeded 510 party files + copied 101 chunk manifests, party reuse 2/2 (no granite party call), per-contract graph = 2 clean signatories/contract + CONTRACTS_WITH (vs the per-chunk 8 entities/22 rels noise), end-to-end 236 clauses/4 PARTY_TO/660 spans/hybrid_search 3 hits. 6 new hermetic tests; 841 green; ruff clean. => a full CUAD run now pays only the UNAVOIDABLE clause-property-extraction pass (template changed); chunking + party extraction are reused. NEXT = (b) run CUAD-FULL-COVERAGE.
  **PHASE 2b (script migration) = RE-ARCHITECTURE, NOT a rewrite; BLOCKED on CUAD-FULL-COVERAGE (2026-07-31 finding).** The 5 scripts are NOT clean pipeline re-implementations -- each has a distinct purpose/write-path the generic pipeline does not cover 1:1, so retiring them is unsafe before the generic path is proven at full scale (own rule): (1) `ingest_cuad` = span index + contract nodes over the SEED=0 20% CLASSIFIER-EVAL HOLDOUT (holdout purpose != corpus ingest; relocate it); (2) `ingest_acord` = ACORD PRE-SEGMENTED clauses via ChunkWriter+sidecar -- the pipeline chunks+segments, so ACORD needs a pre-segmented mode the pipeline LACKS; (3) `populate_clause_kg` = CACHE-based clause KG (the canonical populator, re-run by KG-TRUNCATION-BACKFILL); (4) `populate_entity_graph_extracted` = GP-1B per-CONTRACT + cache (pipeline does per-CHUNK); (5) `populate_property_store` = ACORD + LEGACY write_property_graph (retired write path). SEQUENCE: do 2b per-script AFTER CUAD-FULL-COVERAGE validates the generic pipeline at scale; add ACORD pre-segmentation support to the pipeline first. Also adopt the extraction-cache pattern into the tested path. Follow-ups INGEST-GRAPH-LATENCY + KG-TRUNCATION-BACKFILL both DONE.** |
  | INGEST-GRAPH-LATENCY | Follow-up (found INGEST-REFACTOR): `extract_parties` (GP-1B graph_extraction, docling-graph) had SLOW/hanging calls in the smoke -- one 309s, three >60s -- stalling a document while clause extraction was fine. Separate from the clause fix. Diagnose: transient OpenRouter/granite latency vs a `ContractParties`-template runaway (same class as document_reference); add a per-call timeout + the concurrency the tested GP-1B path used. | 5 Integrate | FR-C.6 | **DONE (2026-07-31). ROOT CAUSE: it was LATENCY, not a runaway -- the 309s call produced tiny output (4 nodes/3 edges) but docling-graph's per-call timeout defaults to 300s (`ReliabilityDefaults.timeout_s`) and we never overrode it, so a stuck `extract_parties` call blocked a document ~5 min. FIX: `build_pipeline_config` now sets `LlmRuntimeOverrides.reliability = ReliabilityOverrides(timeout_s=90, max_retries=1)` (was defaulting to 300/2), threaded through `extract_parties`/`extract_clause`; a hang now fails at ~90s (worst case ~180s) and the caller's per-item tolerance skips it. Concurrency already matched the tested GP-1B path (map_concurrent=8). Regression test in test_dg_model_seam. NOTE: `ContractParties` DOES have free-text `title`/`name` (runaway POTENTIAL like document_reference) but it was not the cause here (small output); if it ever surfaces, apply the same max_length fix. 834 green.** |
  | KG-TRUNCATION-BACKFILL | Follow-up (found INGEST-REFACTOR): the EXISTING CUAD/ACORD clause KGs were populated with the OLD unconstrained clause template at max_tokens=2000, so clauses whose extraction truncated (the document_reference runaway) were SILENTLY dropped -- real gaps in the populated KG. Once the template fix is adopted for the tested path, re-extract to backfill the lost clauses (the span-extraction cache makes this cheap: only the previously-failed spans re-run). Decide timing with the whole-corpus re-ingest (CUAD-FULL-COVERAGE). | 5 Integrate | FR-S | **DONE (2026-07-31; live-data op, no repo code -- the fix was ADR-0037's `document_reference` optional in `clause_template.py`). MEASURED the actual gap (far smaller than the smoke's ~17%): CUAD cache 7097 vs KG 7026 = 71 dropped (~1%); ACORD 3887 vs 3887 = 0. Backfilled CUAD by RE-RUNNING `populate_clause_kg` (resumable -> extracted only the 71 missing, monitored X/N, non-destructive/content-hash-gated): recovered 70/71 (KG 7026 -> 7096 verified live, +209 assertions); 1 persistent failure (not truncation -- unrecoverable, honest gap). ACORD: no action. Retroactively validated the document_reference fix on the real KG.** |
  | SKILL-corpus-ingest | Author the `corpus-ingest-and-connect` Skill: the repeatable recipe for ingesting ANY new contract corpus and auto-connecting it into the one KG — canonical identity backbone + the `CorpusAdapter` seam + the parse->...->link flow. The side-benefit recipe (user-flagged). **DONE (2026-07-31): `src/rag_wright/skills/corpus_ingest/SKILL.md`** — the method skill (okf_navigate style): one generic pipeline + one thin `CorpusAdapter`; the 3 steps (adapter with canonical-id + parse; registry; monitored `run_corpus_ingestion`); the load-bearing rules (never `ingest_xyz()`, canonical `source_doc_id`, concurrent+tolerant+cached extraction, monitor X/N, template-is-code ADR-0037); new-domain path; and what it defers to the pipeline/capabilities. Formalizes `docs/corpus_ingest_recipe.md`. Authored content — no ARD reg / test needed (like okf_navigate). 834 green. | 5 Integrate | ARD | **after LG-3d.** |
  | MODEL-AB | Query-side model A/B (future-reference note): run the SAME two granite calls (constraints + function) with DeepSeek V4 Pro and Kimi-k3, compare on the adopted pipeline | 5 Integrate | FR-Q | **DONE (2026-07-29). `docs/eval/query_side_model_ab.md`. On adopted llm_union v4 (r@10/@20/nDCG@10): granite 0.490/0.713/0.553 · deepseek-v4-pro 0.512/0.735/0.551 · kimi-k3 0.514/0.731/0.553 — frontier models buy only +0.02 recall and ~0 nDCG (dead heat). Function routing alone ~equal (r@20 0.620/0.628/0.652). Granite extracts MORE constraints (avg 1.74 vs 1.19/1.26 — frontier terser, not more thorough). Structured-output: granite+kimi need json_schema (deepseek ok on function_calling); kimi docling-graph extraction UNRELIABLE ("no models" on some queries -> eval now guards per-query extraction failures; kimi profile added). VERDICT: keep granite (open, self-hostable, more thorough, reliable); marginal gain doesn't justify a frontier query LLM. Also: QCACHE now model-keyed so a model A/B re-extracts per model.** |
  | C-6 | Compliance module (roadmap §13) DE-RISKING: the judgment node on ContractNLI — document-grounded 3-way NLI (entailment/contradiction/neutral = compliant/violation/not-addressed core) on labeled data BEFORE building domain (ad-claims) gold. `eval/contractnli_judge.py` + balanced 150-pair slice `data/eval/contractnli_slice.jsonl` (presencesw/contractnli mirror; 50/50/50). Structured LLM judge via the model-profile seam; reports accuracy + per-class recall + confusion (the GATE-bar signal). | 5 Integrate | roadmap §13 | **FLASH MEASURED (2026-07-30, awaiting approval). deepseek-v4-flash on balanced 50/50/50: accuracy 0.780; recall entailment 0.760 / contradiction 0.800 (the "violation" analog — strongest) / neutral 0.780; NO class collapse. Fair/hard test (balanced, though true dist ~10% contradiction). Error direction that matters for compliance: contradiction->entailment 8/50 (16% violations read as supported = false-negative direction) + neutral<->entailment leakage (over-claiming coverage) — exactly what the Flash->Pro escalation + conservative default + human gate are designed to catch. => judgment core DE-RISKED cheaply. NEXT: Pro-ceiling comparison on the same slice to quantify escalation lift + set the GATE bar. Built: eval/contractnli_judge.py + tests/eval/test_contractnli_judge.py (2 passed); slice gitignored (regenerable).** |
  |  |  **COMPLIANCE SUBGRAPHS — RUNG 1 (the ad-compliance engine; plan.md §7). DRAFT ladder, awaiting review.** Concept: two-sided retrieval + entailment (subject doc -> claims; regulatory corpus -> requirements; judge each pair -> compliant/violation/needs-review, both-sided cited). ~90% reuse; the judgment node is the one new capability (C-6 de-risked it). Judge + all LLM on self-hosted **Granite/Modal-A100** via the seam (`RAG_SERVING=vllm`, ADR-0039); ingestion substrate-agnostic (local or co-located on the A100). Two-halves: build the capabilities + 2 hardened subgraphs here; MCP tools + the `check_compliance` GraphWright workflow (C-8) are the compiler half. Rung 2 (ad-claims NAD/FTC gold, C-7) and rung 3 (privacy, DPV/GDPRtEXT) follow. |  |  |  |
  | CC-0 | **Data: acquire the FTC Endorsement Guides (16 CFR 255) rulebook + a small subject-doc sample.** Pull the public FTC guide text (eCFR / FTC.gov) into a committed corpus dir (`data/compliance/ftc_16cfr255/`, gitignored if large) + assemble a handful of ad-claim subject docs for smoke tests. AC: the FTC guide text is on disk in a parseable form + >=3 subject-doc samples. Verify: file listing + a parse smoke. | 5 Integrate | roadmap §13 C-2 | **DONE (2026-08-04).** Two committed acquisition scripts (data/ stays gitignored per convention — scripts are the source of truth, like CUAD): `scripts/acquire_ftc_255.py` fetches 16 CFR Part 255 from the public eCFR API → 7 sections (255.0-255.6), 72.5K chars, clean `16cfr255.{xml,sections.json,txt}`; `scripts/make_compliance_fixtures.py` writes 4 synthetic ad subject samples + `manifest.json` (fictional brands `*.example`; each targets specific 255 rules with a KNOWN expected verdict — 3 violation + 1 compliant, doubles as the CC-7 smoke gold). Parse smoke PASS: both flow through `_parsed_from_text`→DoclingDocument (FTC §255.5→26 items, influencer ad→6 items). Both ruff-clean; 910 unaffected. |
  | CC-1 | **Ontology + contracts: author `compliance_bridge.ttl` + `Requirement`/`Claim` Pydantic contracts.** Small authored sibling to `contract_bridge.ttl` (do NOT enhance the contract one): reuse the public deontic backbone (ODRL/LKIF/LegalRuleML) + PROV + the `Constraint[]` typed-dimension pattern; author the thin advertising vocab (closed `claim_type` / `deontic_type` / `actor` enums, roadmap §13.1). `Requirement` (requirement_id=`<reg>:<section>:<hash>`, deontic_type, actor, applicability_scope: Constraint[], requirement_text, evidence_standard?, severity?) + `Claim` (claim_id, source_doc, span, claim_type, assertion_text, disclosures_present[], evidence_referenced, medium?) contracts in `src/contracts/`, doubling as the registered capability contracts. AC: contracts validate + round-trip; ttl parses (rdflib); ids follow the clause_id scheme. TDD (contract-first). | 3 Contracts | roadmap §13 C-1 | **DONE (2026-08-05).** `src/rag_wright/contracts/compliance.py`: `Requirement` (requirement_id=`<source_reg>:<section>:<hash16>` via `make_id`, deterministic/RAC-1; deontic_type, actor, applicability_scope: list[Constraint], requirement_text, citation always-present [FR-Q.6], confidence default EXTRACTED [FR-S.4], evidence_standard?/trigger_condition?/severity?) + `Claim` (claim_id, source_doc, claim_type, assertion_text, span offsets doc_start/doc_end ordered-validated, disclosures_present, evidence_referenced, medium, confidence) + `Constraint` (frozen (dimension,value) + `.as_tuple()` — SAME shape as the retrieval router's tuple so CC-6 reuses Leg-B) + closed enums `DeonticType`/`ClaimType`(8)/`Severity`. `src/rag_wright/ontology/compliance_bridge.ttl`: small authored SIBLING (not a contract_bridge edit) — deontic values map to public odrl:/lkif: terms, prov: provenance, odrl:Constraint applicability pattern, authored ClaimType closed vocab, Verdict vocab (for CC-4); parses under rdflib. 13 hermetic tests; 923 green; ruff clean. |
  | CC-2 | **Capability `requirement_extraction`: regulatory text -> `Requirement[]`.** New docling-graph template via the kg-extraction-recipe (the file-path / `structured_output=False` / max_tokens gotchas from GP-1B), through the model seam (Granite). Extracts deontic_type + actor + applicability_scope + citation from the FTC guide text. AC: extracts the FTC 255 rules with cited sections; hermetic test with a stub extractor; registered twofold. Verify: pytest + a live extract smoke on CC-0 text (Granite/A100). | 5 Integrate | roadmap §13 C-1/C-2 | **DONE (2026-08-05).** `src/rag_wright/capabilities/requirement_extraction.py`: docling-graph template (`ExtractedRegulationSection`→`ExtractedRequirement[]`) reusing the `extract_parties` seam + `to_requirements` adapter (deontic→closed vocab, claim_types→Constraint applicability, citation=§section, id=content-hash; off-vocab deontic→AMBIGUOUS, off-vocab claim_type dropped, blank text skipped) + `register_requirement_extraction`. Slug added to CANONICAL_CAPABILITY_SLUGS + ARD `CapabilityManifest` (twofold). **KEY FIX (found in the live smoke, [[docling-graph-extraction-contract]]):** the shared `dg_extraction` seam hardcoded `extraction_contract="direct"` (tuned for CONTRACTS — parties in the 8k preamble). For long REGULATION sections `direct` SILENTLY self-rations (measured §255.5: **6 rules** direct vs **31** dense — lost every worked Example, the concrete checkable rules). Threaded `extraction_contract` through `build_pipeline_config`/`extract_parties` (default stays "direct" → contract extraction unchanged); `requirement_extraction` defaults to **"auto"** (auto-picks dense on long sections; dense = skeleton-then-fill + auto-retry-truncation-by-split). 8 hermetic tests + full-suite 932 green; ruff clean. LIVE (OpenRouter granite, A100 stopped): §255.5 → **31 requirements** (30 obligation/1 prohibition, all cited § 255.5). |
  | CC-3 | **Capability `claim_extraction`: subject doc -> `Claim[]`.** New extraction template (closed `claim_type` vocab) through the seam; each claim carries its subject span (provenance). AC: extracts checkable claims from a subject-doc sample with spans; hermetic test; registered twofold. Verify: pytest + a live smoke on a CC-0 subject doc. | 5 Integrate | roadmap §13 C-3 | **DONE (2026-08-05).** `src/rag_wright/capabilities/claim_extraction.py`: docling-graph template (`ExtractedAd`→`ExtractedClaim[]`) reusing the `extract_parties` seam (on "direct" — ads are short) + `to_claims` adapter (claim_type→closed vocab; off-vocab KEPT-but-AMBIGUOUS via fallback EFFICACY since a checkable assertion is never dropped; blank skipped; disclosures_present/evidence_referenced/medium carried; span provenance source_doc+text; content-hash claim_id) + `register_claim_extraction`. Slug + ARD manifest (twofold). 8 hermetic tests; full-suite 941 green; ruff clean. LIVE (OpenRouter granite, all 4 CC-0 ad samples): compliant_disclosed_ad → disclosures `#ad,paid partnership,results vary` + evidence=True (compliant signal); influencer_skincare → "clinically proven to erase wrinkles in 7 days" efficacy with disc=NONE (the §255.5 violation signal); supplement → guarantee "reduce your risk of a heart attack" + health/efficacy; weightloss → "30 pounds in one month without diet" efficacy. The discriminating fields (disclosures/evidence/claim_type) populate meaningfully — the compliant-vs-violator contrast is exactly what CC-4 judges. Minor claim_type judgment calls debatable (efficacy vs health) — CC-7 eval tuning, not a defect. |
  | CC-4 | **Capability `compliance_judgment` (THE new node): `(claim, requirement)` -> verdict.** Extends the grounding judge (`spans/semantic_judge.py`, ADR-0028/0040): structured output via the seam = `verdict in {compliant, violation, needs-review}` + rationale + `citation_claim` (subject span) + `citation_requirement` (reg clause) + confidence. Granite-default with a configurable escalation tier (the Flash->Pro cascade generalized); deterministic lexical checks first where a rule is lexically anchored; **conservative default = needs-review** under uncertainty; every `violation` flagged for human confirmation. Both-sided citation is the trust product. Parallelize per-pair (async+semaphore). AC: correct verdicts on a hand-built `(claim, requirement)` fixture set incl. a clear violation + a clear compliant + an ambiguous->needs-review; hermetic (stub judge); registered twofold. Verify: pytest + a live Granite smoke. | 5 Integrate | roadmap §13 C-5 | **DONE (2026-08-05).** `src/rag_wright/capabilities/compliance_judgment.py`: the judgment node — `JudgeVerdict` (raw LLM output) → `ComplianceFinding` (verdict {compliant/violation/needs_review} + rationale + BOTH-SIDED citation FROM THE INPUTS + confidence). Conservative mapping (None/off-vocab/uncertain → needs_review, never a silent compliant/violation); `_to_verdict` normalizes; `judge_pairs` concurrent (map_concurrent); `build_compliance_judge_fn` via the Granite seam (both sides + disclosure/evidence signals in the prompt). New `Verdict` enum + `ComplianceFinding` contract (confidence∈[0,1]; `needs_human_review` property = violation|needs_review) in `contracts/compliance.py`. Slug + ARD manifest (twofold). 8 hermetic tests; 950 green; ruff clean. LIVE (OpenRouter granite): influencer(undisclosed) vs §255.5 → all 3 claims VIOLATION conf 0.95 [human-gated], correct rationales; compliant_disclosed_ad → COMPLIANT conf 0.95-0.98 citing "#ad/paid partnership". Correct discrimination. **KNOWN granularity residual (→ CC-6 fix): disclosures are AD-LEVEL but claims are per-span, so one compliant-ad fragment with disc=none over-flagged VIOLATION; the conservative human-gate prevents shipping a false accusation. CC-6 will aggregate ad-level disclosure context into the judge.** |
  | CC-5 | **Subgraph `compliance_ingestion`: regulatory corpus -> Requirement KG.** Reuse the generic `contract_ingestion_pipeline` via a new `RegulationAdapter` (regulatory text -> `SourceDocument`s) + the `Requirement` template; hardened on `scaffold.py` (retry -> dead-letter, best-effort index, business_span). AC: ingests FTC 16 CFR 255 into a Requirement KG in ArcadeDB with cited requirement nodes; hermetic test (stub stages) + a live ingest smoke; registered twofold (kind=subgraph). Verify: pytest + node counts on the ingested KG. | 5 Integrate | roadmap §13 C-2 | **DONE (2026-08-05).** SEPARATE DB `ragwright_compliance` (contract KG untouched; user-approved). Store (additive): `Requirement` vertex type + `ensure_compliance_schema` + `write_requirements` (idempotent upsert by requirement_id, applicability stored JSON) + `all_requirements`. `subgraphs/compliance_ingestion.py`: `RegulationAdapter` (sections.json → per-§ SourceDocument w/ section+source metadata) + `build_compliance_ingest` (hardened per-section graph on scaffold: extract[retry]→write[retry], dead-letter per section) + `run_compliance_ingestion` reusing the generic `run_corpus_ingestion` driver (X/N progress, is_done resume) + register (kind=subgraph, twofold). Also generalized run_corpus_ingestion's OK progress line to be corpus-generic (`requirements=N`). `scripts/ingest_ftc_compliance.py` committed driver. 6 hermetic tests; 957 green; ruff clean. **LIVE (OpenRouter granite, RESET into ragwright_compliance): 7 sections, 0 dead-lettered → 155 Requirement nodes** (255.0→61, .1→21, .2→31, .3→6, .4→3, .5→32, .6→1); deontic 133 obligation/19 prohibition/3 permission; 101/155 with claim_type scope. Independently re-verified via a fresh connection. **CC-7 tuning notes (recorded [[ontology-lever-vs-extraction-lever]]): (1) 255.0 definitions over-generated (61) — prompt/corpus lever; (2) 54/155 empty applicability scope — ontology lever = a section→claim_type map, BUILT INTO CC-6.** |
  | CC-6 | **Subgraph `compliance_check` (the headline): subject doc + standard -> cited findings + gap matrix.** Hardened composite on `scaffold.py`: `extract_claims` (CC-3) -> `retrieve_applicable_requirements` (REUSE `typed_property_retrieval` + `llm_union` routing: claim scope <-> `applicability_scope`) -> `judge` per `(claim, requirement)` pair (CC-4, concurrent) -> `assemble` (findings[] + requirement×coverage gap matrix, both-sided cited). Query-side posture: degrade-to-empty on exhausted retries, never fabricate; conservative default. Seams DI'd for hermetic tests; production wires the real capabilities over the store + A100 encoders. **DESIGN REQUIREMENT (from CC-4 finding): aggregate the AD-LEVEL disclosure set (union of every claim's disclosures_present) and pass it into each judge call as ad-level context, so a per-span fragment with disc=none isn't over-flagged when the ad as a whole discloses (fixes the CC-4 granularity residual).** AC: a subject doc vs the FTC KG yields cited findings with verdicts + a gap matrix; hermetic tests (happy path + each seam degrades + ad-level disclosure context) + a live A100 smoke; registered twofold (kind=subgraph). Verify: pytest + a live `compliance_check` transcript. | 5 Integrate | roadmap §13 | **DONE (2026-08-05).** `subgraphs/compliance_check.py`: hardened query-side subgraph extract_claims→retrieve_applicable→judge(concurrent)→assemble (cited findings + per-requirement gap matrix + verdict summary), degrade-to-empty per node; `ComplianceReport` contract; registered kind=subgraph (twofold). **Both design requirements built in + LIVE-validated: (1) section→claim_type applicability map** — the smoke revealed the FTC guides apply by CONTEXT not claim_type, so applicability is section-based with extracted scope only BROADENING (never wrongly excluding granite's noisy per-rule scope); operative sections broad, 255.0 definitions excluded. **(2) ad-level disclosure aggregation** (union of all claims' disclosures → each judge call) — FIXED the CC-4 residual: the "BrightSmile available at" fragment went VIOLATION(CC-4)→COMPLIANT(CC-6). 8 hermetic tests; 966 green; ruff clean. LIVE (OpenRouter granite, ragwright_compliance KG, capped to 3 §255.5 rules): influencer→violations+needs_review (all human-gated), compliant ad→mostly compliant. **CC-7 findings (real, recorded [[ontology-lever-vs-extraction-lever]]): (a) broad applicability × 155 reqs = cross-product + redundant near-identical rules → mixed-verdict noise ⇒ NEEDS SEMANTIC NARROWING (Leg-B top-k per claim + requirement dedup), the main CC-7 item; (b) claim_type applicability near-no-op for this context-based corpus.** |
  | CC-7 | **Eval gate (Track-1): complete C-6 + smoke the engine.** Run the Pro-ceiling comparison on the ContractNLI slice to quantify escalation lift + SET THE GATE BAR (verdict accuracy + citation correctness; precision/recall reported separately; conservative-default calibrated) — closes C-6. Then a smoke `compliance_check` run on the FTC KG (CC-5) + a subject doc (CC-0) end-to-end on the A100. AC: GATE bar set with numbers; end-to-end engine smoke produces cited findings. Verify: eval report + transcript. Rung-2 ad-claims gold (C-7) deferred. | 5 Integrate | roadmap §13 C-6 | **DONE (2026-08-05). GATE CLEARED. `docs/eval/compliance_gate_cc7.md`.** Part A (ContractNLI 150-slice, full, no shortcut): PRODUCT judge **Granite-4.1-8b acc 0.773** (≈ C-6 Flash 0.780; ceiling DeepSeek-Pro 0.793 → only +0.02 headroom); contradiction(violation) recall 0.800; **contradiction→entailment (liability FN) 3/50=6% — SAFEST of the three** (Flash 16%, Pro 10%); no class collapse. ⇒ open product model is judgment-class competitive AND safest on the liability direction; **Granite→Pro escalation NOT enabled by default** (buys +0.02, worse on safety) — conservative-default + human-gate is the safety lever. Part B (end-to-end `compliance_check` on the 4 labeled CC-0 ads, `scripts/compliance_engine_smoke.py`): **ad-level 4/4** — 3 violators flagged, compliant ad passed (0 violation, ad-level disclosure agg working). GATE bar SET (acc≥0.75, violation-recall≥0.80, liability-FN≤10%, no collapse, engine flags-all-violators/passes-compliant, conservative default). 966 green. **RUNG 1 (ad-compliance engine) COMPLETE — CC-0..CC-7 all done.** Next: CC-6 semantic narrowing (Leg-B top-k, the redundant-rule noise) + rung-2 real NAD/FTC ad gold (C-7). |
  | CC-8a | **Ontology enrichment: a content-rule vs context-rule tag on each Requirement (RuleScope).** The narrowing routing lever ([[ontology-lever-vs-extraction-lever]]): CONTENT rules (substantiation/claim-specific, e.g. §255.1 "objective claims need evidence") narrow by semantic similarity; CONTEXT rules (§255.5 disclosure — apply to ANY claim in an endorsement regardless of content) must be always-included, not left to similarity. `RuleScope` enum (content/context) in `contracts/compliance.py` + `cmp:RuleScope` in `compliance_bridge.ttl` + an authored `SECTION_RULE_SCOPE` map + `rule_scope_of(requirement)` (authored-in-code, no ttl drift; the JUDGE-ONTOLOGY-1 pattern). TDD. | 5 Integrate | roadmap §13 | **DONE (2026-08-05).** `RuleScope` enum (content/context) in `contracts/compliance.py` + `cmp:RuleScope` in `compliance_bridge.ttl` + authored `SECTION_RULE_SCOPE` (§255.5/§255.4 = CONTEXT) + `rule_scope_of`. Live KG: 35 CONTEXT / 120 CONTENT. Tests in test_compliance_check.py. |
  | CC-8b | **CC-6 semantic narrowing (the main post-rung-1 engineering item).** Replace broad applicability (≈4 claims × ≈94 rules = ~376 judge calls/ad + redundant near-identical rules → the needs_review noise) with: per claim, semantic top-k of the CONTENT rules (BGE cosine, claim↔requirement) ∪ ALWAYS-INCLUDE the CONTEXT rules (CC-8a) + dedup near-identical rules. NOT a recall lever (broad recall≈100%; narrowing is a PRECISION+cost win, must PRESERVE recall via k + always-include-context + dedup; real recall = rung-2 gold). `build_select_fn(embedder, requirements, k, dedup)` precomputes requirement embeddings once; `build_compliance_check(..., select_fn=)` seam (default = current all-applicable). Reuse BGEM3Embedder/query_embedder + `_cosine`. TDD (fake embedder) + live smoke: pairs/ad drop sharply, discrimination preserved (4/4 ad-level). | 5 Integrate | roadmap §13 | **DONE (2026-08-05).** `build_select_fn(embedder, requirements, k=5, context_k=3, dedup)` precomputes requirement BGE vectors once; per claim returns top-context_k CONTEXT (disclosure, always-kept = recall guarantee) + top-k CONTENT (cosine-ranked) + dedup, both CAPPED so an over-extracted section (§255.5→32) collapses to representatives. `select_fn` seam on `build_compliance_check` (default = broad); `embedder`/`k` on `production_compliance_check`. 12 hermetic tests (rule_scope, context always-in, top-k content, drop-irrelevant, dedup, subgraph-routes-through-select_fn); 970 green; ruff clean. **PROVEN on real KG (selector, no LLM): every claim broad=94 → narrowed=8 (3 ctx + 5 content), §255.5 disclosure retained.** Full live 4/4-with-narrowing BLOCKED by a pre-existing LLM-client hang (NOT narrowing): faulthandler stack dump = one OpenRouter ChatOpenAI call stalled in `ssl.recv` reading the response body, the map_concurrent batch parked on it; the seam's 120s timeout didn't abort it (httpx per-read semantics). Disproved my earlier resource-contention/BGE/docling guesses via isolation tests. → see LLM-CALL-TIMEOUT. |
  | LLM-CALL-TIMEOUT | **Bound every LLM call with a HARD wall-clock timeout + retry so a stalled provider response fails fast instead of hanging the whole concurrent batch (found in CC-8b).** EVIDENCE (faulthandler thread dump of the real hang): a ChatOpenAI→OpenRouter structured call blocked in `ssl.recv` reading the response body; the seam's `timeout=120s` did NOT abort it (httpx read-timeout is per-read-operation, so a trickling/half-open connection slips past). Not resource contention (isolation tests: BGE alone / BGE+docling 6s / BGE+concurrent-stub 0s all fine). FIX: a hard per-call wall-clock bound — a watchdog/timeout in `util/concurrent.map_concurrent` (per-task deadline → treat as failure) AND/OR tighten the seam's httpx timeout semantics; on breach, fail the task (the judge already maps a failed/None ruling → conservative needs_review). Hardens EVERY concurrent LLM loop (evals, judging, extraction). TDD (a stub that sleeps-past-deadline → bounded, not hung). **AFTER the fix, before re-running any eval: first run the CC-8 full live end-to-end (a couple of ads) and confirm it completes without hanging (user directive).** | 5 Integrate | ADR-0039 / infra | **DONE (2026-08-05).** `util/concurrent.map_concurrent` gained `timeout_s`/`timeout_retries`: the timeout path runs `fn` in a DAEMON thread coordinated via a future + `asyncio.wait_for` (KEY SUBTLETY found via a failing test: a plain `to_thread`+`wait_for` still blocks because `asyncio.run`'s cleanup JOINS the default-executor threads — a leaked stalled thread re-blocks the return up to the 300s join timeout; a daemon thread is never joined, so the batch returns at the deadline and interpreter exit isn't blocked). On breach: retry `timeout_retries`× (a fresh connection often clears a stall), else `None`. `judge_pairs` passes `timeout_s=RAG_JUDGE_TIMEOUT_S` (default 90) and maps a `None` (timed-out) pair → a conservative needs_review finding. 3 new tests (timeout bounds a stalled call <deadline; default unchanged; judge timeout→needs_review); 973 green; ruff clean. **LIVE-VERIFIED (user directive, before trusting evals): the exact CC-8 full run that HUNG indefinitely now COMPLETES** (4 ads, narrowed 32/32/24/24 pairs, no hang). Fixes the CC-8b LLM-stall hang + hardens EVERY concurrent LLM loop (evals/judging/extraction). **NEW PRECISION FINDING (rung-2 judge tuning, NOT this fix): full-155-KG run scored 3/4 — the compliant ad false-flagged VIOLATION (7×) because the judge over-reads a substantiated-but-company-sponsored "study… individual results vary" claim as inadequate substantiation; CC-6/CC-7's capped disclosure-only test hid this. Caught by the conservative human-gate; real ad-claims gold (C-7) measures + tunes it.** |
  | RG-1..RG-4 | **Rung 2 (roadmap C-7): first-pass ad-claims GOLD from public FTC decisions + precision/recall.** RG-1 acquire (WebSearch over FTC.gov — real cases: TruHeight/Amare/Teami/Roca/NextMed substantiation, aspartame-influencer §255.5, Rytr/TruHeight §255.2 fake reviews; FTC.gov 403s WebFetch so built from cited search summaries). RG-2 gold = `scripts/make_compliance_gold.py` → 12 cases (8 violation / 4 compliant; 9 real FTC + 3 constructed-compliant), FIRST-PASS/LLM-assisted/EXPERT-REVIEW-PENDING, violation-skewed (FTC acts on violations). RG-3/RG-4 = `scripts/eval_compliance_gold.py` scores `compliance_check` (full 155-KG, narrowing, hang-safe). | 5 Integrate | roadmap §13 C-7 | **DONE (2026-08-05, first increment). `docs/eval/compliance_rung2_cc7.md`.** **RECALL = 1.00 on real FTC cases (8/8, ZERO missed violations — the safety-critical metric is perfect); precision 0.89 real / 0.73 all / 0.00 constructed.** 3 false positives = the substantiation over-flag (judge over-reads subjective/puffery/disclosed claims as unsubstantiated: "calming tea flavor"→12 viol, a #ad-disclosed opinion→6, the weak no-action case→4); conservative human-gate catches all (nothing false ships). **The eval ran clean end-to-end — no hang (validates LLM-CALL-TIMEOUT on a real eval).** CAVEATS: precision on a TINY negative class (4 compliant, 3 constructed) → directional/noisy, needs a larger REAL negative set (NAD substantiated decisions, subscription-gated); gold is first-pass, expert-review pending. **NEXT rung-2 = judge PRECISION tuning (distinguish objective claims [substantiate] from subjective/puffery/disclosed [don't]) via prompt / claim-type gate / escalation, measured vs a larger negative gold.** |
  | RG-5 | **Judge precision tuning (the substantiation over-flag fix).** Refined the `compliance_judgment` prompt (granite-4.1-8b via the seam), eval-driven ×2. v1 (exempt subjective/puffery/disclosed/substantiated) fixed the synthetic FPs but OVER-CORRECTED — lost recall (Amare "clinically proven" → compliant, reading proof-language as substantiation). **v2 ADOPTED:** adds the distinction that proof-adjectives ('clinically proven'/'science backed') WITHOUT a cited study ARE the unsubstantiated claim → violation, while genuine subjective/puffery/disclosed → compliant. | 5 Integrate | roadmap §13 C-7 | **DONE (2026-08-05). RESULT (`docs/eval/compliance_rung2_cc7.md`): recall 1.00 PRESERVED (0 missed real violations), all 3 constructed false-positives FIXED, overall accuracy 0.75→0.92** (real-FTC recall 1.00 / precision 0.89; 1 remaining FP = the weak no-action gold label). 9 judge tests green; ruff clean. General principles, not overfit — but the 12-case partly-synthetic gold means a robust precision number still needs a larger real negative set (NAD substantiated decisions, gated); recall (8 real violations) is the trustworthy signal. |
  | NEG-GOLD | **Grow the REAL negative (compliant) gold so precision becomes trustworthy (rung-2 continue).** The RG-2 gold's compliant class was 1 real no-action case + 3 constructed → precision directional/noisy. Acquire more REAL compliant/negative cases: FTC closing letters (no-action after adequate substantiation) + NAD press releases where NAD found a REASONABLE BASIS / the claim SUBSTANTIATED (public summaries; full NAD decisions gated). Add to `make_compliance_gold.py` (provenance=ftc_case/nad_case), re-run `eval_compliance_gold.py`. AC: real-negative class materially larger; report the updated precision (recall must hold ~1.00). Verify: eval report. | 5 Integrate | roadmap §13 C-7 | **DONE (2026-08-05).** Grew the gold 12→19 (11 viol/8 comp), REAL negative class **1→5** via NAD press-release decisions (NAD-supported=compliant: VKTRY athletes/R&D/APMA; NAD-discontinue=violation: Pamprin/Willow/VKTRY-superiority) + a 2nd FTC closing letter (Life's Vigor). Re-eval (tuned v2 judge, granite): **recall 1.00 (11/11, 0 missed) HELD; precision 0.73** (down from 0.89 on the easier 12-case). **KEY FINDING (`docs/eval/compliance_rung2_cc7.md`): 0.73 is the HONEST precision CEILING, not a tuning gap — the engine judges the claim vs rule FROM AD TEXT ALONE, but whether an OBJECTIVE claim is actually SUBSTANTIATED depends on EXTERNAL evidence (the studies NAD reviewed). NAD-supported objective claims ("worn by pro athletes") look identical to unsubstantiated ones in the text → flagged (arguably correct assistive behavior; all human-gated). The 3 constructed negatives (substantiation VISIBLE in text) all pass → judge is right when it can see the evidence. Lever = architectural (give the judge the substantiation file) or procedural (objective claim + no in-text evidence → needs_review), NOT more prompt tuning.** | 5 Integrate | roadmap §13 C-7 | done |
  | EXTRACT-TUNE | **Requirement-extraction tuning (compliance CC-5/CC-7 residuals).** (a) 255.0 "Purpose and definitions" over-generated 61 "requirements" (definitional sentences labeled obligations) — CHECK whether they leak into judging (any with a non-empty extracted claim_type scope match claims → precision risk) vs harmless bloat (empty scope → applies to nothing); FIX via skipping the definitions section in `RegulationAdapter` and/or a lexical deontic-cue gate (a genuine rule has must/shall/prohibited/may; a definition doesn't — the ADR-0040-analogous symbolic validity gate is the robust version). (b) empty applicability scope 54/155 — already handled at query time by the section→claim_type map (CC-8), so lower priority; note only. Re-ingest `ragwright_compliance`, re-run the gold eval (recall must hold). AC: definitions no longer leak; Requirement count reflects operative rules; eval unchanged-or-better. | 5 Integrate | roadmap §13 C-7 / [[ontology-lever-vs-extraction-lever]] | **DONE (2026-08-05).** DIAGNOSED: of the 61 §255.0 defs, 32 carried a granite `endorsement` scope → DID leak into the endorsement-claim pool (real leak surface + ~40% KG bloat), though narrowing top-k already filtered them from judging. FIX: `RegulationAdapter(skip_definitions=True)` skips heading-contains-"definition" sections (general mechanism; robust version = deontic-cue/SHACL gate). +1 hermetic test. Re-ingested `ragwright_compliance`: **155→96 requirements** (0 dead-lettered). Re-eval (19-case gold, tuned judge): **recall 1.00 HELD; precision 0.69 (vs 0.73, flat within judge variance + shifted narrowing pool).** ⇒ a KG-hygiene/robustness/cost win that CONFIRMED the definitions weren't the FP driver (ceiling = the external-substantiation problem, not definitions). (b) empty scope (31/96) already handled at query time by the CC-8 section→claim_type map — no change. |
  | SKILL-SPLIT | **CORE ARCHITECTURE (highest priority, user-mandated, zero compromise): every LLM-bearing capability must be split so `function`=deterministic/no-model, a single LLM act=authored `agent_skill` (SKILL.md), a workflow=`subgraph`. The 3 compliance "functions" (+ pre-existing `extraction_semantic_judge`) were misclassified as `function` — confirmed against the codebase's own `generation` precedent (agent_skill = "a single grounded/cited LLM act"). Convert all prompts into Skills (SKILL.md) + peel the deterministic adapter into a real function.** | 5 Integrate | ADR / capability-architecture | **IN PROGRESS. REFERENCE DONE (2026-08-05): `compliance_judgment` split.** `skills/compliance_judgment/SKILL.md` (the judgment METHOD authored as an agent skill; three verdicts + the ad-text-only constraint + reserve-violation/escalate rules) loaded by `build_compliance_judge_fn` (the skill runtime via the seam) → **`compliance_judgment` reclassified `function`→`agent_skill`** (contract `JudgeVerdict`); the deterministic mapping peeled into **`assemble_finding` = new `function` `compliance_finding_assembly`** (verdict vocab + conservative default + both-sided citation FROM INPUTS, no model). registry + manifests updated; consumer (compliance_check/judge_pairs) composes skill→function unchanged. 3 tests (skill+function registration, deterministic assembler, SKILL.md loads); 978 green; ruff clean; live-validated (overclaim→violation 0.98, puffery→compliant 0.95). **`claim_extraction` SPLIT DONE (2026-08-05): agent_skill (skills/claim_extraction/ = SKILL.md + `template.py` SCHEMA ASSET — a Skill is a FOLDER, not prompt-only, per user) + `claim_adaptation` function (to_claims). `extract_ad` skill runtime; ExtractedAd/ExtractedClaim moved to the skill asset, re-exported. 980 green.** DECISION (user): `requirement_extraction` → **SUBGRAPH** (auto/dense = MULTIPLE LLM calls; the chaining/workflow is deterministic = the rationale for a subgraph); build it as a chained LangGraph workflow with the LLM step(s) as skills. **`requirement_extraction` SPLIT DONE (2026-08-05): (a) `skills/requirement_extraction/` = SKILL.md (the extraction METHOD + docling-graph reliability method) + `template.py` SCHEMA ASSET (ExtractedRegulationSection/ExtractedRequirement, moved from capabilities, re-exported); (b) `capabilities/requirement_extraction.py` now holds the extraction ACT `extract_regulation_section` (docling-graph seam, `extraction_contract="auto"` → dense on long sections) + the `requirement_adaptation` FUNCTION `to_requirements` (deterministic vocab coercion, off-vocab→AMBIGUOUS/drop, citation, content-hash id) → registered as `function`; (c) `subgraphs/requirement_extraction.py` = the hardened LangGraph `build_requirement_extraction` (extract[retry]→adapt→END, dead-letter on exhaustion) + `production_requirement_extraction`/`run_requirement_extraction` → `requirement_extraction` registered `function`→`subgraph`. registry adds `requirement_adaptation` slug + flips `requirement_extraction` comment; manifests flip requirement_extraction→subgraph + add requirement_adaptation function manifest; `compliance_ingestion` now calls `run_requirement_extraction`. Old single-fn `requirement_extraction()`/`register_requirement_extraction()` (function) removed. 986 green; ruff clean.** **`extraction_semantic_judge` SPLIT DONE (2026-08-05): (a) `skills/extraction_semantic_judge/SKILL.md` = the verify-or-refute reading METHOD (the strictness rule: mere plausibility ≠ support, absence → false; "what this skill does NOT own" = dimension selection + AMBIGUOUS downgrade + concurrency); (b) `spans/semantic_judge.py` `build_semantic_judge_fn` now loads SKILL.md via `judgment_method()` + appends the per-call Property/Meaning/Clause tail (the `_DIMENSION_GLOSS` gloss injected from inputs) → `extraction_semantic_judge` reclassified `function`→`agent_skill` (contract `SemanticVerdict`, was `ClausePropertyRecord`); (c) the deterministic gate `semantic_judge` (target-select surviving non-AMBIGUOUS semantic assertions + concurrent dispatch + AMBIGUOUS downgrade, no model) peeled into NEW function `extraction_semantic_gate` (contract `ClausePropertyRecord`). registry adds `extraction_semantic_gate` slug + flips `extraction_semantic_judge` comment; manifests flip semantic_judge→agent_skill + add the gate function manifest; consumers (clause_kg_extractor, contract_ingestion_pipeline) unchanged (still call `semantic_judge`/`build_semantic_judge_fn`). 989 green; ruff clean. ALL 4 MISCLASSIFIED CAPABILITIES NOW SPLIT (compliance_judgment, claim_extraction, requirement_extraction, extraction_semantic_judge).** **CONTRACT-SIDE AUDIT DONE (2026-08-05):** listed all 40 contract-side capabilities by kind; audited every `function` for hidden model use. FINDING 1 (violation) = `vision_to_text` registered `function` but a single grounded VLM act (received `model: VisionModel`, invoked GENERAL model) → **FIXED: reclassified `function`→`agent_skill`**, prompt authored into `skills/vision_to_text/SKILL.md` (loaded by `transcription_method()`), contract stays `VisionTranscription`, capabilityInterface retained + response_bounds auto-dropped (mirrors its twin `generation`). FINDING 2 (gap) = OKF signpost enrichment is a single LLM act registered nowhere → user: DROPPED from the pipeline (improvements achieved without it), leave as-is. Everything else conforms: all `function`s deterministic; `model`/`agent_skill`/`subgraph` correct; `ontology_registry_derivation` = reserved FR-C.8 slug (no impl). **NAMING AUDIT DONE (2026-08-05):** swept cuad/acord/ftc across core src — capability slugs 100% clean, generic seams clean, contract enums generic (dataset tokens are docstring provenance only). ONE smell fixed: `CuadAdapter` + `run_cuad_ingestion` lived inside the GENERIC `subgraphs/contract_ingestion_pipeline.py` → **relocated to `corpus/cuad_ingestion.py`** (next to `corpus/cuad.py`); the generic-but-private `_corpus_party_link_fn` kept in the pipeline as PUBLIC generic `corpus_party_link_fn` (the CUAD driver passes only the CUAD mention-cache path); 2 scripts + 2 tests updated, + new `tests/corpus/test_cuad_ingestion.py`. 992 green; ruff clean (2 pre-existing `json` orphans in contract_kg_serve.py/okf/links.py flagged, not mine — unrelated dead-import debt). **PROMPT-PARITY + DEBT DONE (2026-08-05): `generation` prompt authored into `skills/generation/SKILL.md` (loaded by `generation_method()`; the FR-Q.6 citation/abstention GUARANTEES stay enforced in code) — every agent_skill's prompt now lives in its SKILL.md. Removed 2 pre-existing unused imports (`typing.Any` in contract_kg_serve.py, `json` in okf/links.py); whole-src ruff now 100% clean. 993 green.** **ROLLOUT REMAINING: item (3) = realize skills' runtime as LangGraph/DeepAgent where justified. DECISION (2026-08-05): make it a documented PRINCIPLE (ADR), NOT speculative code — the heavy runtimes that need it already exist + are tested (rlm = Deep Agent ADR-0015/0017; okf_navigate = create_agent), and single-shot skills correctly use the seam+SKILL.md path (SkillsMiddleware progressive-disclosure only earns its keep when an agent dynamically picks skills/assets, which none need yet). Build the create_agent+SkillsMiddleware runtime WHEN a concrete skill needs asset/tool progressive disclosure; okf_navigate/rlm are the references. **DECISION RECORDED: ADR-0041 (capability-kind rubric + agent-skill runtime tiers) — the SKILL-SPLIT arc is now closed as a written rule (kinds are CI-pinned; single-shot=seam+SKILL.md; heavy=create_agent/Deep-Agent, already built/tested; middleware runtime deferred to real need).** SKILL-SPLIT ARC COMPLETE. |
  | RG-6 | **Procedural fix for the external-substantiation ceiling: objective claim + no in-text evidence → needs_review (+ ad-level threshold).** (1) Judge prompt reserves VIOLATION for what's clearly wrong in the text (OVERCLAIMING proof without a cited study; MISSING disclosure; fake review) and sends unverifiable objective claims to NEEDS_REVIEW (escalate — never clears a violation). (2) `ComplianceReport.verdict` ad-level rollup: VIOLATION only when violation findings ≥2 (a lone spurious finding among many rules escalates, not hard-flags), else NEEDS_REVIEW if anything fired, else COMPLIANT. | 5 Integrate | roadmap §13 C-7 | **DONE (2026-08-05). RESULT (`docs/eval/compliance_rung2_cc7.md`, 3-way): clearance-safety 1.00 (0 of 11 real violations cleared — the safety guarantee); hard-violation-precision 0.73→0.92; hard-FP-rate 0.63→0.12.** NAD-supported objective claims (VKTRY athletes/R&D/APMA) now correctly ESCALATE to needs_review (honest "can't verify from text — human checks the evidence file") instead of hard-flagging; lone residual hard-FP = the weak lifes_vigor closing-letter label. Tradeoff: +escalation load (6 compliant→needs_review), correct for an assistive human-gated tool. Threshold lives on the PRODUCT (ComplianceReport.verdict), so consumers get the principled ad-level verdict. +1 hermetic test; 975 green; ruff clean. Residual limits = external-substantiation reality + small/weak gold → next is a larger expert-graded negative set, not more tuning. |
  | CAP-REG-0 | Ground the LangGraph + DeepAgents surface for the subgraph work: add `langgraph`+`deepagents` to the framework AST index (`refresh_framework_graph.sh` PKGS) + LangChain docs MCP for concepts | 5 Integrate | ARD | **DONE (2026-07-30). AST-only add (both already installed transitively; no LLM, no new dep). Framework graph 19684 -> 23506 nodes; StateGraph/add_node/add_conditional_edges/RetryPolicy/Interrupt/CheckpointSaver/Pregel + create_deep*/SubAgent/DeepAgents ground locally. LangChain docs MCP (`mcp__docs-langchain__*`) available for concepts. Rebuild offline: `UV_NO_SYNC=1 HF_HUB_OFFLINE=1 bash scripts/refresh_framework_graph.sh` ([[framework-rebuild-uv-no-sync]]).** |
  | CAP-REG-1 | Reclassify the 5 mis-kinded existing ARD manifests to the settled taxonomy (`docs/product/capability_profiles.md`): `text_embedding`+`cross_encoder_rerank` function->**model**; `grounded_answer_generation` function->**agent_skill** (DROP response_bounds — skills not callable); `graph_extraction` function->**subgraph**; `semantic_chunking`(rlm_chunking) agent_skill->**subgraph** (drop skill_runtime). Kind-coupled fields per `manifests.py` (response_bounds callable-only, skill_runtime/requires skills-only, capability_interface GraphWright-verified). TDD via `tests/capabilities/test_manifests.py`+`test_registry.py`. | 5 Integrate | ARD | **4/5 DONE (2026-07-30). Reclassified in manifests.py specs + register_* calls: `embedding`->model, `reranking`->model, `graph_extraction`->subgraph, `generation`->agent_skill (author() auto-drops response_bounds for the non-callable skill; capability_interface preserved; vision_to_text stays function). TDD: new pins in test_manifests + updated per-capability register tests; 736 hermetic green. DEFERRED `rlm_chunking`(semantic_chunking): it carries a skill_runtime with requires_dynamic_dispatch=True (RLM recursion = agent-decides, NOT a deterministic LangGraph) + requires=rlm_method; reclassifying to `subgraph` breaks the skill_runtime/requires invariants + tests and mis-labels a dynamic flow as deterministic -> needs a design decision (keep as dynamic RLM agent_skill + add a NEW single-call `semantic_chunking` subgraph, OR make it a subgraph-wrapping-a-DeepAgent per the case-by-case rule). Tie to LG-2. See CAP-REG-1b.** |
  | CAP-REG-1b | DECISION+build for chunking's kind (deferred from CAP-REG-1): `rlm_chunking` is a DYNAMIC RLM agent_skill (skill_runtime requires_dynamic_dispatch=True, requires=rlm_method) — not a deterministic subgraph. Options: (A) keep `rlm_chunking` as agent_skill AND introduce a NEW `semantic_chunking` **subgraph** for the single-call deterministic chunker (LLM boundaries -> validate -> hash-gate); (B) reclassify `rlm_chunking`->subgraph as a subgraph-wrapping-a-DeepAgent (case-by-case rule), moving skill_runtime semantics. Lean (A). Do alongside LG-2. | 5 Integrate | ARD | **DONE — Option A (2026-07-30). `rlm_chunking` stays the dynamic RLM `agent_skill` (unchanged). Registered NEW `semantic_chunking` as a **subgraph** (the single-call CU-B4 discoverer already in rlm_chunking.py:224+): canonical slug + manifests.py spec (kind=subgraph, same {parsed->chunk} capability_interface, deterministic tags) + `register_semantic_chunking` (contract ChunkManifest). TDD: manifest + register tests; 739 hermetic green. LangGraph hardening of its body = LG-2.** |
  | CAP-REG-2 | Register the BUILT-but-unregistered contract-KG capabilities (author ARD manifest + contract + register_*; add capability_interface for query-graph ones GraphWright verifies): functions `typed_value_normalization`,`extraction_grounding_judge`,`operative_span_segmentation`,`typed_kg_write`,`intra_document_scoped_query`,`clause_disambiguation`; model `clause_function_classification`(LegalBERT); agent_skill `query_function_classification` | 5 Integrate | ARD | **DONE (2026-07-30). Registered 7 (dropped `typed_kg_write` — store-I/O infra like chunk_write/graph_storage). 2 new contracts authored: `NormalizedValue` (contracts/value_match.py), `FunctionClassification` (contracts/function.py, shared by the LegalBERT model + the query skill). Slugs (registry.py) + manifest specs (manifests.py) + register_* funcs in each module: `typed_value_normalization`/`extraction_grounding_judge`/`operative_span_segmentation`/`intra_document_scoped_query`/`clause_disambiguation` (function), `clause_function_classification` (model), `query_function_classification` (agent_skill). capability_interface OMITTED (added when GraphWright-governed). Contracts: ClausePropertyRecord/SpanRecord/CitedClause reused. TDD: parametrized author + register tests; 754 hermetic green.** |
  | CAP-REG-3 | Package the retrieval core OUT of `eval/kg_primary.py` into capability modules, then register: functions `candidate_routing`,`typed_constraint_match_rank`,`dense_rank_tiebreak`; subgraph `query_constraint_extraction` (depends on LG-0 pattern) | 5 Integrate | ARD | **DONE (2026-07-31; pulled BEFORE LG-3c, which needs it — user-approved re-order).** The 3 retrieval-core functions lifted out of `eval/kg_primary.py`'s `main()` closures into `capabilities/retrieval_core.py` as PURE deterministic `function`s (no LLM — the LLM front door is query_constraint_extraction + query function classification, upstream): `candidate_routing` (union combiner: union ranked function preds first-wins/recall-safe -> pool via an injected `pool_fn` store seam; NO ACORD okf_path identity baked in / no store-contract expansion), `typed_constraint_match_rank` (grade by `constraint_match_count` KG-5a canon+subsumption, STABLE sort so zero-match kept — recall-safe), `dense_rank_tiebreak` (cosine order; vectors from `embedding`; mirrors eval `_cosine`, 0.0 on zero-norm). Composition (match primary, cosine within ties) belongs to LG-3c, not here — atomic functions. Twofold reg (user-confirmed: the SUBGRAPH is the governed capability, its internal components get twofold reg only, NO capability_interface): +3 slugs +3 ARD manifests +3 register_*. `query_constraint_extraction` subgraph already done under LG-2a. 8 tests; 808 green. NEXT = LG-3c `cross_corpus_retrieval` (now unblocked).** |
  | CAP-REG-4 | Re-back `graph_extraction` (FR-C.6) with the GP-1B docling-graph party/relational extractor; RETIRE the T23-27 hybrid (spaCy NER + contract-LLM + escalation). ADR the swap. Prereq for KG-7 + LG-3d. | 5 Integrate | ARD | **DONE (2026-07-31; user-directed). ADR-0035.** Kept the slug/`ExtractionResult` contract/`Extractor` seam/governed `capability_interface`/LG-2c subgraph; swapped only the impl behind the seam. New `DoclingGraphExtractor` (GP-1B `extract_parties`, granite-4.1-8b -> `parties_to_extraction`: ORG mentions + structural CONTRACTS_WITH; `extract_fn` DI'd for hermetic tests; `production_extract_fn()` binds the real model; `default_extractors()` re-pointed). RETIRED SpacyNer/SpacyPipeline/NerPipeline, ContractExtractor (clause-cat facts = now the typed Clause KG's job), LlmEscalationExtractor (AFFILIATE_OF was gold-only, not in the proven GP-1B path) + their prompts/schemas/tests. Rationale: Leg B -> typed Clause KG, Leg C -> GP-1B (real recall 0.991); the hybrid was an early inter-corpus-recall probe. Observability: LG-2c node now `raw_llm_span` (docling-graph = raw-SDK), was `business_span`. Manifest/display_name updated; ports unchanged. 92 targeted green; ruff clean; 814 full green. NEXT = KG-7 (Party<->Clause link; BOTH graphs already populated -> pure linking, NO re-ingest), then LG-3d.** |
  | LG-0 | Add `langgraph` as a DIRECT dependency (`uv add langgraph`; ask-first done — approved) + establish the reusable subgraph-capability PATTERN: a `StateGraph` scaffold with RetryPolicy, conditional escalation edges, `interrupt()` HITL, per-node try/except -> dead-letter, checkpointing; + a hermetic test harness (inject fake model/store). | 5 Integrate | ARD | **DONE (2026-07-30). `langgraph` added as a DIRECT dep (`uv add langgraph --frozen` — already locked transitively; en_core_web_sm git-dep blocks online re-lock, reconcile with `uv lock` when online). Scaffold `src/rag_wright/subgraphs/scaffold.py`: DEFAULT_RETRY (RetryPolicy) + dead_letter() + the documented pattern (DI model/store, retry, conditional escalation edge, interrupt() HITL, dead-letter terminal). Hermetic harness `tests/subgraphs/test_scaffold.py` proves retry-recovers-transient + conditional-escalation-routing + dead-letter-drops-without-raising on tiny injected StateGraphs (3 passed; 757 hermetic green). NOTE: LangGraph default retry_on skips ValueError/OSError/etc. -> per-node retry_on needed for those. Reference impl = LG-1 typed_clause_extraction. OBSERVABILITY (per GraphWright's `temp/observability-contract.md`): added `subgraphs/observability.py` — a dependency-free, NO-OP-safe seam over the AMBIENT OpenTelemetry tracer (business_span / raw_llm_span+record_tokens / inject_context+attach_context / otel_active), re-exported via scaffold. We do NOT build a tracer provider/exporter or Langfuse client — GraphWright installs global OTel instrumentation (Langfuse or ANY OTLP backend, swappable with zero change here). Rules baked into the scaffold: models via the LangChain seam (auto-captured); the raw-SDK gap (docling-graph/LiteLLM in dg_extraction, sandboxed calls) wrapped in raw_llm_span+record_tokens; context propagation across introduced boundaries. OTel not installed standalone (GraphWright provides it) -> helpers no-op in tests (8 subgraph tests; 762 hermetic green). Langfuse NOT grounded/added (contract = no direct Langfuse); langgraph+deepagents already in the framework index.** |
  | LG-1 | Reference hardened subgraph: `typed_clause_extraction` as a LangGraph (extract[LLM,retry] -> adapt -> reground -> [Flash->Pro escalate loop] -> [HITL gate] -> dead-letter). Contract `{clause_text,function_hint,chunk_id,span_id} -> {ClausePropertyRecord | dead_letter(reason)}`. Register as subgraph. | 5 Integrate | ARD | **DONE (2026-07-30). `src/rag_wright/subgraphs/typed_clause_extraction.py`: a compiled LangGraph on the LG-0 scaffold — extract_cheap [DEFAULT_RETRY; TransientExtraction retried, genuine None -> dead-letter] -> reground (ADR-0028) -> conditional escalation edge (needs_escalation, bounded by `escalated`) -> extract_strong (best-effort, falls back to cheap record) -> optional human_gate (interrupt(), needs checkpointer) -> END. record_fn/escalate_fn dependency-injected for hermetic testing; production_record_fn wires extract_clause+clause_to_record. The docling-graph/LiteLLM raw-SDK call is wrapped in `raw_llm_span` per the observability contract. Registered as `subgraph` (contract ClausePropertyRecord). TDD (tests/subgraphs/test_typed_clause_extraction.py): happy/retry-recovers/dead-letter/escalation-once/HITL-interrupt/register — 6 tests; 769 hermetic green. This is the REFERENCE pattern for LG-2/LG-3.** |
  | LG-2 | Convert the remaining component subgraphs to hardened LangGraphs + register: `graph_extraction`, `query_constraint_extraction`, `semantic_chunking` | 5 Integrate | ARD | **LG-2a DONE (2026-07-30): `query_constraint_extraction` — subgraphs/query_constraint_extraction.py, query-side extract with graceful-empty degradation (no reground/retry/dead-letter; KG-5d), observability-wrapped raw-SDK call, registered subgraph, 4 hermetic tests (774 green). GROUNDED for the rest: `semantic_chunking` — the CU-B4 pipeline already exists in rlm_chunking.py as a seam-based orchestrator (content-hash gate -> SingleCallBoundaryDiscoverer.discover -> _validate_partition -> _finalize_chunks -> _summarize_all[concurrent] -> ChunkManifest). DESIGN FORK: (A) thin subgraph = one hardened node wrapping the existing orchestrator with SingleCallBoundaryDiscoverer + retry/dead-letter/observability (fast, reuses tested code, but hides the steps); (B) granular multi-node graph = gate/discover[retry]/finalize/summarize/manifest as nodes (faithful to "multi-step", more work, must handle the summarize async-loop OTel context). `graph_extraction` — needs its own grounding (NER->OpenIE->LLM->merge stack). Both are deeper conversions than LG-2a; recommend picking (A) for semantic_chunking and doing graph_extraction as its own unit.** LG-2b DONE (option B, per user "do it right"): `semantic_chunking` = GRANULAR multi-node LangGraph (gate->discover->finalize->summarize->manifest), reuses rlm_chunking's deterministic layer verbatim + same injected seams, retry->dead-letter via runtime.execution_info.node_attempt (langgraph 1.2.9 error_handler is called node-style, so used node_attempt), OTel-safe async summarize, registered subgraph, 5 tests. ALSO hardened LG-1 for the same persistent-transient->dead-letter (consistency). 780 green. LG-2c DONE (2026-07-30): `graph_extraction` = granular PARALLEL LangGraph (fan-out the 3 Extractor-seam extractors [spaCy NER + contract-LLM + LLM-escalation] -> reducer-accumulate -> ExtractionResult.merge -> fan-in). Graceful degradation: a failing extractor (re-raised as TransientExtraction -> retried) contributes an EMPTY result on exhaustion, so the chunk keeps the other extractors' facts (never dropped). LLM extractors use the model-profile seam (auto-captured) -> NO raw-SDK gap; business_span per extractor. Registered subgraph, 3 tests. **LG-2 COMPLETE (all 3 + LG-1 hardened); 783 hermetic green.**** |
  | LG-3 | Wrap the composite pipelines as LangGraph subgraphs + register: `contract_ingestion_pipeline`, `intra_document_qa`, `cross_corpus_retrieval`, `relational_qa` (each a self-contained subagent under contract; GraphWright composes them) | 5 Integrate | ARD | **LG-3a DONE (2026-07-30): `relational_qa` = the REFERENCE composite. `subgraphs/relational_qa.py`: traverse[retry]->rehydrate->generate composing graph_query (FR-C.5) -> chunk_read (T38) -> generate_answer (FR-Q.6), the 3 capability calls DI'd (traverse_fn/rehydrate_fn/generate_fn) for hermetic tests, production_relational_qa() wires the real trio. Query-side judicious hardening (like query_constraint_extraction): traverse degrades-to-empty->abstain on retry-exhaust (query never dropped); orphan chunk_id -> dead_letter (chunk_read's no-silent-drop honored, never fabricate); confidence threads graph->evidence->answer (FR-S.4); business_span per node (no raw-SDK gap). Twofold reg: +canonical slug (registry.py) + ARD manifest (manifests.py) + register_relational_qa (contract GeneratedAnswer). 6 tests; 789 green. LG-3b DONE (2026-07-31): `intra_document_qa` = scoped KG query -> REHYDRATE real clause text -> generate. `subgraphs/intra_document_qa.py`: serve[retry->degrade-empty] -> assemble[retry] -> generate composing contract_kg_serve (KG-4) -> operative-span text (via each clause's span_id provenance; the Clause node stores only id/function/folio, text lives on SPAN records) -> generate_answer (FR-Q.6). Evidence = the REAL clause language cited by clause_id, typed (dim=value) facts appended, worst-case confidence surfaced (FR-S.4). Orphan span_id -> dead_letter (no fabricate); property-less clause -> function label; transient serve -> degrade-empty -> abstain. serve_fn/clause_text_fn/generate_fn DI'd; production wires classify_query_functions -> clauses_of_function (fallback contract_clause_index) + spans_by_contract rehydration + generate_answer. Twofold reg: +slug +manifest +register_intra_document_qa (contract GeneratedAnswer). 7 tests; 797 green. LG-3c DONE (2026-07-31; unblocked by CAP-REG-3): `cross_corpus_retrieval` = the richest composite. `subgraphs/cross_corpus_retrieval.py`: parallel fan-out of the 2 LLM front-door steps (extract_constraints[query_constraint_extraction LG-2a] || classify_functions) -> route[candidate_routing] -> hydrate(props+dense vec+text per candidate) -> rank(typed_constraint_match_rank PRIMARY + dense_rank_tiebreak WITHIN equal-match groups) -> assemble(cited RetrievedClause). Composes the 3 CAP-REG-3 fns DIRECTLY (imported+called); the match-primary/cosine-within-ties combination lives in the rank node (kept OUT of the atomic fns). 4 I/O seams DI'd; shared _degrading_io helper = retry->degrade-empty (empty constraints -> dense-only order/query survives; empty functions -> empty results). Production wires only the UNIVERSAL LLM caps (query_constraint_extraction + classify_query_functions); pool_fn/hydrate_fn are caller-supplied (corpus/identity-specific = integration layer's job, two-halves boundary; NO invented store methods, no ACORD okf_path in the capability). Twofold reg: +slug +manifest +register_cross_corpus_retrieval (contract CrossCorpusRetrieval). 6 tests; ruff clean; 815 green. LG-3d DONE (2026-07-31): `contract_ingestion_pipeline` = the GENERIC ingestion pipeline + the `CorpusAdapter` seam (the confirmed no-per-corpus-ingest-fn design). `subgraphs/contract_ingestion_pipeline.py`: `build_document_ingest` = per-doc LangGraph chunk[retry] -> parallel(extract_clauses || extract_graph) -> resolve -> write, per-doc DEAD-LETTER (one bad doc never kills the corpus ingest); `run_corpus_ingestion(adapter, graph, link_fn)` maps docs then runs party_clause_linking (KG-7) ONCE. `CorpusAdapter.documents()->SourceDocument{canonical id (HYG-1), text, metadata}` = the ONLY per-corpus code (parse lives in the adapter). Concrete `CuadAdapter` (reference) smoke-validated on real CUAD (3 docs -> canonical `_` ids). Stage seams DI'd for hermetic tests; production stage-binding = documented deployment glue (two-halves; the INGEST-REFACTOR task wires+migrates). Twofold reg: +slug +manifest +register_contract_ingestion_pipeline (contract IngestionReport). 4 tests; ruff clean; 834 green. **LG-3 COMPLETE (all 4 composites: relational_qa, intra_document_qa, cross_corpus_retrieval, contract_ingestion_pipeline).** NEXT = INGEST-REFACTOR + SKILL-corpus-ingest (+ enhancements PARTY-TO-MANY-TO-MANY, CUAD-FULL-COVERAGE).** |
  | DEMO-1 | FastAPI backend: `GET /documents`, `GET /documents/{id}` (canonical text+meta), `POST /documents/{id}/ask` (understand_query->serve_highlight->HighlightResult), `GET /health`; retrieve/graph routers added as their legs come online | 5 Integrate | FR-Q | **todo** — verify: hermetic `/ask` routing tests (present/absent/extract/out-of-tax, injected store+factory) + live smoke |
  | DEMO-2 | Minimal CLI client over the API (`list`, `ask <document_id> "<q>"`; renders highlighted span + function/confidence/present/low_conf/value) | 5 Integrate | FR-Q | **todo** — verify: CLI hits a live backend, prints a cited highlight |
  | DEMO-3 | Minimal Web client over the API (document dropdown + question box; renders canonical text with `<mark>` spans + side panel) | 5 Integrate | FR-Q | **todo** — verify: end-to-end in a browser against the backend |
- **2026-07-26: ADOPTED — improved (b) is the query-time reranker operating point.** Pipeline: function filter
  (union-top-2) → first-stage LegalBERT hard-neg CE (a) → **ONE Gemma listwise call** over the top-K, with
  **per-candidate scoring + KG-features-in-prompt + K=25** (`scripts/distill/listwise_variants.py`
  `score_feat_k25`). Condensed **nDCG@10 0.702 (all) / 0.634 (contrastive)**, recall@20 ~0.86 — one call/query.
  The measured cost/quality ladder: **(a) 0-LLM CE 0.649/0.542  <  (b) 1-call listwise 0.702/0.634  <
  pointwise-Gemma ~N-call 0.737/0.758.** ZERO-query-LLM paths (LTR, hard-neg CE, KG features, distillation) all
  plateau ~BGE; the 1 listwise call is the pragmatic sweet spot. Prompt/format levers plateaued (~1/3 of the
  gap to pointwise closed; contrastive still the weak axis). **OPEN to push further (not started): DSPy
  few-shot demos for contrastive; Pro for the listwise call (likely biggest lever); (c) full-pool Gemma
  teacher labeling → proper distillation for a zero-query-LLM path.** See memory `reranker-operating-point-b`.
- **2026-07-25: T-DISTILL STARTED (FR-C reranking) — distilled cross-encoder as the query-time reranker
  (0 query-time LLM).** Motivation chain: per-clause LLM rerank = O(N) calls, impractical latency; LTR over
  [BGE + KG features] via GBM AND LambdaMART both cap at ~BGE (nDCG@10 ~0.52), far below the Gemma LLM (0.726)
  — the gap is a SEMANTIC-TEXT-READING gap that hand-features can't reach; but KG features ARE the best RECALL
  signal (recall@20 0.80 vs BGE 0.74). So: **KG features = recall stage; distilled cross-encoder = precision
  stage.** Plan: **P1** text-only MiniLM & LegalBERT cross-encoders fine-tuned on ACORD grades, query-disjoint
  CV, vs BGE 0.526 / LTR 0.52 / Gemma 0.726 (bar: match 0.726 at ~0 query-time cost; Gemma is a REFERENCE not
  a ceiling — trained on human grades the student can surpass it); **P2** add KG features (text-append) →
  precision lift?; **P3** LLM-teacher distillation for cross-corpus generalization (no gold grades). Training
  on **Modal A10** (grounded: modal SDK now in the framework graph + official modal skill installed). Deps
  added: sentence-transformers (runtime), modal (dev).
  **P1 DONE (text-only, RANDOM negatives), honest result:** MiniLM CE nDCG@10 0.628 / LegalBERT 0.642 vs
  BGE 0.629 vs Gemma 0.733 (contrastive: CE ~0.46-0.50 vs BGE 0.503 vs Gemma 0.758). Text-only CE with random
  negatives only MATCHES BGE, far below the LLM. DIAGNOSIS: strided negatives are cross-function (easy); eval
  needs WITHIN-function discrimination -> CE learned coarse relevance (BGE-level saturation), not hard within-
  pool ranking. Modal pipeline validated (~$2, ~80s/fold MiniLM, ~250s LegalBERT; scripts/distill/{export_ce_
  dataset,train_ce,train_modal,eval_ce}.py). LegalBERT (domain) = marginally better base. **NEXT: P1.5 HARD-
  NEGATIVE MINING — per gold, sample negatives from its OWN function pool (the high-BGE same-function non-gold
  = the eval distractors) + optional per-query ranking loss; LegalBERT base. This is the gated lever.**
  **P1.5/P2/P3 DONE — honest conclusion: cheap CE plateaus near BGE, far below the LLM.** Best = LegalBERT +
  HARD NEGATIVES (gold): all-57 nDCG@10 0.649 / contrastive 0.542 (BGE 0.624/0.503, Gemma 0.737/0.758). P2 KG
  features (text-append) HURT (0.614/0.463 — displaces clause text in the 512-token budget; 3rd time features
  fail to lift ranking). P3 distillation (Gemma teacher, NO gold) WORKS as a mechanism but is WEAKER than gold
  (0.591/0.480, ~BGE) — viable for ungraded corpora, doesn't beat grades, doesn't reach the LLM. **NONE of the
  zero-query-time-LLM paths (LTR, hard-neg CE, KG features, distillation) closes the ~0.10-0.20 contrastive gap
  to the LLM.** Scripts: scripts/distill/{relational_features,extract_features,eval_all}.py + train_modal
  features/distill modes. Caches: data/models/ce/{pools.json,bge_scores.jsonl,clause_features.jsonl}. Modal
  total ~$3-4. **OPEN DECISION (not chosen): (a) accept LegalBERT+hard-neg CE (0.649, >BGE, 0 query-LLM) as the
  operating point; (b) cheap query-time LLM (ONE listwise call on the top-K, not per-clause); (c) push the CE
  harder (full-pool Gemma teacher labeling for MORE distill data + ranking loss + bigger student) — uncertain
  payoff.**
- **2026-07-25: NEXT DIRECTION — relational clause KG (directional liability/favorability) to solve the
  genuine-misrank 2/3 at INGESTION time.** Research (this session): the *vocabulary+machinery* exist and are
  reusable — LKIF-Core (deontic/liability/role primitives, OWL), FOLIO (party-role/clause-type IRIs, CC-BY,
  already aligned in T57a), CUAD-41 (clause types); the *directional who-is-liable-to-whom / favorability
  relational schema* is NOT off-the-shelf (not FOLIO=taxonomy, not CUAD/ACORD=labels, the standard LLM+Pydantic
  contract-KG flattens it to text) -> it's a THIN bespoke layer (~6 edge types + party-role map) we author on
  top, populate with Gemma structured extraction (OpenIE optional recall net), verify with a T61-style
  grounding judge on the ROLE/DIRECTION edges. Expert review reduced to validating a ~1-page schema (last
  resort). Prototype (schema + Gemma dry-run on hard contrastive clauses) IN PROGRESS.
  **RETRIEVAL COVERAGE DECISION: use UNION-TOP-2 function outputs, not top-1** (ceiling 0.992 vs 0.939, T58a).
  NOTE: `LegalBertFunctionClassifier.classify()` currently returns argmax/top-1 only; the model exposes logits
  so top-2 is a small `classify_topk` addition (do when wiring the real retrieval path). See memory
  `topk-ordering-levers-t58b`, `retrieval-design-t58`.
- **2026-07-25: TOP-10 ORDERING GAP DIAGNOSED + LEVERS MEASURED (T58b stage 2).** Clean condensed harness
  `eval/condensed_pipeline.py` (judged-only, PERSISTED discriminators, no-cache-on-failure). Pointwise Gemma
  operating point: **recall@10 0.674 / recall@20 0.885 / nDCG@10 0.706** (condensed). Diagnosis of the
  recall@10<recall@20 gap: **67% genuine misrank** (>=10 judged clauses strictly outscore gold) + **33% ties**;
  heavy ties (median 8 distinct scores/pool); contrastive/within-family queries are the weak spot (misrank).
  Levers head-to-head: **full listwise re-order HURTS (-0.015)**; **listwise TIE-BREAK helps +0.018** (only
  positive, 1 serve call); **property tie-break NEUTRAL -0.001** (too sparse: ~1 qconstraint/query, 73% clauses
  0-match). The 2/3 misrank is unmoved by reshuffling Gemma's own scores. Report:
  **`docs/results/2026-07-25-t58b-topk-ordering-levers.md`**. **DECISION: adopt pointwise (+optional
  listwise-tiebreak) as the retrieval operating point; stop chasing misrank with re-rank tricks. NEXT (open,
  not run): measured Pro-escalation ONLY on the ~10 hard compound-contrastive queries (carveout-to-cap, mutual
  cap, exception-to-waiver), like the extraction cascade.**
- **2026-07-25: FULL-PIPELINE RERANK MEASURED (T58b, Gemma 4 31b throughout, 57 queries, 17,715 grades).**
  oracle-function pool → Gemma decompose (hardened discriminator) → Gemma continuous-score rerank.
  **(a) condensed nDCG@10 = 0.702, recall@10 = 0.677, recall@20 = 0.900** (judged-only, the honest signal);
  **(b) full-pool nDCG@10 = 0.179** (ACORD protocol, confounded by pooling — un-judged genuine matches crowd
  top-10 at 0 gain); full recall@50 = 0.548. Report: **`docs/results/2026-07-25-t58b-full-pipeline-rerank.md`**.
  Decision: optimize the **condensed** number (practical downstream-retrieval quality), NOT full-corpus
  leaderboard parity. **NEXT: attack the top-10 ordering gap (recall@10 0.677 vs recall@20 0.900).**
- **2026-07-24: PHASE-2 POPULATION COMPLETE (Flash→Pro cascade + GATE, ~41 min).** Property graph populated:
  3,886 clauses / 393 value nodes / 8,597 edges; confidence EXTRACTED 72% / INFERRED 20% / AMBIGUOUS 7.4%
  (judge+GATE). Full run report + baselines checkpoint: **`docs/results/2026-07-24-t58-property-graph-population.md`**
  (this is the store state the GATE-R recall@50 is measured against). **NEXT: run the full function+property+rerank
  recall@50 measurement.**
- **2026-07-24: T61 grounding judge built (ADR-0028); phase-2 cascade wired, awaiting go.** 3-model extraction
  bench (Pro/Flash/Gemma): Flash ~5x faster, highest coverage, but occasional confident hallucination
  (`carve_out=fraud`); Pro precise but throttled (24s) + one total-failure; Gemma noisy AMBIGUOUS. Deterministic
  local judge (`spans/property_grounding.py`) catches lexical hallucinations → Flash→Pro escalation cascade
  (`EXTRACT_MODEL=…-flash ESCALATE_MODEL=…-pro`), judge also does double duty as a permanent quality gate
  (`reground`/`GATE=1`). **NEXT (awaiting explicit go): full phase-2 run = Flash main + Pro fallback cascade.**
- **APPROVED 2026-07-23: T58a (FR-Q, ADR-0025).** Full-corpus phase-1 population (14,553 spans) + the
  single-function gate ceiling: **best-single 0.939 / union-top-2 0.992** (bar 0.667, baseline 0.379); only 4/57
  below bar, all recovered by union-top-2. KEY: reachability is NOT the ceiling — the 0.379 wall was RANKING, not
  reachability; recall is now a small-pool ranking problem (rerank + HyDE-dense). **Next: phase 2 property
  extraction (~3,887 clauses), then T58b retrieval composition → GATE-R.**
- **APPROVED 2026-07-23: T57c (FR-C.6/FR-C.7, ADR-0025/0026).** Property-graph write-path: ArcadeDB
  `Clause`/`PropertyValue`/`HasProperty`, `write_property_graph` (provenance on every edge), shared value nodes
  deduped by canonical (dimension,value) — no entity_resolution clustering; content-hash-gated idempotency. 5
  tests (1 hermetic + 4 live). **Next up: T58** — query decomposition (→ function + property) + in-store
  retrieval, STARTING with the full-corpus population run (segment → classify → extract → write, via
  `map_concurrent` with progress). T57 (a/b/c) + the function leg (T60) complete.
- **APPROVED 2026-07-23: T57b (FR-C.6, ADR-0025/0026).** Property extractor: function-aware `FUNCTION_DIMENSIONS`
  map, pure `build_record` (scope-filter + AMBIGUOUS `other`-escape coercion + provenance/span-cite/FOLIO),
  `SeamPropertyExtractor` (DeepSeek via seam). 5 hermetic tests + live smoke correct. **Next up: T57c** — property-
  graph population: Clause node ↔ typed property edges + shared value nodes (dedup by canonical (dimension,value)),
  reuse graph_storage pattern, NOT the generic entity graph.
- **APPROVED 2026-07-23: T60 (FR-C.3, FR-Q; ADR-0026).** Function leg now covers all 57 ACORD queries: 45-class
  LegalBERT (44 fn + NONE) via LLM-bootstrapped labels over CUAD (keyword pre-filter + DeepSeek confirm, ACORD
  untouched). Sub-gate met (Indemnification 0.70 / Warranty Disclaimer 0.95 / Damages Waiver 0.70; Cap↔Insurance
  clean); macro-F1 0.586 / micro 0.744. Production trainer (best-model retention, early stopping, resume + adopt-
  guard, dry-run isolation) + reusable `util/concurrent`. **Next up: T57b** — the property extractor (DeepSeek via
  the model-profile seam, reuses `map_concurrent`) → emits `ClausePropertyRecord` per clause span.
- **APPROVED 2026-07-23: T57a (FR-C.6, ADR-0025/ADR-0026).** Property schema as a contract (schema review gate
  passed): two-tier `PropertyDimension` + `CLOSED_VOCAB` (AMBIGUOUS `other` escape), `PropertyAssertion`/
  `ClausePropertyRecord`, extended 44-class FUNCTION taxonomy, FOLIO IRIs for naming only. T57 split into T57a +
  T60 + T57b + T57c.
- **APPROVED 2026-07-23: T55 + T56 (FR-R, ADR-0025).** Operative-span segmenter + ArcadeDB `Span` store (T55),
  and the local FUNCTION classifier (T56) — LegalBERT fine-tune, macro-F1 0.544, Cap↔Insurance confusion
  eliminated. The classifier is a controlled high-accuracy variable, so a downstream recall shortfall is
  attributable to the property/rerank stages, not function-confusion.

- **SPECCED (not yet built), 2026-07-22: FR-K — embedding-free OKF navigation (T45-T53 + GATE-3a/GATE-3).**
  New experimental capability block added to `SPEC.md` (FR-K.1-K.9) and this ledger, motivated by the T41
  query-representation-gap diagnosis: compile the chunk corpus into an Open Knowledge Format (OKF) bundle
  and retrieve by programmatic traversal of signposts (indexes, frontmatter, links) instead of vector
  similarity. Corpus-neutral capability; ACORD is the first validation corpus. Categories manufactured via
  `graph_extraction`; index descriptions reuse T-SUM summaries; bundle is a gitignored data artifact.
  **First execution slice = T45 gold labels → T46 chunk-only compile → T47 reachability analyzer → T48
  category-label control → GATE-3a** (per-corpus reachability kill-switch, no traversal model call spent).
  Behind GATE-3a: T49 cross-linking, T50 `okf_navigate` traversal, T51 trace-and-iterate → GATE-3 (graduate
  or remove) → T52 lifecycle, T53 strategy memory. Harness-profile seam from the feeder draft was dropped
  (traversal binds the T11 model-profile seam). OKF repo cloned + graphify-indexed for grounding at
  `/Users/farhan/work/knowledge-catalog/okf/src/graphify-out/graph.json`. ADR-0022 records the decision.
  Pre-T45 diagnostic (ADR-0022 addendum): category signpost **decoupled** from graph_extraction (41-CUAD
  covers only 67% of ACORD gold; a direct corpus-appropriate classifier hits 91.6%).
- **LAST APPROVED (committed): T45** — ACORD gold-chunk labels. 57 test queries → 475 distinct gold chunks
  (grade≥2), exact qrel-corpus-id→chunk_id map (0 unmapped, validated against the T40 sidecar), any/all-gold
  readings, deterministic stratified debug split (7 debug / 50 held-out headline), + 475 qrels-induced silver
  category labels (`metadata.category`) for T46/T48. 7 tests, full suite 441+27. Artifact
  `data/eval/okf_gold.json` (gitignored, rebuildable).
- **LAST APPROVED (committed): T46** — OKF bundle compile (chunk-only). `okf/{document,enrich,compile,lint}.py`:
  gated+concurrent enrichment (category + one-line description per clause) via the new cheap `OKF_ENRICHMENT`
  model role (Gemma-4-26b-a4b, ADR-0023 — 100% category agreement with DeepSeek on the bench, ~4x cheaper),
  deterministic compile (tree root→category→clause, byte-faithful bodies from the T40 sidecar, index.md per
  level, content-hash gate, selective recompile), conformance linter. Real run: **3,931 clauses, 3,491
  categorized** into ACORD's 9 (440 `_uncategorized`, 1 fallback); lint clean. Category signpost **decoupled
  from graph_extraction** (ADR-0022). Registered `okf_compile` (category-3, no manifest). 10 tests, full suite
  451+27. Bundle gitignored at `data/acord/okf/bundle`.
- **LAST APPROVED (committed): T47** — reachability analyzer + ablation (`eval/reachability.py`, `eval/ablation.py`).
  Model-free ceiling over the ACORD gold: **any-gold 0.930, per-gold recall ceiling 0.776, all-gold 0.491**,
  connectivity 1.000; ablation shows **descriptions carry the ceiling** (cost 0.351), category tree −0.018, tags/
  frontier ~0. **DECISION (ADR-0022 addendum 2): 0.776 > bar 0.667 > baseline 0.379 → VIABILITY SETTLED, not
  go/no-go. T50 is mandated to REALIZE this proven ceiling by refinement+iteration; a shortfall = policy/agent bug
  to fix via T51, not a verdict against FR-K.** GATE-3a resolved = PROCEED; GATE-3 reframed to realization-vs-ceiling
  + cost-vs-control (no "remove-for-viability"). 9 tests, full suite 460+27.
- **LAST APPROVED (committed): T48** — category-label control arm (`eval/category_retrieval.py`). **recall@50
  0.134, containment 0.895** over the 57 test queries, 0 model calls. Category is a strong bucketer (gold in the
  right category 89.5%) but a useless localizer (no intra-category ranking → 0.134, below baseline 0.379). The
  containment→recall gap IS the within-bucket localization OKF descriptions provide → **the cheap control does
  not capture the lift; T50's description-localization is justified.** 4 tests, full suite 464+27.
- **LAST APPROVED (committed): T49** — cross-linking (`src/rag_wright/okf/links.py`), embedding-free
  (Option-1: shared distinctive-term edges, bounded degree ≤8, df>100 skipped). 20,033 edges, mean degree 5.10,
  broken-link 0.0000. **Links RAISE the reachability ceiling: per-gold recall 0.776→0.845, all-gold 0.491→0.614**
  → Option-2 embedding-kNN NOT needed (unused fallback). Linter extended to scan concept-body links; reachability
  gained a `cross_links` channel. 6 tests, full suite 470+27. **NEXT UP: T50** — the `okf_navigate` traversal
  capability that REALIZES the 0.845 ceiling (interpreter + PTC + dynamic sub-agents; RLM machinery T15/T36/T37).
- **LAST APPROVED (committed): T44** — govern the remaining capabilities. `capabilityInterface` on the 7
  ingestion→graph caps (parsing, rlm_chunking, embedding, graph_extraction, entity_disambiguation,
  entity_resolution, vision_to_text), grounded in the real callables; `rlm_method` stays ungoverned (required
  skill, not a data node). Vocabulary extended 6→14 (ingestion shapes). **GraphWright mirrored the 14 +
  confirmed all 7**; all 14 governed manifests re-emitted to `~/.air/registry` — the last force-fit hole is
  closed ("no false resolves" across the whole bound catalog). Full suite **434 passed + 27 skipped**.
  **NEXT UP: nothing running — user picks** (parked candidates unchanged: T41 composition experiments / LLM
  reranker first, T30/T31/T32/T34/T35/T39/T18).
- **LAST APPROVED (committed): T43** — emit `capabilityInterface` typed I/O on our authored ARD manifests
  (GraphWright ADR-0030 / our ADR-0021). Nominal typing on 7 retrieval→answer manifests; fusion out retyped
  `fused_chunk`→`chunk_id` (GraphWright's call). Full suite 426 passed + 27 skipped.
- **LAST APPROVED (committed): T42** — RLM/synthesis latent-hardening (worker query+slice threading enforced,
  non-stub multi-worker split test, messy fixtures). Full suite 412 passed + 27 skipped.
- **LAST APPROVED (committed): T28 (rebuild)** — RLM synthesis = **recursive descent + kept `_reduce` ascent**.
  A `SliceExtractor` seam (`SeamSliceExtractor` via `build_rlm_agent`) decomposes the candidate set (fresh
  `rlm_decomposer` per over-large group, `rlm_slice_worker` extracts per leaf); the Python `_reduce` fan-in
  (kept, ADR-0016) combines. **Recursion GATED** here (ADR-0019): the descent recurses past depth one
  (opaque candidate set, decomposer fires at >1 depth, code-driven fan-out). Per-slice tool use proven;
  every extract cited. Live extract+synthesize **passed** (real deepseek: $500M + Delaware + citations). 6
  passed + 1 skipped; full suite **379 passed + 26 skipped**. **The T15→T17→T28 rebuild is complete** —
  immediate follow-up (needs approval): populate `grantedSubagents` in the 3 manifests + re-emit + notify
  GraphWright.
- **LAST APPROVED (committed): T17 (rebuild)** — RLM chunking = **LLM semantic boundary discovery** (mandatory,
  no fixed-size). A `BoundaryDiscoverer` seam (`SeamBoundaryDiscoverer` via `build_rlm_agent`,
  deepseek-v4-pro) returns spans over `document.texts` items; `_finalize_chunks` is the deterministic-
  given-boundaries layer (join, cap, **T-CHK floor/merge/near-empty preserved over the discoverer's
  spans**, id, validate); summaries stay the concurrent flat map. Recursion **not gated** for chunking
  (ADR-0019). Live boundary-quality test (a coherent clause kept whole) **4/4** on deepseek-v4-pro,
  including a clean semantic partition. **Caveat:** boundary quality is guarded ONLY by the opt-in live
  test — CI does not cover it; run `-m model` before trusting a chunking-boundary change. 14 passed + 2
  skipped hermetic; full suite **379 passed + 26 skipped**. Logged **T34** (no document upsert path
  exists). **Next after approval:** T28 (RLM synthesis).
- **LAST APPROVED (committed): T15 (B′ rebuild)** — RLM skill + reusable machinery (recursive dynamic
  sub-agents). Grounding overturned ADR-0015 Q2's self-dispatch: a self-referential sub-agent is **not
  constructible** on `deepagents==0.6.12` (eager roster compile in `SubAgentMiddleware.__init__`) — I
  surfaced it and you approved **design B′** (the interpreter re-dispatches a fresh `rlm_decomposer` per
  level; `grantedSubagents` unchanged, no manifest churn). Delivered: rewritten `SKILL.md`, `agent.py`
  (`build_rlm_agent` + the two sub-agent configs + shipped `RLM_WORKFLOW_JS`), ADR-0015 Q2 correction,
  and the 4 ADR-0016 fail-if-absent tests (written first, confirmed red vs pre-rebuild, now green;
  recursion asserts `maxSplitDepth>=1` + >1 decomposer level + teeth tests for flat-workflow and
  sequential-dispatch). Plus an **opt-in real-model smoke test** that caught a real defect — the method
  wired as a lazy `skills=` source was never read, so a real model flatten-and-hardcoded; fix (ADR-0018)
  = method into the orchestrator **system prompt** + firmer SKILL.md; redesigned around an opaque working
  set so full leaf coverage proves derivation; **10/10** live after the fix. 6 passed + 1 skipped; full
  suite **379 passed + 26 skipped**. Deterministic chunk core and `_reduce` untouched. **Next after
  approval:** T17, then T28; then populate `grantedSubagents` + re-emit.
- **LAST APPROVED (committed): T-DISP** — `requires_dynamic_dispatch` typed flag on `skill_runtime`,
  the ARD-contract groundwork for the recursive-RLM rebuild (ADR-0015/0017). Landed: the flag +
  implies-interpreter validator on `ard.py`, populated `true` on the 3 RLM manifests, conformance tests,
  ADR-0017. Self-dispatch **confirmed** on `deepagents==0.6.12` (name-based roster; self-referential
  `rlm_decomposer` constructs without infinite recursion) — last pre-T15 grounding item cleared. Full
  suite **373 passed + 26 skipped**. **After approval + commit: T15 stays HELD** until GraphWright's
  applier (translating the flag into the interpreter trigger) lands; then the T15→T17→T28 rebuild runs,
  each through its own gate, re-emitting with `grantedSubagents=["rlm_decomposer","rlm_slice_worker"]`.
- **Done this session (all committed):** T22 (reranking, RAC-22 **b2 still OPEN** pending ACORD), T-CHK,
  T-SUM, **T23/T23b/T24/T25/T26/T27** (graph extraction → fusion), **T28** (RLM synthesis, pre-rebuild),
  **T29** (answer generator + vision-to-text). ARD split of FR-C.9 → `generation` + `vision_to_text`
  (approved spec change). **GATE-2 runs 1+2 not adjudicated** (ADR-0011: CUAD not a retrieval benchmark);
  store bar kept-on-ArcadeDB, not marked met. **Still open (both ask-first): T33** (ACORD → closes
  GATE-2a + RAC-22 b2) and the post-graph **GATE-2b** RLM keep-vs-optional call. ArcadeDB container
  stopped (idle until T25/T33 re-run).
- **Prior last-approved:** **T21** (FR-C.3/FR-Q.1, RAC-21) — Hybrid search. Query-side capability: embed the
  query once (dense + sparse over the query text, via the T19 `Embedder` seam), hand both vectors to
  the new **semantic** query-side `Store.hybrid_search(dense, sparse, *, k, filters)` seam method, which
  the ArcadeDB impl fuses server-side by RRF (`vector.fuse`, the T14-proven SQL) into one ranked
  `Candidate` list honoring metadata filters. Each leg fetched to a `DEFAULT_CANDIDATE_POOL=100` pool,
  filter applied post-fusion, cut to `k`. Registered under FR-C.3 (`function`) + ARD manifest authored
  and published. Live `-m store` verified real server-side RRF (c1 dense-leg + c2 sparse-leg winners
  fuse to the top, weak c3 excluded) and a `source_doc_id` filter. **recall@k on the golden set is
  measurable through this capability but the golden-set run is deferred to GATE-2/T22.** Full suite 285
  passed + 15 skipped. **GATE-1 (2026-07-09): kept the RLM chunker, T18 deferred → earns-its-cost call
  is at GATE-2/T22; if unproven, make the chunker optional, not dropped.**
- **Prior:** **T19** (RAC-19) Embedding (BGE-M3); **T17** (RAC-17) RLM chunking; **T16** Parsing.
  Grounded the hybrid SQL against the official ArcadeDB docs + live 26.7.1, then tested end to end
  through the arcadedb-python `query()` method: `SELECT expand(vector.fuse(vector.neighbors(...),
  vector.sparseNeighbors(...), {fusion:'RRF'}))` returns a sensible fused ranking (dense-leg + sparse-leg
  winners fuse above the weak record), and a metadata filter composes via `... FROM (<fuse>) WHERE
  source_doc_id=...`. **GATE-2 signal: works as documented, no lean toward LanceDB** (early A-T1 signal;
  the real GATE-2 decision is on the golden set at T21/T22). ADR-0007 A-T1 section. `-m store` 2 passed;
  default suite 244 passed + 7 skipped. Server pinned to stable 26.7.1 (grounding correction `179ba3f`).
- **Prior:** **T13** (RAC-13) store seam + ArcadeDB schema; **T12** (RAC-12) forced-structured seam test.
  Live probes through the seam confirmed the empirical profiles (ADR-0006): **DeepSeek V4 Pro**
  (`deepseek/deepseek-v4-pro`) honors the forced tool call while reasoning (bare `function_calling`,
  no `extra_body`); **Qwen 3.7 Plus** (`qwen/qwen3.7-plus`) reproduces risk 3 (`<400> ... tool_choice
  ... in thinking mode`) and is fixed by the structured-only `extra_body={"reasoning":{"enabled":
  false}}`. Slugs corrected from T11 placeholders (also general default `google/gemma-4-31b-it`).
  Opt-in live test `tests/foundation/test_model_seam_structured.py -m model` (2 passed); default suite
  242 passed + 2 skipped, hermetic. `model`/`store` markers registered; `conftest.py` gates opt-in
  tests and loads `.env`.
- **Prior:** **T11** (RAC-11) model-profile seam; **T10** (RAC-10) EDGAR RELATIONAL golden set.
  **19 questions (16 one-hop + 3 two-hop)** built from the human-verified set into
  `eval/golden/relational/set.json` (**committed** as a verified fixture — the derived answer keys,
  the only durable copy in git; the gitignored triage file `data/edgar/verification_set.json` stays
  out; ADR-0005). Registered as `Archetype.RELATIONAL` in the harness; honest property recorded:
  `public-filer-centric, multi-hop-modest`. Rebuild anytime: `uv run python -m eval.multihop` (the
  `resolution` fields, not the rebuild, are ground truth). Along the way T10 also built the T23b
  canonicalizer (`corpus/canonicalize.py`) and the EDGAR loose/lookup helpers (all committed;
  ADR-0004). (T9 golden harness + CUAD set, 2,032 questions, remains the prior CUAD baseline.)
- **Phase 2 (Tasks) ledger** was approved with revisions (RAC prefix; FR-I.6 line redrawn; EDGAR
  multi-hop split out as T10; DeepSeek V4 Pro first in the model-profile seam; ARD registration
  added as a cross-cutting definition of done, T6 emits the manifest skeleton and each bound
  capability authors and validates it), committed `ae593c3`.
- **Open question carried in:** none blocking Phase 3. Matching strategy for entity resolution
  (§16.3) and ArcadeDB index specifics (§16.4) resolve at their own tasks (T8 / T13).

---

## How to read this ledger

- **Status values:** `todo`, `in-progress`, `awaiting-approval`, `done`. A status becomes `done`
  only after your explicit approval, per the working loop in CLAUDE.md. One task, then stop.
- **RAC-N** is the acceptance criterion for task N. The RAG project uses the **RAC-** prefix (RAG
  Acceptance Criterion) deliberately, so a criterion never collides with the orchestration
  engine's `AC-N` scheme when the two projects are cross-referenced side by side. SPEC.md defines
  no pre-numbered acceptance criteria; each RAC here is grounded in the SPEC.md section 12
  checkpoints and the plan.md section 4 verification checkpoints.
- **Verify** commands run through `uv run` only (never a bare `python` / `pytest` / `pip`).
- **Gate rows** are branch points from plan.md section 2, not tasks; they decide what gets built
  next.
- **Grounding:** every Build task's step 2 (per the working loop) queries the `framework` graph
  for the exact symbol and signature before any library call is written. The "grounded surface"
  noted per task is the confirmed Phase 0 target, not a substitute for that per-task query.

## ARD registration (part of every capability's definition of done)

Two registrations, not one, and they must not be conflated.

Internal registration (T6) registers a built capability by its FR-C name with its contract, in
`src/rag_wright/capabilities/registry.py`. Its consumer is the Model Context Protocol (MCP) skill
surface (T31).

ARD registration is the Agentic Resource Discovery manifest that lets the GraphWright compiler
discover and bind the capability when it compiles the ingestion and query graphs from the
Orchestration Spec. Its consumer is the compiler's discovery and gap-analysis gate, not this repo.
The manifest is a `*.json` file conforming to GraphWright's `RegistryEntry` schema (GraphWright
T0.6 / ADR-0005), placed under the real registry root (its location is fixed on the GraphWright
side), then loaded and validated by `RegistryStore` (GraphWright T6.1). This is not a compiler step
run here; it is authoring a file the already-built store consumes.

One key, two consumers: the FR-C / FR-I / FR-Q name is both the internal registry key and the
anchor of the ARD manifest's URN, so the gap report and the registry speak one vocabulary. The T6
seam emits the manifest skeleton from what registration already knows (URN, kind, contract-derived
response bounds); each capability then authors what cannot be derived, above all the representative
queries, since those are the task-bearing field discovery ranks on (GraphWright T6.4), and a
capability with weak ones is one the compiler will not find.

Definition of done. Every task that builds a capability **the compiler discovers by representative
query** — one of the SPEC section 5 canonical slugs — carries the final acceptance bullet appended
below and is `done` only when its ARD manifest is authored (representative queries filled) and loads
under `RegistryStore(root)` with no `RegistryLoadError`. This covers the RLM skill (T15) and each
FR-C / FR-Q capability that has a section-5 slug (T16, T17, T19, T21, T22, T23, T23b, T24, T26, T27,
T28, T29). It does **not** cover the contracts, the seams (T11, T13), **the seam-bound ingestion
pipeline steps that have no section-5 slug — chunk write (T20, FR-I.3) and graph storage (T25,
FR-I.4): they bind the store seam and are wired into the ingestion graph by the compiler, not
discovered by query, so they register nothing and author no manifest**, the foundation tests, the
corpus and eval tasks, the T18 escalation path (it extends the T17 capability, not a new one), the
T30 caching optimization, or the T31 MCP surface.

**The rule (three categories — a canonical slug and an ARD manifest are NOT the same thing; the test is
"is it discovered by query at compile time," per GraphWright's RegistryStore verification, SPEC §5):**

1. **Query-discovered capabilities** — canonical slug **and** an ARD manifest. The compiler discovers
   them by representative query and binds them into the graphs. The FR-C / FR-Q capabilities, now **14**
   after the FR-C.9 split into `generation` + `vision_to_text` (ADR-0014).
2. **Seam-bound pipeline nodes** — **no** slug, **no** manifest (they bind the store seam, wired by the
   compiler, not discovered): chunk write (T20, FR-I.3), graph storage (T25, FR-I.4).
3. **Foundation derivations** — a canonical slug (a real capability with a contract + internal registry
   entry) but **no** manifest, because they run **before** compilation and their output is an input to
   the graph, not a node the compiler binds: `ontology_registry_derivation` (FR-C.8, T8).

A task carries the ARD bullet iff it is category 1. Stated once here, repeated per task so the working
loop enforces it.

---

## Status board

| ID | Task | Phase | FR | Status | Dep |
|---|---|---|---|---|---|
| T1 | Shared identifier contracts (`chunk_id`, `entity_id`) | 3 Contracts | FR-S.2, FR-S.3 | done | - |
| T2 | Provenance and confidence contracts | 3 Contracts | FR-S.4 | done | T1 |
| T3 | Chunk record contract | 3 Contracts | FR-I.3, FR-S.1 | done | T1, T2 |
| T4 | Ontology and extraction-target models | 3 Contracts | FR-C.8 | done | T1, T2 |
| T5 | Graph extraction contract (real OpenIE extension seam) | 3 Contracts | FR-C.6, FR-I.4 | done | T2, T4 |
| T6 | Capability registration seam | 3 Contracts | FR-S.5, contract use | done | T3 |
| T7 | CUAD + EDGAR corpus acquisition and subset | 4 Foundations | Phase 0 | done | - |
| T8 | Ontology and registry derivation | 4 Foundations | FR-C.8, FR-C.7 | done | T4, T7 |
| T9 | Golden eval harness + CUAD-annotation archetype sets | 4 Foundations | §12 | done | T7 |
| T10 | EDGAR-derived relational + multi-hop question construction | 4 Foundations | §12, §8 | done | T7, T9 |
| T11 | Model-profile seam (DeepSeek V4 Pro first for structured-under-reasoning) | 4 Foundations | assumption 2, tech stack | done | T6 |
| T12 | A-T2 forced-structured-output foundation test on DeepSeek V4 Pro (+ ADR) | 4 Foundations | FR-C.6 dep, risk 3 | done | T11 |
| T13 | Store seam + ArcadeDB schema and hybrid indexes | 4 Foundations | FR-S.1, FR-S.5 | done | T3, T6 |
| T14 | A-T1 ArcadeDB `vector.fuse` hybrid foundation test | 4 Foundations | FR-C.3 dep, risk 1 | done | T13 |
| T15 | RLM skill + machinery (recursive dynamic sub-agents; B′) | 4 Build RLM | FR-C.10 | done (rebuild) | ADR-0015/0016/0017/0018 |
| T16 | Parsing (Docling) | 4 Build write | FR-C.1 | done | T3 |
| T17 | RLM chunking (LLM semantic boundary discovery) | 4 Build write | FR-I.1 | done (rebuild) | T15, T16, T11, ADR-0019 |
| T18 | Small-to-large chunking escalation | 4 Build write | FR-I.2 | deferred (GATE-1) | **GATE-1**, T17 |
| T19 | Embedding (BGE-M3; concurrent + backpressure) | 4 Build write | FR-C.2, FR-I.3, FR-I.6 | done | T16 |
| T20 | Chunk write + incremental upsert (content-hash gated) | 4 Build write | FR-I.3, FR-I.5 | done | T13, T17, T19 |
| **GATE-1** | **RLM chunker A/B go / no-go** | 4 Build | §12, plan §2 | run: keep RLM, defer T18 → GATE-2 | T17, T19, T9 |
| T21 | Hybrid search (server-side RRF, metadata filters) | 4 Build read | FR-C.3, FR-Q.1 | done | T14, T20 |
| T22 | Reranking (cross-encoder precision gate) | 4 Build read | FR-C.4, FR-Q.2 | capability done; RAC-22 b2 open | T21 |
| **GATE-2** | **Recall-bar: ArcadeDB hybrid vs LanceDB fallback** | 4 Build | FR-S.5, plan §2 | run 1 not adjudicated (ADR-0011); keep ArcadeDB | T21, T22, T9, T10 |
| T-CHK | RLM chunker degenerate-split fix (T17 bug; ask-first) | 4 Build write | FR-I.1 | done | T17 |
| T-SUM | Concurrent summarization (T17 enhancement, FR-I.6 pattern) | 4 Build write | FR-I.1, FR-I.6 | done | T17 |
| T-DISP | `requires_dynamic_dispatch` typed flag on `skill_runtime` (RLM-rebuild groundwork) | 4 Build RLM | FR-C.10, ADR-0017 | done | ADR-0015, ADR-0017 |
| T33 | ACORD retrieval half of the JOINT query-graph golden-eval (ingest + recall bar + chunk_read) | 4 Foundations | §12, FR-Q | retrieval half EXECUTED + baseline measured (recall@50 0.379, grounded bar 0.667 not met — known limitation, T41); graded conjunction run handed to GraphWright | T21, T22, T38, T40 |
| T40 | Chunk-text sidecar: persist full chunk text keyed by chunk_id at ingest, same content-hash gate as the index | 4 Build write | FR-I.3 | done | T17, T19, T20 |
| T38 | `chunk_read` governed capability (rehydrate chunk_ids → chunks-with-text) — reads the T40 sidecar | 4 Build read | FR-Q | done | T40 |
| T39 | Extraction-depth grading (cited-but-thin) — needs answer-span ground truth ACORD lacks | 5 Integrate | §12 | todo (logged follow-on; not this milestone) | T33 |
| T41 | Retrieval-quality: baseline-with-diagnosis recorded (0.38, query-representation gap, graph leg doesn't help); composition-experiment backlog logged (LLM reranker / category label-retrieval / base-pool sizing) | 5 Integrate | FR-C.3 | investigation concluded → parked as post-integration backlog | T33 |
| T42 | RLM/synthesis latent-hardening pass: enforce worker query+slice threading + non-stub multi-worker split test + messy-fixture discipline | 5 Integrate | FR-Q.5 | done (query→worker prompt; slice→dispatch, both files drift-synced; partition + messy tests) | T28, T38 |
| T43 | Emit `capabilityInterface` typed I/O on authored ARD manifests (GraphWright ADR-0030) — schema + author path + declare the 7 (5 + graph_query + generation) interfaces | 5 Integrate | FR-C, ARD reg | done (ADR-0021) — nominal typing; `CapabilityInterface` + `NOMINAL_TYPE_VOCABULARY` (6 names) in ard.py, threaded through author path, 7 manifests declared; 14 tests, suite 426+27. **Contract closed:** fusion out retyped `fused_chunk`→`chunk_id` (GraphWright's call, `fusion→chunk_read` type-checks); all 15 manifests re-emitted to `~/.air/registry` | T6, T38, ADR-0005 |
| T44 | Govern the remaining capabilities: `capabilityInterface` on the 7 ingestion→graph caps (GraphWright ADR-0030, closes the ungoverned force-fit hole) | 5 Integrate | FR-C, ARD reg | done (ADR-0021 addendum) — vocabulary extended 6→14; parsing/rlm_chunking/embedding/graph_extraction/entity_disambiguation/entity_resolution/vision_to_text declared; `rlm_method` stays ungoverned (required skill, no data node); GraphWright mirrored the 14 + confirmed; all 14 governed manifests re-emitted. 8 tests, suite 434+27 | T43 |
| T34 | Document update/upsert: on doc change, delete a document's chunks + graph nodes + index entries, then re-chunk and re-insert | 5 Integrate | FR-I.5 | todo (finding) | T17, T20, T25 |
| T35 | Concurrent-batch ingestion throughput design (KI-1 correctness floor already always-on) | 5 Integrate | OQ8, ADR-0020 | todo (throughput design; floor landed) | T17, T28 |
| T36 | Working-set via runtime tool `tools.workingSet()` (not message-embedded JSON) + T17/T28 re-validation + skill rename | 5 Integrate | FR-C.10, FR-I.1, FR-Q.5 | done | T17, T28 |
| T37 | Recursion completeness + load faithfulness: coverage tail + load assertion in the skill's interpreter WORKFLOW | 5 Integrate | FR-C.10, FR-Q.5, FR-I.1 | done (both sides; GraphWright node bind_run 6/6 full-load-and-full-depth) | T36 |
| T23 | Graph extraction (contract + spaCy NER; concurrent + backpressure) | 4 Build graph | FR-C.6, FR-I.4, FR-I.6 | done | T5, T8, T16 |
| T23b | Mention disambiguation and canonicalization (normalize, reject, cluster) | 4 Build graph | FR-C.7 | done | T23 |
| T24 | Entity resolution (closed-world to EDGAR CIK) | 4 Build graph | FR-C.7 | done | T8, T23b |
| T25 | Graph storage (nodes/edges carry `chunk_id`, gated) | 4 Build graph | FR-I.4, FR-I.5 | done | T13, T23, T24 |
| T26 | Graph query (cited `chunk_id`s, `entity_id`s, confidence) | 4 Build graph | FR-C.5, FR-Q.3 | done | T25 |
| T27 | Fusion (union/dedup on `chunk_id`, capped) | 4 Build graph | FR-Q.4 | done | T22, T26 |
| T28 | RLM synthesis (recursive descent + kept _reduce ascent) | 4 Build RLM | FR-Q.5 | done (rebuild) | T15, T27, ADR-0019 |
| T29 | Answer generator (grounded, cited, abstains) + vision-to-text | 4 Build RLM | FR-C.9, FR-Q.6 | done | T11, T27 |
| T30 | Prefix + result caching | 4 Build RLM | §13 P3, §16.7 | todo | T28, T29 |
| T31 | MCP skill surface (governed skills over MCP) | 5 Integrate | FR-S.5 | todo | T6, T22, T26 |
| T32 | End-to-end scenarios + per-source ablation | 5 Integrate | §12, §15 | todo | T29, T31 |

**FR-K — Embedding-free OKF navigation (experimental, gated). SHELVED 2026-07-23 (ADR-0025):** the recall
investigation showed the *realizable ranking* recall (~0.38, method-invariant, matching the recorded two-leg
0.379) governs, not the *reachability* ceiling (0.776) FR-K rested on. Superseded by **FR-R** below. Committed
T45-T50 stay in history; not built further. Original spec retained for the record.

Corpus-neutral capability; ACORD is the first validation corpus. First execution slice is T45-T48 → GATE-3a (the
per-corpus reachability kill-switch); the whole program is specified now but built behind that gate. Detail
entries below.**

| ID | Task | Phase | FR | Status | Dep |
|---|---|---|---|---|---|
| T45 | ACORD gold-chunk labels for reachability scoring (extends T9/T33 eval assets) | 5 Integrate | §12, FR-K.8 | done | T33 |
| T46 | OKF bundle compile: chunk-only, category tree via corpus-appropriate classifier, LLM one-line descriptions (no re-chunk; sidecar-fed) | 5 Integrate | FR-K.1, FR-K.2, FR-K.4 | done | T40, T45 |
| T47 | Reachability analyzer + signpost ablation (deterministic, model-free) | 5 Integrate | FR-K.8 | done — **ceiling proven: any-gold 0.930, per-gold recall ceiling 0.776 > bar 0.667 > baseline 0.379** (ADR-0022 addendum 2) | T46 |
| T48 | Category label-retrieval control arm (T41 backlog item, run as the control) | 5 Integrate | FR-C.3 | done — **control recall@50 0.134 (containment 0.895): category is a strong bucketer, useless localizer → OKF traversal's description-localization is JUSTIFIED** | T46 |
| **GATE-3a** | **Reachability ceiling (per-corpus kill-switch): is gold reachable through signposts, and does the coarse-label control already capture the lift?** | 5 Integrate | §13, FR-K.8 | pending | T47, T48 |
| T49 | Cross-linking from measured clause-relation structure | 5 Integrate | FR-K.3 | done — **embedding-free lexical links RAISE the ceiling: per-gold 0.776→0.845, all-gold 0.491→0.614; Option-2 kNN NOT needed** | T46, T41 |
| T50 | `okf_navigate` traversal capability (interpreter + PTC + dynamic sub-agents) | 5 Integrate | FR-K.5, FR-K.6 | **in progress — WORKS: model writes the workflow from an okf_navigate Skill, 11/11 on tpb. (b1 DONE, ADR-0024) reader judging is now a Python-parallel `judgeBodies` PTC tool (asyncio fan-out via the seam, not a sub-agent — the interpreter is single-JS-engine per ADR-0020): tpb 163s→110s, LoL >15min→~5.8min, structured output back on the seam. Big-category coverage (LoL 0/12: budget 50 of 489 fills in index order) is the (b2) target. Pending: (b2) deep-hierarchy clustering, hard-query generalization, ARD manifest** | T46, T47, T49, T35 |
| T51 | Single-query trace-and-iterate harness (the tuning loop) | 5 Integrate | FR-K.8, §12 | todo (behind GATE-3a) | T47, T50 |
| **GATE-3** | **FR-K graduate or remove: OKF traversal vs the category-label control** | 5 Integrate | §13, §15 | pending | T47, T50, T51, T48 |
| T52 | Bundle lifecycle: incremental recompile, update, delete (shares the T40 gate) | 5 Integrate | FR-K.7 | todo (post-GATE-3) | GATE-3, T34 |
| T53 | Strategy memory: store and reuse verified navigation strategies | 5 Integrate | FR-K.9 | todo (post-GATE-3) | GATE-3, T50, T51 |
| T54 | graph_extraction ontology extension (not FR-K): make `ClauseCategory` cover non-CUAD corpora — T8-derived or proposal-seam, NOT a crosswalk (can't invent missing coverage) | 4 Build graph | FR-C.6, FR-C.8 | deferred (ask-first; data-model change) | T8, T23, ADR-0022 |

**FR-R — Function/Property retrieval (ADR-0025; supersedes FR-K).** Solve the ACORD recall bar the way a
paralegal does: every query = a **FUNCTION** (clause type) + a **PROPERTY** (qualifier). Operative-span index →
local function classifier → property graph → rerank for the fuzzy tail; spans + property graph in ArcadeDB
(clauses stay source-of-truth in the clause OKF bundle). Capability-half; pipeline composition is GraphWright's.
Built behind **GATE-R** (clears the 0.667 recall bar / beats the 0.379 two-leg baseline).

**Retrieval design (T58, converged 2026-07-23 — see memory `retrieval-design-t58`):** query decomposition = ONE
LLM structured call → `functions` (enum-bound to the 44 FUNCTION_LABELS, top-1..2), `properties` (enum-bound to
CLOSED_VOCAB), and a **HyDE hypothetical clause**. Flow: function-filtered ranked hybrid search → spans → parent
clauses → property **soft-boost** (hard-filter is an ablation only, never hard-AND-reject) → rerank → recall@50.
**The function gate is a HARD recall ceiling** = (gold bucketed into the query's function) × (query→function
correct), bounded by classifier per-class RECALL not F1 (Warranty 0.98 / Cap 0.79 / Uncapped 0.66); **measure the
single-function ceiling FIRST** (model-free), then **union confusable siblings** (T60 confusion matrix, e.g.
Cap↔Uncapped) where needed. **HyDE for BOTH** (do not miss either): (a) dense ranking in step 2 (only moves
recall@50 on pools >50 clauses — the big LoL/Indemnification pools); (b) a boundary-aligned Function vote by
feeding the hypothetical clause to the local LegalBERT classifier. HyDE folded into the one decomposition call.

| ID | Task | Phase | FR | Status | Dep |
|---|---|---|---|---|---|
| T55 | Operative-span segmenter: re-chunk each clause → operative spans (enumeration/semicolon markers + spaCy legal-sentence, deterministic; byte-faithful reconstruction, size floor), each pointing to its parent clause. Output = `Span` records for ArcadeDB (text, `parent_chunk_id` + parent OKF path, function slot, dense/sparse emb slot) | 5 Integrate | FR-Q, §GATE-2 | **done — (a) `spans/segment.py`: deterministic, byte-faithful tiling, enumeration/sentence split with abbrev/section-ref guards + sub-floor merge (9 tests; run-ons split into operatives). (b) ArcadeDB `Span` type: dense `LSM_VECTOR` + sparse `LSM_SPARSE_VECTOR`, `upsert_span`, `span_hybrid_search` (RRF-fused, parent pointer, `function` filter) + `SpanRecord` contract (live-tested; Chunk path intact). Store POPULATION (embed+classify+upsert) is downstream (uses T56)** | T13, T40, ADR-0025 |
| T56 | Local FUNCTION classifier over spans: single-label {41 CUAD `ClauseCategory` + NONE}; linear head on frozen embeddings (escalate to LegalBERT only if F1 lags); tag + index function-bearing spans | 5 Integrate | FR-C.3, FR-Q | **done — CUAD→operative-span labels (`cuad_labels.py`, max-overlap type else NONE, contract-disjoint split). Frozen linear head (`function_classifier.py`) macro-F1 0.43 but Cap-vs-Insurance F1=0 (frozen embeddings can't separate them) → escalated to **LegalBERT** (`legalbert_classifier.py`, fine-tuned `legal-bert-base-uncased`, class-weighted CE, 4 epochs MPS). Held-out macro-F1 **0.544** / micro 0.706; Insurance F1 0.91, Cap 0.71, Governing Law 0.92; **Cap↔Insurance confusion eliminated** (Cap→Insurance 0, Insurance→Cap 3/95). Adopted as the function classifier (ADR-0025). Hermetic tests green (both classifiers); weights gitignored. Span store POPULATION is downstream (T58)** | T55, T8 |
| T57a | PROPERTY schema as a contract (**schema review gate — passed**): demand-derived from the 57 ACORD queries. Two-tier `PropertyDimension` (6 cross-cutting + 13 function-specific), `CLOSED_VOCAB` (15 closed enums + 4 open-valued) with an AMBIGUOUS `other` escape; `PropertyAssertion(GraphFact)` (FR-S.4 provenance + span citation); `ClausePropertyRecord` (function ∈ taxonomy, clause-anchored). Extended FUNCTION taxonomy (`contracts/function.py`): CUAD-41 + Indemnification/IndirectDamagesWaiver/WarrantyDisclaimer = 44, distinct from `ClauseCategory` (extraction ontology, ADR-0002 not reopened). FOLIO IRIs on 15/16 clause types + 4 carve-out subjects (naming only, no OWL; FIBO rejected as out-of-domain) | 5 Integrate | FR-C.6 | **done — `contracts/{function,property}.py`; 10 tests (vocab validation, AMBIGUOUS escape, anchoring, taxonomy/FOLIO coverage); ADR-0026** | T55, ADR-0025 |
| T60 | Extend function taxonomy + **retrain T56** over the 44 classes: source labels for the 3 new classes (LLM-labeled bootstrap over CUAD indemnification/waiver/disclaimer spans + ACORD graded pairs), retrain LegalBERT, re-measure per-type F1 + confusion (sub-gate: the 3 new classes reach usable F1; Cap↔Insurance stays clean) | 5 Integrate | FR-C.3, FR-Q | **done — label bootstrap over CUAD (`label_new_functions.py`: keyword pre-filter + DeepSeek confirm via seam, contract-disjoint; 400 Indemnification / 400 Warranty Disclaimer / 73 Damages Waiver — ACORD untouched). 45-class LegalBERT (44 fn + NONE). Sub-gate MET: Indemnification F1 0.70, Warranty Disclaimer 0.95, Damages Waiver 0.70; Cap→Insurance 0, Insurance→Cap 1/95. macro-F1 0.586 / micro 0.744; all 57 query functions covered. **Production trainer** (`train_legalbert_function.py`): best-model retention (`load_best_model_at_end`+eval_loss), early stopping, resume-from-weights (constant low LR) + adopt-only-if-better guard, dry-run isolation — validated by a continuation (eval_loss 1.31→1.196, kept ep2 over worse ep3). Reusable `util/concurrent.map_concurrent`+`Progress`. Weights/backup gitignored** | T56, T57a |
| T57b | Targeted PROPERTY extractor (reuse Extractor seam / DeepSeek via model-profile) → emits `ClausePropertyRecord` per clause span | 5 Integrate | FR-C.6 | **done — `spans/property_extractor.py`: function-aware `FUNCTION_DIMENSIONS` map; pure `build_record` (scope-filter + out-of-vocab→AMBIGUOUS coercion + provenance/span-cite/FOLIO); `SeamPropertyExtractor` (DeepSeek via seam, None-retry, injectable runnable). 5 hermetic tests + live smoke (mutual cap → mutuality/carve_out×2/cap_basis/cap_quantum, all correct). Reuses `map_concurrent` at ingestion (T57c)** | T57a, T60 |
| T57c | Property-graph population: Clause node ↔ typed property edges + shared value nodes (`Exception`/`Subject`/`PartyScope`), reuse graph_storage / entity_resolution, NOT the generic entity graph | 5 Integrate | FR-C.6, FR-C.7 | **done (write-path) — ArcadeDB `Clause`/`PropertyValue`/`HasProperty` schema; `write_property_graph` (one txn, provenance on every edge), `property_graph_counts` + `clause_property_values` readback. ONE `PropertyValue` type keyed by (dimension,value); dedup is a deterministic upsert (controlled vocab already canonical → NO entity_resolution clustering needed). Idempotent via content-hash gate (clause_id embeds hash) — this dialect rejects `DELETE EDGE`/`CREATE EDGE UPSERT` (recorded). 1 hermetic + 4 live `-m store` (schema, write+readback+provenance, shared-node dedup, idempotent). Full-corpus population RUN is T58's first step** | T57b, T25, T24 |
| T58a | Full-corpus population (`populate_property_store.py`: segment→classify→embed→upsert_span, then extract→write_property_graph; clause-level; PHASE=spans/extract/all; batched embed) + **single-function gate ceiling** (`eval/function_ceiling.py`, model-free) | 5 Integrate | FR-Q | **done (phase 1 + ceiling) — phase 1: 14,553 spans / 3,931 clauses / 3,887 non-NONE cached. CEILING = best-single 0.939 / union-top-2 0.992 (bar 0.667, baseline 0.379); only 4/57 below bar, ALL recovered by union-top-2 (confusion pairs match T60: No-Solicit↔Non-Compete, non-reliance↔Warranty-Disclaimer, bodily-injury↔Uncapped). Reachability is NOT the ceiling → recall is now a RANKING problem in small pools. Phase 2 (extraction) running next** | T56, T57c |
| T58b | Query decomposition (ONE LLM call → functions enum top-1..2 + property constraints + HyDE clause) + retrieval composition: function-filtered (union-top-2) hybrid search → property soft-boost → HyDE-dense + rerank → recall@50 | 5 Integrate | FR-Q.3, FR-Q.4 | **in-progress — ABLATION `eval/function_rerank.py` (oracle function, pure filter → BGE-rerank, no property): recall@50 ≈ 0.52 (vs baseline 0.379, bar 0.667). Finding: function+rerank beats baseline but the property leg is LOAD-BEARING — bare-function large-pool queries collapse to ~k/pool because the reranker can't split gold from same-type non-gold; property discriminates within-pool. Robust phase-2 runner built (5 controls: live stdout X/N echo, provider throughput routing ADR-0027, per-clause crash-safe writes, RESUME/FRESH, EXTRACT_MODEL switch) + `store.clear_property_graph`. **LEDGER CORRECTION (2026-07-28): the "paused at 34/3887" note was STALE — the phase-2 property extraction (DeepSeek V4 Pro) was RESUMED and RAN TO COMPLETION on Jul 24 12:37 (progress log 3844/3845 100%, ~41 min; DB `ragwright_acord_pivot` = ~3,886 clauses / 393 values / 8,597 edges, per `graph-layer-two-graphs-state`). This flat property graph is now SUPERSEDED by the typed Unified Contract KG (KG-0..KG-6, ADR-0033): KG-2 re-extracts with granite-4.1-8b into the typed schema and the flat `HasProperty` graph is retired.** | T58a, T29 |
| T59 | Reranker for the fuzzy/comparative/novel property tail: `BGEReranker` default; LLM-rerank via the seam optional | 5 Integrate | FR-Q | todo | T58, T29 |
| T61 | Deterministic property-grounding judge (`spans/property_grounding.py`, ADR-0028): checks an EXTRACTED value's surface cue is in the clause text. **DOUBLE DUTY** — (1) Flash→Pro escalation trigger for the extraction cascade (wired: `ESCALATE_MODEL`); (2) **permanent quality gate** on the final graph (`reground`, runner `GATE=1`) — a STANDING gate to keep value nodes clean, not just a Flash mitigation. Lexically-anchored dims only (semantic dims not checkable) | 5 Integrate | FR-C.6 | **done (judge + cascade wiring) — 5 hermetic tests (catches Flash's `fraud` hallucination); 3-model bench `scripts/compare_extraction_models.py`. OPEN: decide whether to make the `reground` quality gate STANDING on every write** | T57b |
| **GATE-R** | **Does function + property + rerank clear the recall bar? recall@50 ≥ 0.667 (+ nDCG@10 diagnostic) vs the 0.379 two-leg baseline, ablated by stage (`eval/acord_retrieval`)** | 5 Integrate | §13, §GATE-2 | pending | T55-T59 |

Extras (§3.2: multi-vector, typed-functional RLM, CLIP embedder, canonical skeleton, domain
LoRA) are **out of scope / ask-first** and are not scheduled here.

---

## Phase 3 — Contracts (gates all Build tasks)

Contracts live in `src/rag_wright/contracts/` (Pydantic v2, per ADR-0001). These double as the
registered capability contracts (contract use). Identifier schemes are load-bearing and fixed
here; changing either later is ask-first (FR-S.2, FR-S.3; SPEC §14).

### Task T1: Shared identifier contracts (`chunk_id`, `entity_id`)

**Description:** Define the two load-bearing identifiers as Pydantic models. `chunk_id` is
computed from source-document identifier + chunk index + content hash; `entity_id` is the
canonical registry identifier. Fixed before anything is built (FR-S.2, FR-S.3).

**RAC-1:**
- [x] `chunk_id` is computed deterministically from `(source_doc_id, chunk_index, content_hash)`;
  identical inputs yield an identical `chunk_id` (determinism test). `ChunkId.of()` computes the
  content hash (SHA-256); `source_doc_id` is constrained to `[A-Za-z0-9._-]` so the `:`-delimited
  `.value` is parse/match-safe for provenance and citation.
- [x] `entity_id` is a canonical-registry identifier type (EDGAR CIK shaped, per ADR-0002). The
  contract is **strict**: canonical 10-digit zero-padded form only; raw EDGAR forms (CIK-prefixed,
  unpadded, integer) are rejected and normalized upstream at the T8 registry loader.
- [x] Both models are frozen/validated; malformed inputs are rejected.

**Verification:** `uv run pytest tests/contracts/test_identifiers.py` — 40 passed.

**Dependencies:** None. **Scope:** S. **Status:** done (commit pending).
**Files:** `src/rag_wright/contracts/identifiers.py`, `tests/contracts/test_identifiers.py`
**Note:** Load-bearing; scheme change is ask-first (SPEC §14). `EntityId` normalization deferred to
T8 by review decision (contract stays strict; the loader owns the world's mess).

### Task T2: Provenance and confidence contracts

**Description:** Every stored unit carries provenance (source document + chunk for text); every
graph-derived fact carries a confidence tag (FR-S.4).

**RAC-2:**
- [x] Text-bearing models require source-document + chunk provenance. `Provenance` carries an
  explicit `source_doc_id` plus `chunk_id`; `Provenance.of(chunk_id)` derives the source doc.
- [x] Graph-fact model requires `confidence ∈ {EXTRACTED, INFERRED, AMBIGUOUS}`; any other value
  is rejected. `GraphFact` base carries `provenance` + `confidence` (the FR-I.4 node/edge unit).

**Verification:** `uv run pytest tests/contracts/test_provenance.py` — 18 passed.

**Dependencies:** T1 (uses `ChunkId`). **Scope:** S. **Status:** done (commit pending).
**Files:** `src/rag_wright/contracts/provenance.py`, `tests/contracts/test_provenance.py`
**Note:** Enforces "no claim without a citation" downstream (FR-Q.6). `source_doc_id` is kept
explicit (not a derived property, unlike a purity collapse) by review decision: it enables
store-level source filtering (FR-Q.1), decouples provenance from the `chunk_id` string format, and
avoids parsing the identifier — safe because the consistency validator fires on every construction
and deserialization path (raw / `model_validate` / `model_validate_json`), closing drift.

### Task T3: Chunk record contract

**Description:** The chunk record contract holds `chunk_id`, the summary, the dense summary
vector, the sparse full-text vector, keywords, entities, and source metadata (FR-I.3), all in one
record per chunk for the single store (FR-S.1).

**RAC-3:**
- [x] `ChunkRecord` carries `chunk_id`, summary, dense summary vector, sparse full-text vector,
  keywords, `entity_mentions`, source metadata, and validates each. No raw full-text field (FR-I.3
  enumerates summary + vectors + metadata; full text lives in the parse manifest keyed by
  `chunk_id`, FR-I.1).
- [x] Vector field shapes/types are declared so the store schema (T13) can bind them:
  `BGE_M3_DENSE_DIM = 1024` (single authoritative dense dimension), `dense_vector` fixed length +
  finite; `sparse_vector: dict[int, float]` (store-bindable token-id index -> weight) coerced from
  BGE-M3's string keys, non-integer keys rejected (not dropped); `source_metadata` constrained to
  JSON scalars for filterability (FR-Q.1).

**Verification:** `uv run pytest tests/contracts/test_chunk_record.py` — 27 passed.

**Dependencies:** T1, T2. **Scope:** S. **Status:** done (commit pending).
**Files:** `src/rag_wright/contracts/chunk.py`, `tests/contracts/test_chunk_record.py`
**Note:** By review decision the extracted-entities field is named `entity_mentions` (unresolved
surface forms for retrieval metadata), NOT `entities` — it must not be joined to graph `entity_id`s
(resolution is FR-C.7 / T24); the name kills the fragmentation ambiguity at the seam.

### Task T4: Ontology and extraction-target models

**Description:** Pydantic ontology models for the 41 CUAD clause categories plus party and entity
types (FR-C.8, §16.2, ADR-0002). This is the contract shape; the derivation that populates it is
T8.

**RAC-4:**
- [x] The 41 CUAD clause categories (`ClauseCategory`, authoritative, exactly 41) plus party/entity
  types (`EntityType`) and relationship types (`RelationshipType`) are expressed as Pydantic models.
  T4 owns ontology **structure**; **T8 is the authority for entity/relationship membership** (a
  provisional seed ships now: `ORGANIZATION`/`PERSON`, `CONTRACTS_WITH`/`AFFILIATE_OF`).
- [x] A fact that does not conform to the ontology is rejected (enum-typed fields on `EntityNode`,
  `ClauseFact`, `RelationshipFact`; off-ontology category/type/relationship raises).

**Verification:** `uv run pytest tests/contracts/test_ontology.py` — 19 passed.

**Dependencies:** T1, T2. **Scope:** M. **Status:** done (commit pending).
**Files:** `src/rag_wright/contracts/ontology.py`, `tests/contracts/test_ontology.py`
**Note:** Review decisions — (1) ship the provisional entity/relationship seed, T8 finalizes
membership from the Data Catalog; (2) `RelationshipFact` is **directed** (`source_ref -> target_ref`)
so T8 can add directed corporate-hierarchy types without reopening it; (3) endpoints are
**pre-resolution mention refs**, self-loop rejected at the ref level here, while the post-resolution
same-`entity_id` self-loop check belongs with entity resolution (T24); (4) a `CONTRACTS_WITH` fact
references its agreement via required provenance (`source_doc_id`), making shared-party multi-hop
answerable.
**Files:** `src/rag_wright/contracts/ontology.py`, `tests/contracts/test_ontology.py`

### Task T5: Graph extraction contract (real OpenIE extension seam)

**Description:** The contract graph extraction (T23) emits: conforms to the ontology (T4), carries
`chunk_id` provenance and a confidence tag (T2), and exposes the OpenIE path as a **real seam in
the contract**, an extractor interface that additional extractors bind, **not a comment or a
TODO**. Adding the deferred Open Information Extraction (OpenIE) path later plugs a new extractor
into the seam without reopening or editing the capability (plan §1 note, FR-C.6, FR-I.4).

**RAC-5:**
- [x] Extraction result conforms to the ontology and carries originating `chunk_id` + confidence.
  `ExtractionResult` bundles `ClauseFact`/`RelationshipFact` (T4, provenance+confidence) plus typed
  `EntityMention`s, and a validator anchors every fact to the result's `chunk_id` (FR-I.4).
- [x] The contract defines an extractor interface (a real abstraction the capability iterates
  over): `Extractor` is a `@runtime_checkable Protocol`; `run_extractors(...)` iterates a list.
  A new extractor (OpenIE) is added by appending to that list — no change to the seam.
- [x] A stub second extractor is bound behind the seam in test and its output validates: two stubs
  (`_ClauseStub` ships-first, `_OpenIEStub` the future path) merge with no change to
  `run_extractors`/`ExtractionResult`, proving the seam is load-bearing.

**Verification:** `uv run pytest tests/contracts/test_extraction.py` — 11 passed.

**Dependencies:** T2, T4. **Scope:** S. **Status:** done (commit pending).
**Files:** `src/rag_wright/contracts/extraction.py`, `tests/contracts/test_extraction.py`
**Note:** Adding the OpenIE dependency itself is ask-first (ADR-0001, risk 10); the seam makes it a
plug-in, not a reopen. Review decisions: keep typed `EntityMention` (captures standalone entities,
distinct from T3's retrieval-metadata mentions); `Protocol` over `abc.ABC` (conform-without-inherit)
— `runtime_checkable` isinstance is a presence check only, output conformance is enforced by
`ExtractionResult` validation. `EntityMention.text` and relationship `source_ref`/`target_ref` are
the same surface-form notion → T24 resolves both channels as one stream (see T24).

### Task T6: Capability registration seam

**Description:** A registry seam where each built capability registers under its FR-C name with
its contract (contract use, FR-S.5). Registration is what the MCP skill surface (T31) later
exposes. The same seam also emits the capability's Agentic Resource Discovery (ARD) manifest
skeleton (see "ARD registration"), so the internal registry and the ARD manifest share the FR-C
name as their one key and stay coherent.

**RAC-6:**
- [x] A capability registers by canonical slug with its contract; lookup by slug returns it.
- [x] Registering an unknown (non-canonical, per SPEC §5) or duplicate name is rejected; unknown
  lookup raises. The name is the cross-spec join key — a canonical slug (`hybrid_search`, never
  `fr-c-3`), enforced against the mirrored slug set.
- [x] Registration emits an ARD manifest skeleton conforming to the mirrored GraphWright
  `RegistryEntry` schema (URN `urn:air:dreamai.io:rag_wright:<slug>`, kind, callable response bounds),
  leaving the authored fields (representative queries 2-5, trust attestations) for the capability
  task. The skeleton is a DRAFT that cannot validate as a `RegistryEntry`; only
  `ManifestSkeleton.author(...)` yields the loadable entry, kept out of the glob'd path until then.

**Verification:** `uv run pytest tests/capabilities/test_registry.py` — 27 passed.

**Dependencies:** T3 (uses a contract type). **Scope:** M. **Status:** done (commit pending).
**Files:** `src/rag_wright/capabilities/ard.py`, `src/rag_wright/capabilities/registry.py`,
`tests/capabilities/test_registry.py`, `docs/adr/0003-ard-registration-and-capability-kinds.md`
**Note:** Review decisions in **ADR-0003** — emit JSON, no `graphwright` dependency; the ADR-0005
schema is a shared wire-format contract mirrored by deliberate duplication (cross-repo coordination
point) with a RAG-side conformance test; kind is explicit per capability by the binding rule
(`mcp_tool` = MCP boundary, `function` = in-process node, `agent_skill` = loaded knowledge); the
canonical slug set is mirrored from SPEC §5 and enforced; `generation` (FR-C.9) is one capability.
**Note:** The ARD manifest is the interface to the GraphWright compiler's discovery; the internal
registry is the interface to the MCP surface (T31). One key (the FR-C name), two consumers.

### Checkpoint: Contracts complete
- [x] All contract tests pass (142 in the suite). Identifier schemes fixed and approved. Phase 3
  (Contracts) done: T1 identifiers, T2 provenance/confidence, T3 chunk record, T4 ontology, T5
  extraction seam, T6 registration seam + ARD mirror (ADR-0003). Ready to build on them.
- Forward notes carried into Phase 4: T8 registry loader normalizes messy EDGAR CIK forms to
  canonical `EntityId` (from T1 strictness); T24 resolves relationship refs and standalone
  `EntityMention`s as one mention stream and dedupes the post-resolution self-loop (from T4/T5).

---

## Phase 4 — Foundations (spec Phase 0 / early Phase 1)

After contracts, the foundation seams (model, store, ontology/registry, RLM skill) and the
corpus + eval build are largely parallel (plan §2). Two foundation tests prove the risky seams
before capabilities depend on them.

### Task T7: CUAD + EDGAR corpus acquisition and subset

**Description:** Acquire and subset CUAD to roughly 100–150 contracts (~100MB), deliberately
including some scanned filings so the Docling parse and vision-to-text path is exercised; pull the
SEC EDGAR entity data. Confirm the Creative Commons Attribution 4.0 (CC BY 4.0) license at
download (SPEC §13 Phase 0, ADR-0002).

**RAC-7 (acquisition; folds `docs/Corpus_Acquisition.md`):**
- [x] Two acquisition scripts pull CUAD and EDGAR; all outputs land under gitignored `data/`,
  nothing corpus-sized committed. CC BY 4.0 confirmed and recorded (attribution in `LICENSE.txt`).
- [x] CUAD: three artifacts from one pinned snapshot (`zenodo:4595826`, more reproducible than
  mixing sources) — contract PDFs, master-clauses CSV (41-category annotations, feeds T9), SQuAD
  JSON. 510 contracts; pool 501 (9 `Filename`↔PDF mismatches skipped).
- [x] Subset is a **deliberate recorded filter after the full pull** (150 contracts, 28 agreement
  types, 29.3MB), coverage-driven — **not a first-N slice** — with multi-party (121) and
  shared-party (16 groups / 38 contracts) coverage. **CUAD ships no image-only PDFs**, so 10
  selected contracts are deterministically **rasterized to true image-only PDFs** (poppler +
  Pillow, verified 0 text chars) to exercise the vision-to-text path (Option A). Manifest + criteria
  + `scanned.json` recorded → reproducible.
- [x] EDGAR: `company_tickers.json` seed (10,415 companies; feeds T8) + submissions for the
  subset's proposed parties. Fetch sends a monitored User-Agent and respects **≤10 req/s aggregate
  sliding-window + durable disk cache** — verified: cold run 24 network calls, warm re-run **0**.
- [x] Mechanical name→CIK proposals are **structurally UNVERIFIED** (explicit `status` field +
  separate `data/edgar/proposed/` location). 23/323 resolved (high-precision), 300 unresolved
  (expected private/variant entities; carries `UNRESOLVED_NOTE`). Human verification is T10's job.

**Verification:** `uv run python scripts/acquire_cuad.py --check` and
`uv run python scripts/acquire_edgar.py --check` (coverage + license printed, no writes);
`uv run pytest tests/corpus/` — 37 passed.

**Dependencies:** None. **Scope:** L. **Status:** done.
**Files:** `scripts/acquire_cuad.py`, `scripts/acquire_edgar.py`,
`src/rag_wright/corpus/{selection,http,edgar,cuad}.py`, `tests/corpus/`, `data/` (gitignored).
**Note:** Data task, not a capability. Gates T8, T9, T10. Acquisition spec:
`docs/Corpus_Acquisition.md`. Core committed `f1e8820`; CLIs + live run complete the task. Real-data
finding: CUAD has no image-only PDFs (resolved by rasterizing 10); conservative matching is
high-precision/low-recall by design (see T10 unresolved-set note).

### Task T8: Ontology and registry derivation

**Description:** Populate the ontology models (T4) from CUAD's 41 clause categories, and build the
entity registry from EDGAR CIK data with CIKs as the canonical `entity_id`s. Resolution is
closed-world against this registry (FR-C.8, FR-C.7, §16.2/§16.3, ADR-0002). The matching strategy
(§16.3) is decided at T24; this task delivers the registry it resolves against.

**RAC-8:**
- [x] The T4 ontology is reconciled against the real CUAD `master_clauses.csv` columns
  (`reconcile_clause_categories`, case-insensitive + answer-spacing-tolerant). **Live: 41/41
  matched, 0 missing, 0 extra** — the 41 categories are confirmed against real data with no ontology
  change; the reconciliation yields a CSV-column→category map for T9.
- [x] The entity registry is built from EDGAR CIK data; CIKs are the canonical `entity_id`s. **Live:
  8,006 companies from the T7 seed (10,415 ticker-rows collapse to unique CIKs), 0 invalid skipped.**
- [x] **T8 owns EDGAR normalization** — the registry loader **reuses `corpus.edgar.normalize_cik`**
  (the single CIK→`EntityId` point, zero-pad-to-10 + strict T1 validate), so it cannot drift. The
  fragmentation test passes: messy forms (`int`, unpadded, `CIK`-prefixed) all land on the canonical
  `EntityId`; an invalid CIK row is skipped, not invented.
- [x] The registry is seeded from the T7 `company_tickers.json`; submissions→former-name aliases are
  supported (`aliases_by_cik`) and wired at T24 where resolution consumes submissions.
- [x] Closed-world lookup works: `resolve` returns the canonical id for a known name/ticker/alias
  (case/punctuation-insensitive) and `None` for an unknown surface form (never fabricated).

**Verification:** `uv run pytest tests/ontology/test_derivation.py` — 10 passed.

**Dependencies:** T4, T7. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/ontology/derive.py`, `src/rag_wright/ontology/registry.py`,
`tests/ontology/test_derivation.py`
**Note:** The `EntityId` contract (T1) is strict canonical-only; this loader is the single place
raw EDGAR forms enter and get normalized. Keep normalization here, not in the contract.
**ARD category — foundation derivation (GraphWright RegistryStore verification, ADR-0014 taxonomy):**
`ontology_registry_derivation` (FR-C.8) is a **canonical slug with NO ARD manifest**. It is not
seam-bound; it is a real capability with a contract and an internal registry entry, but it runs **before**
compilation and its output (the ontology + registry) is an **input** to the graph, not a node the
compiler discovers by query — so it authors no manifest. Category 3 in the three-category ARD rule above.

### Task T9: Golden eval harness + CUAD-annotation archetype sets

**Description:** Build the pytest evaluation harness and the CUAD-derived golden sets: CUAD expert
clause annotations give ground truth for the exact/lexical archetype, the semantic archetype, and
clause-finding answer-and-citation questions (SPEC §12, plan §4). The harness measures recall@k
per archetype per leg (text and graph measured separately). The relational and multi-hop archetype
is built as its own task, T10, deliberately not folded in here.

**RAC-9:**
- [x] Golden sets exist for the exact/lexical, semantic, and clause-finding archetypes, grounded in
  CUAD expert annotations (SQuAD span answers). **Live: 2,032 subset questions** (exact_lexical 893,
  clause_finding 737, semantic 402), pinned to `zenodo:4595826` + the 150-contract subset in the
  build record → gitignored `data/eval/golden.json`, rebuilt by `uv run python -m eval.build_golden`.
- [x] Harness measures recall@k per archetype, text and graph legs separately (`evaluate` returns
  `archetype/leg` means; `retrieve` injected so the plumbing is proven before capabilities exist).
- [x] Harness runs green on a tiny fixture (recall correctness, per-leg separation, builder spread +
  skipping, map covers all 41 categories).

**Verification:** `uv run pytest eval/test_harness.py` — 5 passed (now in the default suite, 194).

**Dependencies:** T7. **Scope:** M. **Status:** done.
**Files:** `eval/harness.py`, `eval/golden.py`, `eval/build_golden.py`, `eval/test_harness.py`,
`data/eval/golden.json` (gitignored). **testpaths** now includes `eval`.
**Note:** Everything after Phase 0 is measured against this; feeds GATE-1 and GATE-2. The
`ARCHETYPE_BY_CATEGORY` map is a **testable hypothesis** to revisit once per-category/per-leg recall
is observable (review: Termination for Convenience → clause-finding; Parties kept lexical as a known
borderline; Post-Termination Services + Competitive Restriction Exception flagged revisit).

### Task T10: EDGAR-derived relational + multi-hop question construction

**Description:** Construct the relational and multi-hop golden questions **from the EDGAR
party-and-entity graph** (SPEC §12, §8, ADR-0002). This is a distinct foundation task on purpose:
CUAD alone is single-document clause extraction and under-tests the archetype the graph layer
exists for, and a multi-hop set is the easiest thing to accidentally under-build into a set of
single-document questions. Making it visible and separate is what makes the graph leg and the
per-source ablation (T32) measurable at all.

**RAC-10:**
- [x] Questions require traversal across the EDGAR party-and-entity graph (relational and
  genuine multi-hop, not single-document lookups), with ground-truth answer entities and the
  `entity_id`/`chunk_id` evidence path recorded. **Live: 19 questions** — 16 one-hop (hub's full
  co-party set) + 3 two-hop; `entity_paths` records the entity_id evidence chain per answer, chunk_id
  path resolved at eval time (same deferral as CUAD `relevant_ids`).
- [x] **The name→CIK links are human-verified, not auto-generated** (folds
  `docs/Corpus_Acquisition.md`): linking a contract's parties to CIKs is itself the entity-resolution
  problem (FR-C.7), so building the answer key by fuzzy matching and then testing fuzzy matching
  against it is circular. **Only the human-verified `resolution` fields (45/45: 28 CIK + 17 PRIVATE)
  enter the set** — `build_relational` reads ground truth, never re-matches. CIK filers and
  verified-PRIVATE entities are **both first-class** (axis = verified-vs-unverified); variant
  spellings collapse by resolved identity; distinct subs keep separate keys (ScanSource vs ScanSource
  Latin America); SKIP excluded.
- [x] The set is registered as its own archetype split in the harness (T9), separate from the
  CUAD-annotation sets. `RelationalQuestion.to_golden()` → `Archetype.RELATIONAL`; `evaluate()`
  reports `relational/text` and `relational/graph`, each leg separate.
- [x] Coverage is enough to measure the graph leg's recall and its per-source ablation loss (T32),
  not a token handful. **Honest property recorded** (`public-filer-centric, multi-hop-modest`): the
  corpus is star-shaped, so 16 one-hop but only 3 genuine 2-hop hubs — modest *by the corpus*, not by
  under-building. Made visible by measurement (`make-load-bearing-work-a-visible-task`), not hidden.

**Verification:** `uv run pytest eval/test_multihop_set.py` — 6 passed (full suite 233). Rebuild the
set: `uv run python -m eval.multihop`.

**Dependencies:** T7, T9. **Scope:** M. **Status:** done.
**Files:** `eval/multihop.py`, `eval/golden/relational/set.json` (**committed** verified fixture),
`eval/test_multihop_set.py`, `docs/adr/0005-relational-golden-set.md`.
**Note:** The entire reason the ArcadeDB graph layer exists is measured here. Do not fold into T9.
The built set is committed (verified answer keys, the only durable copy in git); the gitignored
triage file `data/edgar/verification_set.json` stays out. Decisions in **ADR-0005** (identity key,
`PRIVATE:<key>` sentinel keeps `EntityId` strict, commit-the-fixture rationale).
**Unresolved-set note (from T7):** the T7 name→CIK proposals are conservative (normalized
conformed-name only), so the UNRESOLVED set is *expected* to contain real entities — private
companies/individuals (absent from EDGAR) and name variants (subsidiaries, former names, DBAs). The
verifier must not read "unresolved" as "not an entity"; graph entity coverage is public-filer-centric
(FR-C.7), a property to keep in mind when interpreting multi-hop results. See
`rag_wright.corpus.edgar.UNRESOLVED_NOTE`.

### Task T11: Model-profile seam (DeepSeek V4 Pro first for structured-under-reasoning)

**Description:** One construction point that builds the model client (`langchain_openai.ChatOpenAI`
per ADR-0001) keyed by model id, with the structured-output method (default `function_calling`)
and an optional structured-only extra body carried in a per-model profile config, never a
hardcoded provider/model flag in call sites. Free-text and reasoning calls are unaffected
(assumption 2, tech stack, CLAUDE.md standing rule). Model priority is a **seam config decision,
not capability code**:

- **DeepSeek V4 Pro is the default profile for structured-output-under-reasoning calls** (contract
  extraction, grader-style, and synthesis calls), because it handles reasoning together with
  forced structured output more reliably, which is exactly the failure surface this system leans
  on. Its empirically-determined structured-output method and any structured-only extra body are
  recorded in the seam's ADR (T12).
- **Qwen 3.7 Plus is kept as a secondary profile** for the same call class.
- **The Gemma 4 class model remains the default** for the local-deployment mode and general
  generation.

**RAC-11:**
- [x] A profile keyed by model id supplies structured-output method + optional structured-only
  extra body; the extra body applies only to the forced-structured call. `ModelProfile{model_id,
  structured_method (default function_calling), structured_extra_body}`; the seam threads
  `extra_body` through `with_structured_output(schema, method=..., extra_body=...)` so it binds to
  the structured runnable only — a test asserts it reaches the structured call but never the base
  client's constructor (free-text / reasoning unaffected).
- [x] The structured-under-reasoning default resolves to DeepSeek V4 Pro; Qwen 3.7 Plus is a
  selectable secondary; Gemma 4 class is the local-mode / general-generation default. All of this
  lives in profile config, and no provider/model-specific flag appears in any call site. `ModelRole`
  → id via `model_for`, each env-overridable (`RAG_MODEL_*`); an unregistered model gets the safe
  default profile so no call site special-cases a model.
- [x] `with_structured_output(...)` is reached only through the seam (`seam.build_structured`; the
  base `seam.build_model` carries no structured flag or `extra_body`).

**Verification:** `uv run pytest tests/models/test_profile_seam.py` — 9 passed (unit, fake client, no
network; full suite 242).

**Dependencies:** T6. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/models/profiles.py`, `src/rag_wright/models/seam.py`,
`tests/models/test_profile_seam.py`
**Note:** Priorities live here, never in capability code (CLAUDE.md standing rule). The model slugs
(`deepseek/deepseek-v4-pro`, `qwen/qwen-3.7-plus`, `google/gemma-4-class`) are documented,
env-overridable placeholders and DeepSeek's `structured_extra_body` ships `None`: the exact slug and
the empirical thinking-disable `extra_body` are confirmed against a live call and recorded in a dated
ADR at **T12** (no unconfirmed provider flag baked in, per the standing rule). Grounded against
`langchain_openai.chat_models.base` (ADR-0001): `with_structured_output` forwards kwargs into the
tool binding, which is what makes `extra_body` structured-call-only.

### Task T12: A-T2 forced-structured-output foundation test on DeepSeek V4 Pro (+ ADR)

**Description:** Prove the model-profile seam against an actual forced-schema call **on DeepSeek V4
Pro**, the model the seam now prioritizes for structured-output-under-reasoning and the model
extraction (T23) will use, the same failure surface open models hit in thinking mode (plan A-T2,
risk 3). Learn and record the working profile (method, structured-only extra body) in a dated ADR
before extraction depends on it.

**RAC-12:**
- [x] A forced-schema call on DeepSeek V4 Pro returns a valid contract instance through the seam.
  Live: `seam.build_structured("deepseek/deepseek-v4-pro", _PartyExtraction).invoke(...)` returned a
  valid Pydantic instance; the Qwen secondary passes too (via its profile's `extra_body`).
- [x] The working profile (method + structured-only extra body) for DeepSeek V4 Pro is recorded in
  a dated ADR; Qwen 3.7 Plus is noted as the secondary profile. **ADR-0006** (dated 2026-07-07):
  DeepSeek = `function_calling`, no `extra_body` (honors the forced tool call while reasoning);
  Qwen = `function_calling` + `{"reasoning":{"enabled":false}}` (bare call fails risk-3 in thinking
  mode). Slugs corrected from T11 placeholders.

**Verification:** `uv run pytest tests/foundation/test_model_seam_structured.py -m model` — 2 passed
(primary + secondary; requires OpenRouter access, marked `model` so it is opt-in; default suite skips
it, 242 passed + 2 skipped).

**Dependencies:** T11. **Scope:** S. **Status:** done.
**Files:** `tests/foundation/test_model_seam_structured.py`, `docs/adr/0006-model-profile.md`
(renumbered — `0003` is taken by ARD), `src/rag_wright/models/profiles.py` (empirical slugs +
`extra_body`), `conftest.py` + `pyproject.toml` (opt-in `model`/`store` markers, `.env` load).
**Note:** Needs OpenRouter access (`.env`). Validated the profile on the prioritized model and
reproduced risk 3 live on the secondary, proving the seam's structured-only `extra_body` is
load-bearing. De-risks FR-C.6 before it is built.

### Task T13: Store seam + ArcadeDB schema and hybrid indexes

**Description:** Define the query-skill seam (so the store is swappable, FR-S.5) and create the
ArcadeDB schema with the dense `LSM_VECTOR` and sparse `LSM_SPARSE_VECTOR` indexes via the
`arcadedb_python` `SyncClient` / `DatabaseDao` (FR-S.1, §16.4). Resolves the schema/index
specifics open question at this task.

**RAC-13:**
- [x] The query-skill seam interface is defined; the ArcadeDB implementation sits behind it.
  `store/seam.py` `Store` (runtime_checkable Protocol, semantic not SQL: `ensure_schema`,
  `type_names`, `property_names`, `index_names`, `ping`, `close`); `ArcadeDBStore` binds it.
- [x] Schema created with dense `LSM_VECTOR` (dims=`BGE_M3_DENSE_DIM` 1024, COSINE) and sparse
  `LSM_SPARSE_VECTOR` indexes; chunk records and graph nodes carry `chunk_id`. `Chunk` (chunk_id
  UNIQUE, source_doc_id, dense, sparse_indices, sparse_weights) + `Entity` (entity_id UNIQUE,
  chunk_id). **Empirical (26.7.2): sparse index needs two parallel arrays not a map** → T3
  `sparse_vector: dict[int,float]` decomposed at the store boundary (T20). Idempotent by schema
  introspection (ArcadeDB rejects `IF NOT EXISTS` here). ADR-0007.
- [x] A second stub implementation can bind the same seam (proves swappability for the LanceDB
  fallback path). `_InMemoryStore` in the test satisfies `isinstance(..., Store)` and the schema
  surface.

**Verification:** `uv run pytest tests/store/test_arcadedb_schema.py -m store` — 3 passed (live
ArcadeDB, opt-in); hermetic stub tests run in the default suite (244 passed + 5 skipped). Local run:
`docs/ArcadeDB_Local.md`.

**Dependencies:** T3, T6. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/store/seam.py`, `src/rag_wright/store/arcadedb.py`,
`tests/store/test_arcadedb_schema.py`, `docs/adr/0007-arcadedb-store-schema.md`,
`docs/ArcadeDB_Local.md`, `.env.example`, `conftest.py` (marker-gate fix).
**Note:** Grounded against `arcadedb_python` 0.4.0 (`SyncClient`/`DatabaseDao`) and the live 26.7.2
server (driver is v0.x, risk 2). `vector.fuse` confirmed present (de-risks GATE-2; proven end-to-end
at T14). Query-side vector-search function name pinned at T14/T21.

### Task T14: A-T1 ArcadeDB `vector.fuse` hybrid foundation test

**Description:** Write a few chunk records and run a `vector.fuse` Reciprocal Rank Fusion (RRF)
hybrid query end to end, confirming the sparse index and server-side fusion behave as documented
(plan A-T1, risk 1). Surfaces early whether we are heading for the LanceDB fallback.

**RAC-14:**
- [x] A few records written; a `vector.fuse` RRF hybrid query returns a sensible fused ranking
  honoring a metadata filter. Live: `SELECT expand(vector.fuse(vector.neighbors('Chunk[dense]',...),
  vector.sparseNeighbors('Chunk[sparse_indices,sparse_weights]',...), {fusion:'RRF'}))` fuses the
  dense-leg and sparse-leg winners above the weak record; metadata filter via
  `SELECT ... FROM (<fuse>) WHERE source_doc_id='docA'` returns only that source.
- [x] The result (works as documented / lean-toward-fallback) is recorded as an early signal for
  GATE-2. **Signal: works as documented, no lean toward the LanceDB fallback** (ADR-0007 A-T1
  section). Early A-T1 signal only; the GATE-2 decision is measured on the golden set at T21/T22.

**Verification:** `uv run pytest tests/foundation/test_arcadedb_hybrid.py -m store` — 2 passed (live
ArcadeDB 26.7.1, opt-in); default suite 244 passed + 7 skipped.

**Dependencies:** T13. **Scope:** S. **Status:** done.
**Files:** `tests/foundation/test_arcadedb_hybrid.py`, `docs/adr/0007-arcadedb-store-schema.md`
(A-T1 result). Adds no src code — a foundation probe over the real T13 schema via the grounded
arcadedb-python DAO.
**Note:** Early de-risk of the recall-bar gate; not the gate itself. Hybrid SQL grounded against the
official ArcadeDB docs (the Python API does not wrap `vector.fuse`/`sparseNeighbors`) + confirmed on
the live server.

### Task T-DISP: `requires_dynamic_dispatch` typed flag on `skill_runtime` (RLM-rebuild groundwork)

**Description:** The recursive-RLM rebuild (ADR-0015) reopens T15/T17/T28 to make RLM interpreter +
dynamic sub-agents. Its dynamic dispatch is prompt-triggered by the interpreter (langchain-quickjs's
"workflow" word), a **silent under-performance** if it fails to fire. Grounding the pinned
`langchain-quickjs==0.3.2` proved a skill **cannot** inject that trigger from its own authored content
(the trigger reads the *user's request*, and the skill content lands in the *system message*) — so the
requirement is declared as a **typed flag** `requires_dynamic_dispatch: bool` on `skill_runtime`, and
GraphWright's runtime (not our wire contract) owns the magic word (ADR-0017). This is the ARD-contract
prerequisite that gates T15's reimplementation.

**Acceptance:**
- [x] `SkillRuntime.requires_dynamic_dispatch: bool = False` mirrored on `ard.py`; a validator rejects
  `requires_dynamic_dispatch=True` with `needs_interpreter=False` (dynamic dispatch is exposed by the
  interpreter), kept a **distinct** field (not collapsed into `needs_interpreter`).
- [x] `agent_skill`-only, like the rest of `skill_runtime` (a `function` manifest carrying it fails
  `RegistryEntry` validation).
- [x] Populated `true` on the three RLM specs (`rlm_method`, `rlm_chunking`, `rlm_synthesis`); the wire
  form carries `skillRuntime.requiresDynamicDispatch: true` (camelCase).
- [x] Grounding recorded: **self-dispatch confirmed** on `deepagents==0.6.12` — dispatch is name-based
  (`subagents_by_name`/`subagent_graphs` name-keyed dicts, `deepagents/middleware/subagents.py:585,588`)
  and a self-referential `rlm_decomposer` roster constructs with **no infinite recursion**. So
  `task(subagentType="rlm_decomposer")` self-dispatch (ADR-0015 Q2) is structurally sound. Last pre-T15
  grounding item cleared.

**Verification:** `uv run ruff check` clean; `uv run pytest tests/capabilities/test_manifests.py` —
22 passed; full suite **373 passed + 26 skipped**. Wire form verified:
`skillRuntime: {needsInterpreter, rlm, grantedSubagents: [], requiresDynamicDispatch: True}`.

**Dependencies:** ADR-0015, ADR-0017. **Scope:** S. **Status:** done.
**Files:** `src/rag_wright/capabilities/ard.py`, `src/rag_wright/capabilities/manifests.py`,
`tests/capabilities/test_manifests.py`, `docs/adr/0017-dynamic-dispatch-trigger-as-typed-flag.md`.
**Note:** `grantedSubagents` stays `[]` until the T15/T17/T28 rebuild lands (populated then to
`["rlm_decomposer","rlm_slice_worker"]`, ADR-0015). **T15 stays held** until both this flag (landed here)
and GraphWright's applier (translating the flag into the interpreter's trigger) are in place (ADR-0017).

### Task T15 (REOPENED 2026-07-15, B′ rebuild): RLM skill + machinery (recursive dynamic sub-agents)

**Description:** The ADR-0015/0016 rebuild turns RLM from interpreter-plus-model-calls into interpreter
+ dynamic sub-agents. T15 delivers the reusable machinery T17/T28 apply: the rewritten `SKILL.md`, the
two Deep Agents sub-agent configs (`rlm_decomposer`, `rlm_slice_worker`), the interpreter-driven
recursive `decompose()` workflow, and the ADR-0016 fail-if-absent tests. **Design B′** (ADR-0015 Q2,
corrected): the recursion lives in the interpreter, which re-dispatches a *fresh* `rlm_decomposer` per
level; a self-referential agent is **not constructible** on `deepagents==0.6.12` (eager roster compile).

**RAC-15 (rebuild):**
- [x] `SKILL.md` teaches the B′ method: load the working set into the interpreter, write a recursive
  `decompose()` workflow that dispatches a fresh decomposer per level + a worker per leaf, combine in
  code. States the workflow trigger is declared (`requiresDynamicDispatch`) and applied by the runtime.
- [x] Machinery in `src/rag_wright/skills/rlm/agent.py`: `RLM_DECOMPOSER`/`RLM_SLICE_WORKER`/
  `GRANTED_SUBAGENTS`, `decomposer_config`, `slice_worker_config` (per-slice tools + skills),
  `RLM_WORKFLOW_JS` (the shipped recursive descent), and `build_rlm_agent(...)` assembling
  `create_deep_agent` + `CodeInterpreterMiddleware`, models through the profile seam (never a string id).
- [x] **Four ADR-0016 fail-if-absent tests, written first and confirmed red against pre-rebuild code**
  (no `build_rlm_agent` entrypoint), then green — driving the REAL machinery + shipped `RLM_WORKFLOW_JS`
  with scripted fake models (hermetic, genuine `task()` dispatch): (1) recursion-forced — the eval result
  shows `maxSplitDepth>=1`, `leafCount==3` (only reachable via two levels), and the decomposer fired at
  >1 depth; a **teeth** test proves a flat one-level workflow fails it; (2) a leaf worker invokes a tool
  mid-handling; (3) a leaf worker loads a skill (source present in its context); (4) code-driven fan-out —
  dispatches carry the parent `eval_id`; a **teeth** test proves sequential (top-level `task`) dispatch is
  rejected.
- [x] **Opt-in real-model smoke test** (`@pytest.mark.model`) proving what the fakes cannot: a REAL
  orchestrator, given the method, recurses on the decomposer's output. It surfaced a defect **here**: the
  method wired as a lazy `skills=` source was **never read**, so the model flatten-and-hardcoded the
  split. Fix (ADR-0018): load the method into the orchestrator's **system prompt**; strengthen SKILL.md
  (explicit recursion mandate + anti-pattern). Test redesigned with an **opaque** working set whose leaves
  only the decomposer reveals, so full leaf coverage proves derivation (not a lowered bar). **10/10**
  live runs on `google/gemma-4-31b-it` after the fix (was 0-1 before).
- [ ] `grantedSubagents` population to `["rlm_decomposer","rlm_slice_worker"]` + re-emit is deferred to
  after the full rebuild (T17, T28) lands, per the sequencing directive (manifests currently `[]`).

**Verification:** `uv run pytest tests/capabilities/test_rlm_method.py` — 6 passed + 1 skipped (the
`model` smoke test); `-m model` — 1 passed live (10/10 across the hit-rate harness). Red confirmed by
moving `agent.py` aside (ModuleNotFoundError). ruff clean. Full suite **379 passed + 26 skipped** (+1
opt-in). Deterministic chunk core (`_split_into_chunks`, `ChunkId`, gate, `Chunk`) and the `_reduce`
fan-in are **untouched** (T17/T28 preserve them as separate functions).

**Dependencies:** ADR-0015, ADR-0016, ADR-0017, ADR-0018. **Scope:** L. **Status:** done.
**Files:** `src/rag_wright/skills/rlm/SKILL.md`, `src/rag_wright/skills/rlm/agent.py`,
`tests/capabilities/test_rlm_method.py`, `tests/capabilities/_fixtures/rlm_probe_skill/SKILL.md`,
`docs/adr/0015-rlm-dynamic-subagents-and-granted-subagents.md` (Q2 correction),
`docs/adr/0018-rlm-method-is-the-orchestrator-system-prompt.md`.

### Task T15 (original, superseded by the rebuild above): RLM skill authoring (general method only)

**Description:** Author the RLM SKILL.md as the general divide-and-conquer method (load a working
set into an interpreter, slice and dispatch in code, synthesize). It has no testable behavior of
its own; the RLM chunking (T17) and RLM synthesis (T28) capabilities each apply it with their own
contract and tests (FR-C.10, plan §1 note).

**RAC-15:**
- [x] `SKILL.md` teaches the method (interpreter load, code-side slice/dispatch, synthesize) as
  authored software, not a build-tool feature. `src/rag_wright/skills/rlm/SKILL.md` (frontmatter +
  the three-step method, recursive; shows how RLM chunking and RLM synthesis each apply it).
- [x] It defers all determinism/boundary/gating behavior to the applying capabilities. Explicit
  "What this skill does NOT own" section: determinism, boundary validation, gating, model choice.
- [x] ARD-registered as an `agent_skill`: manifest authored via `ard.write_manifest` and **present in
  the shared root** (`~/.air/registry/rlm_method.json`, `urn:air:dreamai.io:rag_wright:rlm_method`,
  `application/ai-skill+md`). Mirror conformance tests green; the on-disk manifest re-validates as a
  `RegistryEntry`. Live `RegistryStore(root)` load is deferred to the GraphWright side (not done here).

**Verification:** Manual review of `SKILL.md`; `uv run pytest tests/capabilities/test_manifests.py`
— 3 passed (authoring + on-disk shape). Publish to the root: `uv run python scripts/publish_manifests.py`.
Full suite 251 passed + 7 skipped.

**Dependencies:** None. **Scope:** S. **Status:** done.
**Files:** `src/rag_wright/skills/rlm/SKILL.md`, `src/rag_wright/capabilities/manifests.py`
(reusable ARD-authoring seam, one `CapabilityManifest` per capability — extended each T15-T29 task),
`scripts/publish_manifests.py`, `tests/capabilities/test_manifests.py`.
**Note (cross-cutting, per the T15-T29 directive):** every capability authors its ARD manifest via
`ard.py` into the shared root under `urn:air:dreamai.io:rag_wright:<slug>` with the mirror conformance
test green and the manifest present in the root; no live `RegistryStore` load here (GraphWright side).

### Checkpoint: Foundations complete
- [ ] Contract, model-seam, and registry unit tests green. A-T1 and A-T2 run and their outcomes
  recorded (ADR for A-T2 on DeepSeek V4 Pro). Corpus + both eval sets (CUAD-annotation and
  EDGAR multi-hop) in place. Ready for the write-side.

---

## Phase 4 — Build, write-side (spec Phase 1)

> **FR-I.6 note (line redrawn at Phase 2 review).** FR-I.6 has three parts on two sides of the
> boundary. (1) **Model-tiering per task** (which model each capability uses) is a built-capability
> concern via the model-profile seam (T11) and the escalation task (T18). (2) **CPU-GPU
> decoupling, pooled inference, and backpressure** is a property of *how the GPU-calling
> capabilities are implemented* (poolable, non-blocking, backpressure at the capability boundary),
> so it is an acceptance criterion on the embedding (T19) and extraction (T23) tasks, built in now
> because it is cheap to build in and expensive to retrofit. (3) The **two run modes** (bulk takes
> the machine, background yields to serving) are orchestration and deployment, genuinely not a
> capability, and are out of scope here (compiler / deployment concern).

### Task T16: Parsing (Docling)

**Description:** Turn source documents (PDF, Office files, scans) into a clean structured
representation (reading order, headings, sections, tables, OCR), parsed once and reused by
chunking, embedding, and extraction (FR-C.1). Grounded surface: `docling` `DocumentConverter`.

**RAC-16:**
- [x] A CUAD PDF and a scanned filing both parse into the structured representation (headings,
  sections, tables; OCR text present for the scan). **Live (`-m parse`, 2 passed, 203s):** the
  smallest text-layer CUAD contract parsed to headings/sections/markdown; the image-only scanned
  NETGEAR filing OCR'd to text (RapidOCR). `DocumentConverter` behind a `Parser` seam →
  `DoclingDocument`.
- [x] The parsed result is cached/reusable so it is parsed once. Content-hash gated: the
  `DoclingDocument` is cached (`save_as_json`/`load_from_json`) keyed by source content hash;
  unchanged content is a cache hit, changed content re-parses (proven hermetically via a
  call-counting stub `Parser`).
- [x] Registered under FR-C.1. `register_parsing` → `parsing`, kind `function`, contract
  `ParsedDocument` (source_doc_id reuses T1's citation-safe charset).
- [x] ARD-registered: manifest authored + **present in the shared root**
  (`~/.air/registry/parsing.json`, `urn:air:dreamai.io:rag_wright:parsing`, kind `function` with
  response bounds); mirror conformance green; re-validates as a `RegistryEntry`. Live `RegistryStore`
  load / discovery is the GraphWright-side step (not done here).

**Verification:** `uv run pytest tests/capabilities/test_parsing.py` (6 hermetic passed); live:
`... -m parse` (2 passed); manifest: `tests/capabilities/test_manifests.py`. Full suite 259 passed +
9 skipped. Publish: `uv run python scripts/publish_manifests.py`.

**Dependencies:** T3. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/capabilities/parsing.py`, `tests/capabilities/test_parsing.py`,
`src/rag_wright/capabilities/manifests.py` (+`parsing` spec), `tests/capabilities/test_manifests.py`
(scaling test over all specs), `pyproject.toml` + `conftest.py` (`parse` opt-in marker).

### Task T17 (REOPENED 2026-07-15, rebuild): RLM chunking = LLM semantic boundary discovery

**Description:** The B′ rebuild makes chunking what the SPEC requires: **LLM-driven semantic boundary
discovery, mandatory, no fixed-size chunking ever**. A strong model (STRUCTURED_REASONING / deepseek-v4-pro)
explores the parsed document via the T15 machinery (`build_rlm_agent` + a `peek` tool), and returns
semantically coherent boundary **spans over the document's items**. Reproducibility is moot (ingestion
chunks once and persists; only a document change re-chunks — task T34): the value is a good boundary, not a
repeatable one. The layer **after** boundaries are chosen is deterministic-given-boundaries and lives in a
separate function (`_finalize_chunks`): join each span, enforce the cap, apply the **T-CHK floor + merge +
near-empty rejection** (an LLM can just as easily emit a boundary around a lone heading), compute
`chunk_id`, validate. Summaries stay the existing concurrent flat map. Recursion is available-when-warranted,
**not gated** for chunking (ADR-0019).

**RAC-17 (rebuild):**
- [x] Boundaries are **LLM-found and semantic, not fixed-size**: a `BoundaryDiscoverer` seam
  (`SeamBoundaryDiscoverer` via `build_rlm_agent`, deepseek-v4-pro), spans over `document.texts` items.
  Hermetic tests inject a stub discoverer; **live** test asserts a known coherent clause (items 3..6) is
  **not split across chunks** — boundary quality, the T15-opaque-proof analogue. Live: **4/4** (1 via
  pytest + 3 via the hit-rate harness) on deepseek-v4-pro, incl. a clean semantic partition
  `(0,1)(2,2)(3,6)(7,8)` isolating the clause. (Thinner than T15's 10/10; this test is load-bearing — see
  the coverage caveat below.)
- [x] **Per-slice tool use** in the exploration: the discoverer's `peek(index)` tool is invoked mid-handling
  (hermetic, fake-model-driven through the real machinery).
- [x] **id/gate/validation deterministic given the spans**: stable `chunk_id`s given the same spans;
  content-hash gate skips the re-chunk **and the LLM call**; partition validation rejects gaps/overlaps/short
  coverage. **T-CHK preserved over the discoverer's spans**: the floor + merge + near-empty rejection now
  fire in `_finalize_chunks`; the dense-header pathology (one span per item) coalesces, no near-empty chunk;
  the ASIANDRAGON regression fixture still passes over spans. Added a residual-rebalance so the floor holds
  even for a tiny trailing span.
- [x] **Not recursion-gated** (ADR-0019): recursion available-when-warranted, intrinsic to synthesis/T28.

**Verification:** `uv run pytest tests/capabilities/test_rlm_chunking.py` — 14 passed + 2 skipped (live);
`-m model` coherent-clause **passed** (+ reliability re-runs). ruff clean. Full suite **379 passed + 26
skipped**. `_split_into_chunks` (the old header heuristic) removed; its floor/merge logic preserved in
`_finalize_chunks`. Added `tools=` (orchestrator tools) to `build_rlm_agent` for the `peek` tool.

**COVERAGE CAVEAT (deliberate, recorded):** the hermetic suite proves the *mechanism* (spans → correct
join, cap, T-CHK floor/near-empty, ids, gate, partition validation) but **asserts no boundary quality** —
that a coherent unit is kept whole is model judgment and cannot be checked against a fake. Boundary quality
is guarded **only** by the opt-in live test (`-m model`). So: **CI does not cover boundary quality**, and
`-m model` must be run before trusting any change that touches boundary discovery or the SKILL/prompt.
Removed the two old pure-heuristic tests (subsection-stays, major-boundary) — they asserted the old
deterministic heuristic's specific choices; that decision is now the LLM's and is covered only by the live
coherent-clause test. This is a real shift in what CI catches; not silent.

**Dependencies:** T15, T16, T11, ADR-0019. **Scope:** L. **Status:** done.
**Files:** `src/rag_wright/capabilities/rlm_chunking.py`, `src/rag_wright/skills/rlm/agent.py` (+`tools=`),
`tests/capabilities/test_rlm_chunking.py`, `docs/adr/0019-recursion-is-optional-for-chunking-required-for-synthesis.md`.
**Finding logged:** T34 (document update/upsert path does not exist).

### Task T17 (original, superseded by the rebuild above): RLM chunking (deterministic, content-hash gated)

**Description:** Read the whole parsed document through an interpreter (not bounded by a context
window) using the RLM skill (T15), splitting along topic/section/chapter boundaries into
semantically coherent chunks (variable size, capped ~20,000 tokens), writing a summary per chunk,
a manifest per document, and stable `chunk_id`s. Deterministic and reliable: temperature zero or
structured output, boundary validation, and a content-hash gate so an unchanged document is not
re-chunked (FR-I.1).

**RAC-17:**
- [x] Same document in yields identical chunk boundaries and `chunk_id`s across runs. Boundaries are
  code-deterministic (from the parsed section structure + the token cap); `chunk_id` via
  `ChunkId.of` (canonical `<src>:<idx>:<64hex>`). Summaries deterministic via structured-output /
  temperature-zero. Test: chunk twice → identical `ChunkManifest`.
- [x] Boundary validation runs; chunks capped at ~20,000 tokens; a summary + manifest produced.
  `_validate_boundaries` (uniqueness, sequential index, non-empty, ≤ cap); over-cap items hard-split;
  per-chunk summary; per-document `ChunkManifest`. **Cap-accumulation bug (joined length vs summed
  per-item estimates) found by the live end-to-end run and fixed; hermetic regression added.**
- [x] The content-hash gate skips an unchanged document (no re-chunk). Manifest cached by content
  hash; unchanged → cache hit (stub summarizer call-count proves no new summaries).
- [x] Registered under the RLM chunking capability. `register_rlm_chunking` → `rlm_chunking`,
  `agent_skill`, contract `ChunkManifest`.
- [x] ARD-registered: manifest present in the shared root (`~/.air/registry/rlm_chunking.json`,
  `agent_skill`, `requires: ['rlm_method']`); mirror conformance green; re-validates as a
  `RegistryEntry`. Live `RegistryStore` load is the GraphWright-side step.

**Verification:** `uv run pytest tests/capabilities/test_rlm_chunking.py` (6 hermetic passed); live:
`-m model` (summarizer) and `-m "parse and model"` (**end-to-end real parse + 6 real DeepSeek Flash
summaries, all chunks ≤ cap; 41s**). Full suite 266 passed + 10 skipped.

**Dependencies:** T15, T16, T11. **Scope:** L. **Status:** done.
**Files:** `src/rag_wright/capabilities/rlm_chunking.py`, `tests/capabilities/test_rlm_chunking.py`,
`src/rag_wright/models/profiles.py` (new `SUMMARIZATION` role → DeepSeek V4 Flash, FR-I.6 tiering),
`src/rag_wright/capabilities/manifests.py` (rlm_chunking spec, requires rlm_method).
**Note:** Resolves chunking-skill internals (§16.1). Determinism is testable (risk 4). The real-doc
end-to-end run is the level at which the cap bug surfaced — hermetic stubs did not reach it.

### Task T18: Small-to-large chunking escalation

**Description:** A less reliable small chunking model handles the bulk for throughput; any
document that fails boundary validation is re-run on a larger model (FR-I.2). **Gated by GATE-1:**
built only if the RLM chunker beats the baseline. If not, this and the elaborate chunking
apparatus are dropped (plan §2).

**RAC-18:**
- [ ] The bulk runs on the small model; a document failing boundary validation is re-run on the
  larger model and passes or is dead-lettered.
- [ ] Model tiers are chosen through the model-profile seam (no hardcoded model flag).

**Verification:** `uv run pytest tests/capabilities/test_chunking_escalation.py`

**Dependencies:** GATE-1, T17. **Scope:** M.
**Files:** `src/rag_wright/capabilities/rlm_chunking.py` (escalation path),
`tests/capabilities/test_chunking_escalation.py`
**Note:** Conditional — do not build before GATE-1 clears. This is the FR-I.6 model-tiering slice.
**Deferred (GATE-1, 2026-07-09):** the dense-only A/B did not justify the escalation apparatus, so
T18 is not built now; the RLM chunker is kept and the earns-its-cost decision moves to GATE-2/T22
(see the GATE-1 section). If the chunker is unproven there, make it optional rather than drop it.

### Task T19: Embedding (BGE-M3: dense over summary, sparse over full text)

**Description:** Produce dense vectors over summaries and native sparse vectors over full chunk
text, from one model (FR-C.2, FR-I.3). The dense-over-summary + sparse-over-full-text split is the
summary-miss mitigation (§12, risk 8). This is a GPU-calling capability, so it carries the FR-I.6
decoupling property. Grounded surface: `FlagEmbedding` `M3Embedder`.

**RAC-19:**
- [x] Dense vector over the summary; native sparse vector over the full chunk text, from the one
  BGE-M3 model. The sparse leg reads the chunk's full text (`Chunk.text`, from the T17 chunk/parse
  manifest keyed by `chunk_id`), not the record; the dense leg reads `Chunk.summary`. Routing
  verified hermetically (`dense_inputs==[summary]`, `sparse_inputs==[text]`); **live BGE-M3 (`-m
  embed`) produced real dense + native sparse.**
- [x] Output shapes match T3/T13: dense length `BGE_M3_DENSE_DIM` (1024); sparse `dict[int, float]`
  (BGE-M3's `Dict[str,float]` string keys converted to ints). Wrong dense dimension is rejected.
- [x] **FR-I.6 decoupling property:** `embed_chunks` is async/non-blocking; each encode runs through
  `asyncio.to_thread` (poolable boundary) bounded by a semaphore (backpressure). Concurrency test:
  in-flight encodes reach exactly `max_concurrency` (and `=1` is strictly serial); concurrent run is
  faster than serial.
- [x] Registered under FR-C.2. `register_embedding` → `embedding`, kind `function`, contract
  `ChunkEmbedding`.
- [x] ARD-registered: manifest present in the shared root (`~/.air/registry/embedding.json`,
  `urn:air:dreamai.io:rag_wright:embedding`); mirror conformance green; re-validates as a
  `RegistryEntry`. Live `RegistryStore` load is the GraphWright-side step.

**Verification:** `uv run pytest tests/capabilities/test_embedding.py` (6 hermetic passed); live:
`-m embed` (real BGE-M3, 1 passed, ~7min incl. model download). Full suite 273 passed + 12 skipped.

**Dependencies:** T16. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/capabilities/embedding.py`, `tests/capabilities/test_embedding.py`,
`src/rag_wright/capabilities/manifests.py` (+embedding spec), `pyproject.toml` + `conftest.py`
(`embed` opt-in marker).
**Note:** FR-I.6 decoupling is built in here because it is cheap now, expensive to retrofit.
**GATE-1 is now runnable** (RLM chunker A/B needs T17 + T19 + T9 — all done); it is a human-gated
branch point that decides whether T18 (small→large escalation) is built.

### Task T20: Chunk write + incremental upsert (content-hash gated)

**Description:** Write chunk records to the store, upserting by `chunk_id`; make ingestion
incremental, resumable, and idempotent with per-document and per-chunk checkpoints and a
dead-letter queue for failed documents (FR-I.3, FR-I.5). Content-hash gating means an unchanged
document does effectively no work.

**RAC-20:**
- [x] A chunk record upserts by `chunk_id` (re-write of the same id updates, does not duplicate).
  ArcadeDB `UPDATE Chunk SET ... UPSERT WHERE chunk_id = ...`; the sparse vector is decomposed into
  the two parallel arrays at the store boundary (ADR-0007). **Live (`-m store`): re-writing a
  chunk_id updates in place, count stays 1.**
- [x] Re-running an unchanged document does effectively no work (content-hash gate). Per-document
  checkpoint keyed by content hash; a completed same-hash run returns `skipped` with no store writes.
- [x] A failed document lands in the dead-letter queue; a resumed run continues from checkpoints.
  Per-chunk checkpoints; a write exception dead-letters the doc; a retry skips already-written chunks
  and completes, clearing the dead-letter entry.
- **(No ARD bullet.)** Chunk write is a seam-bound ingestion pipeline step with no SPEC section-5
  slug (FR-I.3), so it registers nothing and authors no manifest — per the ARD-registration rule
  above (only query-discovered capabilities register). It reaches the store only through the T13
  `Store` seam (extended here with `upsert_chunk` / `get_chunk` / `chunk_count`).

**Verification:** `uv run pytest tests/capabilities/test_chunk_write.py` (6 hermetic passed);
`-m store` (real ArcadeDB upsert dedup/update, 1 passed). Full suite 279 passed + 13 skipped.

**Dependencies:** T13, T17, T19. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/capabilities/chunk_write.py`, `tests/capabilities/test_chunk_write.py`,
`src/rag_wright/store/{seam,arcadedb}.py` (write-side seam: `upsert_chunk`/`get_chunk`/`chunk_count`),
`tests/store/test_arcadedb_schema.py` (stub extended to the write-side seam).
**Note:** Capability-level idempotence and checkpoints (filesystem checkpoint + dead-letter dirs); the
store holds the records. The bulk-vs-background run modes (FR-I.6 part 3) are
deployment/orchestration, not built here. Not ARD-registered: a seam-bound pipeline step, no §5 slug
(see the ARD-registration rule).

### GATE-1: RLM chunker A/B go / no-go (branch point) — RUN; decision recorded

**Not a task.** A/B the RLM chunker (T17) against a simpler baseline chunker on the golden set
(T9): boundary quality + summary fidelity (§12, plan §2).
- **Beats baseline →** keep the RLM chunker; build the small-to-large escalation (T18) and the
  full apparatus.
- **Does not beat baseline →** drop T18 and the elaborate chunking apparatus; fall back to the
  simpler chunker; redirect the plan. Raise with the human before proceeding either way.

> **SUPERSEDED (2026-07-12, T-CHK):** these RLM numbers were measured on the buggy splitter (degenerate
> over-fragmentation + near-empty chunks, fixed in T-CHK). They do **not** change the GATE-1 decision
> (ADR-0009 keeps the RLM chunker regardless of any single-corpus result), but they must **not** be cited
> later as evidence about the chunking strategy. Any RLM-vs-baseline A/B after T-CHK requires a full
> re-ingest on clean chunks (the stored chunks are stale).

**Run (`eval/gate1_chunker_ab.py`, 2026-07-09) — dense-only proxy (T17 + T19 + T9; no store, no
sparse leg), 4 golden docs / 59 questions:**

| chunker | boundary quality | recall@1 | recall@3 | recall@5 | chunks/doc |
|---|---|---|---|---|---|
| RLM (T17, ~~superseded~~) | **0.920** | 0.200 | 0.536 | **0.799** | 4–7 |
| baseline (fixed window) | 0.886 | **0.322** | **0.562** | 0.685 | 13–17 |

Read honestly: RLM wins boundary quality; retrieval is **mixed and confounded** — RLM's recall@5
edge is inflated by having only ~5 chunks/doc (top-5 ≈ retrieve-everything), and on the fair metric
(recall@1) the baseline wins. Decisively, this proxy is **dense-over-summary only** and cannot see
the RLM design's core advantage — the **sparse-over-full-text leg** — which is exactly where the
baseline currently wins (literal matches). So GATE-1 is not the definitive test of the RLM chunker.

**Decision (human, 2026-07-09): KEEP the RLM chunker (not dropped). Do NOT build T18 now — defer.**
The elaborate small→large escalation is not justified on this dense-only proxy; the earns-its-cost
call is made at **GATE-2 / T22**, with the full hybrid pipeline. If the RLM chunker does not prove its
value there, **make it optional (a seam/config toggle), not dropped**. Proceed to T21.

**Eval caveat (standing rule, [[evals-in-depth-no-shortcuts]]):** this run used only the 4 shortest
well-covered docs to finish faster — a shortcut. GATE-2's eval must be in-depth and honest: a full or
genuinely representative sample (short AND long docs, all archetypes), never downscaled for time.

---

## Phase 4 — Build, read-side (spec Phase 1, FR-Q)

### Task T21: Hybrid search (server-side RRF, metadata filters)

**Description:** Fuse dense-over-summary and sparse-over-full-text results server-side by RRF in
ArcadeDB into one ranked candidate list, honoring metadata filters (FR-C.3, FR-Q.1). Grounded
surface: ArcadeDB `vector.fuse` (proven end to end at T14).

**RAC-21:**
- [x] A query returns one RRF-fused ranked candidate list from the dense and sparse legs. The query
  is embedded once (dense + sparse over the query text) via the T19 `Embedder` seam; both vectors go
  to the new semantic query-side seam method `Store.hybrid_search(dense, sparse, *, k, filters)`, which
  the ArcadeDB impl fuses server-side (`vector.fuse` RRF over the dense `vector.neighbors` and sparse
  `vector.sparseNeighbors` legs, the T14-proven SQL). Each leg fetched to `DEFAULT_CANDIDATE_POOL=100`,
  fused, cut to `k`. `hybrid_search(...)` returns a `HybridSearchResult` (RRF-ranked `Candidate`s).
  **Live (`-m store`): c1 (dense-leg winner) + c2 (sparse-leg winner) fuse to the top two; weak c3 is
  excluded.**
- [x] Metadata filters are honored. Equality filters applied post-fusion as a wrapping `WHERE` on the
  stored columns (proven at T14). **Live: a `source_doc_id='docA'` filter returns only docA candidates.**
- [x] recall@k per archetype is **measurable** on the golden set: `hybrid_search(query, ..., k)` gives
  the `retrieve(query, k)` shape the T9 harness injects. **The golden-set recall run itself is deferred
  to GATE-2/T22** (it must be in-depth, all archetypes, short AND long docs, per
  [[evals-in-depth-no-shortcuts]]); T21 delivers the measurable capability, not the gate.
- [x] Registered under FR-C.3. `register_hybrid_search` → `hybrid_search`, kind `function`, contract
  `HybridSearchResult`.
- [x] ARD-registered: manifest authored + **present in the shared root**
  (`~/.air/registry/hybrid_search.json`, `urn:air:dreamai.io:rag_wright:hybrid_search`, kind `function`
  with response bounds; representative queries authored); mirror conformance green; re-validates as a
  `RegistryEntry`. Live `RegistryStore(root)` load / `discover` is the GraphWright-side step (not here).

**Verification:** `uv run pytest tests/capabilities/test_hybrid_search.py` (5 hermetic passed); live:
`... -m store` (2 passed — real ArcadeDB server-side RRF + metadata filter); manifest:
`tests/capabilities/test_manifests.py`. Full suite 285 passed + 15 skipped. Publish:
`uv run python scripts/publish_manifests.py`.

**Dependencies:** T14, T20. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/capabilities/hybrid_search.py`, `tests/capabilities/test_hybrid_search.py`,
`src/rag_wright/store/{seam,arcadedb}.py` (query-side seam method + ArcadeDB RRF SQL, `_sql_literal`,
`DEFAULT_CANDIDATE_POOL`), `src/rag_wright/capabilities/manifests.py` (+`hybrid_search` spec),
`tests/store/test_arcadedb_schema.py` (stub extended to the query-side seam).
**Note:** No new ADR — reuses ADR-0007's ArcadeDB store/schema + the T14-grounded `vector.fuse` SQL.
Metadata filtering is **post-fusion** (filter the pooled fused list, then cut to `k`); the `pool=100`
per leg gives fusion and the filter room. The capability preserves the server's RRF order and does not
re-rank — that is the reranker's job (T22, the precision gate).

### Task T22: Reranking (cross-encoder precision gate)

**Description:** A cross-encoder in the BGE-reranker family reranks the candidate list and cuts it
to a top set before any expensive work, the precision gate before synthesis (FR-C.4, FR-Q.2).
Grounded surface: `FlagEmbedding` `FlagAutoReranker`.

**RAC-22:**
- [x] The candidate list is reranked and cut to a top-k set. `rerank(query, passages, *, reranker,
  top_k)` scores each `(query, passage)` pair via the BGE cross-encoder seam, sorts by score desc
  (stable — ties keep fused order), cuts to `top_k`. `Passage` in → `RerankResult` (ranked
  `ScoredCandidate`s). Pure query-side function; it does not fetch text (the graph wires summary vs
  full text). Live `-m rerank`: real `bge-reranker-v2-m3` ranks a relevant passage above an irrelevant.
- [ ] **PENDING valid queries.** Rerank improves precision@k over the raw fused list on the golden set.
  **GATE-2 run 1 could NOT adjudicate this** — rerank was ~neutral only because the relevant chunk was
  rarely in the candidate pool (CUAD-question query artifact, ADR-0011), which is not a rerank result.
  Closes when measured on ACORD's content-bearing queries (the ACORD task). **Do not check this bullet
  until then; T22 is committed with it explicitly open.**
- [x] Registered under FR-C.4. `register_reranking` → `reranking`, kind `function`, contract `RerankResult`.
- [x] ARD-registered: manifest present in the shared root (`~/.air/registry/reranking.json`,
  `urn:air:dreamai.io:rag_wright:reranking`, kind `function`); mirror conformance green; re-validates as
  a `RegistryEntry`. Live `RegistryStore(root)` load is the GraphWright-side step.

**Verification:** `uv run pytest tests/capabilities/test_reranking.py` (8 hermetic passed); live:
`... -m rerank` (1 passed, real cross-encoder). Full suite 294 passed + 16 skipped. Publish:
`uv run python scripts/publish_manifests.py`.

**Dependencies:** T21. **Scope:** M. **Status:** capability done; RAC-22 b2 open pending ACORD.
**Files:** `src/rag_wright/capabilities/reranking.py`, `tests/capabilities/test_reranking.py`,
`src/rag_wright/capabilities/manifests.py` (+`reranking` spec), `pyproject.toml` + `conftest.py`
(`rerank` opt-in marker; `transformers>=4.44.2,<5` pin — ADR-0010).
**Note:** transformers pinned <5 (ADR-0010): FlagEmbedding's reranker needs the `prepare_for_model` that
transformers 5.x removed. RAC-22 b2 is the GATE-2 rerank-precision measurement (see GATE-2 run 1 below).

### GATE-2: Recall-bar, ArcadeDB hybrid vs LanceDB fallback (branch point)

**Not a task.** GATE-2 carries **two** branch decisions, measured on the golden sets (T9 + T10), each
leg measured separately (§12, plan §2).

**(a) The store recall bar — ArcadeDB hybrid vs. LanceDB fallback.**
- **Meets the bar →** continue with ArcadeDB.
- **Underperforms →** substitute LanceDB for the retrieval leg behind the query-skill seam
  (FR-S.5) — the one eval-gated fallback, not a default. Raise with the human; the seam (T13)
  already makes the swap local to the store implementation.

**(b) The RLM chunker earns-its-cost call (deferred here from GATE-1).** The gate **measures** the RLM
chunker; it does **not get to delete** it. The only decision space is **keep-as-default vs. make-optional
(a seam/config toggle)** — never "drop" (ADR-0009). Rationale: RLM chunking's job is efficient semantic
*boundary preservation* (meaningful units — chapters/sections/sub-sections — not fixed-window cuts), and
meaningful chunks + the Knowledge Graph are two halves of one design for complex cross-part/temporal
reasoning grounded and cited (why ArcadeDB holds both in one store, FR-S.1). This is enterprise-grade RAG
for *any* corpus, not eval-chasing the current one: a capability that does not win on this corpus/query
mix is not thereby useless; semantic chunking's advantage is corpus- and query-type-dependent.
- **Consequence for the eval:** GATE-2 must be able to *see* RLM's advantage or it proves nothing about
  it — it must exercise cross-part / multi-hop / temporal queries (the T10 relational set exists for
  this) and account for KG synergy, on a full or genuinely representative sample (short AND long docs,
  all archetypes), never downscaled for time ([[evals-in-depth-no-shortcuts]]). GATE-1's 4-shortest-docs
  dense-only proxy is explicitly **not** sufficient for this call.
- Record the outcome (keep-default vs. optional) with its eval evidence, and raise with the human.

**GATE-2 run 1 (2026-07-11, `eval/gate2_hybrid_rerank.py`, 21-doc stratified sample) — NOT ADJUDICATED.**
The run used CUAD golden questions as cross-corpus retrieval queries and returned implausibly low recall
(exact_lexical recall@1 0.027; recall@10 0.13–0.29 across archetypes, both chunkers; rerank ~neutral).
Diagnosed before reporting: **CUAD is an extraction/classification benchmark, not a retrieval one** — its
questions/category labels share ~2–9% vocabulary with the answers, so the numbers measure a
query-formulation artifact, not the retriever (ADR-0011, [[cuad-not-a-retrieval-benchmark]]). GATE-1's
per-doc RLM recall@5 0.799 proves the pipeline retrieves correctly in a small haystack; the collapse is
the big-haystack × content-free-query combination, which hits both chunkers and any store equally.
Strict bookkeeping:
- **(a) store bar:** *not adjudicated on this run; no signal favoring LanceDB.* Keep ArcadeDB. **Not marked
  met** (we neither switch on an inconclusive measurement nor claim a bar we did not measure).
- **RAC-22 b2 (rerank precision):** *pending valid queries*, not checked.
- **(b) RLM earns-its-cost:** unchanged — post-graph GATE-2b (needs cross-part/multi-hop/temporal + KG),
  and blocked additionally by the T17 chunker bug below.
- **Genuinely proven (the run's real result):** the full read+ingest pipeline ran end to end at scale with
  zero crashes — 21 docs, both chunkers, parse → chunk → embed (dense+sparse) → ArcadeDB write →
  server-side RRF hybrid → BGE rerank, over 630–840 chunks. Real T16–T22 integration validation.
- **Fix path:** content-bearing queries via **ACORD** (T33, ask-first), and the **T17 chunker fix** (T-CHK)
  before any RLM-vs-baseline numbers.

**GATE-2 run 2 (2026-07-12, `--per-doc`, after T-CHK + T-SUM) — within-document signal only.** Re-ingest
on the fixed chunker: **0 summarizer fallbacks** (was 7 — confirms they were the split bug), RLM chunks
296 (was 632), no near-empty. Per-doc (within-document retrieval) recall is now believable (exact_lexical
R@1 ≈ 0.49 vs 0.027 cross-corpus in run 1 — confirms run 1's collapse was the CUAD-query artifact). RLM
edges baseline on recall in nearly every cell (reversing GATE-1's superseded signal), **but that edge is
partly confounded by RLM's lower chunk count** (fewer chunks → easier recall@k); on **precision@5 the two
are ~tied** (RLM .232/.136/.143 vs baseline .215/.122/.153). Rerank is marginally positive/neutral per-doc
(small haystack gives it little room). **Adjudicates nothing:** not the store bar (cross-corpus), not
RAC-22 b2 (rerank — stays pending ACORD), not the RLM keep-vs-optional call (GATE-2b, needs
cross-part/multi-hop/temporal + KG). Honest takeaway: on clean chunks RLM is **at least as good as
baseline**, not worse.

### Task T33: ACORD content-query retrieval eval (extends T9) — ask-first

**Description:** Ingest ACORD (Atticus Clause Retrieval Dataset: CC-BY-4.0, BEIR, 114 attorney-authored
queries, ~126k graded query-clause pairs, corpus of SEC/EDGAR + F500 ToS clauses — same family as ours,
distinct clause pool) into the store via the pipeline and run its expert queries through hybrid search +
rerank, scored against qrels. Supplies the content-bearing cross-corpus retrieval bar CUAD cannot
(ADR-0011). Adjudicates **GATE-2(a) store bar** and **RAC-22 b2 rerank precision**; does NOT exercise the
RLM chunker (pre-segmented clauses), so the RLM call stays at GATE-2b. **LLM-generated queries are rejected
as circular; if ACORD is unusable, return to the human before any alternative.**
**Status:** UNHELD / criteria LOCKED (2026-07-17). ACORD IS the **joint golden set**: one benchmark, one
ground truth (its qrels), two metrics. **T33 is RAG_Wright's retrieval half** — recall/nDCG over qrels on
the retrieve→rerank→fusion legs. GraphWright owns the **synthesis half** — citation-recall on
rehydrate→synthesis→answer. Both repos build toward this shared criteria; T39 (extraction-depth grading)
is a logged follow-on out of this milestone.

**Grading model:** **deepseek-v4-pro** (`STRUCTURED_REASONING`) pins the answer half — the project's
"larger model for quality-sensitive extraction" (ADR-0006), what the synthesis workers actually run and
what RAG_Wright's re-validations ran on. The joint eval pins ONE model (GraphWright's node had run
qwen3.7-plus/`_SECONDARY`); RAG_Wright owns the benchmark design and pins deepseek-v4-pro.

**Locked acceptance criteria (set before the run):**
- **Retrieval (RAG's half) — primary gate: `recall@50 ≥ 0.70`.** k=50 = the fused/reranked pool that
  feeds synthesis; this is the true system ceiling — a relevant clause that never reaches the evidence is
  unrecoverable downstream. `recall@10 ≥ 0.50` and `nDCG@10 ≥ 0.40` are reported, **not gated** (ranking
  diagnostics).
- **Answer (GraphWright's half) — `citation-recall ≥ 0.75`, conditioned on evidence reaching synthesis**
  (isolates the answer half without penalizing it for retrieval's misses).
- **PIN 1 — acceptance is the CONJUNCTION, with ordering: `recall@50 ≥ 0.70` AND `citation-recall ≥ 0.75`.**
  The retrieval gate is the **precondition** that makes citation-recall interpretable. Citation-recall
  alone is never a pass: conditioning on *arrived* evidence shrinks the denominator when retrieval
  underperforms, so a high conditioned value over a tiny arrived set (extreme: 1 clause arrived + cited →
  1.0) can look best exactly when retrieval failed. Retrieval passing = substantial evidence arrived = the
  citation-recall number is trustworthy.
- **PIN 2 — zero-arrival queries** (no relevant clause reached synthesis; citation-recall undefined) are
  **counted as retrieval failures under the recall gate, EXCLUDED from the citation-recall population** —
  not scored 1.0 or 0.0, not silently dropped. Pinned before the run because how the degenerate cases fold
  in affects the aggregate.
- **Pre-run-judgment caveat:** thresholds are calibrated to ACORD's difficulty (hard attorney clause
  retrieval), not measured achievability (T33 unbuilt). If a dry run shows them miscalibrated, recalibrate
  **before the graded run, never after** — preserves the "set before results" discipline.
- **PIN 3 — the conditioning assumed a passing retrieval bar; the product rule grounds the bar (2026-07-18).**
  This makes PIN 1's precondition explicit and resolves how the retrieval bar is set (supersedes the
  ungrounded `recall@50 ≥ 0.70`).
  - **Hidden assumption, now explicit:** conditioned citation-recall (denominator = relevant clauses that
    *reached synthesis*, not all relevant) only isolates the synthesis half *fairly when a substantial,
    representative evidence set arrives* — i.e. when retrieval clears its bar. Below the bar the arrived set
    is small and **biased toward easy-to-retrieve clauses**, so the conditioned number measures synthesis on
    an unrepresentative subset and is **not interpretable alone**.
  - **Reading rule below bar:** the honest end-to-end number is the **product**
    `end_to_end_completeness = retrieval_recall × conditioned_citation_recall` (telescopes to
    `|cited ∩ relevant| / |relevant|`, completeness over ALL relevant, since cited ⊆ arrived). At the current
    fused recall ~0.38 the answer can miss ~60% of relevant evidence while the conditioned value looks
    excellent. **If synthesis is run while retrieval is below bar, report the product, never the conditioned
    value alone** — it is a product *diagnostic*, not a graded citation-recall.
  - **Consequence:** the conjunction's left operand is not met, so the graded run **cannot** be framed as
    "retrieval validated, now test synthesis." Retrieval reaches its grounded bar first.
  - **RESOLUTION — the grounded recall bar = `E / 0.75`**, where `E` = the end-to-end evidence-use
    completeness target (what fraction of ALL relevant clauses a good answer must draw on) and `0.75` is the
    citation-recall gate. This derives the recall bar from the eval's actual success criterion, not a guess —
    what was missing when 0.70 was ungrounded. **nDCG stays DIAGNOSTIC, not the gate** (it measures top-10
    ranking; the end-to-end outcome is bounded by recall, not nDCG). Not "achievable recall" either (that is
    fit-to-result). **Coherence requirement:** the recall-gate `k` MUST equal the synthesis evidence-feed cap
    (top-`k` fused → synthesis), so `recall@k` = the fraction of relevant that arrives = the product's
    retrieval term (today `DEFAULT_UNION_CAP=20` ≠ 50 — align before the graded run).
  - **`E = 0.5`, `k = 50` SET (grounded bar = 0.5/0.75 = `recall@50 ≥ 0.667`).** Rationale: a complete answer
    draws on the *majority* of the relevant evidence (principled, not fit — current 0.38 fails it, so it is a
    real target, not a lowered bar). Framing endorsed 2026-07-18; `E` adjustable but do NOT lower it to make
    0.38 pass (0.38 fails E=0.375/0.45/0.50 alike). `k=50` chosen so the synthesis evidence-feed cap must be
    aligned to 50 (today `DEFAULT_UNION_CAP=20` — align before the graded run so `recall@50` = the arrived set).
    **Achievability flag:** raw-hybrid `recall@200 = 0.65` is the retrieval *ceiling*, and `recall@k ≤
    recall@200`, so 0.667 is *above* today's ceiling — reaching it requires improving the retriever itself
    (legal-tuning / stronger model / higher-recall retrieval), NOT just rerank. The gap (0.38 → 0.667, ceiling
    0.65) is the retrieval investment the grounded bar demands; 0.38 is the gap to close, not a bar to lower to.
    The three earlier gate options collapse: nDCG-gate dropped (wrong metric), recall regrounded as `E/0.75`
    (grounded, not fit), retrieval-investment is the *consequence* of the grounded bar.

**STRATEGY — ceiling, not bug (2026-07-18): decouple integration from retrieval quality.** Both real bugs are
fixed (SQL-newline dead-lettering; NOTE the dense leg was never misconfigured for ACORD — summary=text — so
0.45–0.47 pool-nDCG is BGE-M3's genuine off-the-shelf ceiling, not a post-bug residual). The remaining gap to
ACORD's 0.54–0.64 baseline is a **capability ceiling**, which gets invest-or-scope, not more diagnosing.
- **The query-graph INTEGRATION PROOF and the graded eval are separate achievements; retrieval quality must
  not block the integration.** A correct graph over a known-weak retrieval leg is still a correct graph (a
  known-weak leg, not a broken architecture) — the KI-1 move: a bounded, liftable, documented limitation,
  proceed on the correct path around it. RAG's integration-readiness is DONE: every query-side capability is
  built, tested, registered, and ARD-published (hybrid_search, reranking, fusion, chunk_read, rlm_synthesis),
  so GraphWright can compile+run the query graph binding them (that graph is compiler work, not this repo's).
- **Run the graded eval as an HONEST BASELINE, not a pass/fail to force.** Report the real conjunction:
  retrieval below its grounded bar → the eval does not pass; and the **product** `retrieval_recall ×
  conditioned_citation_recall` = true end-to-end evidence-use (caps low at ~0.38 regardless of synthesis).
  "Retrieval is the bottleneck at 0.38, here is the measured state" is a valid, useful result. Do NOT lower E.
- **Retrieval quality becomes its own scoped workstream (T41), NOT inline W10 work.** Its first question is
  ceiling-vs-tuning-gap (below), and its broader question is whether clause-level legal retrieval wants a
  different approach than the summary-era design — the THIRD time the summary-centric assumption has bitten
  (synthesis needed text T40; general dense-over-summary for real docs; and this clause-level retrieval gap,
  all downstream of the same clause-level-precision demand).

**Description (build scope):** Ingest ACORD (Atticus Clause Retrieval Dataset: CC-BY-4.0, BEIR, 114
attorney-authored queries, ~126k graded query-clause pairs, corpus of SEC/EDGAR + F500 ToS clauses) into
the store via the pipeline and run its expert queries through hybrid search + rerank + fusion, scored
against qrels for the recall gate. Rehydration to text is the **`chunk_read` governed capability (T38)**,
not caller-side plumbing. Does NOT exercise the RLM chunker (pre-segmented clauses), so the RLM
keep-vs-optional call stays at GATE-2b. **LLM-generated queries are rejected as circular; if ACORD is
unusable, return to the human before any alternative.**
**Dep:** T21, T22, T38 (chunk_read), T40 (sidecar). License + corpus provenance verified (2026-07-12).

**Ingest constraint (load-bearing, verified before build 2026-07-17):** ACORD ingest MUST go through
`ChunkWriter.write_document` (index upsert + sidecar write under one gate + completeness guard), **never a
direct `store.upsert_chunk`** — a direct write would populate the index while bypassing the sidecar and
break the invariant below. Pre-segmented ACORD clauses skip the RLM chunker but still flow chunk → embed
(BGE-M3) → `write_document(records, texts=...)`.

**Rehydration invariant + eval-integrity handling (pinned before the run):** the chain
**indexed ⟹ sidecar-text-present** (T40 guard) and **fusion surfaces only indexed chunk_ids** (hybrid_search
returns only `Chunk`-index rows; reranking passes ids through; the graph leg is empty on the retrieval
path) → `chunk_read` **cannot miss on a clean ingest**. Therefore a `chunk_read` `KeyError` during the eval
is a **can't-happen**, i.e. the invariant was violated (a real ingest/sidecar bug). So the harness treats a
`chunk_read` raise as an **eval-integrity failure**: caught, surfaced loudly with the query and chunk_id,
**halt-and-investigate** — never folded into a metric, never swallowed by a generic run abort. (Sits
alongside the already-pinned zero-arrival-query handling: those count as retrieval failures under the
recall gate and are excluded from the citation-recall population.)

**First synthesis run on pipeline-persisted text:** T40 (persist) + T38 (rehydrate) close the loop, so this
is the first time synthesis runs on pipeline text rather than test-provided text (earlier proofs used the
phantom-manifest text source). Carry into the run notes.

**ACORD acquired + fully mapped (2026-07-17).** Source pinned: HuggingFace `theatticusproject/acord`
(CC-BY-4.0), `scripts/acquire_acord.py` (download + extract + attribution under gitignored `data/acord/`).
BEIR format: `corpus.jsonl` = **3,931 clauses** `{_id, text}`; `queries.jsonl` = 114 `{_id, text, metadata}`;
`qrels/{test,valid,train}.tsv` = `query-id, corpus-id, score`. **Test split = 57 queries**, 61,988 judged
pairs. Grade scale **0–4** (readme 1–5 minus 1; "zero-score = irrelevant").

**Locked build decisions (grounded from ACORD's own semantics, set before results):**
- **Relevance floor = qrels grade ≥ 2** (readme 3★ "partially relevant" and up) — ACORD's OWN official bar
  (their 3-star precision@5). Three converging grounds: official metrics use a 3★ floor; grade 1 is
  explicitly "non-relevant but helpful"; and grade ≥ 2 gives ~10.9 relevant/query matching the readme's
  stated ~10 (grade ≥ 1 balloons to ~31/query, contradicting the benchmark). NOT a judgment call, not
  overridable — anything else measures against a relevance set ACORD's authors don't endorse and is
  incomparable to ACORD's results. grade ≥ 3 is stricter than the floor; grade ≥ 1 counts non-relevants.
- **Eval split = standard held-out test (57 queries).** The graded run is on test; train/valid are for
  tuning, not grading (never score an easier subset).
- **Metric roles (unambiguous):** **recall@50 binary at grade ≥ 2 is the pass/fail GATE** (recall@50 ≥ 0.70
  = the retrieve→rerank→fusion legs surface ≥70% of the ~10.9 relevant clauses/query in the top 50 —
  coherent with what was calibrated). **nDCG@10 uses the full graded 0–4 scale** (ACORD-official, NO
  threshold — nDCG rewards higher grades by design), **reported-not-gated**, the ranking diagnostic.
  recall@10 also reported. The relevance floor applies to the binary recall gate only; nDCG deliberately
  does not threshold.
- **Ingest mapping:** one clause = one chunk; `chunk_id` minted with `source_doc_id` = the ACORD `_id`, so a
  retrieved `chunk_id` rsplits back to its corpus-id for scoring with **no side map** (cannot drift);
  through the real `ChunkWriter.write_document` (index + sidecar, same-hash gate) — a real end-to-end
  ingest, not synthetic.
- **Dry-run before the graded run (pinned discipline):** run the retrieval legs against the grade-≥2 test
  qrels FIRST to confirm recall@50 ≥ 0.70 is achievable, not aspirational. If off, recalibrate BEFORE the
  graded run (never after), reasoning recorded. **Report the dry-run recall at the gate before the graded
  measurement locks.**

**Ingest completed (2026-07-17):** 3,931/3,931 clauses in `ragwright_acord`, 0 dead-letters — after fixing a
store bug the real corpus exposed: `_sql_str` didn't escape newlines, so ArcadeDB's SQL tokenizer rejected
every multi-line clause on upsert and silently dead-lettered 685 (~17%), exactly the long content-rich ones
(commit `51e8492`, regression test added). Two macOS operational fixes: BGE-M3 and the reranker default to a
multi-process pool that DEADLOCKS on macOS → forced single-process CPU (`devices=["cpu"]`) in the ingest and
the runner; embedding `max_length=1024` caps the vector compute (full text preserved in the sidecar).

**DRY-RUN RESULT — BELOW THRESHOLD (2026-07-17):** on the complete 3,931-clause index, 57 test queries:
`recall@50 = 0.379` (gate ≥ 0.70), `recall@10 = 0.137`, `nDCG@10 = 0.138`. Below-baseline nDCG was the
ADR-0011 red flag → diagnosed fully before any recalibrate-vs-fix verdict.

**DIAGNOSIS COMPLETE (2026-07-18) — retrieval is HEALTHY; the low full-corpus numbers are a TASK MISMATCH,
not a bug:**
1. **Leg isolation** (full-corpus, pool=200): dense recall@50 0.301 / nDCG@10 0.109; sparse 0.232 / 0.089;
   fused 0.283 / 0.102. Both legs work (dense > sparse), and **RRF fusion slightly HURTS recall@50**
   (0.283 < dense 0.301) — a real, tunable finding (the weak sparse leg drags dense-found relevants below
   rank 50). Not a dead/broken leg.
2. **Spot-check** (top-10 fused, length-annotated): hits are **topically plausible, not garbage** — "Audit
   Rights" → all 10 audit clauses; "England Governing Law" → 8/10 relevant, first at rank 1; "multiple
   governing laws" → all governing-law clauses but *single*-law ones (the "multiple" nuance missed, first
   relevant at rank 16). Failure class = fine-grained legal-nuance ranking = genuine difficulty, NOT a bug
   (relevant clauses mean 583 chars — no short-vs-long embedding pathology seen).
3. **Baseline reference (ADR-0011 completed)** — ACORD paper Table 3 retrieval-only nDCG@10: BM25 0.540,
   MiniLM bi-encoder 0.572, OpenAI-large 0.641 (topline MiniLM+GPT4o-reranker 0.812). Those are per-query
   judged-POOL rankings (~1,088 explicitly-annotated clauses/query), not full-corpus. Ranking my retriever
   **like-for-like on the judged pool: nDCG@5 = 0.417, nDCG@10 = 0.452** — squarely in ACORD's baseline band
   (slightly below BM25's 0.54, as expected for off-the-shelf BGE-M3). So the earlier full-corpus nDCG 0.10
   was the *harder task* (rank 3,931 vs rank ~1,088), not a defect.

**VERDICT:** NOT a bug — retrieval performs at ACORD-baseline level on the like-for-like task. The `recall@50
≥ 0.70` bar was an ungrounded pre-run guess against the WRONG task: full-corpus recall@50 on ACORD is far
harder than the pool-ranking ACORD baselines measure, and ACORD publishes no recall@50 to anchor it. Two
real levers remain (improvements, not bug-fixes): (a) **fusion tuning** — dense-alone beats fused @50, so
RRF weighting / dropping the weak sparse leg is worth testing; (b) lift embedding `max_length`. **OPEN
DECISION at the gate (do NOT lock a graded bar until decided):** recalibrate the bar to grounded/achievable,
and/or switch the retrieval metric to pool-ranking nDCG@10 (directly comparable to ACORD baselines), and/or
invest the fusion/max_length improvements first.

### Task T40: Chunk-text sidecar — persist full chunk text at ingest (FR-I.3, RAC to follow)

**Why:** grounding for T38 (`chunk_read`) surfaced that the full chunk **text is persisted nowhere**. The
index is dense-over-summary by design (`ChunkRecord` = summary + vectors, no raw text), and the text is
used once to compute the `chunk_id` content hash then **discarded** (`chunk_write.to_chunk_record`). Both
the `ChunkRecord` docstring and the `SynthesisChunk` docstring claimed the text *"lives in the parse
manifest keyed by chunk_id"* — **a phantom manifest that was never written** (the only parse manifest is
the whole `DoclingDocument`, keyed by doc, not per-chunk). So the synthesis leg (FR-Q.5) was built against
a text source that never existed; its earlier proofs (T28) ran on test-provided text. **T40 provides the
text source synthesis has always assumed and never had** — the first pipeline-persisted-text path.

**Confirmed synthesis needs text, not summary:** (1) architectural — `SynthesisChunk` is `{chunk_id, text}`
and the extractor operates over `.text`, never reading `summary`; (2) eval — ACORD qrels are relevant
*clause text* and citation-recall grades citing the actual clauses; summaries are lossy for legal language,
so summary-fed synthesis would grade a mismatched measure. Summary does **not** suffice.

**Design (chosen: sidecar, Option 1 of 3):** `ChunkTextStore` (`store/chunk_text.py`) — one JSON file per
`source_doc_id`, mapping the canonical `chunk_id` string → full text. NOT part of the swappable `Store`
seam (holds no vectors, answers no query). Rejected: adding `text` to the ArcadeDB schema (reverses the
dense-over-summary decision, bloats the index with large legal text) and reconstruct-from-parse-manifest
(recompute-at-retrieval the architecture avoids). Shape trimmed to `{chunk_id, text, source_doc_id}` for
T38 (dropped `summary` — no consumer reads it).

**Same-gate coupling (load-bearing):** the sidecar write is inside `write_document`, at the same loop point
as `upsert_chunk`, driven by the **one** content-hash gate — an unchanged doc skips both, a changed chunk
writes both, so index and sidecar cannot drift. A completeness guard rejects any `records` write whose
`texts` map is missing a chunk_id **before any write**, so a chunk can never land in the index without its
text in the sidecar. Delete-by-`source_doc_id` (`delete_document`) is built as the T34 lifecycle seam.

**Integrity check (self-verifying store):** `ChunkTextStore.put` asserts `sha256(text) == chunk_id.content_hash`
before persisting — the `chunk_id` already carries the hash `ChunkId.of` computed over these exact bytes at
chunking, so the sidecar can prove it stores the faithful text for the id. A mismatch (loop index error,
mismatched map) is the **silent-wrong-text** class that would otherwise surface only as synthesis citing a
structurally-valid-but-wrong `chunk_id` — which citation-recall may not catch. Write-time-cheap (the hash
was already computed at ingest; the check compares a value in hand against a value in hand) and impossible
to add faithfully later; rejected at the store boundary so no caller can bypass it.

**Files:** `src/rag_wright/store/chunk_text.py` (new), `src/rag_wright/capabilities/chunk_write.py`
(`ChunkWriter` gains required `text_store`; `write_document` gains `texts` + gated sidecar write),
`tests/store/test_chunk_text.py` (new), `tests/capabilities/test_chunk_write.py` (updated + same-gate and
guard tests). Contract comments corrected in `contracts/chunk.py` and `rlm_synthesis.SynthesisChunk`.
**Verify:** `uv run pytest tests/store/test_chunk_text.py tests/capabilities/test_chunk_write.py` (15 passed,
1 skipped live). Full suite 395 passed + 26 skipped. **Status:** awaiting-approval. **Dep:** T17, T19, T20.

### Task T38: chunk_read — governed text-rehydration capability (FR-Q)

**Why:** fusion (FR-Q.4) yields a capped set of `chunk_id`s; synthesis (FR-Q.5) extracts over full chunk
text the index does not hold (dense-over-summary). `chunk_read` is the rehydration step between them,
reading the T40 sidecar → text per `chunk_id`. Exposed as a **governed, discovered, bound capability**
(the reconciliation's requirement: rehydrate is a node, not caller-side plumbing), so GraphWright's
compiler binds a real registered capability rather than doing the lookup itself.

**Design:** mirrors the `fusion` FR-Q node (function-kind, result contract, `register_*`). `chunk_read.py`:
`ChunkText` = `{chunk_id, text, source_doc_id}` (summary dropped — no consumer reads it; `source_doc_id`
derived from the id, no second store read), `ChunkReadResult{chunks}`, `chunk_read(chunk_ids, *, text_store)`,
`register_chunk_read`. **No silent drop:** a `chunk_id` the sidecar cannot supply (orphan, or an id never
received) raises `KeyError`, never skipped — the FR-Q.6 no-claim-without-citation discipline applied to
evidence. Registered as a canonical FR-Q slug (precedent: `fusion` is FR-Q.4, not an FR-C-catalog capability
either); the parametrized manifest test now authors + publishes chunk_read's ARD manifest.

**Note (carried from T40):** this is the read side of the first **pipeline-persisted-text** path — synthesis
had only ever run on test-provided text (phantom manifest). T38 + T40 close that loop end to end.

**Files:** `src/rag_wright/capabilities/chunk_read.py` (new), `capabilities/registry.py` (+`chunk_read`
canonical slug), `capabilities/manifests.py` (+ARD manifest spec), `tests/capabilities/test_chunk_read.py`
(new). **Verify:** `uv run pytest tests/capabilities/test_chunk_read.py` (4 passed) + manifest/registry
regression (55 passed). Full suite 400 passed + 26 skipped. Publish to the shared ARD root
(`scripts/publish_manifests.py`) on approval. **Status:** awaiting-approval. **Dep:** T40.

**GRAPH-LEG CORRECTION (2026-07-18) — supersedes the "ceiling" framing; pauses the premature handoff.**
The 0.38 dry-run is **dense+sparse ONLY**: the graph leg was empty (`_EMPTY_GRAPH`) and no ACORD graph was
ever built (`entities: 0`). Crucially, the graph is **NOT bounded to entity/`CONTRACTS_WITH`** — that is
merely the CUAD/EDGAR instantiation. The graph capability is a **general, domain-adaptable relationship
layer**, and constructing domain-appropriate relationships (a **clause-relation graph** for ACORD) plus a
**clause-anchored graph retrieval**, fused into hybrid search, is the *design intent* of putting graph in
the hybrid engine — not a new capability. (My earlier "graph leg inapplicable to ACORD / three mismatches"
read was WRONG: it assumed the one fixed `CONTRACTS_WITH` schema.) So **0.38 is a TWO-LEG PARTIAL
measurement, and the "capability ceiling" verdict was premature.** Completing the three-leg measurement
precedes any ceiling/T41 judgment; the graded-run handoff pauses until retrieval is fully measured.
**Design the graph EMPIRICALLY, not by guess:** analyze ACORD's relevance structure (for failing queries,
what relates a query's relevant clauses to each other / connects missed relevants to retrievable anchors —
shared entities, clause category, cross-references, semantic clusters) → construct the edges the data
implies → wire clause-anchored graph-expansion into the candidate set → re-measure three-leg recall@50 /
pool-nDCG. If it moves toward the grounded bar, the "ceiling" was a missing leg; if not, the ceiling is real
across the full design and T41 stands with much stronger justification.

**HANDOFF to GraphWright — UN-PAUSED for the INTEGRATION PROOF (2026-07-18, integration-first).** Strategic
call: integration proof first, retrieval optimization later. The three-leg question is resolved (the graph
leg does not help ACORD — mechanism proof below), so the retrieval baseline is settled at two-leg ~0.38, and
that is **good enough to produce cited answers end to end** — which is all the integration proof needs.
**The graded number is a BASELINE the recomposition experiments will move, NOT a pass/fail gate.** The five
query-graph capabilities are PUBLISHED + DISCOVERABLE in the shared root `~/.air/registry` (verified
2026-07-18: `hybrid_search`, `reranking`, `fusion`, `chunk_read`, `rlm_synthesis` — valid URN + kind +
representativeQueries each; none local-only) → GraphWright's discover-and-compile is unblocked. Ready:

**INTEGRATION PROOF PASSED (GraphWright dry-run, 2026-07-18).** Compiled-from-discovery (5 nodes, all bound
by registry discovery) → real retrieval over the 3,931 ingested ACORD clauses → grounded, query-relevant,
chunk-tied citations, end to end. "Audit Rights": 10 citations, 6/6 checked resolve via `chunk_read`, 6/6
extracts match their cited chunk, extracts topically correct. RAG's capabilities are integration-validated.
The one low number is RETRIEVAL not synthesis (1/12 cited-are-qrel-relevant = the known recall@50 0.379 /
query-representation gap, T41), and it's a top-10-capped dry-run sanity signal, not the citation-recall
metric. Two RAG live-bugs surfaced only by real data along the way (SQL-newline dead-letter; `[` in extract
text) → **standing test-discipline item: RLM/synthesis/store live tests want at least one deliberately
messy fixture (brackets, newlines, quotes), not just clean values** — fold into the latent-hardening pass.
- **Capabilities** — all query-side ones built, tested, registered, and ARD-published to `~/.air/registry`
  (`hybrid_search`, `reranking`, `fusion`, `chunk_read`, `rlm_synthesis`, `graph_query`, `generation`).
- **Store** — `ragwright_acord` DB (3,931 chunks) + the chunk-text sidecar (`data/acord/chunk_text/`) for
  `chunk_read`. **Eval assets** — `eval/acord.py` (loader, grade≥2 floor, test split), `eval/acord_retrieval.py`
  (recall + graded nDCG), the diagnostics under `scripts/`.
- **Feed-cap / gate-`k` coherence — CORRECTION to my earlier proposal:** do NOT change `DEFAULT_UNION_CAP`
  (20 is a spec value, §16.7). Instead the graded run binds `fuse(..., cap=k)` via the existing parameter so
  `recall@k` = the arrived set (the product's retrieval term). **Open sub-choice:** `k=20` (the spec
  production cap — most production-representative; `recall@20` is lower) vs `k=50` (PIN 3 as-set — more
  synthesis context but above the spec production cap). Recommend the graded run set `cap` explicitly = the
  chosen gate-`k`; flagged for GraphWright/user, not silently defaulted.
- **Read the result as the product** `retrieval_recall × conditioned_citation_recall` (PIN 3); retrieval is
  below its grounded bar, so the run is an honest baseline, not a forced pass. Do NOT lower E.

### Task T41: Retrieval-quality mini-project — ceiling-vs-tuning-gap, scoped workstream (T33 finding)

**Why:** after fixing the one real ingest bug (SQL-newline), ACORD retrieval sits at full-corpus recall@50
0.379 / pool-ranking nDCG@10 0.45–0.47 vs ACORD's published retrieval-only baselines (nDCG@10: BM25 0.540,
MiniLM 0.572, OpenAI-L 0.641). Dense was never misconfigured (summary=text for pre-segmented clauses), so
this is a **capability ceiling, not a bug** — it gets invest-or-scope, in its OWN workstream, NOT inline in
the integration milestone (which proceeds on current retrieval; the weak leg is documented, KI-1 style).

**Scope — answer the ceiling-vs-tuning question BEFORE any model swap:**
1. **First task (the fork):** establish *what retriever produced ACORD's 0.54–0.64 baseline*. If it used a
   stronger/differently-tuned retriever, our gap is "weaker setup than baseline" → match it (cheaper). If it
   used BGE-M3 or comparable and still beat 0.45–0.47, then BGE-M3 has headroom we're not reaching (chunk
   granularity, query formulation, fusion weighting, un-capped `max_length`) → tune to its actual ceiling,
   not swap. Determines expensive-model-change vs cheaper-tuning-close.
2. **Cheap levers to test regardless:** drop/downweight the weak sparse leg (dense-only already beats fused:
   pool nDCG 0.452→0.469, recall@50 0.291→0.307); un-cap embedding `max_length` (1024→full; marginal, ~1–3%
   of clauses); higher hybrid pool feeding rerank.
3. **Broader question to ASK (not answer now):** does clause-level legal retrieval want a different retrieval
   approach than the summary-era design? The summary-centric assumption has now bitten three times (T40
   synthesis; general dense-over-summary for real docs; this retrieval-quality gap) — all downstream of the
   clause-level-precision demand. Scope the project to ask it, not default to a model swap and hope.

**RELEVANCE-STRUCTURE ANALYSIS (2026-07-18, `scripts/diagnose_acord_relstructure.py`) — STRONG positive
signal for the graph leg; confirms 0.38 was a two-leg partial.** 57 queries: relevant-set cluster tightness
**0.686** >> random-pair 0.561, and > query→relevant **0.534** (gap **+0.152**) — the relevant clauses
cluster tighter among themselves than the query is to them (the query sits OUTSIDE the relevant cluster),
the exact case a clause-anchored graph beats query-anchored retrieval. **50/57 queries have a retrieved
anchor** to expand from (7 zero-anchor → need better base retrieval, not a graph). Missed relevants are
reachable from retrieved anchors: **missed→anchor max cosine mean 0.706 (88% ≥0.6, 56% ≥0.7)**. So a
clause-relation graph — clause-clause semantic kNN and/or a **category graph** (ACORD's query `category`
metadata + `graph_extraction`'s clause-category tagging already exist) — with **clause-anchored expansion
from retrieved anchors** should recover much of the ~70% dense/sparse miss → real headroom toward the 0.667
grounded bar. **T41 build:** construct the graph the data implies, wire clause-anchored expansion into the
hybrid candidate pool, re-measure three-leg recall@50 / pool-nDCG. Residual the graph cannot fix: 7
zero-anchor queries + ~12% of missed relevants unreachable (<0.6 to any anchor).

**T41 MECHANISM PROOF — the semantic-kNN graph leg does NOT deliver recall (2026-07-18)**
(`scripts/diagnose_acord_knn_expansion.py` RRF; `scripts/diagnose_acord_knn_rerank.py` rerank).
- **RRF** fusion of clause-anchored expansion HURTS recall@50 (0.301 → 0.23–0.28 across A∈{5..50},
  E∈{5..20}) — expanding from mostly-irrelevant anchors floods the pool with noise RRF can't downweight.
- **+ cross-encoder rerank** (the real precision gate): **NEUTRAL** — recall@50 0.301 → 0.303 (+0.002),
  pool-nDCG@10 +0.03. The reranker filters the noise but can't PROMOTE the expanded relevants (query-distant).
- **ROOT CAUSE — the query–clause representation gap.** query→relevant cosine **0.534 is BELOW** the
  random-relevant-pair baseline **0.561**: the query embeds FARTHER from its own relevant clauses than
  random. Clause-clause structure is strong (cluster 0.686, missed→anchor 0.706), so expansion REACHES the
  relevants, but ranking them for the query needs a query-relevance signal that is fundamentally weak for
  ACORD's short type-phrase queries; neither RRF nor the cross-encoder overcomes it.
- **The lever that DOES move recall:** a bigger BASE pool + rerank (BASE 50→100 gave two-leg recall@50
  0.301→0.379). The reranker recovers recall from the base pool; graph-expanded query-distant candidates it
  cannot promote.
- **IMPLICATION:** headroom is query-side (query representation; bigger base pool + rerank) and — per ACORD's
  own Table 3 — an LLM reranker (GPT4o reached nDCG@10 0.81 vs 0.54–0.64 for bi/cross-encoders), NOT a
  semantic-kNN graph leg. The **category graph** (direct label-retrieval, a DIFFERENT mechanism that bypasses
  the embedding gap but is coarse + needs clause category-tagging via `graph_extraction`) is the remaining
  system-proof test — tempered expectations, and the graph leg's role for ACORD now looks small.

**RETRIEVAL REFERENCE BASELINE (integration-first, concluded 2026-07-18).** The retrieval investigation is
concluded for now, and recorded as a **baseline-with-diagnosis, not a shortfall**: two-leg BGE-M3 hybrid +
cross-encoder reaches **recall@50 ~0.38 / pool-nDCG 0.45–0.47** (near BM25/MiniLM — baseline-healthy
like-for-like); **root cause = the query-representation gap** (short type-phrase queries embed farther from
their relevant clauses than random pairs); the **graph leg does not help** (reachability ≠ rankability under
the weak query signal); the **indicated lever per ACORD Table 3 is an LLM reranker** (GPT4o nDCG@10 0.81 vs
0.54–0.64 bi/cross-encoders). This is the measured starting point the later recomposition experiments move.

**COMPOSITION-EXPERIMENT BACKLOG (post-integration; the compose→recompile→evaluate menu).** When the
integration proof lands, these become the experiments the system was built to run — each an add/remove-a-
capability, recompile, new-number loop against the ACORD baseline above:
- **LLM reranker** — ACORD's indicated lever (Table 3): swap the cross-encoder for an LLM reranker, recompile,
  measure the recall/nDCG lift toward the ~0.8 the paper demonstrates.
- **Category label-retrieval** — the graph mechanism that may sidestep the query-representation gap by
  matching on clause-category *label* (not embedding); needs clause category-tagging via `graph_extraction`.
- **Base-pool sizing** — bigger hybrid pool feeding rerank (BASE 50→100 already showed recall@50 0.301→0.379);
  quantify the curve. Cheap, optional, NOT gating anything under integration-first.

**Status:** investigation concluded → parked as the composition-experiment backlog (own workstream, post-
integration). **Dep:** T33 (baseline measured). base-pool+rerank is **optional, no longer a gating task**.

### Task T42: RLM/synthesis latent-hardening pass (parked, post-integration)

**Why:** two "works-by-luck / caught-only-on-real-data" items surfaced during the integration proof
(2026-07-18). Both confirmed non-blocking (integration passed), so parked — but real hardening, promoted to a
visible task rather than left as prose notes:
1. **Worker query/slice threading is unenforced** (see the LATENT HARDENING ITEM note at the top of this
   file). The RLM leaf dispatch `task({description: "handle leaf depth D", subagentType: "rlm_slice_worker"})`
   threads NEITHER the query NOR the slice; query-relevant extraction works only because the orchestrator
   model *chooses* to thread them (GraphWright dry-run verdict: did not bite — but it's model-luck, not
   enforcement). Affects standalone synthesis too.
2. **Live tests use clean fixtures**, so two real-text bugs stayed invisible until real data hit them:
   the SQL-newline dead-letter (T33 store fix) and `[` in extract text (the `_parse_slice_outputs` fix).

**Scope (design question to resolve first, then implement):**
- **Enforce worker query + slice threading.** The RLM workflow is GENERAL (chunking has no query; synthesis
  does), so the query cannot be hardcoded into `RLM_WORKFLOW_JS`. Decide between / combine: **(a)** the
  synthesis capability bakes the query into the worker's system prompt (`worker_system_prompt`) — enforced,
  capability-specific, does NOT touch the general workflow or the drift test; **(b)** the workflow threads
  the slice `items` into the leaf-dispatch description so the worker sees its slice (consistent with "the
  *orchestrator* never sees the whole set"; the worker MUST see its own slice) — this DOES touch
  `RLM_WORKFLOW_JS` + `SKILL.md` (byte-identical, drift-tested, re-verify after). Prefer (a) for the query
  (cleanest, no general-workflow change); resolve whether (b) is needed for the slice or the PTC path already
  covers it.
- **Non-stub worker test.** Current synthesis tests stub the sub-agent responders (they return canned output
  regardless of dispatch content), so they never assert the worker *received* the query/slice. Add a test
  that drives the real worker and asserts the query + slice reach it.
- **Messy-fixture live-test discipline.** Add at least one deliberately messy fixture (brackets, newlines,
  quotes) to the RLM/synthesis/store live tests so real-text bugs are caught by the suite, not by production.

**Acceptance:** the query (and slice) reach the worker by enforcement, verifiable in a non-stub test; a messy
fixture exists in the relevant live tests. **Verify:** `uv run pytest tests/capabilities/test_rlm_synthesis.py
tests/capabilities/test_rlm_method.py` (+ the SKILL.md↔RLM_WORKFLOW_JS drift test if option (b) is taken).
**Files:** `capabilities/rlm_synthesis.py` (worker prompt / `_request`); possibly `skills/rlm/agent.py` +
`skills/rlm/SKILL.md` (if (b)); `tests/capabilities/test_rlm_synthesis.py`, `test_rlm_method.py`.
**Status:** DONE (2026-07-18). Implemented: **(1)** slice threaded into every leaf dispatch
(`JSON.stringify(items)`) in `RLM_WORKFLOW_JS` AND `SKILL.md`, byte-synced (drift test green) — the
committed pre-T42 state had NEITHER file threading the slice (both `"handle leaf depth D"`; GraphWright had
misread `agent.py` as already serializing items). **(2)** query enforced by baking it into the synthesis
worker's system prompt (`_build_agent(chunks, query)`) — NOT the dispatch description: the query isn't in the
workflow's JS scope, so description-threading would reintroduce model initiative / break the general
workflow's capability-agnosticism; system-prompt baking is the enforceable contract. **(3)** two non-stub
tests: `…messy_slice_by_enforcement` (query in prompt + a bracketed/newlined/quoted slice reaches the worker
— fixture discipline) and `…each_worker_only_its_own_slice` (forces a 4-worker split, asserts each worker
got EXACTLY its own partitioned slice + the query — the multi-worker path the flat dry-run never exercised,
GraphWright point 3). Full suite 412 passed + 27 skipped; ruff clean.
**Dep:** T28 (synthesis), T38 (chunk_read). GraphWright's node should re-pull the hardened skill (strictly
more robust; same output, now enforced).

### Task T43: Emit `capabilityInterface` typed I/O on authored manifests (GraphWright ADR-0030) — BLOCKED

**Why:** GraphWright's lowering pass verifies that a realization's capabilities actually chain (inputs/outputs
produce what a step needs). Today our manifests carry a rich `description` + representative queries but **no
typed I/O interface**, so their checker can only verify a *model's asserted* interface — a wrong interface
still "verifies" (they caught exactly this: a capability force-fit to a step it cannot do). They asked us to
confirm/correct a guessed typed interface (`capabilityInterface`) for the 5 query-graph capabilities. We
grounded all 5 against the real bound callables and are **owning the emission** (governed data we author),
rather than letting GraphWright hand-write into our registry files.

**Grounded confirm/correct (the interfaces to emit; full detail + reply in
`docs/handoff/2026-07-20_graphwright_capability_interface_reply.md`):**
- `hybrid_search` in `query: text` → out `candidates: {chunk_id, source_doc_id}[]` (filters/k are config).
- `reranking` in `query: text` + **`passages: {chunk_id, source_doc_id, text}[]`** (needs TEXT, not ids) → out
  `ranked: {chunk_id, source_doc_id, score}[]` (top_k config). **The load-bearing correction.**
- `fusion` in `reranked: rerank_result` + `graph: graph_answer` (two distinguishable legs, not variadic) → out
  `fused: {chunk_id, sources[]}[]` (union, no score; cap config).
- `chunk_read` in `chunk_ids: chunk_id[]` → out `chunks: {chunk_id, text, source_doc_id}[]` (ordered, drops nothing).
- `rlm_synthesis` in `query: text` + `chunks: {chunk_id, text}[]` (takes text, doesn't rehydrate) → out
  `answer: text` + `chunk_ids: chunk_id[]` (citations) + `slice_outputs: {chunk_id, extract}[]`.
- Set completeness: **`graph_query`** (produces fusion's 2nd input) and **`generation`** (alt answer step)
  also want interfaces if the whole graph is to be governed.

**BLOCKED ON GRAPHWRIGHT (two asks, in the reply) — do NOT build until resolved + approved:**
1. **Schema lockstep.** Both `ard.py` (`RegistryEntry`, `extra="forbid"`, `ard.py:55`) and GraphWright's
   `entry.py` (mirror, ADR-0005) forbid extra fields. A manifest carrying `capabilityInterface` fails to load
   on whichever side hasn't added the field. Neither side ships until both schemas accept it — agree the
   top-level field name + nested shape verbatim.
2. **Type vocabulary.** GraphWright's `{text, chunk_id}` scalar set can't express the compound records that
   actually flow (`{chunk_id, source_doc_id}`, `{…, score}`, `{chunk_id, text, source_doc_id}`,
   `{chunk_id, sources[]}`) — and that granularity is what lets their checker catch the `reranking`-needs-text
   force-fit. Agree scalar-vs-compound and the shape so we emit something their checker can read.

**Scope (when unblocked):** add an optional `CapabilityInterface` model to `ard.py`
(`RegistryEntry.capability_interface`, camelCase `capabilityInterface`, matching the agreed wire shape); thread
it through `ManifestSkeleton.author(...)`; declare the confirmed interfaces at the `register_*` calls for the 5
(+ `graph_query`, optionally `generation`); re-emit the manifests. Short RAG-side ADR mirroring GraphWright
ADR-0030 (the cross-repo coordination record, ADR-0005 style).
**Acceptance:** a manifest carrying `capabilityInterface` round-trips through `RegistryEntry` (conformance
test); `write_manifest` emits it; the emitted interfaces match the real signatures above.
**Verify:** `uv run pytest tests/capabilities/` (ard conformance + registry). **Files:**
`src/rag_wright/capabilities/ard.py`, `registry.py`, the per-capability `register_*` calls,
`tests/capabilities/…`, a new `docs/adr/` entry, `tasks.md`.
**Status:** DONE (2026-07-20, ADR-0021). GraphWright answered both asks (nominal typing, not compound types;
their `entry.py` mirror is in). Implemented: `CapabilityInterface` (plain BaseModel, `extra="forbid"`,
snake_case inner keys) + `NOMINAL_TYPE_VOCABULARY` (mirror of their §3 table, validated at author time) in
`ard.py`; optional `capability_interface` on `RegistryEntry` (→ `capabilityInterface` on the wire); threaded
through `ManifestSkeleton.author()`; declared the 7 interfaces in `manifests.py` (5 + graph_query + generation),
grounded in the real callables. 13 new tests (interfaces match signatures; reranking needs `chunk_with_text`;
vocabulary rejects typos/list-sugar; snake_case inner keys on the wire; round-trips through `extra="forbid"`).
Full suite **426 passed + 27 skipped**, ruff clean. Confirmation to GraphWright drafted at
`docs/handoff/2026-07-20b_graphwright_interface_confirmation.md` (confirms §4, answers abstain = boolean flag /
synthesis citation shape, flags 2 chain-level notes). **Contract now CLOSED (2026-07-20):** GraphWright resolved
the one open item — fusion output retypes `fused_chunk`→`chunk_id` (it is an id-only reference; the `sources[]`
provenance does not fork the type name, so `fusion→chunk_read→synthesis` type-checks), retiring `fused_chunk`
(vocabulary down to 6 names). Applied + all 15 manifests re-emitted to `~/.air/registry` (regenerable via
`scripts/publish_manifests.py`). **Dep:** T6 (registration/author path), T38 (chunk_read, one of the 7),
ADR-0005 (the schema mirror this coordinates against).

### Task T34: Document update/upsert path (finding, logged during T17) — later

**Description:** Confirmed during the T17 rebuild (2026-07-15): **no document-level update/upsert path
exists**. The store has `upsert_chunk` (per-chunk) and `write_document` (a document's chunks, content-hash
gated) but **no delete-by-document**. On a document *change* the content hash changes → new `chunk_id`s →
`write_document` upserts the new chunks while the **old chunks orphan** (and their graph nodes + index
entries with them). This delete-and-re-chunk route is the only way a document is ever chunked more than
once, and it is what makes the "chunk once, persist, never recompute" ingestion lifecycle complete.

**Scope (when built, not now):** on a changed document, delete all existing chunks for that `source_doc_id`
(and their graph nodes, hybrid-index entries, **and their chunk-text sidecar entries — `ChunkTextStore.delete_document`, T40**),
then re-chunk and re-insert from scratch. Touches chunking (T17), the chunk write/store (T20, ArcadeDB
`store/`), the chunk-text sidecar (T40), and the graph layer (T25). Needs a delete-by-`source_doc_id` on
the store seam + graph (+ the sidecar's `delete_document`, already built), wired into a document-update
entry point.

**Why the sidecar raises T34's priority (T40, 2026-07-17):** a re-chunk already orphans index entries today,
so the sidecar orphaning text is the *same existing gap*, cleaned together when T34 lands — nothing
new-orphans that wasn't already. But the orphaned thing differs in kind: an orphaned index entry is a
stale summary + vectors (a **correctness** problem), while an orphaned sidecar entry is stale **full text**
(correctness **plus unbounded storage growth** — text is far larger than a summary and accumulates one copy
per re-chunked version of every updated doc). So T34 becomes **the thing that bounds sidecar storage growth**.
**Inertness for this milestone is conditional:** the ACORD eval corpus is ingested once and never updated,
so no orphaning triggers and T34 stays correctly deferred — but that inertness depends on the corpus not
being updated. On an **updating corpus, T34 is required, not optional** (fine for the eval, required for
production-with-updates).

**Status:** todo (finding — not a T17 blocker; makes the ingestion lifecycle complete; bounds T40 sidecar
growth on an updating corpus). **Dep:** T17, T20, T25, T40.

### Task T37: Deep-recursion completeness — structural coverage guarantee (T36 finding)

**Finding (GraphWright bind_run, 2026-07-17):** with the working set genuinely out of context (prompt-render
cut, delivery only via `tools.workingSet()`), the real RLM skill's deep-recursion completeness is variable
— full leaf coverage only ~half the time, sometimes silently missing a deep leaf while reporting success
(same silent-under-performance class as the earlier RLM findings). Masked earlier because step-1 delivery
was additive (working set in tool AND prompt), so the model could reach coverage by reading the prompt
without depending on recursion. **My tests had the same masking:** T28's opaque proof is hermetic (a fake
orchestrator emits a fixed correct workflow — never tested real-model completeness); T28/T17 live tests use
small/shallow sets. My synthetic reproduction (clean probe binary tree) is 5/5 even at depth 4 — structurally
easier than the real run, so it does not reproduce the ~50%. The finding is real (GraphWright measured it on
the real skill).

**Fix — the coverage guarantee belongs in the skill's interpreter WORKFLOW** (GraphWright's layering
correction): coverage-repair is *capability semantics*, so it must not live in the compiler's node
(domain-coupling the two-repo split forbids) nor in RAG_Wright's Python wrapper (doesn't run in the node).
The skill's workflow runs in the node by construction AND in this harness — one guarantee, every consumer.
- [x] **Grounded in-interpreter-expressible** (the analogue of the truncation/PTC-only checks): a spike ran
  an incomplete descent, then the coverage tail (JS) diffed `tools.workingSet()` against a `handled` set and
  re-dispatched the missed — all in interpreter code before returning.
- [x] **Moved into the workflow.** `RLM_WORKFLOW_JS` (and SKILL.md's canonical workflow) now: works over an
  item-list (each item has an `id`), the decomposer returns partition `cuts`, and after the recursion a
  **coverage tail** diffs the working set against `handled` and dispatches any missed item — code checking
  coverage, before returning. Chunking's discovery instructions carry the span-gap coverage tail; synthesis
  reads the method's tail. The working-set tools now expose `id` (chunking: item index; synthesis: chunk_id).
- [x] **Removed the Python wrapper guarantee** (`_guarantee_coverage`/`Synthesizer.extract` from synthesis;
  `_repair_partition` from chunking) — moved, not duplicated. `_validate_partition` stays as a loud check.
- [x] **Proven deterministically:** `test_coverage_tail_covers_an_incomplete_descent_in_the_interpreter` —
  a workflow whose descent handles only half the items reaches full coverage via the tail (6/6, 3 descent +
  3 tail). Full suite 384 passed + 26 skipped.

**bind_run findings (GraphWright, 2026-07-17) — the masking moved one level down, to recursion depth:**
- **Finding 1 — coverage passes FLAT.** The flat item-list + cuts made coverage trivially satisfiable (no
  deep leaves to miss), so a run that does NO divide-and-conquer (one worker over all items,
  maxSplitDepth=0, tail-fire=0) still passes the coverage bar. GraphWright saw 1/5 runs go fully flat with
  full coverage. **The honest signal is maxSplitDepth, not coverage.** At production scale a flat chunking
  run = one worker over the whole document = context overflow, RLM defeated, invisible on small test sets.
- **Finding 2 — root cause (confirmed).** SKILL.md dispatched the decomposer as `"decompose depth D"` with
  **no item count**, while `RLM_WORKFLOW_JS` (what the deterministic tests ran) sent `"...over N items"`.
  The decomposer needs the count to return index `cuts`, so a model following SKILL.md **literally can't
  partition and falls flat**; the count-bearing variant recurses. The green test validated a **different
  artifact** than what ships.

**Fix (landed):**
- [x] **Eliminated the drift:** SKILL.md's canonical workflow is now **byte-identical to `RLM_WORKFLOW_JS`**
  (count included), asserted by `test_skill_md_canonical_workflow_is_byte_identical_to_rlm_workflow_js` — so
  skill-vs-tested-artifact drift fails a test here, not at GraphWright's node.
- [x] **Depth signal in validation:** the recursion test asserts `maxSplitDepth >= 1` (recursion happened),
  the flat-teeth test asserts `< 1` (flatness caught) — not just coverage.
- [x] **Re-validated the real model reliably RECURSES out of context:** a real deepseek following the aligned
  skill on an 8-item set = **5/5 genuine recursion** (maxDepth=3, 15 decomposer dispatches), vs GraphWright's
  1/5 flat on the old skill. Full suite **385 passed + 26 skipped**.

**Acceptance bar (updated by GraphWright):** genuine recursion RELIABLY — `maxSplitDepth > 0` on a working
set that requires decomposition — measured by GraphWright's cut-prompt bind_run against the aligned skill,
NOT coverage (coverage was shown to pass flat).

**bind_run finding 3 (2026-07-17) — the silent drop moved to the LOAD boundary:** with delivery cut
clean (channel proven to fill the field with all 8), on ~1/11 runs the synthesis model **under-loaded** at
`const workingSet = await tools.workingSet()` — it read 3 of 8. The channel delivered 8 (deterministic);
the model loaded 3. The **coverage tail cannot catch this**: the tail guarantees coverage over what was
loaded, so with 3 loaded it faithfully covers those 3 and reports `missed=0`, success. The under-load is
**upstream of every guarantee** (delivery proven, coverage proven, evidence still vanishes) — the same
silent class one step earlier, at the load boundary.

**Fix (load-completeness assertion, mirroring the coverage tail at the other end):**
- [x] The workflow, right after the load, verifies it loaded the whole delivered set:
  `const _delivered = await tools.workingSetSize(); if (workingSet.length !== _delivered) throw`. The size
  is a **scalar from the runtime the model cannot under-read**, so an under-load fails **loud** before any
  leaf runs over the truncated set — silent evidence-drop → visible error. In `RLM_WORKFLOW_JS` + SKILL.md
  (byte-identical, drift-tested) and in chunking's discovery instructions. Both RLM nodes get it.
- [x] A `working_set_size` PTC tool added to synthesis + chunking (the truthful delivered count).
- [x] Test `test_load_assertion_fails_loud_on_an_under_read` — working_set returns 3 while size reports 8:
  the assertion throws loud, no leaf runs. Full suite **386 passed + 26 skipped**.
- [x] **Mechanical vs elective (GraphWright's question): ELECTIVE.** Dumped the real model's eval code —
  it loads the full set then writes `workingSet.slice(0, 2)`: it electively **samples/subsets** (here a
  diagnostic, but the exact tendency that lands on the processing path as "read 3 of 8"). The channel is
  not truncating; the model elects. So the fix is **assertion-plus-instruction** (both landed): the
  instruction prevents the elective subset, the assertion enforces it (fails loud on any residual
  under-read, elective or mechanical). The dump confirms the aligned skill is followed (full load +
  `workingSetSize`).
- Fixed a broken `skills/rlm/__init__.py` (externally added; absolute `from skills...` → `from rag_wright...`).

**Status:** DONE, both sides (2026-07-17). GraphWright's node-side depth-and-load bind_run against the
aligned skill passed **6/6 full-load-and-full-depth**. Skill side: drift eliminated (SKILL.md ==
RLM_WORKFLOW_JS, byte-identical, drift-tested), count fixed, depth signal, load-completeness assertion,
elective-subset instruction. The whole RLM arc (delivery, load faithfulness, recursion depth, coverage,
cross-node flow) is proven on both sides. **Dep:** T36.

### Task T35: Per-process interpreter-session serialization (KI-1 cross-graph constraint) — ADR-0020

**Description:** GraphWright's KI-1 resolution (2026-07-16): two QuickJS interpreter runtimes coexisting
in one process race on shared Rust-side state and **silently complete without dispatching ~half the
time, with zero exceptions**. GraphWright's compile-time guard rejects two interpreter nodes within one
graph, so a single ingestion graph is safe (one `rlm_chunking` interpreter session, concurrent workers
inside = reliable single-session case). What its guard cannot see: a harness running **multiple
documents' graphs concurrently in one process** brings up coexisting sessions across graphs — the exact
race. This is harness-level, so ours to enforce (ADR-0020).

**Current state — SATISFIED, with an ALWAYS-ON GUARD now in place.** The harnesses are serial (plain
`for d in docs:` loops), so KI-1 cannot bite today. AND, because the failure is silent, the enforcement is
**always on**, not deferred to this task: `skills/rlm/agent.py` holds a process-wide `BoundedSemaphore(1)`
+ `rlm_interpreter_session()` that serializes the **full** interpreter-session lifetime (build → run →
`_registry.close()` teardown, all in-lock) for both the chunking discoverer and the synthesis extractor.
Uncontended (zero cost) while serial; if concurrency is ever added without designing T35, it turns a
silent-correctness failure into a visible-performance one (serialized, slower, noticed) rather than
quietly-degraded chunks. **Load-bearing — not to be "cleaned up"** (ADR-0020). Tested: sessions never
overlap across threads; registry torn down on exit.

**T35's job is THROUGHPUT design, not correctness rescue.** The semaphore is the correctness floor; T35
designs the real concurrent batch path (batch sizes, worker counts, OQ8) that serializes interpreter
sessions **consciously**, and carries a **fail-if-silent** check — that dispatch actually FIRED under
concurrency, not merely that the run completed — because the failure has no error signal. Everything else
(parse, embed, summarize, write) may stay concurrent. Closes the interpreter-concurrency dimension of **OQ8**.

**Exit path:** lifted when the upstream coexistence bug (langchain-quickjs/deepagents) is fixed — or via a
sandbox-based RLM build / RLM-in-LangGraph-via-DSPy — verified by the KI-1 regression harness at full
dispatch under concurrency. Then this and GraphWright's in-graph guard lift together.

**Status:** todo (binding constraint; satisfied while the harness is serial). **Dep:** T17, T28.

### Task T36: Working-set delivered by a runtime tool, not message-embedded JSON (GraphWright contract)

**Description:** Cross-repo contract from GraphWright (docs/handoff/working-set-binding.md, 2026-07-16).
Today RAG_Wright delivers the working set **in the model's context**: rlm_synthesis embeds the candidate
set as JSON in the HumanMessage; rlm_chunking embeds an item-view (metadata + 80-char previews) + a `peek`
tool. That partially defeats RLM (the model should hold interpreter variables, not the whole set). Fix:
GraphWright binds a deterministic PTC tool returning the node's channel input, exposed **inside the
interpreter** as `globalThis.tools.workingSet()` (host tool name `working_set`; grounded:
`to_camel_case('working_set')='workingSet'`, PTC exposes `tools.<camel>` async, `task` excluded from PTC
so no collision). The result stays a JS value, **never enters context** — runtime-guaranteed wiring, same
principle as the workflow trigger and eager method load.

**Scope (pending GraphWright binding the tool):**
- SKILL.md canonical workflow becomes: `const workingSet = await tools.workingSet(); decompose(workingSet, 0);`
  — replaces the `WORKING_SET`-binding assumption.
- rlm_synthesis: drop JSON-in-HumanMessage; the extractor binds `working_set` (PTC) returning the
  candidates in RAG_Wright's own harness (GraphWright binds it from the channel in production).
- rlm_chunking: drop item-view-in-message; `peek` is **subsumed** (the model reads item text from the
  returned JS value); the discoverer binds `working_set` returning the document items.
- **Re-validation, NOT a free swap:** re-run **T28**'s opaque-candidate-set recursion + citation-preservation
  proof AND **T17**'s coherent-clause boundary-quality proof under tool-based delivery (both proofs ran
  against the OLD message-embedded seam). Also validate a **large** working set via the tool does not hit a
  PTC result-size cap (`max_result_chars`), since the whole point is a big value that stays in JS.
- **Skill rename (cosmetic, folded in):** frontmatter `name: rlm-method` → `name: rlm` to match the `rlm`
  directory (Agent Skills spec compliance). Directory can't be `rlm-method` — `rag_wright.skills.rlm` is an
  imported Python package and module names can't contain hyphens. Bundled here since it also touches SKILL.md.

**Status:** done (2026-07-16). GraphWright's bind landed; implemented + re-validated. **Dep:** T17, T28.
**Re-validation (green):** T28 opaque-recursion + citations and T17 coherent-clause-kept-whole both **passed live** under tool-delivery (real model calls `tools.workingSet()` and follows the method); no-truncation is a committed hermetic test (300-item doc + full last-item text intact via the tool). Grounded PTC-only exposure: `working_set` is exposed as `tools.workingSet()` inside `eval` and is NOT a top-level tool, so the working set never enters context. Full suite 383 passed + 26 skipped.

### Task T-CHK: RLM chunker degenerate-split fix (T17 bug) — ask-first

**Description:** GATE-2 run 1 surfaced degenerate RLM chunking: `_split_into_chunks` flushes at every
header item with no minimum-chunk-size floor and no tiny-section coalescing, so header-dense/OCR'd docs
over-fragment (ASIANDRAGON 113 chunks/10k tok; ADAMSGOLF 49 chunks/24KB) and emit near-empty heading-only
chunks (`_validate_boundaries` only rejects fully-empty text). This depresses RLM retrieval independently
of the query artifact — must be fixed before any RLM-vs-baseline comparison so a chunker bug is not
attributed to the chunking strategy. Fix: a min-size floor / merge tiny adjacent sections; reject
near-empty chunks in validation. Changes an approved capability → **ask-first**.

**RAC-CHK:**
- [x] Min-size floor (`MIN_CHUNK_CHARS=1000`, ~250 tokens) via forward accumulation: a heading starts a
  new chunk only once the current chunk meets the floor AND the heading is a *major* boundary
  (same-or-higher level than the section the chunk opened with). Below-floor or deeper-subsection
  headings keep accumulating.
- [x] **Hierarchy-preserving** (per review constraint): a deeper subsection is never split away from its
  parent (test: level-2 subsection stays in the level-1 parent's chunk); tiny sections fold into a
  neighbour within the same parent (`_merge_below_floor`, backward-first, never over the cap), never by
  destroying a boundary. A real section break still splits when both sides meet the floor.
- [x] Validation rejects near-empty/heading-only chunks (`_validate_boundaries`, `<20` chars when >1
  chunk); a lone short chunk (short doc) is allowed. Cap guarantees preserved (hard-split unchanged).
- [x] Regression fixture on the ASIANDRAGON pathology (synthetic 112 level-1 headings) + an opt-in real-doc
  check on the cached parse. **Real ASIANDRAGON: 113 → 34 chunks, char min 1006 (was 10 near-empty),
  median 1141, all ≥ floor.** Distribution (min/median/max) reported.
- [x] Summarizer text-fallbacks re-checked at re-ingest: **0 fallbacks** with the fixed chunker (run 1
  had 7). Confirms the fallbacks were a symptom of the split bug (near-empty text → structured None), not
  a separate failure. Re-ingest chunk distribution: total 296 (was 632), median 9/doc, **no near-empty**.

**Verification:** `uv run pytest tests/capabilities/test_rlm_chunking.py` (12 passed incl. real ASIANDRAGON
regression; 2 opt-in skipped). Full suite 300 passed + 16 skipped.
**Files:** `src/rag_wright/capabilities/rlm_chunking.py` (`MIN_CHUNK_CHARS`, hierarchy-aware
`_split_into_chunks`, `_merge_below_floor`, near-empty validation), `tests/capabilities/test_rlm_chunking.py`.
**Consequence recorded:** GATE-1's RLM numbers marked **superseded** (measured on buggy chunks; ADR-0009
keeps RLM regardless). Any post-T-CHK A/B needs a full re-ingest (stored chunks stale).
**Status:** done (fallback re-check + per-doc signal at the re-ingest step, next). **Dep:** T17.

### Task T-SUM: Concurrent summarization (T17 enhancement)

**Description:** RLM `chunk()` summarized chunks in a serial loop — 900 chunks × ~4s serial OpenRouter
calls ≈ 1 hour, the re-ingest bottleneck (Docling was cached; no rate-limit in our code). Applied the
embedding capability's proven async+backpressure pattern (T19, FR-I.6) to summarization: `_summarize_all`
runs each blocking `summarize()` in a thread (`asyncio.to_thread`), bounded by an `asyncio.Semaphore`
(`DEFAULT_SUMMARY_CONCURRENCY=8`); `gather` preserves order so chunking stays deterministic (each
summary is independent of concurrency). `chunk()` stays synchronous (internal `asyncio.run`), so no
caller changes.

**RAC-SUM:**
- [x] Summaries run concurrently, bounded by a semaphore. Hermetic: `max_inflight == 4` at cap 4, `== 1`
  at cap 1 (strictly serial); order preserved (determinism holds; `chunk twice → identical` still passes).
- [x] **Validated with real LLM calls** (the T17 lesson — stubs are not enough): live `-m model` test,
  12 real DeepSeek summaries, **concurrent(8)=8.1s vs serial(1)=27.1s = 3.4× speedup**, thread-safe, no
  throttling errors, all summaries valid.

**Verification:** `uv run pytest tests/capabilities/test_rlm_chunking.py` (14 passed + 2 opt-in skipped;
full suite 302 passed + 16 skipped); live `... -m model` (3.4× speedup).
**Files:** `src/rag_wright/capabilities/rlm_chunking.py` (`_summarize_all`, concurrent `chunk()`,
`DEFAULT_SUMMARY_CONCURRENCY`), `tests/capabilities/test_rlm_chunking.py` (thread-safe stub + concurrency
+ live-speedup tests). **Status:** done. **Dep:** T17.

---

## Phase 4 — Build, graph layer (spec Phase 2)

### Task T23: Graph extraction (contract + spaCy NER/dependency; LLM escalation)

**Description:** A hybrid stack over the parsed documents (T16), all conforming to one ontology
(T4/T8): Pydantic-contract extraction for schema entities, a lightweight NER-plus-dependency path
(spaCy) for the bulk, and an open-ended language-model escalation for hard cases (FR-C.6, FR-I.4).
OpenIE stays deferred behind the T5 extractor seam (risk 10; adding it is ask-first). This is a
GPU-calling capability (the LLM escalation and spaCy pipelines), so it carries the FR-I.6
decoupling property.

**RAC-23:**
- [x] Contract extraction, the spaCy NER path, and the LLM escalation each produce ontology-conforming
  facts carrying `chunk_id` + confidence. **`SpacyNerExtractor`** emits typed mentions only
  (ORG→ORGANIZATION, PERSON→PERSON, EXTRACTED) — **no proximity edges** (ADR-0012; `EntityMention` gained
  a `confidence` field). **`ContractExtractor`** emits ClauseFacts + party mentions + **CONTRACTS_WITH
  from the signing-party structure** (the only source of that edge, EXTRACTED). **`LlmEscalationExtractor`**
  emits hard-case RelationshipFacts (INFERRED). All anchored to `chunk_id` via `ExtractionResult`/`Provenance`.
- [x] The LLM extractors call the model only through the seam under the DeepSeek V4 Pro
  `STRUCTURED_REASONING` profile (ADR-0006); no provider/model flag in the code. **Live `-m model`: real
  DeepSeek round-trips the 41-value `ClauseCategory` enum + `RelationshipType` schemas.**
- [x] **FR-I.6 decoupling:** `extract_chunks` is async; each chunk's stack runs in `asyncio.to_thread`
  bounded by a semaphore. Concurrency test: `max_inflight == 4` at cap 4, `== 1` serial.
- [x] Registered under FR-C.6. `register_graph_extraction` → `graph_extraction`, `function`, contract
  `ExtractionResult`.
- [x] ARD-registered: manifest present in the shared root (`~/.air/registry/graph_extraction.json`); mirror
  conformance green; re-validates as a `RegistryEntry`. Live `RegistryStore` load is the GraphWright step.

**Verification:** `uv run pytest tests/capabilities/test_graph_extraction.py` (10 hermetic passed); live:
`-m ner` (real spaCy, 1 passed) and `-m model` (real DeepSeek contract + escalation, 2 passed). Full suite
313 passed + 20 skipped. Publish: `uv run python scripts/publish_manifests.py`.

**Dependencies:** T5, T8, T16. **Scope:** L. **Status:** done.
**Files:** `src/rag_wright/capabilities/graph_extraction.py`, `tests/capabilities/test_graph_extraction.py`,
`src/rag_wright/contracts/extraction.py` (`EntityMention.confidence`, ADR-0012),
`tests/contracts/test_extraction.py`, `src/rag_wright/capabilities/manifests.py` (+`graph_extraction`),
`pyproject.toml`/`uv.lock` (`en_core_web_sm` MIT, wheel-pinned) + `conftest.py` (`ner` marker),
`docs/adr/0012-entity-mention-confidence-no-proximity-edges.md`.
**Note:** Co-occurrence edges rejected as false-edge generators — CONTRACTS_WITH from party structure only
(ADR-0012), because T26 surfaces but does not filter by confidence (FR-C.5/FR-Q.3). spaCy model is
config-driven (`RAG_SPACY_MODEL`, swappable md/lg/trf, not trf); OntoNotes NER noise is expected and
handled by T23b + the human gate. OpenIE still slots behind the T5 seam later (ADR-0001).

### Task T23b: Entity disambiguation and canonicalization (normalize, reject, cluster)

**Description:** Between recognition (T23) and linking (T24), turn raw extracted party and entity
mentions into canonical mention clusters: normalize surface forms (legal-suffix and
whitespace/punctuation canonicalization), reject non-entities (template placeholders, role
artifacts, over-broad or degenerate matches), and cluster the survivors that denote one real-world
entity (blocking plus similarity). The output is a set of canonical clusters, each a proposal a
human verifies, that T24 then links to an EDGAR CIK. This is the canonicalization stage FR-C.7
resolution presupposes, and it keeps human name-to-CIK verification (SPEC §14) scaling with entity
count, not mention count. Full coreference (pronouns, definite descriptions) is deferred behind a
real seam, the same discipline as OpenIE at T5.

**RAC-23b:**
- [x] Normalization (ADR-0004 N1-N5): reuses the T10 `corpus.canonicalize.normalize_entity_name`
  (legal-suffix, whitespace/punctuation, possessive-apostrophe, NFKC). Test: the three "Bank of America"
  variants collapse to one cluster; "Stremick's"/"Stremicks" collapse.
- [x] Rejection filter (ADR-0004 R1-R5): reuses `corpus.canonicalize.is_entity`. Test: placeholder,
  role artifact, bare generic ("Services"), and alias-only ("formerly known as Tradeum, Inc.") are
  rejected (in `DisambiguationResult.rejected`, never a cluster); "Acme Inc. d/b/a SuperBrand" recovers
  "Acme".
- [x] Clustering (ADR-0004 C3/C4): grouped by (normalized key, entity_type); ambiguous near-duplicates
  **flagged, never merged** — `_flag_near_duplicates` sets `ambiguous_with` for proper-token-subset or
  shared-first-token pairs. Test: ScanSource / ScanSource Latin America and Armstrong Flooring / Armstrong
  Hardwood Flooring stay 4 clusters, each flagged; Bank of America / Bank of England neither merged nor
  flagged. Cluster precision/recall == 1.0 on a labeled fixture.
- [x] Regression fixtures cover the T10 misses: possessive-apostrophe merge, bare-generic reject,
  alias-prefix reject.
- [x] Output `MentionCluster`s carry `chunk_id` provenance (the chunks the mentions came from) and the
  **weakest** confidence over the cluster (test: an AMBIGUOUS member → AMBIGUOUS cluster), shaped as
  proposals (`ambiguous_with` = human decision points), never auto-committed merges.
- [x] Full-coreference `CoreferenceResolver` seam (deferred, mirrors T5): `disambiguate(...,
  coreference_resolvers=...)`; a stub resolver bound in test rewrites the cluster set (load-bearing);
  default is no resolvers.
- [x] Registered under `entity_disambiguation` (FR-C.7); contract `DisambiguationResult`.
- [x] ARD-registered: manifest present in the shared root (`~/.air/registry/entity_disambiguation.json`);
  mirror conformance green; re-validates as a `RegistryEntry`.

**Verification:** `uv run pytest tests/capabilities/test_disambiguation.py` (11 passed, hermetic). Full
suite 324 passed + 20 skipped. Publish: `uv run python scripts/publish_manifests.py`.

**Dependencies:** T23. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/capabilities/disambiguation.py`,
`tests/capabilities/test_disambiguation.py`, `src/rag_wright/capabilities/manifests.py`
(+`entity_disambiguation`), `docs/adr/0004-entity-disambiguation.md` (existing; honored).
Reuses `src/rag_wright/corpus/canonicalize.py` (T10) — normalize/reject/cluster kept, not rewritten.
**Note:** Recognition (T23) and linking (T24) already existed; this fills the canonicalization gap
between them that the T10 data exposed (Bank-of-America variant fragmentation, template and
role-artifact noise). It converts the human from cluster generator to proposal verifier, which is
what makes the ground-truth discipline scale. Folded under FR-C.7 in the coverage map; see the SPEC
note below. **The normalize/reject/cluster rules were first built and human-verified at T10** in
`src/rag_wright/corpus/canonicalize.py` (normalize_entity_name / is_entity / cluster_entities, 17
tests); T23b hardens them into the capability behind the extractor/coreference seam.
The normalize and reject rules are recorded in ADR-0004 and are corpus-derived: the possessive-apostrophe rule (Stremick's equals Stremicks), the bare-generic-token reject ("Services", "Bank"), and the alias-prefix reject ("formerly known as", "d/b/a") each came from a real T10 verification-set miss.

### Task T24: Entity resolution (closed-world to EDGAR CIK)

Description: Resolve the canonical mention clusters from T23b to the registry's canonical entity_id (EDGAR CIK), closed-world against the known set (FR-C.7). Surface-form fragmentation is handled upstream at T23b, so this task links a clean cluster to a CIK rather than fighting variants. Decide and record the matching strategy (exact / fuzzy / embedding / LLM-assisted, §16.3) at this task.

**RAC-24:**
- [x] A known cluster resolves to the correct EDGAR CIK `entity_id`; an unknown resolves to `None`
  (closed-world, not fabricated). `resolve_entities` links each `MentionCluster` via
  `registry.resolve` (representative then variants, first hit). Test: "Acme Corporation" → CIK, a
  private co → None, and an alias variant ("Acme Inc") links.
- [x] Fragmentation rate is **measured** — `fragmentation_rate(result, gold_by_key)` = fraction of true
  entities ending as >1 node. Test: two clusters T23b left separate both link to one CIK → 0.0
  (resolution reduces fragmentation via alias linking); a split entity → 1.0. Full golden-set run is
  eval-time (like recall).
- [x] **Post-resolution self-loop check** (moved from the T4 contract, needs resolved ids): a
  relationship whose two *distinct* refs resolve to the same non-None `entity_id` is dropped. Test:
  "Acme Corporation" AFFILIATE_OF "Acme Inc" (both → 0000000001) → dropped; two unlinked refs → kept.
- [x] **Both mention channels as one stream:** a relationship ref resolves by matching a cluster key
  first (taking that cluster's id, even if None), falling back to the registry only for a cluster-less
  ref — so an entity as both a standalone mention and a relationship endpoint is one node. Test: the
  CONTRACTS_WITH refs take the standalone Acme/Beta cluster ids.
- [x] Matching strategy recorded: **ADR-0013** — exact normalized, closed-world, no fuzzy/embedding/LLM
  (conservative-merge bias; a wrong fuzzy link is a silent false merge). Fuzzy is a later, eval-gated
  option behind the same `resolve` boundary.
- [x] Registered under `entity_resolution` (FR-C.7); contract `ResolutionResult`. ARD manifest present
  in the shared root (`~/.air/registry/entity_resolution.json`); mirror conformance green.

**Verification:** `uv run pytest tests/capabilities/test_entity_resolution.py` (9 passed, hermetic). Full
suite 333 passed + 20 skipped. Publish: `uv run python scripts/publish_manifests.py`.

**Dependencies:** T8, T23b. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/capabilities/entity_resolution.py`,
`tests/capabilities/test_entity_resolution.py`, `src/rag_wright/capabilities/manifests.py`
(+`entity_resolution`), `docs/adr/0013-entity-resolution-matching-strategy.md`.
**Note:** Resolves §16.3 (ADR-0013). Fragmentation is risk 5. **ADR is 0013**, not the ledger's earlier
"0005-entity-resolution.md" reference (0005 is the relational golden set).

### Task T25: Graph storage (nodes/edges carry `chunk_id`, content-hash gated)

**Description:** Write nodes and edges into the same ArcadeDB store, carrying the originating
`chunk_id`, conforming to one ontology, gated by content hash (FR-I.4, FR-I.5). A chunk and its
extracted entities connect in one transaction (FR-S.1). The graph is the relationship layer only;
heavy structured data does not go in it (SPEC §8).

**RAC-25:**
- [x] Nodes/edges written carrying `chunk_id` + confidence; a chunk and its entities land in **one
  transaction** (`DatabaseDao.execute_transaction`). Entity nodes upsert by `node_key` (CIK when linked,
  `UNLINKED:<key>` surrogate otherwise; `cik`/name/type/confidence/chunk_id props); `Relationship` edges
  connect resolved node keys; `Mentions` edges connect each `Chunk` to its `Entity` (FR-S.1). Live
  (`-m store`): 2 nodes + 1 relationship + 2 Mentions created in one transaction. Ref-only endpoints get
  a minimal node so every edge connects.
- [x] Content-hash gate: a per-document checkpoint keyed by content hash makes an unchanged re-run a
  no-op (`skipped`, no store write, no duplicate edges). Live-verified; hermetic test proves the store is
  written exactly once; a changed hash re-writes.
- [x] Relationship layer only (SPEC §8): nodes carry id/name/type/confidence, edges carry the
  relationship — no heavy structured data.
- **(No ARD bullet.)** Seam-bound ingestion step, no §5 slug (FR-I.4) → registers nothing, no manifest
  (per the ARD-registration rule). Writes through the T13 `Store` seam (extended: `write_graph`,
  `graph_counts`, + `GraphNode`/`GraphEdge`).

**Verification:** `uv run pytest tests/capabilities/test_graph_storage.py` (5 hermetic passed); live
`-m store` (1 passed: real transaction, nodes/edges/Mentions, gate); T13/T14 store tests still green with
the extended `Entity` schema. Full suite 338 passed + 21 skipped.

**Dependencies:** T13, T23, T24. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/capabilities/graph_storage.py`, `tests/capabilities/test_graph_storage.py`,
`src/rag_wright/store/{seam,arcadedb}.py` (graph-write seam: `write_graph`/`graph_counts`,
`GraphNode`/`GraphEdge`, `Entity` props + `Relationship`/`Mentions` edge types),
`tests/store/test_arcadedb_schema.py` (stub extended to the graph-write seam).

### Task T26: Graph query (cited `chunk_id`s, `entity_id`s, confidence)

**Description:** Answer relational and multi-hop questions via graph query, returning an answer
with cited `chunk_id`s, `entity_id`s, and confidence tags, from the same store as the retrieval
index; the answer is treated as evidence, not truth (FR-C.5, FR-Q.3, SPEC §8/§14).

**RAC-26:**
- [x] A relational/multi-hop question returns an answer with cited `chunk_id`s, `entity_id`s, and
  confidence tags. `graph_query(start_entity_id, *, store, relationship_type, max_hops)` traverses via
  the store's `graph_neighbors` (ArcadeDB `MATCH` over `Relationship` edges — `bothE` cites each edge's
  `chunk_id`/`confidence`, `bothV` the reached entity; one-hop + two-hop, `$matched` de-dup; grounded
  live). Returns a `GraphAnswer` of `GraphEvidence` (entity_id, name, `path_entity_ids`, `chunk_ids`,
  `confidences`, hops). **Live (`-m store`): one-hop co-parties (B, C) cited from their edges; two-hop
  A→B→D with the full path `[A,B,D]` and both edges' chunks.** Confidence is **surfaced, not filtered**
  (FR-C.5/FR-Q.3); gating on it is the generator's job (FR-Q.6/T29). Full golden-set run is eval-time.
- [x] Shaped as evidence for fusion (T27), not a final ranked list: `GraphAnswer.evidence` is an unranked
  list of cited candidates.
- [x] Registered under FR-C.5. `register_graph_query` → `graph_query`, `function`, contract `GraphAnswer`.
- [x] ARD-registered: manifest present in the shared root (`~/.air/registry/graph_query.json`); mirror
  conformance green; re-validates as a `RegistryEntry`.

**Verification:** `uv run pytest tests/capabilities/test_graph_query.py` (4 hermetic passed); live
`-m store` (2 passed: real one-hop + two-hop MATCH). Full suite 343 passed + 23 skipped. Publish:
`uv run python scripts/publish_manifests.py`.

**Dependencies:** T25. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/capabilities/graph_query.py`, `tests/capabilities/test_graph_query.py`,
`src/rag_wright/store/{seam,arcadedb}.py` (`graph_neighbors` traversal), `manifests.py`
(+`graph_query`), `tests/store/test_arcadedb_schema.py` (stub extended with in-memory traversal).

### Task T27: Fusion (union/dedup on `chunk_id`, capped)

**Description:** Union and deduplicate on `chunk_id` between the reranked top set (T22) and the
graph-cited chunks (T26), capped; not a score fusion, since the graph returns an answer, not a
comparable ranked list (FR-Q.4).

**RAC-27:**
- [x] The reranked top set (T22 `RerankResult`) and graph-cited chunks (T26 `GraphAnswer` evidence) are
  unioned and deduplicated on `chunk_id`, capped (`DEFAULT_UNION_CAP=20`). `fuse(reranked, graph, *, cap)`
  → `FusionResult` of `FusedChunk{chunk_id, sources}`. Test: retrieval [c1,c2] + graph [c2,c3] → [c1,c2,c3],
  c2 tagged both sources; cap cuts the union.
- [x] Deterministic (retrieval order first, then graph first-appearance; same inputs → identical output)
  and **not a score fusion** — `FusedChunk` carries `sources`, no score (the graph returns an answer, not
  a comparable ranked list).
- [x] Registered under FR-Q.4. `register_fusion` → `fusion`, `function`, contract `FusionResult`. ARD
  manifest present in the shared root (`~/.air/registry/fusion.json`); mirror conformance green.

**Verification:** `uv run pytest tests/capabilities/test_fusion.py` (6 passed, hermetic). Full suite 350
passed + 23 skipped. Publish: `uv run python scripts/publish_manifests.py`.

**Dependencies:** T22, T26. **Scope:** S. **Status:** done.
**Files:** `src/rag_wright/capabilities/fusion.py`, `tests/capabilities/test_fusion.py`,
`src/rag_wright/capabilities/manifests.py` (+`fusion`).

---

## Phase 4 — Build, RLM synthesis tier (spec Phase 3)

### Task T28 (REOPENED 2026-07-15, rebuild): RLM synthesis = recursive descent + kept _reduce ascent

**Description:** The query-side RLM rebuild (ADR-0015/0016). Two halves: **descent** (new, recursive) — a
`SliceExtractor` seam (`SeamSliceExtractor` via `build_rlm_agent`) decomposes the candidate set through
the T15 machinery (fresh `rlm_decomposer` per over-large group, `rlm_slice_worker` extracts query-relevant
facts per leaf; per-slice tools/skills live in the worker); **ascent** (kept, ADR-0016) — the Python
`_reduce` fan-in combines the extracts into the synthesis. Unlike chunking, **recursion IS gated** here
(ADR-0019). Every `SliceOutput` keeps its `chunk_id` (no claim without a citation, FR-Q.6).

**RAC-28 (rebuild):**
- [x] Descent + ascent compose: `rlm_synthesize` = `extractor.extract` then `_reduce`; cited `chunk_id`s;
  empty candidates → empty. Hermetic with a stub extractor + stub combine.
- [x] **Recursion GATED (ADR-0016/0019):** the descent recurses past depth one — an opaque candidate set
  (`C0`) whose leaves only the decomposer reveals forces re-entry; the decomposer fires at >1 depth,
  workers extract the leaves, dispatch is code-driven (`eval_id`, fail-if-sequential). Driven by scripted
  fake models through the real machinery.
- [x] **Per-slice tool use** in extraction: a worker invokes a `cite` tool mid-extraction.
- [x] **`_reduce` kept** exactly (the ascent): recursive fan-in, ≤ fanout per combine (9 extracts, fanout
  3 → 4 combine calls). No sub-call ever sees the whole set of extracts.
- [x] **Live** (`-m model`): real deepseek-v4-pro extracts from candidates ($500M, Delaware) + combines +
  preserves citations. **Passed.**

**Verification:** `uv run pytest tests/capabilities/test_rlm_synthesis.py` — 6 passed + 1 skipped (live);
`-m model` **passed**. ruff clean. Full suite **379 passed + 26 skipped**. No external callers of the
removed flat `synthesize_slice`/`rlm_synthesize_async`. Dropped the old flat-descent concurrency tests
(that path is replaced by the agent; the `_reduce` fan-in is retained + tested).

**Dependencies:** T15, T27, ADR-0019. **Scope:** L. **Status:** done.
**Files:** `src/rag_wright/capabilities/rlm_synthesis.py`, `tests/capabilities/test_rlm_synthesis.py`.
**Rebuild complete:** T15→T17→T28 all rebuilt. **Follow-up done (separate commit):** populated
`grantedSubagents = ["rlm_decomposer","rlm_slice_worker"]` in the 3 RLM manifests (bound from the skill's
own `GRANTED_SUBAGENTS`, with a conformance test asserting manifest roster == skill roster so it cannot
drift), re-emitted to the shared root. **GraphWright handoff:** the sub-agents are real; GraphWright
re-runs mirror-vs-store verification and this unblocks its T9d.6 interpreter arm / W10.

### Task T28 (original, superseded by the rebuild above): RLM synthesis (interpreter load, slice in code, recursive sub-calls)

**Description:** Using the RLM skill (T15), load the candidate chunks (T27) into an interpreter as
data, slice and filter in code, and recursively call sub-models on the small focused portions, so
it never attends over the full chunk volume (FR-Q.5). The recursive sub-calls are
structured-under-reasoning work, so they resolve to the DeepSeek V4 Pro default in the seam.

**RAC-28:**
- [x] Candidate chunks load into the interpreter as data (a Python list of `SynthesisChunk`) and are
  sliced in code — one focused unit per chunk. `rlm_synthesize(query, chunks, *, synthesizer, ...)`.
- [x] Sub-model calls run on focused portions and the capability **never attends over the full volume**:
  each `synthesize_slice` sees one chunk; the code-side `_reduce` combines at most `fanout` notes per
  call, recursing. Test asserts each chunk is sliced alone and no combine sees > `fanout` notes (the
  reduce recursed). Dispatch is **concurrent + bounded** (async + `asyncio.Semaphore`, per the CLAUDE.md
  parallel-LLM rule; `max_inflight == N`). Sub-calls use the DeepSeek V4 Pro `STRUCTURED_REASONING`
  profile (ADR-0006); no model flag in code.
- [x] Registered under `rlm_synthesis` (FR-Q.5), kind **`agent_skill`** (applies the RLM method, requires
  `rlm_method`), contract `SynthesisResult`.
- [x] ARD-registered: manifest present in the shared root (`~/.air/registry/rlm_synthesis.json`,
  `requires: ['rlm_method']`); mirror conformance green; re-validates as a `RegistryEntry`.

**Verification:** `uv run pytest tests/capabilities/test_rlm_synthesis.py` (6 hermetic passed); live
`-m model` (1 passed, real DeepSeek, 3 concurrent slice calls + reduce in ~10s). Full suite 357 passed +
24 skipped. Publish: `uv run python scripts/publish_manifests.py`.

**Dependencies:** T15, T27. **Scope:** L. **Status:** done.
**Files:** `src/rag_wright/capabilities/rlm_synthesis.py`, `tests/capabilities/test_rlm_synthesis.py`,
`src/rag_wright/capabilities/manifests.py` (+`rlm_synthesis`, requires rlm_method).

### Task T29: Answer generator (grounded, cited, abstains) + vision-to-text

**Description:** Produce grounded, cited answers (no citation, no claim), confidence-aware, that
abstain when the retrieved context does not support an answer (FR-Q.6); plus the image-and-scan-
to-text step at ingestion (FR-C.9), on a Gemma 4 class model through the model-profile seam.

**RAC-29:**
- [x] Every non-abstaining answer carries a citation, and an unsupported question abstains rather than
  fabricates — **enforced in code** around the model: empty evidence abstains without a model call;
  citations not in the evidence are dropped; an answer left with no valid citation is coerced to an
  abstention (FR-Q.6). `generate_answer(query, evidence, *, model) -> GeneratedAnswer{answer, citations,
  abstained}`. **Live (`-m model`): real Gemma answer cited only from the real evidence ids.**
- [x] Confidence-aware: graph-fact confidence tags are put in front of the generator (`_evidence_block`
  surfaces `[confidence: …]`); a test asserts the tag reaches the model's prompt.
- [x] Vision-to-text converts a scanned image to text at ingestion (`vision_to_text`, Gemma 4 multimodal
  via the seam). **Live: transcribed a synthetic PNG ("HELLO WORLD").**
- [x] Registered under FR-C.9 as **two** capabilities/slugs (split per GraphWright's RegistryStore
  verification, **ADR-0014**): `generation` (answer generation, contract `GeneratedAnswer`,
  `register_generation`) and `vision_to_text` (transcription, contract `VisionTranscription`,
  `register_vision_to_text`) — different inputs/callers/failure modes, so discovery ranks each on its own
  intents (a bundled slug diluted both).
- [x] ARD-registered: **both** the `generation` and `vision_to_text` manifests are present in the shared
  root (`~/.air/registry/{generation,vision_to_text}.json`), each with representative queries scoped to
  its own behavior; mirror conformance green; 14 manifests total. GraphWright re-verifies after the emit.

**Verification:** `uv run pytest tests/capabilities/test_answer_generator.py` (8 hermetic passed); live
`-m model` (2 passed: real Gemma generation + real image transcription). Full suite 366 passed + 26
skipped. Publish: `uv run python scripts/publish_manifests.py`.

**Dependencies:** T11, T27. **Scope:** L. **Status:** done.
**Files:** `src/rag_wright/capabilities/answer_generator.py` (`register_generation`),
`src/rag_wright/capabilities/vision_to_text.py` (`register_vision_to_text`, `VisionTranscription`),
`tests/capabilities/test_answer_generator.py`, `src/rag_wright/capabilities/registry.py` (+`vision_to_text`
slug), `src/rag_wright/capabilities/manifests.py` (+`generation`, +`vision_to_text`),
`docs/adr/0014-split-generation-and-vision-to-text.md`, SPEC §5 + FR-C.9 (split).
**Note:** Enforces "no claim without a citation" and abstention (risk 9). Vision-to-text exercises
the scanned-filing subset (ADR-0002). **FR-C.9 split into `generation` + `vision_to_text` (ADR-0014)**
after GraphWright's RegistryStore verification flagged discovery dilution from the bundled slug.

### Task T30: Prefix + result caching

**Description:** Add prefix/prompt caching of the code scaffold and interpreter state and
memoization of sub-call and graph results (SPEC §13 Phase 3, §16.7). Resolves the caching-design
open question at this task.

**RAC-30:**
- [ ] Repeated sub-calls / graph results are memoized (a cache hit does no model work).
- [ ] Caching does not change answer correctness or citations.

**Verification:** `uv run pytest tests/capabilities/test_caching.py`

**Dependencies:** T28, T29. **Scope:** M.
**Files:** `src/rag_wright/capabilities/caching.py`, `tests/capabilities/test_caching.py`
**Note:** Round-trip and tail latency are measured here to inform routing thresholds (routing
itself is orchestration; §16.6).

---

## Phase 5 — Integrate and end to end

### Task T31: MCP skill surface (governed skills over MCP)

**Description:** Expose the registered capabilities (T6) as governed skills over a Model Context
Protocol (MCP) interface, so agents call the component and do not own it (FR-S.5, SPEC §1).
Grounded surface: the official `mcp` SDK (ADR-0001).
**ARD manifest kind is not affected by this surface (GraphWright RegistryStore verification):** the ARD
manifest `kind` describes how the **compiler** binds a capability — the 10 non-RLM capabilities are
`function` because the compiler binds them as **in-process callables**, deliberately, not over MCP. T31's
MCP surface is a **separate exposure** of RAG_Wright's capabilities to external callers; it does **not**
change any manifest `kind`. Do not revisit `function` → `mcp_tool` when building this.

**RAC-31:**
- [ ] Retrieval and graph capabilities are reachable only through the query-skill interface over
  MCP; the store implementation stays swappable behind it.
- [ ] The surface grows from the capability registry (no capability hardcoded outside it).

**Verification:** `uv run pytest tests/mcp/test_skill_surface.py`

**Dependencies:** T6, T22, T26. **Scope:** M.
**Files:** `src/rag_wright/mcp/server.py`, `tests/mcp/test_skill_surface.py`
**Note:** This is the capability surface, not the ingestion/query graphs (those are compiled from
the Orchestration Spec).

### Task T32: End-to-end scenarios + per-source ablation

**Description:** Run the end-to-end golden scenarios across the four archetypes and the per-source
ablation (turn off text retrieval, then graph, then RLM synthesis; measure the loss by archetype),
confirming the acceptance bar from Phase 0 (SPEC §12, §15, plan §4).

**RAC-32:**
- [ ] End-to-end answers are correct, cited, and abstain when unsupported, across the four
  archetypes, at the Phase 0 bar.
- [ ] The per-source ablation shows the graph leg's contribution on the EDGAR-derived multi-hop
  questions (T10) — the leg the graph exists for.
- [ ] An unchanged corpus re-run does effectively no work (incremental-update success criterion).

**Verification:** `uv run pytest eval/test_end_to_end.py -m e2e`

**Dependencies:** T29, T31. **Scope:** L.
**Files:** `eval/test_end_to_end.py`, `eval/ablation.py`
**Note:** Final review gate for Phase 5.

### Checkpoint: Complete
- [ ] All capabilities built and tested; every **query-discovered §5 capability** is internally
  registered and ARD-registered (its manifest loads and validates under `RegistryStore`), while the
  **seam-bound write/store steps (chunk write T20, graph storage T25) register nothing** (they are
  wired by the compiler, not discovered). Both gates resolved and recorded. End-to-end bar met. Ready
  for the compiler to discover and bind the graphs from the Orchestration Spec.

---

## Phase 5 — FR-K: Embedding-free OKF navigation (experimental, gated)

Corpus-neutral capability; ACORD is the first validation corpus. The T41 diagnosis (query-representation
gap, strong clause-clause structure) is the motivation: OKF traversal is the one mechanism that never
computes a query-to-chunk similarity. Categories are **manufactured via `graph_extraction`** (the corpus
does not ship them; verified for ACORD, BEIR corpus is `{_id, text}` only). Index descriptions reuse
existing chunk summaries (T-SUM). The compiled bundle is a gitignored, rebuildable data artifact.
Grounding: OKF producer patterns ground against the cloned reference agent's `bundle/` modules
(`/Users/farhan/work/knowledge-catalog/okf/src/graphify-out/graph.json`: `OKFDocument.parse/serialize`,
`regenerate_indexes`, `concept_id_to_path`, `write_concept_doc`); the reference agent is Google ADK +
Gemini + BigQuery, not our stack, so the compiler is reimplemented on langchain_openai/deepagents and
only the format logic is mirrored. Build order: T45-T48 → GATE-3a → T49-T51 → GATE-3 → T52-T53.

### Task T45: ACORD gold-chunk labels for reachability scoring

**Description:** Produce the gold-chunk label set the reachability metrics score against. For ACORD this
is cheap and deliberately so: clauses are pre-segmented, so a clause is a chunk, and the graded
query-clause pairs (qrels) at the existing grade floor already name the gold chunks. The work is mapping
qrel corpus-ids onto `chunk_id`s for the ingested clauses, recording gold as a set per query (ACORD
questions routinely have several relevant clauses), and exposing both any-gold and all-gold readings. No
span projection is needed, which is why ACORD is the first slice rather than a span-annotated corpus.

**RAC-45:**
- [ ] Every ACORD test-split query maps to its gold `chunk_id` set, derived from qrels at the same grade
  floor `eval/acord.py` uses (`RELEVANCE_FLOOR = 2`), with unmapped qrel corpus-ids reported, not dropped.
- [ ] Gold is a set per query; the harness exposes any-gold and all-gold readings separately.
- [ ] The label set is regenerable by command and keyed to the ingested corpus, so a re-ingest
  regenerates rather than invalidates it.
- [ ] Coverage is reported against the T33 baseline population, so reachability and recall@50 are
  computed over the same queries.
- [ ] A debug split is declared and recorded: the queries reserved for single-query iteration (T51) are
  named up front and held out of the headline GATE-3 number.

**Verification:** `uv run pytest eval/test_okf_gold.py` — 7 passed. Rebuild: `uv run python -m eval.okf_gold`
→ 57 test queries, 475 distinct gold chunks, **0 unmapped**, 475 induced category labels / 9 categories,
debug split 7/50. Full suite 441 passed + 27 skipped.
**Dependencies:** T33. **Scope:** S. **Status:** done.
**Files:** `eval/okf_gold.py`, `eval/test_okf_gold.py`, `data/eval/okf_gold.json` (gitignored artifact).
**Note:** Corpus-neutral shape, ACORD-cheap instance. Also emit the **qrels-induced silver category labels**
here as a side artifact: read `queries.jsonl` `metadata.category` (the loader ignores it today) and induce
a category on each gold clause from the query it is relevant to (test split: 57 queries → 475 gold clauses,
only 1 multi-label). These feed T46's category-quality report and T48's control. On a span-annotated corpus
(for example CUAD) gold must instead be anchored to source-document coordinates and projected onto
`chunk_id`s per compile; that is a separate task, not needed here.

### Task T46: OKF bundle compile (chunk-only, category tree + LLM one-line descriptions via a cheap model)

**Description:** Compile the ingested clauses into an OKF v0.1 conformant bundle. **Not** a re-chunk:
clauses are pre-segmented, `chunk_id`s are fixed (FR-S.2), bodies come from the chunk-text sidecar (T40)
which already guarantees the text matches the identifier's content hash. The work is signpost
construction: the **manufactured category signpost** (each clause classified into a corpus-appropriate
label set — for ACORD, its own 9 attorney categories — via the model-profile seam, **not** bound to
`graph_extraction`'s 41 CUAD ontology, driving the directory tree root→category→clause and the `tags`),
**LLM-generated one-line descriptions** (ACORD's stored summary IS the full clause text, not a one-liner
— verified at build — so a discriminating description is generated in the SAME gated enrichment call as the
category, via the cheap `OKF_ENRICHMENT` model role, ADR-0023, so it is not a second model pass), non-empty
`type` frontmatter, source-document fields, `index.md` per directory, and a conformance lint. The
bundle root is stamped with `okf_version` and the compile-recipe version. Cross-links are **not** written
here (T49, behind GATE-3a), so the first reachability read (T47) measures the hierarchy, tag, category,
and description channels and treats links as a later lever.

**RAC-46:**
- [x] Every ingested clause has a bundle file whose body is byte-faithful to the sidecar text for its
  `chunk_id`, and whose frontmatter carries a non-empty `type`. No chunk identifier changes.
- [x] The category signpost is produced by a corpus-appropriate classifier (**not** bound to
  `graph_extraction`'s 41 CUAD ontology); coverage and confidence are reported against the qrels-induced
  labels (T45), and clauses with no confident category land in a recorded fallback subtree, not dropped.
- [x] `index.md` files exist at every level, each entry carrying an LLM-generated one-line description
  (a real discriminator, not a restated title). log.md is deferred to T52 (incremental-recompile lifecycle).
- [x] The conformance linter passes and reports orphan rate, description coverage, and broken-link ratio
  as numbers.
- [x] Bundle root records `okf_version` and the compile-recipe version.
- [x] Content-hash gated (recompiling an unchanged corpus does effectively no work); the bundle is
  written under gitignored `data/`, never committed.
- [x] Selective recompile: a single subtree recompiles under a modified recipe without a full rebuild
  (T51 depends on this).

**Verification:** `uv run pytest tests/capabilities/test_okf_compile.py` — 10 passed. Compile:
`uv run python -m rag_wright.okf.compile` → **3,931 clauses, 3,491 categorized** into ACORD's 9 (440
`_uncategorized` = honest "None of these"; 1 deterministic fallback); lint **passes=True, orphan 0.000,
description_coverage 1.000, broken_link 0.000**. Full suite 451 passed + 27 skipped.
**Dependencies:** T40, T45. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/okf/{__init__,document,enrich,compile,lint}.py`, `capabilities/registry.py`
(+slug `okf_compile`), `models/profiles.py` (+role `OKF_ENRICHMENT`, ADR-0023),
`tests/capabilities/test_okf_compile.py`; bundle + enrichment cache under gitignored `data/acord/okf/`.
**ARD category:** canonical slug `okf_compile`, internal registry entry, **no ARD manifest** (category 3,
foundation derivation, same as `ontology_registry_derivation`). Open question 14 applies: this builds the
chunk-only bundle; a concept layer above the chunks is a lossy synthesis, deliberately not built yet.
**Diagnostic (pre-T45, 2026-07-22 — resolved; ADR-0022 addendum):** the assumed `graph_extraction` fit
does NOT hold — its 41 CUAD categories cover only **67%** of ACORD's gold-clause mass (Indemnification
121 + Affirmative Covenants 36 = 157/475 have no CUAD home). A **direct classifier into ACORD's own 9
categories agrees with the qrels-induced labels 91.6%** (deepseek-v4-pro, 475/475, per-category:
Indemnification 100%, only Restrictive Covenants weak at 62%). Decision: **decouple the signpost from
graph_extraction's ontology** (above). **Skew caveat:** 318/475 gold clauses (67%) sit in 2 categories, so
category is a strong *bucketer* but a coarse *localizer* — within-branch localization (descriptions, links)
carries the queries that dominate the eval. The graph_extraction ontology gap itself is logged as **T54**
(deferred, ask-first); it is a graph-quality issue for non-CUAD corpora, separate from this experiment.

### Task T47: Reachability analyzer and signpost ablation

**Description:** The deterministic, model-free analyzer that computes whether each gold chunk is
discoverable from the bundle root through signposts within the depth bound and frontier budget, plus the
ablation runner that recomputes reachability with one channel removed at a time. It separates the
compile-side ceiling from traversal-side realized recall, so a disappointing traversal number is
diagnosable. It runs before the traversal exists and is the per-corpus reachability spike (GATE-3a): a
ceiling below the grounded bar redirects the experiment cheaply. Evaluation software: no runtime
capability, no registration, no manifest.

**RAC-47:**
- [ ] Reachability is computed per gold chunk, model-free and reproducible, reporting connectivity
  reachability (present and connected at all) and signpost reachability within bounds separately.
- [ ] Reported at both any-gold and all-gold readings, over the same query population as the T33 baseline.
- [ ] Per successful hop, the signpost channel that carried it is recorded, so the ablation has
  something to attribute to.
- [ ] The ablation runner recomputes reachability with each channel removed (tags, index descriptions,
  category tree, and — once T49 lands — cross-links), reporting the cost of each channel.
- [ ] Signpost what-if: reachability for a single chunk is recomputable under a hypothetical frontmatter
  or description change, without recompiling the bundle (T51 depends on this).
- [ ] Results stamped with the compile-recipe version.

**Verification:** `uv run pytest eval/test_reachability.py` — 9 passed. Run: `uv run python -m eval.reachability`
/ `uv run python -m eval.ablation`. **Real ceiling (tightened lexical proxy, budget 50):** connectivity 1.000,
**any-gold 0.930, per-gold recall ceiling 0.776, all-gold 0.491**; ablation: description carries it (cost
0.351), category_tree −0.018, tags 0.018, frontier 0.000. Full suite 460 passed + 27 skipped.
**Dependencies:** T46. **Scope:** M. **Status:** done.
**Files:** `eval/reachability.py`, `eval/ablation.py`, `eval/test_reachability.py`.
**DECISION (ADR-0022 addendum 2):** the ceiling (recall-relevant 0.776) clears the bar (0.667) and doubles the
baseline (0.379) with NO model call, so **viability is settled — FR-K can work for ACORD.** This is no longer
go/no-go. T50 is mandated to **realize** the ceiling by refinement+iteration; a shortfall is a policy/agent
failure to diagnose+fix (T51), not a verdict against the approach. Description quality is the lever (it carries
the ceiling); the category tree buys navigation efficiency, not reach.
**Note:** Open question 11 (within-branch full-text as a channel) and 13 (any-gold vs all-gold headline) — the
headline is the per-gold recall ceiling (0.776); full-text-as-channel deferred (descriptions already carry it).

### Task T48: Category label-retrieval control arm

**Description:** Build the T41 backlog item and run it as the **control arm for GATE-3a and GATE-3**, not
an independent experiment. It matches on the manufactured clause-category label directly (T46, from
`graph_extraction`) instead of on an embedding — the cheapest mechanism that bypasses the query-
representation gap. Its purpose is to establish how much of the lift comes from coarse label matching
alone, so OKF traversal is measured against a fair alternative, not against the 0.379 two-leg baseline it
will trivially differ from. Because control and bundle tree share the same manufactured labels, they
share one point of failure (category-tagging quality); that is stated, and category quality is reported
by T46 so the control's ceiling is known.

**RAC-48:**
- [ ] Category label-retrieval is wired into the candidate pool and measured on the same ACORD test
  split, at the same evidence-feed cap, as every other arm.
- [ ] Recall at the gate k and the diagnostic nDCG@10 are reported alongside the two-leg baseline.
- [ ] Cost is reported on the same axes as T50 (latency above all), so the comparison is
  quality-per-cost.

**Verification:** `uv run pytest eval/test_category_retrieval.py` — 4 passed. Run: `uv run python -m
eval.category_retrieval`. **Real result (57 test queries, 0 model calls):** **recall@50 0.134**, recall@10
0.019, nDCG@10 0.022, **containment (gold-in-bucket) 0.895**. vs two-leg baseline 0.379, reachability ceiling
0.776. Full suite 464 passed + 27 skipped.
**Dependencies:** T46. **Scope:** M. **Status:** done.
**Files:** `eval/category_retrieval.py`, `eval/test_category_retrieval.py`.
**GATE-3 read (cost/value):** the category label is RIGHT (containment 0.895 ≈ T47 depth-1) but CANNOT localize
within large buckets (LoL 489, IP 817) with no ranking → recall@50 0.134, below even the embedding baseline
0.379 and far below the ceiling 0.776. The whole containment→recall gap is within-bucket localization, exactly
what OKF's description-based traversal provides. So the cheap control does NOT make OKF redundant: T50's value
(description localization) is justified. Honest caveat: control has no intra-category ranking by design (label,
not embedding), so 0.134 is label-only; 0.895 is its ranking-independent ceiling.
**Note:** Running this as the control keeps the gates honest. If coarse label matching captures most of
the lift, the OKF compile is not justified by the numbers — a valid, cheap early result. The control uses
the same corpus-appropriate direct classifier as T46 (not graph_extraction) against the T45 induced labels.
The pre-T45 diagnostic (ADR-0022) previews its ceiling: classification agreement is high (91.6%), so
bucketing is strong, but recall is bounded by within-branch localization given the 2-category skew — the
control likely lands in the middle, which is exactly the tension GATE-3/GATE-3a adjudicate.

### GATE-3a: Reachability ceiling — **RESOLVED (2026-07-22): PROCEED. Viability settled, not a kill-switch.**

**Outcome (recorded):** T47 measured the ceiling at **any-gold 0.930 / per-gold recall 0.776 / all-gold 0.491**,
connectivity 1.000, model-free. The recall-relevant ceiling (0.776) clears the grounded bar (0.667) and doubles
the two-leg baseline (0.379). So the compiled bundle **has the information** to reach the gold — the compile is
NOT the bottleneck for ACORD. **GATE-3a is no longer a go/no-go**: viability is proven (ADR-0022 addendum 2).
The reframe: T50 is **mandated to realize this ceiling** by refinement+iteration; a shortfall is a policy/agent
failure to diagnose+fix (T51), not a redirect-or-shelve verdict.

**Still live (not viability, but cost/value):** T48 (category-label control) — is OKF traversal's complexity
justified against the cheap mechanism? The ablation says **descriptions carry the ceiling** (cost 0.351; category
tree −0.018), so recipe refinement (T51) targets description quality, and the category tree is kept for
navigation efficiency, not reach.

**Per-corpus reuse:** for a NEW corpus, GATE-3a stays the reusable model-free spike (run T45-T48, read the
ceiling) — there it can still say "shelve" if that corpus's ceiling is low. For ACORD it said proceed.

### Task T49: Cross-linking from measured clause-relation structure

**Description:** Write the link graph into the bundle, standard markdown links absolute from the bundle
root (not double-bracket wikilinks). Edges are derived from structure T41 measured (relevant-set cluster
tightness 0.686, missed-to-anchor cosine 0.706; clause category as a coarser relation), not guessed. The
T41 discipline: measurements justify the edges existing, they do **not** predict that following them helps
(T41's finding was reachability without rankability). Links are navigation affordances for a model reading
signposts, not similarity expansion — the thing GATE-3 tests. When links land, reachability (T47) is
re-measured with the link channel and the ablation shows its marginal contribution.

**RAC-49:**
- [ ] Links are standard markdown, absolute from the bundle root, and resolve (broken-link ratio reported
  by the T46 linter).
- [ ] Edge construction is derived from measured structure, the derivation recorded, and edge density
  bounded so the link graph does not degenerate into near-complete connectivity.
- [ ] Link traversal degrades on a dangling link rather than faulting (OKF broken-link tolerance).
- [ ] Cross-links are regenerable independently of the bundle compile, so link recipes can be ablated
  without a full recompile.

**Verification:** `uv run pytest tests/capabilities/test_okf_links.py` — 6 passed. Apply: `uv run python -m
rag_wright.okf.links`; re-measure: `uv run python -m eval.reachability`. **Real result (Option-1 embedding-free,
shared-distinctive-term edges):** 20,033 edges over 3,931 clauses, mean degree 5.10 (cap 8, df>100 skipped), 952
isolated, broken-link 0.0000. **Cross-links RAISE the reachability ceiling: per-gold 0.776→0.845 (+0.069),
all-gold 0.491→0.614 (+0.123), any-gold 0.930→0.947** (carry 43 gold instances descriptions/category missed).
**→ Option-2 embedding-kNN NOT needed** (embedding-free links already lift it; kept as an unused fallback). Full
suite 470 passed + 27 skipped.
**Dependencies:** T46, T41. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/okf/links.py`, `tests/capabilities/test_okf_links.py`; `okf/lint.py` extended to scan
concept-body links; `eval/reachability.py` gained the `cross_links` channel + `load_cross_links`.
**Bugs the live run caught:** the linter only scanned `index.md` links (missed all cross-links — a RAC-49 hole);
`_clause_body` newline inconsistency broke idempotent re-linking. Both fixed.

### Task T50: `okf_navigate` traversal capability

**MANDATE (post-GATE-3a, ADR-0022 addendum 2):** T47+T49 proved the bundle can reach a per-gold recall ceiling of
**0.845** (T47 descriptions 0.776 + T49 cross-links; > bar 0.667, ~2.2x baseline 0.379), model-free. So T50 is NOT
testing whether navigation works — it is **realizing a known-achievable ceiling**. The success target is the
reachable set: for each query, the traversal should reach the gold clauses the analyzer marked reachable. Read
every run against the 2x2 — reachable-and-reached (good) vs **reachable-not-reached (the policy gap to close via
T51)**. If realization lags the ceiling, iterate the recipe/prompt/params (T51); do not conclude the approach
fails. Description quality + cross-links are the proven levers.

**Description:** Build the traversal: one interpreter node that reads the bundle root `index.md`, filters
candidate subtrees by frontmatter predicate and index description, expands the frontier through links and
index entries, dispatches one reader sub-agent per surviving body, deduplicates against a visited set, and
stops on convergence or the depth and frontier bounds, returning a `chunk_id` shortlist plus the trace. It
computes no query-to-chunk similarity anywhere. Structurally this is the RLM dynamic-sub-agent machinery
(T15/T36/T37) applied to a bundle: PTC does the deterministic sift (enumerate, read index, parse
frontmatter, filter, resolve link) so filtering costs no model call; loop-until-done with a `seen` set
drives frontier expansion so completeness does not depend on a fixed k; fan-out-and-synthesize reads
bodies one per sub-agent so per-call context stays bounded; recursion handles depth. Run parameters (model
via the T11 model-profile seam, depth and frontier bounds, PTC allowlist, repeat count) are ordinary
configuration recorded with each result — no separate harness-profile seam.

**RAC-50:**
- [ ] Returns a `chunk_id` shortlist and a full trace (nodes visited, order, prune decisions); no code
  path computes a query-to-chunk embedding similarity.
- [ ] Filtering by frontmatter predicate happens before any body is read, verifiable in the trace as
  bodies-read being a small fraction of candidates-considered.
- [ ] One interpreter session per traversal; sub-agents fan out with parallel dispatch inside a single
  interpreter (ADR-0020, KI-1; the T35 per-process serialization floor applies).
- [ ] Per-call token usage stays bounded as round count grows, and each reader sub-agent receives its own
  body and the query by enforcement, not orchestrator model choice (the T42 lesson).
- [ ] Cost telemetry per query: serial round count (primary latency proxy), total dispatches, peak
  frontier, total and peak-per-call tokens, wall-clock latency, max depth.
- [ ] Variance characterized: k repeated runs per query under fixed run parameters, Reached reported as
  reached-always / reached-sometimes / never-reached plus the cost spread. Exact replay is not assumed
  (fresh navigation code each run); the spread is the run-variance floor any tuning improvement is judged
  against.
- [ ] All run parameters recorded with every result.
- [ ] Registered under canonical slug `okf_navigate` with an ARD manifest whose representative queries are
  authored (category 1), loading under `RegistryStore(root)` with no `RegistryLoadError`.

**Verification:** `uv run pytest tests/capabilities/test_okf_navigate.py`, plus an opt-in live model test
(`-m model`) for traversal quality (branch-choice is model-dependent; stubbed responders miss it).
**Dependencies:** T46, T47, T49, T35. **Scope:** L. **Status:** todo (behind GATE-3a).
**Files:** `src/rag_wright/capabilities/okf_navigate.py`, `capabilities/registry.py`,
`capabilities/manifests.py`, `tests/capabilities/test_okf_navigate.py`.
**Note:** GraphWright needs nothing new: the node is `needs_interpreter` + a PTC allowlist + configured
sub-agents + the optional `rlm` marker, all of which exist. Do **not** split the sift and the read into
two interpreter nodes: the working set (frontier, visited set, shortlist) lives in interpreter variables;
splitting forces serialize-and-rehydrate across a node boundary and loses the convergence check.

### Task T51: Single-query trace-and-iterate harness

**Description:** The tuning loop. Take one failing query, see exactly where and why the traversal went
wrong, change a recipe or run parameter, re-run until it works, then confirm the change generalizes rather
than fitting the query. This is the instrument that **produces** the data-specific signpost recipes the
design assumes. The loop tunes the procedure (compile recipe, prompts and skills, run parameters, tool
surface), never the data — a hand-edited bundle file makes the compile-recipe stamp a lie and evaporates
on the next compile, so if a fix cannot be expressed as a recipe or parameter change, it is not a fix.

**RAC-51:**
- [ ] One command takes a query id and dumps every artifact: reachability verdict, traversal trace, the
  2x2 cell, cost telemetry.
- [ ] Reachability runs first and model-free — a gold chunk that was never reachable is reported before
  any model call is spent.
- [ ] Divergence point: the trace is joined against gold to report the frontier step where a gold chunk's
  ancestor was available and not expanded, with the verbatim signpost text the model saw (index entry,
  description, the predicate that rejected it). Distinct from a list of prune decisions.
- [ ] Fast iteration: a signpost what-if (T47) and a selective subtree recompile (T46) both run without a
  full rebuild.
- [ ] Persist and diff: traces persisted with compile-recipe and run-parameter versions, two runs
  diffable at the trace level.
- [ ] Compare distributions, not runs: a change is evaluated as k runs before against k after (exact
  replay unavailable, so a single before-and-after pair is not evidence).
- [ ] Variance floor: an improvement on a single query is reported against the k-run spread from T50.
- [ ] Regression guard: any recipe or parameter change triggers a population re-run reporting per-query
  deltas in both directions.
- [ ] Held-out reporting: results reported separately for the declared debug split (T45) and the held-out
  remainder, so the generalization claim is demonstrated, not asserted.

**Verification:** `uv run pytest eval/test_trace_iterate.py`. Run:
`uv run python -m eval.trace_iterate --query <id>`.
**Dependencies:** T47, T50. **Scope:** L. **Status:** todo (behind GATE-3a).
**Files:** `eval/trace_iterate.py`, `eval/trace_diff.py`, `eval/test_trace_iterate.py`.
**Note:** The held-out split turns a reasonable generalization expectation into evidence GATE-3 can score.
The variance floor matters because navigation code is model-emitted at run time, so the tuning signal is
noisier than for a deterministic pipeline.

### GATE-3: FR-K — realization vs the proven ceiling, and cost vs the control (NOT graduate-or-remove-viability)

**Reframed (ADR-0022 addendum 2):** viability was settled at GATE-3a (per-gold recall ceiling 0.776, model-free).
GATE-3's **"remove because it can't reach" branch is off the table for ACORD.** GATE-3 now asks: (a) how close did
T50 realization get to the 0.776 ceiling (and is the residual reachable-not-reached gap closed or diagnosed via
T51), and (b) is OKF traversal's cost justified against the T48 category-label control. Outcomes narrow to
graduate (realized near the ceiling, worth its cost) or keep-narrow (works but the cheap control captures most of
the lift); "remove" only returns if realization is fundamentally, unfixably below the proven ceiling — which the
mandate treats as a bug to fix, not an expected outcome.

**Decision:** whether the embedding-free OKF path graduates from experimental, stays a narrow capability,
or is removed.

**Inputs:** T47 reachability and ablation, T50 Reached and cost telemetry, T51 held-out results, T48
control arm, all against the T33 ACORD baseline (two-leg recall@50 0.379, grounded bar 0.667, raw-hybrid
recall@200 ceiling 0.65) at a single, explicitly bound evidence-feed cap.

**Reading rules (set before the run):**
- Compare arms at the **same** gate k and the same `fuse(..., cap=k)` binding.
- Report the **2x2**: reachable-and-reached; reachable-not-reached (policy failure, fix traversal/params);
  not-reachable-not-reached (scaffolding failure, fix the compile recipe); not-reachable-but-reached (the
  reachability model under-counts a channel, fix the model). A single recall number without this split is
  not interpretable.
- Low reachability is not a traversal verdict.
- Score the held-out split, debug split reported separately.
- Judge against T48, not only against the baseline.
- Report the winning compile-recipe and run-parameter versions with the number.
- Report cost alongside quality (serial round count and latency distributions first-class), since FR-K is a
  capability, not a default.
- Strategy memory (T53) is off for the graded run; memory-on is measured separately, after.
- Do not lower the grounded bar (E / 0.75 with E = 0.5).

**Outcomes:** graduate (FR-K leaves experimental, a routing question opens as its own task), narrow (keep
as a registered capability for a bounded question class, no default change), or remove (excise the FR-K
block; the reachability instrument, the trace harness, and the ACORD gold labels are kept regardless).

### Task T52: Bundle lifecycle

**Description:** Incremental recompile, update, and delete for the bundle, sharing the single content-hash
gate that already couples the index and the chunk-text sidecar (T40), so a bundle cannot drift from the
store. Deleting a chunk removes its file and repairs or records inbound links, since a stale link silently
lowers reachability and reports itself nowhere. Gated behind GATE-3: lifecycle work on a bundle that has
not earned its place is wasted.

**RAC-52:**
- [ ] Bundle write is driven by the same content-hash gate as the index and sidecar writes, so an
  unchanged document skips all three and a changed chunk writes all three.
- [ ] A completeness guard rejects a bundle state where an indexed chunk has no bundle file, before any
  write, mirroring the T40 guard.
- [ ] Delete-by-`source_doc_id` removes bundle files and repairs or records inbound links; a post-delete
  reachability regression check runs against the golden subset.
- [ ] Recompiling an unchanged corpus does effectively no work.

**Dependencies:** GATE-3, T34. **Scope:** M. **Status:** todo (post-GATE-3).
**Files:** `src/rag_wright/okf/lifecycle.py`, `tests/capabilities/test_okf_lifecycle.py`.
**Note:** Couples to T34 (document update/upsert), the existing lifecycle gap. If T34 lands first, the
bundle is a third consumer of the same lifecycle seam rather than a parallel path.

### Task T53: Strategy memory

**Description:** Store what worked, and reuse it. When a traversal solves a query, record the originating
query, the navigation strategy, and the outcome, so a later query of similar shape retrieves a known-good
strategy instead of generating one. The second answer to run-to-run variance, and a better one: it does
not make sampling deterministic, it removes the sampling step for queries that resemble solved ones.
Placed after GATE-3 deliberately: it makes the evaluation stateful, and recognizing a strategy worth
keeping presupposes knowing what a good one looks like (T51's output).

**What is stored.** The primary artifact is a **parameterized strategy** (for queries of this shape: filter
frontmatter by these tags, expand along these link kinds, at this depth, read bodies at these leaves), with
the raw emitted code attached as a reference artifact, not the thing retrieved. The parameterized form is
the transferable unit; raw code hardcodes paths and breaks on the first compile-recipe change.

**Admission, two regimes.** Offline, gold is available, so admission means verified (a case enters only if
the traversal retrieved the gold chunks). In production, gold is absent, so admission is judge-approved or
downstream-signal-approved. Bootstrap: build memory offline from gold-verified runs, ship it seeded, let
production accumulate under judge approval.

**RAC-53:**
- [ ] A solved query records a parameterized strategy plus the raw emitted code as an attached artifact,
  stamped with the compile-recipe and run-parameter versions.
- [ ] Case retrieval does not use query embeddings — cases are keyed on structural features (types and
  tags involved, predicates that fired, subtrees that proved productive). Indexing by query embedding would
  reintroduce the query-representation gap this path exists to avoid, one layer up.
- [ ] Offline admission is gold-verified; production admission is judge-approved or
  downstream-signal-approved, and the two paths are distinguishable in the stored case.
- [ ] Staleness: a case whose compile-recipe version no longer matches the live bundle is invalidated, not
  silently applied.
- [ ] Exploration path: a configured fraction of runs re-derives from scratch, so a subtly wrong cached
  strategy cannot lock the system into being reliably wrong.
- [ ] Eval-state discipline: memory is cold- or warm-started explicitly; cold and warm runs reported
  separately, a warm run records query order.
- [ ] No cross-split leakage: cases from the declared debug split (T45) do not populate memory serving
  held-out queries.
- [ ] Variance reduction is measured: k runs memory-on vs off, reporting the change in the reached-always /
  reached-sometimes / never-reached distribution and the cost spread.

**Dependencies:** GATE-3, T50, T51. **Scope:** L. **Status:** todo (post-GATE-3).
**Files:** `src/rag_wright/okf/strategy_memory.py`, `tests/capabilities/test_strategy_memory.py`.
**Note:** The natural implementation stores cases as an OKF bundle navigated with the same progressive
disclosure the corpus uses, so memory and corpus share one mechanism. Scoped to traversal strategies only;
does not settle the general agent-memory question.

### Task T54: graph_extraction ontology extension (deferred, ask-first — NOT FR-K)

**Description:** Spun off from the pre-T45 diagnostic (ADR-0022). `ClauseCategory` is a hardcoded 41-member
CUAD enum that `ClauseFact` strictly validates against, so `graph_extraction` (and therefore `graph_query`)
cannot represent clause types CUAD lacks — for ACORD, Indemnification (25% of the eval's gold mass) and
Affirmative Covenants have no home. This is a **graph-quality** issue for any non-CUAD corpus, separate from
the OKF experiment (which decouples its signpost classifier and needs none of this). Not scheduled; recorded
so the gap is visible. The viable extension shapes (ADR-0022 addendum): **T8-derived ontology** (SPEC §17 /
assumption 3, the aligned path), **static enum extension** (cheapest, pollutes the CUAD contract), or an
**open-set proposal/verification seam** (the FR-C.7 / T23b pattern). A **crosswalk/hierarchy layer is ruled
out** — it can coarsen an existing taxonomy but cannot invent missing coverage. Any option is a data-model
change (ask-first, load-bearing) and must preserve strict-reject of the genuinely-unknown.

**Dependencies:** T8, T23. **Scope:** M-L (T8-derived) / S (static). **Status:** deferred (ask-first).
**Files (if taken up):** `src/rag_wright/contracts/ontology.py`, `src/rag_wright/ontology/derive.py`,
their tests. **Note:** decide the shape with us before touching the ontology; do not extend it as a side
effect of any OKF task.

---

## Requirements coverage map

Every spec requirement traces to a task (or is explicitly out of scope / compiler work).

| Requirement | Task(s) |
|---|---|
| FR-S.1 one store | T3, T13, T25 |
| FR-S.2 `chunk_id` | T1 |
| FR-S.3 `entity_id` | T1, T8 |
| FR-S.4 provenance/confidence | T2 |
| FR-S.5 query-skill seam / MCP | T6, T13, T31 |
| FR-C.1 parsing | T16 |
| FR-C.2 embedding | T19 |
| FR-C.3 hybrid search | T21 |
| FR-C.4 reranking | T22 |
| FR-C.5 graph query | T26 |
| FR-C.6 graph extraction | T5, T23 |
| FR-C.7 entity resolution (canonicalization + linking) | T8, T23b, T24 |
| FR-C.8 ontology/registry derivation | T4, T8 |
| FR-C.9 reasoning/generation/vision-to-text | T29 |
| FR-C.10 RLM skill | T15 |
| FR-I.1 RLM chunking | T17 |
| FR-I.2 small-to-large escalation | T18 (GATE-1 conditional) |
| FR-I.3 chunk record | T3, T19, T20 |
| FR-I.4 graph extraction over parsed docs | T23, T25 |
| FR-I.5 incremental / idempotent / dead-letter | T20, T25 |
| FR-I.6 (three parts) | **Model-tiering:** T11, T18. **CPU-GPU decoupling / pooled inference / backpressure:** built-in acceptance criterion on T19 (embedding) and T23 (extraction). **Two run modes (bulk vs background):** out of scope — orchestration/deployment, not a capability |
| FR-Q.1 hybrid search | T21 |
| FR-Q.2 rerank cutoff | T22 |
| FR-Q.3 graph answer as evidence | T26 |
| FR-Q.4 fusion union/dedup | T27 |
| FR-Q.5 RLM synthesis | T28 |
| FR-Q.6 grounded/cited/abstains | T29 |
| §12 golden eval + archetypes | T7, T9, T10, GATE-1, GATE-2, T32 |
| §8 graph relational/multi-hop | T10, T26, T32 |
| FR-K.1, FR-K.2, FR-K.4 (OKF bundle compile) | T46 |
| FR-K.3 (cross-linking) | T49 |
| FR-K.5, FR-K.6 (navigation primitives + traversal) | T50 |
| FR-K.7 (bundle lifecycle) | T52 |
| FR-K.8 (reachability spike) | T47, T51 |
| FR-K.9 (strategy memory) | T53 |
| §13 Phase 5 / §15 (FR-K gates) | GATE-3a, GATE-3 |
| §3.2 extras | Out of scope / ask-first (not scheduled) |
| §3.2 durable memory backend | Out of scope (engine-side integration; no FR-S.6, no task) |

---

## Risks carried from plan.md section 3

De-risked early by foundation tests: risk 1 (ArcadeDB recall) by T14/GATE-2; risk 3 (structured
output on open models) by T12 on DeepSeek V4 Pro. Risk 2 (arcadedb-python v0.x) is mitigated by
grounding every call (T13, T21, T25, T26). Risk 4 (chunking determinism) is tested in T17. Risk 5 (entity-resolution fragmentation) is addressed at T23b (canonicalization) and measured at T23b and T24. Risk 7 (identifier schemes) fixed at T1.
Risk 8 (summary-miss) is designed into T19 and tested there and at T32. Risk 9
(citation/abstention) is enforced at T29. Risk 10 (OpenIE undecided) is deferred behind the T5
extractor seam.
