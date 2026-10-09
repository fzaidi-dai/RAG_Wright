# Classification & decision models

Much of a pipeline's per-unit work is not LLM-shaped. A "do everything" LLM call over every unit of a long document
is a bundle of separable decisions — and most of the bundle is cheaper, faster, and more robust as a deterministic
rule, a trained classifier, or a typed decision model. This page is the decision framework; the deep playbooks are
the skills linked below.

## 1. Find the opportunities — the `classifier-opportunity-analysis` skill

Map each per-unit LLM call and name the **shape** of each decision it bundles. Pick the mechanism cheapest-and-most-
robust first:

| decision shape | mechanism |
|---|---|
| boundary / "is this a unit / extractable?" | **deterministic structure** first (numbering, headings, layout); a **decision model** only for the ambiguous residue, through the `boundary_decider` hook: one batched call, a structural rubric stated once, the lines sent without their surrounding text |
| closed-vocab value with an enumerable cue list | a **deterministic cue-rule** (no ML) |
| single-label routing (closed set, no clean cues) | a **classifier** |
| multi-label soft-tagging (guidance, not a gate) | a **soft-tag classifier** (top-k) |
| verbatim text vs a paraphrase | extract the **verbatim span** (deterministic) |
| open / numeric values (amounts, durations, places) | **deterministic candidate spans** + a **decision model** that says what each candidate is (one `choice` per candidate, one call per unit, none when there are no candidates); the LLM only as the fallback |
| verify extracted values (a judge) | a **decision model**: one batched `noul` per unit, one question per value |
| free-text / synthesis | keep the **LLM** |
| a verdict over a closed label set (compliant/violation, relevant/not) | a **classifier / decision model** (often query-time) |

> Measured in the reference pack (re-measure on YOUR data; ADR-0122, ADR-0040):
> - **Boundary residue:** 460 of 461 hand-labelled lines (99.8%) with zero flips across 3 calls, after the
>   question was rewritten as a structural rubric (was 94-96%, with answers flipping between calls).
> - **Judge:** 95.3% vs the LLM judge's 90.1% on 192 cases hand-labelled blind to both.
> - **Residual values:** held-out value-level recall 0.83 / precision 0.88 vs the LLM's 0.60 / 0.79.

Ingestion (per-unit over long documents) is the biggest win; query-time is low-volume and often fine to leave on
the LLM. Decide the **role** first: a *soft tag* that only augments is safe at modest accuracy; a *hard gate* needs
high accuracy + a graceful-degrade fallback.

## 2. Pick the builder — trained classifier vs System-1 decision model

For a closed-set decision, three realistic builders (A/B them on the same eval — see step 3):

- **Trained classifier (SetFit)** — a Sentence-Transformer body + a small head; CPU-cheap per unit, runs in the
  ingestion hot path, needs labeled data. The **`setfit`** skill: symmetric leakage-safe eval, per-class floor,
  soft-tag/top-k, rare-class curation, calibration, checkpointing, serve-behind-a-seam.
- **System-1 decision model (managed): Jev** — the OpenRouter Decisions API returns a calibrated typed answer
  (`noul`/`choice`/`score`) with **no training**, strong zero/few-shot (ADR-0119). Reach for it first when data is
  scarce or labels are ambiguous, or when you need calibrated uncertainty to route/gate. It is an I/O call, cheap
  enough to run per unit at ingestion: in the reference pack's live run on one contract (131 provisions), with the
  judge and the residual values on Jev, ingestion made 215 calls, all Jev and none to the LLM, for $0.013 in 276 s,
  against 277 calls (276 LLM), $0.277 and 459 s with the LLM judge and LLM residual values (ADR-0040).
- **System-1 decision model (open): Laya** — the open-weight sibling; near-random zero-shot, must be fine-tuned.
  Use when a managed API is unacceptable (on-prem/data-residency). The **`laya`** skill.

> Directional, from the reference project (re-measure on YOUR data): on one closed-set gate, zero-shot Jev reached
> the LLM's accuracy where a trained SetFit plateaued below it (ADR-0119). Numbers never transfer — the eval decides.

### Lessons from running a decision model at ingestion

- **It is not repeatable call to call.** Setting `temperature` or `seed` does not make Jev give the same answer
  twice. Answers that flip sit near 0.5, which means the question is ambiguous: fix the question (state a structural
  rubric once, send the lines without their surrounding text), do not vote.
- **Cache decisions, with the prompt in the key.** Key each cached decision by the decision model, the prompt (or
  the extraction method) and the exact inputs, so a re-ingest is repeatable and free, and a changed prompt never
  reuses old answers. The engine has no generic decision cache yet (engine gap G6).
- **Calls are metered.** Each `jev_decision` call is recorded in `measure_usage` with its cost.
- **Keep a fallback switch.** In the reference pack, `RAG_SEMANTIC_JUDGE=llm` and `RAG_RESIDUAL_EXTRACTOR=llm` put the
  judge and the residual values back on the LLM; the LLM is also used when no decision model is available.

## 3. Decide with the eval — the `creating-evals` skill (first)

Write the capability's eval **before** you build it (TDD): a reproducible gold set + the metric + a gate from the
acceptance criterion. It is the neutral judge you **A/B** the options on — deterministic rule vs trained classifier
vs decision model vs LLM — selecting on the gate + diagnostics + cost + calibration. The training data you build IS
the eval. This is the engine's "identify → **eval-first** → build" chain.

## 4. Wire it as a capability (behind the seam)

- A trained classifier or a decision model is a **`model`-kind capability** with an `impl_ref`
  (`def <slug>(resources, inputs) -> result`), invoked by name through the engine
  ([authoring capabilities](authoring-capabilities.md)) — never hand-constructed around the invoker.
- **`jev_decision` is an engine capability** (a generic `model` capability, in `ENGINE_CAPABILITY_SLUGS` with its
  manifest in `engine_capabilities()`), opt-in like every capability: register it first
  ([authoring capabilities](authoring-capabilities.md)), then invoke it by name. It runs only when the profile's key
  (`OPENROUTER_API_KEY` by default) is set.

  ```python
  out = await ainvoke_model("jev_decision", {
      "state": "<the rubric, stated once>\n\n<the text or the numbered items>",
      "questions": {
          "c0": {"type": "choice", "instructions": "Candidate [0]",
                 "criteria": {"batch_id": "the identifier of the production batch", "none": "none of these"}},
      },
  }, resources=ws)
  out["answers"]["c0"]   # {"type": "choice", "choice": ..., "probabilities": {...}}
  ```

  Question types are `noul` (yes/no, a score in `noul`), `choice` (one of the criteria keys) and `score`.
- A **decision model rides a `DecisionModelProfile`** (`rag_wright.models.profiles`, ADR-0119): the model id,
  served id, endpoint, `api_key_env` and timeout live in the profile. `RAG_DECISION_MODEL` (or the `model` input)
  picks a profile; the default is `jev-1.13`. Only the built-in profiles exist today, so pointing at an on-premises
  decision server (Laya) still needs an engine change (engine gap G12).
- The decision **questions/criteria live in the `.ttl`** (ADR-0066), not in the capability code — the ontology is
  the knowledge, the capability is the mechanism. Author an option criterion, an option order and one instructions
  string, as the reference pack does with `cbr:ResidualRole` + `cbr:decisionCriterion` + `cbr:roleOrder`
  (`contract_bridge.ttl`) and `cmp:decisionCriterion` (`compliance_bridge.ttl`); see
  [ontology authoring](ontology-authoring.md). Your capability reads them with its own `rdflib` reader (the engine
  has no generic one yet, engine gap G7).
- Keep a **graceful-degrade path** (an abstention/low-confidence routes to the residual LLM or an explicit
  "ambiguous"), never a silent wrong answer.

## 5. Swapping the decision behind a hook

A hook is where a domain decision plugs into an engine pipeline: `span_tagger=` on `build_ingestion` gives each span
its soft tags, for example. Keep the model out of the hook. The hook invokes a `model` capability **of your own** by
name, and that capability's `impl_ref` says what answers it: a trained classifier, the decision model or an LLM.
Trying another model is then a registration change: register a manifest under the same slug with a different
`impl_ref` (`register_capability` replaces a manifest by slug). The hook, the pipeline and the eval stay as they are.

The hook. The capability's contract is yours to define; here it takes `{"texts": [...]}` and returns one
`{"tags": [...], "scores": {label: probability}}` per text, tags strongest first:

```python
from rag_wright.api import TaggedSpan, ainvoke_model


def make_span_tagger(ws, slug="span_labelling"):
    """A span_tagger for build_ingestion that asks the `slug` capability, whatever implements it."""
    async def tag(chunk_text, spans):
        out = await ainvoke_model(slug, {"texts": [s.text for s in spans]}, resources=ws)
        return [TaggedSpan(span=s, tags=r["tags"], scores=r["scores"]) for s, r in zip(spans, out["results"])]
    return tag
```

Two implementations of that one contract. A trained classifier (sync, so `ainvoke_model` runs it off the event
loop), and the decision model: one `choice` question per span, all of a chunk's spans in one call, with a `none`
option so it can abstain. The labels and their one-line definitions belong in your pack's `.ttl` (ADR-0066); they are
inline here only to keep the example short.

```python
from rag_wright.api import ainvoke_model

LABELS = {
    "batch_id": "states the identifier of a production batch",
    "test_result": "states a measured test value",
    "none": "none of these",
}


def classifier_span_labelling(resources, inputs):
    results = []
    for probs in my_classifier.predict_proba(inputs["texts"]):   # your model: one {label: probability} per text
        tags = sorted((k for k, p in probs.items() if p >= 0.2), key=probs.get, reverse=True)[:3]
        results.append({"tags": tags, "scores": probs})
    return {"results": results}


async def jev_span_labelling(resources, inputs):
    texts = inputs["texts"]
    state = "Label each numbered item by what it states.\n\n" + "\n".join(f"[{i}] {t}" for i, t in enumerate(texts))
    questions = {f"s{i}": {"type": "choice", "instructions": f"Item [{i}]", "criteria": LABELS}
                 for i in range(len(texts))}
    out = await ainvoke_model("jev_decision", {"state": state, "questions": questions}, resources=resources)
    results = []
    for i in range(len(texts)):
        answer = out["answers"][f"s{i}"]
        scores = {k: p for k, p in answer["probabilities"].items() if k != "none"}
        results.append({"tags": [] if answer["choice"] == "none" else [answer["choice"]], "scores": scores})
    return {"results": results}
```

The swap. Both functions live in a module of yours (`my_product.caps.labelling` here); the manifest names the one in
use. `jev_decision` is an engine capability, registered from `engine_capabilities()`:

```python
from rag_wright.api import CapabilityManifest, engine_capabilities, register_capability


def register_span_labelling(impl):
    register_capability(CapabilityManifest(
        slug="span_labelling",
        kind="model",
        display_name="Span labelling",
        description="Soft labels for each span, with a probability per label.",
        representative_queries=("label these spans",),
        impl_ref=f"my_product.caps.labelling:{impl}",
    ))


for m in engine_capabilities():
    register_capability(m)
register_span_labelling("classifier_span_labelling")   # the current arm
register_span_labelling("jev_span_labelling")          # the other arm: the same slug, replaced
```

Fill `scores`, not only `tags`. A unit is labelled and cited by its representative span, and a
`unit_representative` that votes reads `TaggedSpan.scores`; a tagger that returns tags alone leaves it nothing to
vote with. A `choice` answer carries a probability for every option, so the decision model's scores come for free.
A voting rule (the reference pack chose its own by measurement, ADR-0126):

```python
from rag_wright.api import build_ingestion


def vote(members):
    """The unit's label is the one with the highest probability summed over its members; the member most confident
    in that label represents it."""
    totals = {}
    for m in members:
        for label, p in m.scores.items():
            totals[label] = totals.get(label, 0.0) + p
    if not totals:
        return members[0]
    label = max(totals, key=totals.get)
    best = max(members, key=lambda m: m.scores.get(label, 0.0))
    return best.model_copy(update={"primary": label})


pipeline = build_ingestion(my_extractor, span_tagger=make_span_tagger(ws), unit_representative=vote)
```

Before you choose an arm:

- **Measure both on the same gold** (section 3, the `creating-evals` skill). Judge by the per-label floor, and by
  top-1 accuracy wherever anything acts on the primary label, not only by top-k recall. Run the comparison on spans
  the pipeline produced (headings, fragments, spans with no label), not only on curated snippets.
- **Count the calls and their cost.** The decision model costs one call per chunk here, recorded in
  `measure_usage`; a classifier costs none. Estimate the count for a bulk run before starting it.
- **Make it repeatable.** The decision model's answers can change between identical calls (section 2); cache them,
  with the prompt in the key, so a re-ingest gives the same tags.
- **A long label list is a harder question.** A `choice` over dozens of options may lose accuracy; measure it. If it
  does, try a two-step question (the group first, then the label within it), and measure that too: a cascade
  multiplies the two steps' errors.
- **The open-weight decision model (Laya)** fits behind the same slug once its server is reachable through a
  `DecisionModelProfile` (engine gap G12).

## Skills

`classifier-opportunity-analysis` (identify) → `creating-evals` (eval-first) → `setfit` / `laya` (build the
decision) → `authoring-a-capability` (register). Bulk teacher-labeling for training data runs on the flat-GPU
substrate (`qwen-vllm-modal`), not ad-hoc paid calls.
