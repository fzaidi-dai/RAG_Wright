# Plan: Engine-prep — ready the RAG_Wright engine for consumption by a new product

Status: **PLAN / awaiting review** (drafted 2026-10-05). This is a scoped engagement plan **and** its task
ledger: run it under the standard working loop (one task → ground → contract/TDD where code is touched → verify →
stop for approval → commit atomically). It is subordinate to the root `SPEC.md` and the engine-platform child spec
(`docs/specs/engine-platform/SPEC.md`), and governed by ADR-0052 (engine/product split, GraphWright parked),
ADR-0066 (knowledge in the `.ttl`), ADR-0117/0118 (engine API + capability runtime). It does not add engine
requirements; it makes the existing engine cleanly installable, documented, and adaptable to a new domain.

The first consumer of the output is **TexWright** (textile fabric design) — a separate repo the user creates after
this engagement. TexWright is NOT built here.

## Goal

Turn this repo into something a new product team (and its coding agent) can adopt the way they'd adopt any
open-source framework: install the package, read a coherent doc set, follow a domain-adaptation guide, and stand up
a new domain — without wading through the architecture's history. Keep the full decision record in the repo but
out of the installed package and out of the newcomer's way.

## Non-goals (explicit, with the gate that would change them)

- **Public PyPI publish.** We make the package release-*ready* and validate a clean-venv install; the actual
  `twine upload` to public PyPI is a separate, user-approved gate (it is a hard-to-reverse public claim for an
  open-core product). TexWright consumes the engine via path/git/private index in the meantime.
- **A hosted docs site (mkdocs-material / readthedocs).** We ship grounded markdown + a generated API reference
  now; a published site is a later extension (noted in WS3).
- **The TexWright build itself** (new repo, filling the templates, the domain capabilities). A later engagement.
- **Re-litigating any landed ADR.** Legacy ADRs get a status banner + an index, not edits to their decisions.

## Decisions locked (user-approved 2026-10-05)

1. **PyPI:** make-ready-and-hold; public publish is a separate later gate (see Non-goals).
2. **ADRs:** archived **in place** with a `Status:` banner + a grouping index; not moved or renumbered (they are a
   cross-referenced immutable log). Only the `0015` numbering collision is repaired.
3. **Docs format:** plain grounded markdown in `docs/` now; generated API reference; a hosted site is later.
4. **Templates + onboarding skill home:** in the **engine repo** (`.claude/skills/using-the-rag-wright-engine/` and
   `docs/templates/product-starter/`); the consumer pulls them in via a setup step (as RuleWright pulls the Addy
   Osmani skills).
5. **Sequencing:** WS0 (archive) first so the repo is clean before new docs land; WS1 (packaging) next; WS2–WS4
   (docs) build on WS1; WS5 (onboarding) depends on WS1 + WS4. See "Sequence & dependencies".

## Standing constraints for every task here

- **Version pins are `>=` only — never `==`.** Part of WS1 is converting the two existing `==` pins to `>=`. No
  task in this plan may introduce an `==` pin.
- **Docs must be grounded and, where runnable, executed.** Every API claim is checked against the code graph
  (Graphify `framework`/`project`) and the live symbols; every quickstart/snippet runs end-to-end against a real
  ArcadeDB + the reference pack. The current stale README (it still says GraphWright compiles the graphs) is the
  cautionary tale — we do not ship aspirational docs.
- **`uv` only** (`uv run`, `uv add`, `uv build`); never bare `python`/`pip`.
- Archive/doc-move tasks use `git mv` to preserve history; no content is deleted.

## Grounded baseline (as surveyed 2026-10-05)

- Package `rag-wright` v0.1.0, hatchling src-layout, `uv build` works; Python `>=3.12,<3.13`. All data assets
  (3 `.ttl`, 11 capability `SKILL.md`, templates, `dim_fleet.json`) are under `src/rag_wright/` → already ship in
  the wheel. `docs/`, `.claude/skills/`, `tasks.md` are repo-root → never in the wheel.
- Publish blocker: `en-core-web-sm` is a `tool.uv.sources` direct-URL dep (PyPI-forbidden). Metadata gaps: no
  `py.typed`, no `classifiers`/`[project.urls]`/`license`/`authors`; `langgraph` has no floor; two `==` pins.
- Public API is `rag_wright.api` (22 symbols); `register_capability`/`load_reference_pack` live in
  `rag_wright.capabilities.manifests` (the one seam to smooth). ARD ships empty (`MANIFEST_SPECS={}`); the
  36-capability contract+compliance reference pack is opt-in (`load_reference_pack()`); 9 of 36 are invocable-by-name
  via `impl_ref`.
- Docs: 120 ADR files (incl. a duplicate `0015`); ~13 GraphWright/RLM/OKF-era ADRs parked by ADR-0052; many
  delivered plans/handoffs/results/design/engine-issues are historical. Current source-of-truth is scattered across
  `contract_pipeline_explainer.md`, `new-domain-build-sequence.md`, `ARCHITECTURE_OVERVIEW.md`, the engine-platform
  child spec, and `docs/product/*`.
- 6 engine-authored Claude Code skills in `.claude/skills/` (stay committed; not in the wheel). Addy Osmani
  generics are an external MIT plugin repo, symlinked as setup. RuleWright already has a "How to reuse"
  templatization seam in its `CLAUDE.md` + `docs/playbook.md`.

---

## Task ledger

Last approved: **PREP-1.6** (clean-venv install gate PASS). **WS0 + WS1 complete.** Next up: **PREP-2.2**
(PREP-2.1 awaiting approval).

Each task carries: driver, what, acceptance, verify, files, deps, status (`todo`/`in-progress`/`awaiting-approval`/
`done`). Status changes only per the working loop. Verify commands are run and shown at the gate.

### WS0 — Repo hygiene & archive (docs/record only; no code, no wheel impact)

**PREP-0.1 — Create the archive home.**
- Driver: Decision 2; repo navigability for newcomers/agents.
- What: create `docs/archive/` with subfolders `plans/ handoffs/ results/ design/ engine-issues/ misc/` and a
  `docs/archive/README.md` stating this is superseded/historical material kept for the record, not shipped in the
  package, and not a source of truth (point to the current doc set).
- Acceptance: the folder + README exist; README names the current source-of-truth docs.
- Verify: `ls docs/archive` + read README.
- Files: `docs/archive/**` (new).
- Deps: none. Status: **done**.

**PREP-0.2 — Archive delivered plans / handoffs / results / design / engine-issues.**
- Driver: Decision 2.
- What: `git mv` into `docs/archive/`: `demo_plan.md`, `gp1b_docling_graph_plan.md`, `unified_contract_kg_plan.md`,
  `unified_contract_kg_ontology_bridge.md`, `Corpus_Acquisition.md`; `docs/results/*` (3); `docs/plans/async-migration.md`;
  `docs/design/*` (4); `docs/handoff/*` (44, incl. the 3 GraphWright-era ones); `docs/engine-issues/*` (12). Grep the
  surviving current docs + README for links to any moved file and update them.
- Acceptance: all listed files live under `docs/archive/`; no current doc links to a moved path without an updated
  link; `git log --follow` shows history preserved.
- Verify: `rg -n "docs/(results|plans|design|handoff|engine-issues)/" docs README.md` returns only archive-relative
  or updated links; `git status` shows renames.
- Files: the moves above + any link fixes. Deps: PREP-0.1. Status: **done** (70 files; 7 current-doc/source
  links fixed; ADR-body links deferred to PREP-0.4; `tasks.md` internal refs left as historical record).

**PREP-0.3 — Orient the root SPEC/plan/tasks trio (banner, don't move) — AMENDED at gate.**
- Driver: Decision 2; repo navigability — but grounding at the gate showed the root trio is NOT dead legacy.
- Amendment (user-approved 2026-10-05, "banner, don't move"): root `tasks.md` is the still-live authoritative
  ledger, hard-wired into CLAUDE.md's session-start + working-loop + commit/memory rules, and already carries a
  routing banner to the active child ledger; archiving it would break that contract and force a CLAUDE.md rewrite.
  All three are repo-root (never in the wheel). So we **keep the trio in place and banner it** instead of moving it
  — the same "banner not move" treatment as the ADRs.
- What: add an ADR-0052 orientation banner under the title of `SPEC.md` and `plan.md` (engine/product split,
  GraphWright parked, the capability-half/Orchestration-Spec language superseded, where to read current material);
  confirm `tasks.md`'s existing routing banner is adequate and leave it unchanged.
- Acceptance: a reader opening `SPEC.md`/`plan.md` is oriented at the top; no file moved; CLAUDE.md's session-start
  references still resolve unchanged.
- Verify: `head -8 SPEC.md plan.md` shows the banners; `rg -n "tasks.md|plan.md" CLAUDE.md` unchanged & resolving.
- Files: `SPEC.md`, `plan.md` (banner only). Deps: PREP-0.1. Status: **done** (banners added; `tasks.md` left as-is;
  no moves; CLAUDE.md untouched).

**PREP-0.4 — ADR index + legacy status banners (in place).**
- Driver: Decision 2.
- What: write `docs/adr/README.md` indexing all ADRs grouped **Current spine / Parked (GraphWright–RLM–OKF) /
  Superseded**, with the supersession map (0006→0045, 0032→0045, 0036→0091, 0040→0082, 0056→0057, 0087→0088,
  0108→0110, 0048→0114; GraphWright/RLM/OKF set parked by 0052, ARD reframed by 0117/0118). Add a one-line
  `> Status: PARKED by ADR-0052` / `> Status: SUPERSEDED by ADR-XXXX` banner to the top of each legacy ADR that
  lacks one (GraphWright-era: 0003, 0009, 0014, 0015×2, 0016–0024; superseded: 0006, 0032, 0036, 0040, 0056, 0087,
  0108, 0048). Do not move or renumber.
- Acceptance: index lists 100% of ADRs; every legacy ADR has a banner pointing to its successor/park reason;
  no ADR number changed.
- Verify: a quick check script counting `docs/adr/0*.md` vs index rows; `rg -L "^> Status:" docs/adr/00{03,09,14}*.md`
  etc. confirms banners.
- Files: `docs/adr/README.md` (new) + banner lines on 21 ADR files. Deps: none. Status: **done** (index covers all
  120; 21 Status banners added [0087 already self-labeled]; 4 deferred ADR-body link fixes from PREP-0.2 done
  [0021/0033/0057/0110]; RLM set 0009/0014/0016/0019 also PARKED per user direction — revivable later; 0015
  collision left to PREP-0.5).

**PREP-0.5 — Repair the ADR-0015 numbering collision.**
- Driver: baseline cleanup (two files claim 0015; one heading reads "ADR-00XX").
- What: determine which 0015 is canonical; give the other the next free id (e.g. `0120`) with a `> Superseded
  numbering: formerly mis-filed as 0015` note, OR merge if they're the same decision. Fix the "ADR-00XX" heading.
  Update the PREP-0.4 index + any inbound references.
- Acceptance: no two ADRs share a number; no "ADR-00XX" heading remains; references resolve.
- Verify: `ls docs/adr | sort | uniq -d` on the numeric prefix is empty; `rg "ADR-00XX" docs` empty.
- Files: the two 0015 files + index. Deps: PREP-0.4. Status: **done** (stray file renumbered to 0120 via `git mv`;
  heading fixed to `# ADR-0120:` + a numbering note; index updated; no duplicate prefix, no live `ADR-00XX`).

### WS1 — Package for release (ready, not published)

**PREP-1.1 — Make spaCy a runtime asset; remove the `en-core-web-sm` publish blocker.**
- Driver: baseline blocker; Non-goal (make installable). ADR-0121.
- Grounding finding: the spaCy NER path was retired (ADR-0035) — no live `import spacy`/`spacy.load` in `src`;
  `spacy` + the model were a dead, heavyweight chain pulled only by our own declarations. The model
  (`en_core_web_sm`) is not on PyPI, so it can never be a declared dep; only `spacy` (the library) can be an extra.
- What (per user "make it a runtime asset now, keep the seam"): drop `en-core-web-sm` from deps + delete the
  `[tool.uv.sources]` URL; move `spacy` into an optional extra `rag-wright[ner]`; add
  `rag_wright.util.spacy_model.load_spacy_model` (lazy import, honors `RAG_SPACY_MODEL`, one actionable error);
  write ADR-0121 superseding ADR-0012 point 4 + a partial-update note on ADR-0012 + index update. Installation-doc
  step belongs to PREP-2.4.
- Acceptance: built wheel metadata has no direct-URL dep and `spacy` only under `extra == 'ner'`; the loader raises
  an actionable error when spaCy/model absent; suite green.
- Verify (TDD): `tests/util/test_spacy_model.py` (3 tests, hermetic via a fake `spacy`) — red first, then green;
  `uv lock` resolves ("Removed en-core-web-sm"); `uv build` wheel METADATA shows no `@ http` dep, no
  `en-core-web-sm`, `spacy` only under `extra == 'ner'`; full suite 1691 passed / 88 skipped.
- Files: `pyproject.toml`, `uv.lock`, `src/rag_wright/util/spacy_model.py`, `tests/util/test_spacy_model.py`,
  `docs/adr/0121-*.md`, `docs/adr/0012-*.md` (note), `docs/adr/README.md`. Deps: WS0 done. Status:
  **awaiting-approval**.

**PREP-1.2 — `>=`-only pins + add the `langgraph` floor.**
- Driver: user's standing no-`==` rule; baseline gap.
- What: `docling-graph[templategen]==1.9.1` → `>=1.9.1`; `langchain-quickjs==0.3.2` → `>=0.3.2`; add
  `langgraph>=<current-resolved>` floor. `uv lock`.
- Acceptance: no `==` anywhere in `[project.dependencies]`; `langgraph` has a `>=` floor; lock resolves; full
  suite green.
- Verify: `rg "==" pyproject.toml` shows none in the deps table; `uv lock`; `uv run pytest`.
- Files: `pyproject.toml`, `uv.lock`. Deps: PREP-1.1 (same file). Status: **awaiting-approval**
  (docling-graph→`>=1.9.1`, langchain-quickjs→`>=0.3.2` [it is LIVE — the RLM interpreter agent], langgraph→
  `>=1.2.11`; no real `==` pins remain; `uv lock` resolves; suite 1691 passed / 88 skipped).

**PREP-1.3 — Release metadata.**
- Driver: baseline gaps (twine-check readiness).
- What: add `license` (reference the existing MIT `LICENSE`), `authors`/`maintainers`, `[project.urls]`
  (Homepage/Repository/Documentation), and `classifiers` (Python 3.12, MIT, dev status, intended audience, topic).
- Acceptance: `uvx twine check` on the built artifacts passes with no warnings.
- Verify: `uv build && uvx twine check dist/*`.
- Files: `pyproject.toml`. Deps: PREP-1.2. Status: **done** (added `license="MIT"` SPDX + `license-files`,
  `authors`/`maintainers` = Farhan Zaidi <farhan.zaidi@dreamai.io> [user-confirmed], `[project.urls]` →
  github.com/fzaidi-dai/RAG_Wright [user-confirmed canonical], 7 classifiers + keywords; refreshed the stale
  GraphWright `description`. twine check PASSED for wheel + sdist).

**PREP-1.4 — Ship type information (`py.typed`).**
- Driver: baseline gap (consumer type-checkers see nothing today).
- What: add `src/rag_wright/py.typed`; confirm hatchling sweeps it into the wheel.
- Acceptance: the built wheel contains `rag_wright/py.typed`; a `pyright`/`mypy` probe in a clean venv resolves
  `rag_wright.api` types.
- Verify: `uv build` then `unzip -l dist/*.whl | rg py.typed`; a one-line type probe in the clean-venv script
  (PREP-1.6).
- Files: `src/rag_wright/py.typed` (new). Deps: PREP-1.3. Status: **done** (0B marker; wheel contains
  `rag_wright/py.typed`; clean-venv type-checker probe folded into PREP-1.6).

**PREP-1.5 — Smooth the public API seam.**
- Driver: baseline (one inconsistent import path); makes the documented surface "everything is `rag_wright.api`".
- What: re-export `register_capability`, `load_reference_pack` (and `reference_pack`) from `rag_wright.api`, keeping
  the existing `rag_wright.capabilities.manifests` paths working; update `rag_wright.api.__all__`.
- Acceptance: `from rag_wright.api import register_capability, load_reference_pack, reference_pack` works and so do
  the old paths; no circular import.
- Verify (TDD): a test importing both paths and asserting identity (red first); `uv run pytest`.
- Files: `src/rag_wright/api/__init__.py`, `tests/api/test_capability_reexports.py`. Deps: none. Status: **done**
  (re-exported all three from `rag_wright.api`, added to `__all__`; same objects as `capabilities.manifests`
  [identity-asserted], old path intact; import-linter domain-free contract still passes; suite 1694 / 88 skipped).

**PREP-1.6 — Clean-venv install validation (the packaging gate).**
- Driver: "live test at the gate" for packaging; proves the consumer path.
- What: a committed `scripts/verify_wheel_install.sh` that `uv build`s, creates a throwaway venv, installs the
  wheel (no source tree), and asserts: `import rag_wright`; `from rag_wright.api import EngineConfig,
  open_workspace, register_capability, load_reference_pack`; the reference-pack `.ttl` and ≥1 capability `SKILL.md`
  are present in site-packages; `py.typed` present.
- Acceptance: the script exits 0 against the built wheel.
- Verify: `bash scripts/verify_wheel_install.sh` (shown at the gate).
- Files: `scripts/verify_wheel_install.sh` (new). Deps: PREP-1.1–1.5. Status: **done** (PASS: resolved 260 pkgs +
  installed clean from the index, no direct-URL dep, `spacy` absent [extra]; 17 api symbols incl the re-exports;
  py.typed + 2 ttls + 11 SKILL.md shipped; all resolved from site-packages). **WS1 complete.**

### WS2 — Core docs (grounded; runnable quickstart)

**PREP-2.1 — Rewrite `README.md`.**
- Driver: stale README (GraphWright framing) contradicts ADR-0052.
- What: engine/product (open-core) framing; what the engine is; install (uv add + spacy step); a 60-second
  quickstart snippet (mirrors PREP-2.5); links into `docs/`. Remove all "GraphWright compiler"/"Orchestration Spec"
  language.
- Acceptance: no parked-architecture claims; the snippet is copy-run identical to the tested `examples/quickstart.py`.
- Verify: `rg -i "graphwright|orchestration spec|capability half" README.md` empty; snippet diffed against the example.
- Files: `README.md`. Deps: WS1 (install story + api surface final). Status: **done** (rewritten to open-core
  framing; no parked claims; layout matches the real tree; grounded `rag_wright.api` quickstart teaser [reconciled
  against the runnable example in PREP-2.5]; docs-set links are forward-refs that go live across WS2).

**PREP-2.2 — `docs/concepts.md`.**
- What: the mental model — workspace & `open_workspace`, capabilities & ARD (the kinds, invocable vs composed),
  the store seam, the model-profile seam, ontology-as-knowledge (ADR-0066), the KG (two graphs), the reference pack.
  Distilled from `contract_pipeline_explainer.md` + `capability_profiles.md` + `ARCHITECTURE_OVERVIEW.md`; grounded
  against code.
- Acceptance: every named symbol/kind exists in the code graph; no parked concepts (RLM-as-compiler, OKF, graph
  brief).
- Verify: Graphify spot-checks of each symbol named; `rg -i "graph brief|okf|dagster" docs/concepts.md` reviewed.
- Files: `docs/concepts.md`. Deps: WS1. Status: todo.

**PREP-2.3 — `docs/architecture.md` (the final architecture doc).**
- What: engine/product boundary (ADR-0052), the layer map (api / capabilities / subgraphs / models / ontology /
  store / spans / …), ingest and query data flow, the import-linter domain-free rule, the two seams. From
  `ARCHITECTURE_OVERVIEW.md` + engine-platform SPEC.
- Acceptance: the boundary + import-linter rule stated correctly; diagram or layer table matches `src/rag_wright/`.
- Verify: layer list diffed against `ls src/rag_wright`.
- Files: `docs/architecture.md`. Deps: PREP-2.2. Status: todo.

**PREP-2.4 — `docs/installation.md` + `docs/configuration.md`.**
- What: install (`uv add rag-wright` or path/git; the spacy-model step from PREP-1.1); ArcadeDB Docker bring-up +
  JVM heap note; `EngineConfig`/`StoreConfig`/`EngineOptions`/`IngestOptions` with every field; env vars + model
  profiles (OpenRouter default / self-hosted vLLM) and the model-profile seam.
- Acceptance: a reader can go from zero to a configured `open_workspace` call; all config fields match the frozen
  dataclasses.
- Verify: field lists diffed against `api/config.py`; the ArcadeDB steps match `docs/ArcadeDB_Local.md`.
- Files: `docs/installation.md`, `docs/configuration.md`. Deps: WS1. Status: todo.

**PREP-2.5 — `docs/quickstart.md` + a runnable example (the docs gate).**
- Driver: "docs must be executed".
- What: `examples/quickstart.py` — `open_workspace` → `load_reference_pack()` → `source_document`/`parse_document`
  on a small bundled sample → ingest via `ainvoke_subgraph("contract_ingestion_pipeline", …)` → a query via
  `ainvoke_subgraph("intra_document_qa"/"relational_qa", …)`, end to end against a live ArcadeDB; `docs/quickstart.md`
  walks through it.
- Acceptance: `uv run python examples/quickstart.py` completes green against a real store and prints a cited answer;
  README + quickstart snippets are generated from / verified against this file.
- Verify: run the example (shown at the gate); diff README/quickstart snippets against it.
- Files: `examples/quickstart.py`, `docs/quickstart.md`, a small sample fixture (non-restricted). Deps: WS1,
  PREP-2.4. Status: todo.

**PREP-2.6 — `docs/reference-pack.md`.**
- What: what the contract/compliance worked example is; `load_reference_pack()`; the 36 caps (9 invocable-by-name);
  how to read it as a template; and that restrictively-licensed corpora (CUAD/ACORD) are NOT shipped (separate
  acquisition).
- Acceptance: cap counts/kinds match `manifests.py`; the license caveat is explicit.
- Verify: counts diffed against `_SPECS`.
- Files: `docs/reference-pack.md`. Deps: PREP-2.2. Status: todo.

### WS3 — API reference (generated, drift-guarded)

**PREP-3.1 — Stand up the generator.**
- Driver: Decision 3 (generated, not hand-copied → can't drift).
- What: a `scripts/build_api_docs.sh` using `pdoc` over `rag_wright.api` + the runtime surface
  (`capabilities.manifests` public names), emitting into `docs/api/` (markdown/html); add a CI-style freshness check
  (regenerate → diff → fail on delta), mirroring the ontology-overlay zero-drift rule.
- Acceptance: `docs/api/` is generated from live symbols; the freshness check fails on a stale commit.
- Verify: `bash scripts/build_api_docs.sh && git diff --exit-code docs/api`.
- Files: `scripts/build_api_docs.sh`, `docs/api/**` (generated), a CI note. Deps: WS1 (final surface). Status: todo.

**PREP-3.2 — Docstring pass on the public surface.**
- What: ensure every public symbol (the 22 api + `register_capability`/`load_reference_pack`/`reference_pack`) and
  each public module has a docstring with params/returns; fix any pdoc "missing docs" on public names.
- Acceptance: pdoc emits no missing-doc warnings for public names.
- Verify: re-run PREP-3.1; inspect warnings.
- Files: docstrings across `src/rag_wright/api/*` (+ the two manifest symbols). Deps: PREP-3.1. Status: todo.

> Extension (later gate): wrap `docs/` in mkdocs-material for a hosted site. Out of scope here.

### WS4 — Domain-adaptation guide + companions (lean main doc, detailed companions)

**PREP-4.1 — Promote the builder's guide.**
- What: `docs/domain-adaptation/README.md` — the lean guide built on the 9-step `new-domain-build-sequence.md`
  (which keeps the eval-first step added earlier), each step a short paragraph linking to its companion; move
  `new-domain-build-sequence.md` content in and leave a pointer.
- Acceptance: the main guide is short (fits on a screen or two) and links to every companion below.
- Verify: read-through; link check.
- Files: `docs/domain-adaptation/README.md`, pointer at old path. Deps: WS2. Status: todo.

**PREP-4.2 — `ontology-authoring.md`.**
- What: authoring a domain `.ttl` pack (closed value sets, schema/classes/properties/edge types, SHACL constraints,
  mappings/synonyms) per ADR-0066; how `EngineConfig(pack=…)` loads it; how to validate; the reference packs as
  examples. Cross-link the ontology loaders.
- Acceptance: a new-domain author can write + load + validate a pack; grounded against `ontology/loader.py`.
- Verify: Graphify checks of the loader functions named. Files: `docs/domain-adaptation/ontology-authoring.md`.
  Deps: PREP-4.1. Status: todo.

**PREP-4.3 — `kg-construction.md`.**
- What: how ingestion builds the KG (parse → chunk → segment → extract → graph), the two graphs (entity KG vs
  property graph), content-hash gating, provenance/confidence (`EXTRACTED`/`INFERRED`/`AMBIGUOUS`), the `chunk_id`/
  `entity_id` schemes (ask-first to change).
- Acceptance: matches the ingestion subgraphs + identifier rules.
- Verify: cross-check against `subgraphs/` + `contracts/`. Files: `docs/domain-adaptation/kg-construction.md`.
  Deps: PREP-4.1. Status: todo.

**PREP-4.4 — `entity-resolution.md` (and surface the gap).**
- Driver: ER/disambiguation are canonical-but-reserved slugs (ADR-0004/0013) with no clear new-domain story today.
- What: document ER & disambiguation as they work now (the matching strategy, `entity_id` canonicalization,
  `entities_by_name`), how a new domain configures/extends it, and **explicitly flag** where the seam is
  under-exposed for a new domain. Any genuine engine gap is logged in PREP-4.7, not hand-waved.
- Acceptance: an honest account of current ER + a named list of what a new domain cannot yet do cleanly.
- Verify: grounded against `ontology/` ER code + `api/kg.py`. Files: `docs/domain-adaptation/entity-resolution.md`.
  Deps: PREP-4.1. Status: todo.

**PREP-4.5 — `authoring-capabilities.md`.**
- What: building & registering domain capabilities — the kinds; `impl_ref` invocable-by-name (the 9-of-36 pattern)
  vs composed-by-direct-import (the 27); `CapabilityManifest` fields; `CANONICAL_CAPABILITY_SLUGS`;
  `register_capability`/`load_reference_pack`; ARD discovery + MCP exposure. Cross-link the `authoring-a-capability`
  skill.
- Acceptance: a developer can register an invocable cap with zero engine edits following this doc.
- Verify: grounded against `capabilities/*` + the conformance guardrail test.
  Files: `docs/domain-adaptation/authoring-capabilities.md`. Deps: PREP-4.1. Status: todo.

**PREP-4.6 — `classification-and-decision-models.md`.**
- What: identifying classifier/routing opportunities (the `classifier-opportunity-analysis` recipe), SetFit vs
  System-1 decision models (Jev/Laya), the `DecisionModelProfile`, and the eval-first discipline (`creating-evals`).
  Cross-link the `setfit`/`laya`/`creating-evals` skills.
- Acceptance: a developer can decide rule vs classifier vs decision-model vs LLM and knows which skill to open.
- Verify: cross-links resolve; grounded against `models/profiles.py` + `capabilities/jev_decision.py`.
  Files: `docs/domain-adaptation/classification-and-decision-models.md`. Deps: PREP-4.1. Status: todo.

**PREP-4.7 — Engine-gaps register.**
- What: `docs/domain-adaptation/_engine-gaps.md` — every under-exposed seam the guide work surfaced (ER exposure,
  the api re-export [resolved in 1.5], the spacy-model requirement, the ArcadeDB prerequisite, any
  domain-assumption leak), each with a proposed resolution; cross-post the real engine items into the
  engine-platform `TASKS.md` as follow-ups.
- Acceptance: a concrete, de-duplicated list; the genuine engine items appear in the child ledger.
- Verify: read-through; link into `docs/specs/engine-platform/TASKS.md`.
  Files: the register + a child-ledger append. Deps: PREP-4.2–4.6. Status: todo.

### WS5 — Consumer onboarding (skill + product-starter templates)

**PREP-5.1 — The `using-the-rag-wright-engine` coding-agent skill.**
- Driver: Decision 4; lets a coding agent in a consumer repo adopt the engine effectively.
- What: `.claude/skills/using-the-rag-wright-engine/SKILL.md` — install the engine (uv add / path / git); build the
  Graphify grounding lanes over the installed package (the `engine` lane) + the product's own `project` lane, and
  the grounding discipline (code graph = authority; this doc set + docs-MCP = concepts); then follow the
  domain-adaptation guide: configure `EngineConfig`, author/ingest the `.ttl`, run ER, register capabilities, build
  the product seam. Cross-link every doc in WS2–WS4.
- Acceptance: the skill names the exact commands, the exact public imports, and the exact doc paths; reviewed
  against the real API; no parked concepts.
- Verify: Graphify/code cross-check of every symbol + command; link check. Files:
  `.claude/skills/using-the-rag-wright-engine/SKILL.md` (new). Deps: WS1, WS4. Status: todo.

**PREP-5.2 — `CLAUDE.md` product-starter template.**
- Driver: Decision 4; RuleWright's `CLAUDE.md` already exposes the reuse seam.
- What: `docs/templates/product-starter/CLAUDE.md.template` — the generic core verbatim (boundary, grounding
  discipline, working loop, code rules, background-work rule, memory, version control, boundaries, the
  no-pause/no-`==` rules) with `{{PLACEHOLDERS}}` for the specifics the survey enumerated: product name, domain,
  engine package+path, stack slots, grounding-lane paths, docs-source table, requirement/journey scheme, ADR refs,
  project-scoped skill name. A leading "fill these first" block.
- Acceptance: every product-specific token is a placeholder; the generic rules are intact and engine-agnostic.
- Verify: `rg "RuleWright|contract|compliance" docs/templates/product-starter/CLAUDE.md.template` returns only
  placeholder examples. Files: the template. Deps: PREP-5.1. Status: todo.

**PREP-5.3 — Playbook product-starter template.**
- What: `docs/templates/product-starter/playbook.md.template` — same treatment of RuleWright's `docs/playbook.md`;
  include the Phase-0 setup that pulls the Addy Osmani skills + the `using-the-rag-wright-engine` skill and builds
  the Graphify lanes; placeholders as in 5.2.
- Acceptance: generic phases/loop intact; product tokens placeholdered; setup references the onboarding skill.
- Verify: placeholder scan as in 5.2. Files: the template. Deps: PREP-5.2. Status: todo.

**PREP-5.4 — Starter README + dry-run instantiation.**
- What: `docs/templates/product-starter/README.md` explaining how to copy the starter into a new product repo, fill
  the placeholders, and run setup. Then a dry run: instantiate into a scratch dir with TexWright values and confirm
  no `{{PLACEHOLDER}}` remains and references resolve.
- Acceptance: the dry-run scratch copy has zero unfilled placeholders and resolvable links.
- Verify: `rg "{{" <scratch>` empty; link check. Files: the starter README (+ a throwaway scratch instantiation, not
  committed). Deps: PREP-5.2, PREP-5.3. Status: todo.

---

## Sequence & dependencies

```
WS0 (0.1→0.2→0.3, 0.4→0.5)            clean the repo first
  └─► WS1 (1.1→1.2→1.3→1.4; 1.5 ∥; 1.6 gate)   package ready + validated
        ├─► WS2 (2.1,2.4 need WS1; 2.2→2.3,2.6; 2.5 needs 2.4)   core docs
        │     └─► WS3 (3.1→3.2)                 generated API ref
        └─► WS4 (4.1→{4.2..4.6}→4.7)            domain-adaptation guide
              └─► WS5 (5.1 needs WS1+WS4; 5.2→5.3→5.4)   onboarding + templates
```

Recommended order of gates: **WS0 → WS1 → WS2 → WS4 → WS3 → WS5**. WS3 (generated API ref) can slot in any time
after WS1's surface is final; WS4 can proceed in parallel with WS2 once concepts (2.2) exists.

## Later gates (explicitly deferred)

- Public PyPI `twine upload` (your approval).
- mkdocs-material hosted site (WS3 extension).
- The **TexWright** build engagement (new repo; fill the product-starter; spec-driven domain build).

## How to run this plan

Standard working loop, one task at a time, stop for approval, commit atomically per approved task (code + this
ledger's status update + any ADR + docs together). Code-touching tasks (WS1, the WS2 example, WS3 scripts, WS5
skill) ground library calls against the `framework` graph first and go contract/TDD. Doc tasks are grounded against
the code graph and, where runnable, executed. This file is the ledger — update task status only as the loop allows.
