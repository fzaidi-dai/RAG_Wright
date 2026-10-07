---
name: setfit
description: >-
  Recipe for replacing an LLM-based classification/decision step with a small trained SetFit classifier over
  short text (spans, clauses, sentences, snippets). Use it when framing a decision as classification, or when
  building/improving a few-shot-to-full-data text classifier: problem framing, data & evaluation design,
  backbone selection, soft-tagging/top-k, ensembling, rare-class data curation (including sourcing examples
  online), calibration, and checkpointing. ALSO covers when NOT to train -- Phase 0.5: A/B a zero/few-shot
  System-1 decision model (Jev / Laya) first, which often wins on scarce or label-ambiguous data (the hard-won
  CIC-1c lesson). It exists so the NEXT classifier converges faster and re-uses hard-won lessons instead of
  re-discovering them.
---

# SetFit classifier recipe

A distilled, problem-agnostic playbook for turning "an LLM decides X from short text" into a small trained
classifier that is faster and cheaper at equal-or-better accuracy. Follow the phases in order; each phase's
output feeds the next. **Every number you see about outcomes is illustrative of a DIRECTION, never a target —
your label set, data volume, and confusable structure differ, so re-measure everything on your own problem.**

## When to use this skill
- You are replacing an LLM call that maps short text to a label (a tag, a route, a type, a yes/no) — a common
  latency/cost sink that multiplies over bulk inputs.
- You are building or improving any short-text classifier and want a reliable path to good results.
- Trigger it BEFORE writing training code, so the data/eval design is right from the start.

---

## Phase 0 — Frame it as classification (and pick the shape)
- **Is it classification?** If the decision is "short text -> one of a fixed set of labels," yes. A small trained
  model can then replace the LLM call.
- **Single-label vs multi-label.** Decide up front; they need different data shapes and metrics.
- **Soft-tag vs hard decision.** A *soft* tag (several labels allowed, used as guidance, never a hard gate) is far
  more forgiving than a *hard* decision (one answer that gets acted on). If the downstream only needs hints/augmentation, choose soft-tagging — it turns many "errors" into acceptable extra tags.
- **Define the label set explicitly**, and include an `OTHER`/`NONE` escape so out-of-taxonomy inputs don't get
  force-fit to a wrong class.

## Phase 0.5 — Before you train: A/B a System-1 decision model (Jev / Laya)

Once the task is framed as a closed-set decision (Phase 0), a trained classifier is NOT the only option — and
often not the best. A **System-1 decision model** returns a CALIBRATED typed answer (yes/no, choice, score) with
**no training, no text, in ~70–500 ms**. Evaluate one *before* committing to training; it frequently wins when:
- **data is scarce or label-ambiguous** (training plateaus; the decision model is zero/few-shot). Measured
  (RAG_Wright CIC-1c, ADR-0119): a trained SetFit operative-rule classifier capped at ~0.82 on a 0.905-agreement
  gold (confident, unroutable errors; Laya no better; no public labeled negatives), while **Jev zero-shot hit 0.92**
  (the label ceiling), with claim_types micro-F1 0.85 / actor 0.90 few-shot;
- **you need honest uncertainty to route/gate** — a decision model's probabilities are calibrated; a SetFit
  logistic head is overconfident, so a confidence-routed hybrid couldn't be rescued (ADR-0119);
- **low-to-moderate volume** where there's no reason to host a local model (~$0.00002/call for a managed one).

Newer measurements (ingestion, ADR-0122 / ADR-0040 ING-9/9b): the provision-boundary residue on Jev 460/461
(99.8%) with zero flips across 3 calls; the extraction judge on Jev 95.3% vs the LLM judge's 90.1% (192 blind
hand-labelled cases); residual values (deterministic candidates + a Jev role choice) value-level recall 0.83 /
precision 0.88 vs the LLM's 0.60 / 0.79. Live, one contract's 131 provisions took 215 Jev calls and 0 LLM calls
for $0.013 (the LLM path: 277 calls, $0.277).

**Unstable answers mean an ambiguous question.** Jev flipped 5/181 answers across identical calls, all at scores
0.47-0.56; temperature/seed did not help and majority voting barely helped. Rewriting the question did: state the
rubric ONCE in the `state`, phrase each criterion structurally (what the line IS, not what it might mean), and
pass the lines to decide without their surrounding text. Measure repeatability (the `creating-evals` skill).

**Prefer a trained classifier** when: high volume where per-call API cost compounds AND you have a large labeled
corpus (the contract Step-3a fleet, ADR-0115/0116); on-prem-only with no managed API (then fine-tune **Laya**, the
open decision model — the `laya` skill); or the decision needs features a prompt can't express.

Two decision models:
- **Jev** (TypeSafe, managed) — the **OpenRouter Decisions API** (`typesafe/jev-1.13`, POST `/api/alpha/decisions`,
  NOT chat completions). `questions` keyed by id: `noul` (yes/no → P(yes)), `choice` (options + probabilities),
  `score`. **Multi-label = several `noul` in one call.** **Few-shot = labeled exemplars in the `state`** (helps
  choice/multi-label; can BIAS a binary gate — zero-shot the gate). Tune the `noul` threshold for precision/recall
  (and note the threshold is a config knob, not a precision fix for confident errors).
- **Laya** (Convai, open, Apache-2.0, ModernBERT-large + RL head) — on-prem; near-random zero-shot so **fine-tune
  it** (the `laya` skill). The open fallback when a managed API is unacceptable.

**Wire it as a capability, don't hand-call it** (route-production-through-the-capability-layer): register a
`kind="model"` capability with an `impl_ref` client (RAG_Wright ships `jev_decision`, an ASYNC model cap invoked via
`ainvoke_model`) and keep the model id / endpoint / thresholds in a decision-model **profile**
(`DecisionModelProfile`), never hardcoded — so swapping Jev ↔ Laya ↔ a future open-weight alternative is config.
See `authoring-a-capability` + ADR-0119. The decision knowledge (the questions / criteria) belongs in the ontology
(`.ttl`), not the capability (ADR-0066).

**Rule of thumb: for a closed-set decision, A/B a decision model (Jev zero/few-shot) against a trained classifier
on your REAL gold, and select on accuracy + calibration + cost — do not assume training is the answer.**

## Phase 1 — Data & evaluation design (do this FIRST; it is the foundation)
- **Judge by the per-class FLOOR, not overall accuracy.** A high average hides dead classes. Set an explicit
  per-class bar (e.g., "every class must exceed threshold T") and hold *every* class to it.
- **Build a SYMMETRIC eval:** equal test (and val) count per class, so per-class numbers are comparable and not
  dominated by big classes. Small per-class n is quantized and noisy — where possible use more; otherwise report
  both a strict small-n view and a steadier pooled view, and treat single-class swings as noise.
- **Prevent leakage:** split by the natural GROUPING unit (document / source / record), not by row, so
  near-duplicate rows from one source can't straddle train and test. When a class is concentrated in a few
  sources, strict disjointness may be impossible — detect and flag those cases rather than pretend.
- **"k-shot" means k examples PER class**, not k total.
- **For a soft-tagger, the per-class metric is TOP-K RECALL** (is the true label among the top-k emitted?), and you
  always report it against **average tags-per-span** (the precision cost). Recall and tag-budget trade off.

## Phase 2 — Baseline SetFit (know the two phases)
- SetFit = a Sentence-Transformer **body** + a small **head**, trained in two phases: (1) contrastively fine-tune
  the body on labeled pairs; (2) fit the head on the frozen body's embeddings.
- **Keep the DEFAULT head** (a logistic-regression head) unless you have a specific, tested reason to change it —
  it is robust and strong for this task.
- **`num_iterations` sets the number of contrastive PAIRS (roughly 2 x num_iterations x dataset_size).** Scale it
  DOWN as the dataset grows: tiny few-shot sets want a high value; a large set wants a very low one, or training
  explodes and over-trains. Keep total pairs/steps roughly matched across regimes you want to compare fairly.
- **Always stream X/N progress + loss + ETA and actively monitor** any run over ~30s; a run whose only output is
  at the end is unmonitorable.
- **Cap the body's max sequence length** for short inputs (faster, avoids out-of-memory); some backbones default high.

## Phase 3 — Improve, by return on investment
Apply in this order; stop when the per-class bar is met.

1. **More/better DATA for weak classes — usually the biggest lever — but CAP it.** Heavy class imbalance makes the
   contrastive sampler favour big classes and the rare tail can collapse toward zero. **Cap per class at
   `min(available, N)`**: abundant classes get plenty, imbalance stays modest (a few-to-one), the tail survives.
   This beats both strict balance (too little data for hard classes) and full imbalance (tail collapse).
2. **BACKBONE choice — try 2–3; they are COMPLEMENTARY** (they fail on *different* classes). A domain-matched
   encoder and a strong general sentence-embedding encoder often trade wins; encoders pre-trained for similarity
   start stronger than raw masked-LM backbones (which SetFit must first teach to embed). No single backbone
   dominates — which is exactly why an ensemble helps.
3. **SOFT-TAG / TOP-K instead of forcing one label.** For genuinely confusable classes the true label is often the
   2nd or 3rd candidate, not the 1st. Emitting top-k (or all above a threshold) lifts per-class recall over the bar
   where single-label never can. This is the natural frame for tagging/augmenting steps.
4. **ENSEMBLE the backbones — but only when they're COMPLEMENTARY.** Run all, combine their tags. Union-of-top-k
   maximizes recall (more tags/span); averaging probabilities then taking top-k gives a fixed tag budget. Mind the
   **recall vs tags-per-span tradeoff**: pick the SMALLEST tag budget that clears the bar. **Caveat (measured): the
   ensemble only helps when the backbones fail on DIFFERENT classes** (as in a large many-class soft-tagger). On a
   SMALL single-label task where one domain-matched backbone simply DOMINATES (the others are weaker, not
   complementary), averaging regresses toward the weaker consensus and *lowers* the strong model — verified on a
   3-class distilled property-dimension (LegalBERT 0.84 alone vs 0.80 ensembled). So: **for small single-label
   classifiers, pick the best single backbone; reserve ensembling for many-class / multi-tag problems** — and
   always A/B the ensemble against the best single model rather than assuming it wins.
5. **CALIBRATION** (temperature or Platt scaling, fit on a held-out val set) makes probabilities honest so a
   chosen threshold has predictable precision. **It is monotonic** — it cannot separate a confidently-wrong
   prediction from a confidently-right one. Use it to pick a principled threshold, not to fix separability.

## Phase 4 — Rare / failing classes: data curation & sourcing
- **Silver bootstrapping.** When a class lacks labeled data, MINE candidate examples from unlabeled or
  negative-bucket text with keyword/pattern search; enforce **per-source diversity** (cap candidates per source so
  you don't collect near-duplicates); then **curate** with a higher-capability model, rejecting definitions,
  headings, cross-references, **negations** ("no X", "nothing grants X"), and off-type false positives. Mark
  silver so it is never conflated with gold, and report silver-vs-gold separately.
- **Reject-rules scale better than hand-picking** once there are many candidates: encode the false-positive
  patterns, keep the rest, print and spot-check.
- **When the corpus is exhausted for a rare class, source examples online** (public example/clause/snippet
  libraries, documentation). Add them to **TRAIN ONLY**; keep val/test as in-corpus examples so it stays an honest
  transfer test. **Caveat:** external examples are often stylistically cleaner (template-like) than embedded
  in-corpus ones, so transfer can be partial — measure whether it actually moves the class.
- **Watch for TAXONOMY OVERLAP.** A class that resists every lever may be a SUBTYPE of another class. The model
  "failing" it by predicting the parent is frequently CORRECT for a soft-tagger (emit both). Recognize this before
  chasing more data — it is a labeling-taxonomy question, not a model deficiency.

## Phase 5 — Operationalize: checkpointing & serving (non-negotiable)
- **Never overwrite a good model.** Version every run: keep a **registry** (run id, config, a DATA FINGERPRINT,
  and metrics) and **snapshot** good weights to a named checkpoint (a server-side copy, no re-download).
  **Adopt-only-if-better** against the registry. Load by checkpoint to score — **load, don't retrain.**
- Retraining IS required when the DATA changes (contrastive pairs span all classes, so new examples must
  re-enter the body) — but **snapshot the prior best first.**
- **Serving:** these models are small and CPU-capable (milliseconds per inference). Prefer in-process/local for
  throughput (no network hop) or a shared endpoint, and slot the classifier **behind the existing implementation
  seam/adapter** so nothing upstream (contracts, APIs) changes.
- **Register it as a capability — do not stop at a loose seam.** A trained classifier becomes a `kind="model"`
  capability: an `impl_ref` factory `def <slug>(resources, inputs)` over the cached checkpoint, registered in ARD
  and invoked BY NAME through the engine (`invoke_model` / `ainvoke_model`; from an ingestion hook,
  `ainvoke_model(slug, inputs, resources=ws)`) — routed THROUGH the capability layer, never hand-constructed around it. The full registration + invocation
  contract (the DoD) is the **`authoring-a-capability`** skill; follow its `model` section. (Upstream, the
  **`classifier-opportunity-analysis`** skill is where you decide a classifier belongs here at all.)
- **Train/serve version parity — the single biggest operational trap (verified this session).** A model saved by a
  NEWER `sentence-transformers` (or `transformers`) can FAIL TO LOAD under an older one — module paths move between
  majors (e.g. the pooling/normalize modules relocated in ST 6.x), so the serving env throws `ModuleNotFoundError`
  on load. Before training, decide the SERVING env's versions and EITHER (a) pin the training image to the same
  `sentence-transformers` / `transformers` majors, OR (b) plan to upgrade the serving env — and then
  **regression-test the serving env's OTHER consumers** (a shared embedder/reranker that also pulls these libs) for
  *silent embedding drift* (embed a fixed input before/after and diff the vectors; it should be ~0). Record the
  training-time library versions next to the checkpoint. Expect CASCADES: bumping `sentence-transformers` can force
  a `transformers` major bump, which touches every transformer-based capability — bump the whole cascade to
  compatible versions and run the full suite; don't patch model files to dodge it.
- **Serve WITHOUT the training framework.** A default-head SetFit model on disk is just a `SentenceTransformer` body
  (`model.safetensors` + configs) plus a joblib-pickled sklearn head (`model_head.pkl`). Run inference with only
  `sentence-transformers` + `scikit-learn` + `joblib` — `body.encode(...)` then `head.predict_proba(...)`, mapping
  `head.classes_` columns to labels — so the serving env needs no `setfit` dependency. Honor the body's configured
  normalization (check `config_setfit.json` / the module list). **Fidelity-gate it:** re-score the held-out set in
  the SERVING env and confirm it reproduces the training-env numbers exactly before trusting the loader.

---

## Anti-patterns — measured dead ends, do not repeat
- **Judging by overall accuracy** instead of the per-class floor — hides dead classes.
- **Using the differentiable (neural) head to "fix calibration"** — its softmax saturates and becomes *more*
  overconfident on errors, with no accuracy gain. Keep the default head; calibrate separately if needed.
- **A router/cascade** (route each input to a per-group specialist) — a two-stage cascade MULTIPLIES errors
  (router accuracy × specialist accuracy), so the "oracle" gain from splitting evaporates once the router is
  imperfect. A flat classifier plus multi-tag beat it. It only wins if groups are cleanly separable AND both
  stages are near-perfect.
- **Grouping classes by accuracy rank** for a router — this scatters mutually-confusable classes across groups and
  forces the router to make the hardest distinctions. If you must group, group by CONFUSION CLUSTERS (confusables
  together) — but the cascade caveat above still applies.
- **Naive all-available training under full imbalance** — crushes the rare tail; cap per class instead.
- **Retraining from scratch and overwriting the prior good model** — snapshot first, always.
- **Trusting a raw confidence threshold as a clean router replacement** — confidently-wrong predictions exist, so
  a threshold is a precision/recall knob, not a clean gate.
- **Spawn-and-return fan-out + filename-based "done" checks** — the launcher returns, the app dies, nothing trains,
  and a poll on same-named result files fires green on the STALE prior run. You then "measure" a model that was
  never built. Block on `.get()`, stamp+verify `data_sha`, use a manifest (see Operational notes). Also: more data
  does NOT fix a *confusable* value (its sibling is embedded too close) — that needs top-k or a better backbone;
  silver only fixes a value that is *absent/starved from TRAIN* (top-k=0 even at max k because the class isn't in
  the head). Diagnose which failure you have before spending compute.

## Run preflight & monitoring — do this BEFORE any labeling/training run (non-negotiable)
Read this section FIRST when you return to this skill after a gap. These are the mistakes re-learned 2026-10-04
after the 21-classifier runs — each one cost real time and each is avoidable with a 30-second preflight. Do them in
order before you fan out any bulk job.

1. **Resolve the model from the engine, never hardcode a model id.** A hardcoded id rots: `ibm-granite/granite-4.1-8b`
   now 404s on OpenRouter ("No endpoints found"). Use the engine resolver (`models.profiles.model_for(ModelRole.<ROLE>)`,
   or `ws.model_id(role)` on a workspace; the model-profile seam) so the id and backend come from config, and **confirm it with a one-call smoke before any
   bulk run**. Bulk teacher labeling runs on **self-hosted Qwen on Modal** (`qwen-vllm-modal` skill + ADR-0110), NOT
   OpenRouter (flat-GPU = the warn-before-bulk rule); reasoning ON for label fidelity; client concurrency ≤16; raise
   the structured timeout for reasoning-ON bulk; `modal app stop` when done.
2. **Load `.env` by EXPLICIT path in any script run from outside the repo root** (a scratchpad / one-off script).
   `load_dotenv()` with no path searches the CALLING SCRIPT's directory, so a script in `/tmp/...` silently misses
   the project `.env` → `KeyError` or wrong creds. Pass the repo `.env` path (`load_dotenv("/abs/repo/.env")`) or
   export the vars in the run command. Do NOT assume the shell already has them — some keys are shell-exported,
   others live only in `.env`, so one working (e.g. `OPENROUTER_API_KEY`) does not mean all do (`VLLM_BASE_URL`).
3. **Smoke ONE item end-to-end before the bulk fan-out** (early error catching). One real call through the EXACT
   path the bulk will use; assert the parsed label/shape; only then fan out. This catches a broken prompt, a wrong
   served-model-name, a dead endpoint, or a schema mismatch for the cost of one call, not a whole run.
4. **X/N progress is mandatory and so is active monitoring.** Every bulk/label/train run writes a start line with
   the total N, then `[stage] i/N <what>` (FLUSHED) per item or small batch, then an end summary — to a LOG FILE,
   and you tail the file / Monitor-loop it (never pipe a live run through `grep`/`tail`; never a run whose only
   output is at the end — it is unmonitorable). Report progress back in X/N form. This is how errors are caught
   early instead of after an hour.
5. **Checkpoint + registry + adopt-only-if-better** (ADR-0030, and Phase 5 below): snapshot before retrain, version
   every run with a data fingerprint, load-don't-retrain. **Read metrics from the result artifact (JSON), not
   stdout.**

## Operational notes — avoid the friction we already hit (remote training/eval)
Concrete tool gotchas from a real run; following them saves real time.
- **Downloading a whole model DIRECTORY from a cloud volume expects the destination PARENT** — the tool recreates
  the source directory name inside it. Passing an explicit new destination path errors `Is a directory` (Errno 21).
  Download into the parent (or file-by-file). (Single files download fine to an explicit path.)
- **A fan-out launcher MUST BLOCK on every spawned handle (`.get()`), or it silently trains NOTHING.** This is the
  single most expensive trap and it cost a full session. On Modal (and any ephemeral-app runner), a local entrypoint
  that calls `.spawn()` for each task and then RETURNS lets the app tear down the instant the entrypoint exits — the
  spawned jobs are cancelled and NO training happens, with no error. `--detach` alone does NOT save you (Modal's own
  warning: detached mode "only keeps the LAST triggered function alive"). The fix: collect the handles and
  `for h in handles: h.get()` so the launcher blocks until all finish — this keeps the app alive AND surfaces any
  per-task exception instead of losing it. Run the launcher itself under `nohup … &` (so it survives your turns) and
  add `--detach` as belt-and-suspenders. Stream `X/N` per-task completion from the launcher and step `X/N`+loss to a
  per-run progress log on the volume. A run whose app shows `stopped / 0 tasks` seconds after launch did NOT train —
  confirm via the platform task count and the registry, not by assuming.
- **Mind the ACCOUNT CONTAINER CAP when sizing the fan-out.** A workspace has a max concurrent-container count (10 on
  the fzaidi2014 account). Spawning more than that does NOT run them all at once — the excess QUEUES and runs in waves
  (correct + no double-spend, but ~N/cap× the wall-clock, and only `cap` show as running). Size the wave to ≤ the cap
  (chunk spawns + gather between waves) or report the wave count honestly. Distinct from any in-container concurrency.
- **When multiple entrypoints exist, name the one to run** (`script.py::entrypoint`) and use the FULL script path;
  a bare filename or an ambiguous target fails.
- **Read metrics from the RESULT ARTIFACT (JSON), never by scraping stdout.** Scraping a printed table with
  sed/grep truncates (it stops at the first matching token) and silently drops rows. Write metrics to JSON and load
  them.
- **Never infer a file path or a model's identity from a DISPLAY NAME via a substring check.** A case-sensitivity
  slip (e.g. lowercase `"legal"` not matching a capitalized display name) silently loads the WRONG file and yields
  plausible-but-wrong numbers — *two columns coming out identical is the tell*. Map names to paths explicitly.
- **Completion detection by FILENAME EXISTENCE is a false-positive trap — verify FRESHNESS, not presence.** If runs
  write same-named artifacts (`results/<tag>.json`, `models/<tag>`), a PRIOR run's file reads as "done" and you
  measure the STALE model while the new one never trained. This actually happened: a poll on filename existence
  fired "results ready" on pre-session files; the reported floors were the old model's. Defenses, all three: (1)
  **stamp identity INTO every artifact** — `run_id` (timestamp), a `data_sha` fingerprint of the exact TRAIN rows,
  and the run `label` — and (2) **VERIFY** a read-back result's `data_sha` equals the fingerprint of the data you
  just uploaded before trusting its metrics; (3) the launcher's block-on-`.get()` return value IS the freshly
  trained result — prefer it over re-reading files. The tell that you're on a stale model: numbers that match a
  previous run to the decimal, or `train_examples` that doesn't match your current prep.
- **A low/zero yield from an LLM-labeling step is an INFRA failure until proven otherwise — never read it as "rare".**
  Teacher/curation calls fail silently in bulk: out of API credits (HTTP 402), rate limits, provider down. If the
  loop swallows the exception and counts the item as "no label," a dim where 100% of calls errored writes `0 kept`
  that looks identical to "this value is genuinely rare." This happened: an entire mining wave ran ~86–100% failed
  (account out of OpenRouter credits) and the 0-yields were nearly reported as "corpus-exhausted." Defenses: (1) the
  labeling loop MUST COUNT failures and ABORT (refuse to write, don't overwrite prior data) above a small
  fail-rate (~15%); (2) when you MONITOR the job, the grep MUST INCLUDE the failure signatures (`402|RateLimit|
  APIError|Traceback`) — filtering errors out "to reduce noise" is how you miss a total outage and mis-conclude;
  (3) before trusting any yield, check the success/error counts, exactly as for eval timeouts. Silence is not rarity.
- **Version every run with a LABEL (+ data_sha), and write a per-run MANIFEST.** A fixed tag (`dim_<x>`) makes
  silver/baseline/backbone variants OVERWRITE each other, so you can't tell which model produced a metric. Put a
  `label` in the model path/tag/result; on completion write one `manifest_<label>_<run_id>.json` listing every
  task's `{tag, run_id, data_sha, train_examples, floor, verified}` and READ METRICS FROM THE MANIFEST, not by
  globbing result files. This is what lets you prove you are testing the right model.

## Quick checklist for the next problem
1. Classification? single vs multi-label? soft-tag or hard? define labels + OTHER.
2. Symmetric, leakage-safe eval; set the per-class bar; use top-k recall for soft-tags.
3. Baseline SetFit: default head, `num_iterations` scaled to data size, progress-monitored.
4. Improve: cap-per-class data -> 2–3 backbones -> top-k/soft-tag -> ensemble (mind tags/span) -> calibrate.
5. Rare classes: mine + curate silver (diversity + reject-rules) -> source online if exhausted (train-only) ->
   check for taxonomy overlap.
6. Register + snapshot every keeper; adopt-only-if-better; load, don't retrain; serve behind a seam.
7. Match the training env's `sentence-transformers`/`transformers` versions to the SERVING env (or plan the cascade
   upgrade + regression test); serve with body+joblib-head (no `setfit` dep); fidelity-gate the serving loader
   against the training numbers before trusting it.

## Reference implementation
A worked, converged implementation of every mechanism above (training + registry + snapshot + `ckpt:` loading,
the top-k and ensemble evaluators, the mine/curate/select scripts, the symmetric-eval and capped-train prep)
lives at `~/work/clause-classifier-ab/` (`modal_setfit_train.py` and the `prep_*`/`mine_*`/`select_*` scripts).
Re-use its PATTERNS; do not copy its label set, thresholds, or numbers — those are specific to that problem.
