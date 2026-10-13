---
name: building-an-agent-harness
description: The blueprint for an agent harness over a retrieval engine - the three-tier loop, the eight doors the domain comes through and how to keep it out, tenancy the model cannot reach, human-in-the-loop under replay, what to do when a step does not finish, context and memory management, per-tier reasoning, and where a decision model pays. Load before building or changing a harness, an agent tier, a tool surface, a middleware or a guardrail. Built on DeepAgents/LangGraph; every rule here was paid for by a live run, and the figures were corrected once a real separation measured them.
---

# Building an agent harness over an engine

Repository paths in this skill (`docs/`, `scripts/`, `tests/`, `src/`) are in the engine repository: read them there or on GitHub, at the tag matching your installed engine.

> **This skill was distilled from a shipped harness and is written for a DIFFERENT product.** It ships in the
> engine wheel; link it into your repo's `.claude/skills/` (`docs/installation.md`).
>
> **Every rule below was paid for by a live run**, and the measurements are kept because they are why
> anybody follows the rule. Where a number appears, it was observed, not estimated.
>
> **What is generic and what is yours.** The loop, the error levels, the deadlines, the interrupt protocol,
> context and memory PLUMBING, metering and the decision taxonomy are generic: they are about running an
> agent, not about your domain. Your tools, your workers, your reports, your prompts, your tenancy shape,
> your ledger schema, your continuation types, your model choice and your checkpointer are all yours.
>
> **That split is more lopsided than it looks, and the next line is the one to believe.** Separating a
> 7,514-line harness yielded roughly **1,750 lines of genuinely generic substrate and ~1,900 lines of
> product code that had to move out of the harness package** -- the rest being prompts and tests. (That
> first figure was 1,300 when the separation was two thirds done; finishing it moved modules INTO the
> liftable set, which is the direction to expect.) If you
> are reading this before building, the payoff is real; if you are reading it to estimate, estimate with
> the script in the next section and not with this sentence.

## FIRST: check whether the engine already SHIPS the substrate

**Do not start by building. Start by importing.**

```python
import importlib
tier = importlib.import_module("rag_wright.harness")   # ModuleNotFoundError -> build; otherwise consume
print(sorted(tier.__all__))
```

**From `rag-wright` 0.10.0 the engine ships it** as `rag_wright.harness`, its third public tier (ADR-0139,
PS-32): the separation was handed over and adopted, and the tier declares `__all__` (reference:
`docs/api/harness.md`). On an older engine the import raises `ModuleNotFoundError` and everything below applies
as instructions. On 0.10.0 or later, **consume the tier**: most of this skill becomes background reading, and
following it as instructions means hand-building several thousand lines the engine ships and maintains. A
product declares an `AgentDefinition` (its `ToolSurface`, planning split, workers, context type satisfying
`RunScope`, retry set, model id, `AgentPrompts`, rules and decision points) and runs it through `start_run` /
`resume_run` / `stream_run`.

So check, every time, and pin what you find:

- **Record the tier's surface the way you record the engine's API**, in whatever file already holds that
  pin, and fail your suite when reality disagrees with the record **in either direction**. A guard that
  *skips* while the tier is absent is a guard that never runs -- and the absent state passes trivially, so
  drive it into the published state once with a planted module or you have tested nothing.
- **Require the tier to declare `__all__`.** An undeclared surface makes every import a guess about what
  is public, and under an editable or path dependency an unreleased name is indistinguishable from a
  released one. That mistake has been made twice here.
- **Build the guard BEFORE you consume the tier.** One written after the first import is shaped to agree
  with whatever you happened to import.

**What stays yours either way** is the list in the preamble above, and it does not shrink because the
substrate arrives pre-built: your tools, workers, prompts, tenancy shape, ledger, continuations, model
choice and checkpointer. **The sections below on the eight doors, tenancy the model cannot reach, the
replay rule, and the decision registry apply unchanged to a harness you consume** -- they are about what
you hand the substrate, not about how the loop is written.

**And the second product (build order step 4) survives the tier's arrival with its question inverted.**
Building, it asks *"did I write this generically?"* Consuming, it asks *"does the engine's tier actually
satisfy my seams?"* -- which is the cheaper question and the one you can answer in an afternoon, so there
is no version of this where writing that fake product is wasted.

## The thesis, measured -- and CORRECTED, which is the first lesson

**An earlier version of this skill said "32 modules, 7,514 lines, and only THREE import anything from the
product." That was wrong, and it is worth knowing how wrong before you trust anything else here.** When the
harness was actually separated, module by module, the figures were:

| claimed | measured |
|---|---|
| 3 modules import product code | **13** (6 of them a domain package) |
| 21 name no domain concept | **18** |
| "turning three imports into protocols, not a refactor" | **~1,900 lines of product code to move** |

The claim had been made by counting the *kinds* of door rather than the modules that walk through them, and
it was repeated into a downstream ADR before anybody re-measured. **If you take one thing from this
section: a separability claim you have not derived with a script is a guess, and it will be believed.**

What actually came out of a 7,514-line harness when it was separated: the tool registry and tool surface
(1,129 lines), the worker roster with its domain prompts and reports (226), the continuation types and
their renderer (308), the planning classification, the ledger schema, a vendor and model name, a database
driver, and a context type encoding the product's tenancy. **Not "mostly prompt text."**

What genuinely stayed generic, and did lift: the loop, dispatch, error levels and deadlines, retry
composition, the interrupt protocol, checkpoint and memory plumbing, tracing, the decision taxonomy, the
worker-spec builder, the report envelope. That half is real and worth the work. Finished, it was **13
modules and about 1,750 lines** of the 7,500 -- measured on the three axes intersected, which is the only
figure worth quoting because each axis alone credits modules the others fail.

## The import guard is NECESSARY and NOT SUFFICIENT -- three axes, and a second product

**This is the section the rest of the skill was missing, and its absence cost a full separation's worth of
rework.** "No harness module imports the retrieval primitives" is a good rule and a cheap test. It is also
blind to most of what actually ties a harness to one product:

| axis | question | what it caught |
|---|---|---|
| 1. imports | does a module import product code? | the obvious half |
| 2. vocabulary | does its CODE name a domain concept? | prompts and reports, with comments and docstrings stripped |
| 3. **deployment, vendor, tenancy** | does it carry a choice only THIS product made? | everything the first two missed |

Axis 3 is the one nobody writes. In a harness that passed axes 1 and 2 cleanly it found: a module
hardcoding `"openai:qwen/..."` and an `is_openrouter()` helper; a module importing `psycopg` and converting
a connection URL; four modules keying memory and ledgers on a `(customer, workspace)` pair; and -- the
sharpest -- **the context type every agent is built with, declaring `customer_id` and `workspace_id` as
fields.** A product with one tenancy level, or three, could not fill it.

**And axis 3's own first implementation was blind to that last one**, because it matched call shapes
(`f(customer_id=..., workspace_id=...)`) and not field declarations (`customer_id: UUID`). The most central
instance of the thing it checked for scored zero. **Match declarations too.**

### None of the three measures the thing you actually want

All three ask *"is the substrate coupled to this product?"* -- a necessary condition. None can ask *"is what
the substrate asks for usable by someone who is NOT this product?"* Those come apart, and the gap is where
a bad seam hides: handing the substrate three pre-computed sets scores perfectly on every axis while
letting the next product supply three that disagree with each other.

**So write a second product. On day one, before the seams exist.**

A ~200-line fake product in a domain with nothing in common with yours -- ours was fabric composition
against a legal-contract harness -- implementing each seam as it stands. Hermetic: no model, no store, no
credentials. Mark what it cannot do yet as `xfail(strict=True)` so a gap that closes without its exemption
being removed fails.

It is the cheapest high-value test in the whole harness. In one separation it caught, each time before the
code shipped:

- a seam design that would have let a second product state a tier split inconsistent with its own
  classification -- it scored perfectly on all three axes;
- the context type encoding the first product's tenancy;
- a surface contract gaining a parameter (`build(..., only=)`) that an outside implementer could not have
  known about;
- a required member added to a definition object, which every product must now supply.

**The rule it encodes: when a seam changes, change the fake product FIRST.** If that is awkward, the seam is
wrong.

## The doors the domain comes through -- there are EIGHT, not three

**An earlier version of this section said three.** When the harness was separated, these are what actually
had to become seams or move. Build them as doors from the start and the separation is bookkeeping; discover
them later and each one is a refactor with its own test fallout:

| door | shape that worked |
|---|---|
| the tool surface | one object: `build(context, resources, only=None)` + `exposed_names` |
| the worker roster | a tuple of factories, passed in |
| which operations need PLANNING | declared by the product; the substrate DERIVES the direct surface from it |
| the context type | a `runtime_checkable` protocol whose member is `scope: tuple[str, ...]` |
| accounting | an object with `collecting()`, `record(...)`, `trace_metadata` |
| the checkpointer | built by the product, accepted by the substrate |
| resuming a suspended run | the continuation renders ITSELF; the substrate asks |
| the agent's chat model | from the engine's model profile, never a string in the substrate |

**Collect them into ONE object the substrate is handed** -- we called it `AgentDefinition`, carrying the
tool surface, the planning classification, the worker roster and the context type. Four declared facts, and
**the substrate derives everything else**.

That last clause is the whole trick. The alternative -- let the product pass the derived sets too -- is a
smaller diff and scores perfectly on every axis, and it exports an invariant as a convention the next
product can break silently. **If a value follows from two others, derive it on the substrate side.**

### Three doors are still worth building first

Build these three and most of the rest follows:

1. **The tool surface, DERIVED from your operation set.** Never hand-list it. A hand-written manifest
   silently omits the operation somebody added last week, and nothing fails: the capability exists, is
   routed, is tested, is reachable by API, and the agent simply cannot see it. Derive it, and assert exact
   equality against the operation registry.
2. **A tenant-bound facade over the primitives.** One object, built per run from the run's own context,
   exposing moves the agent can recombine. The harness imports the facade and nothing below it. Two guards:
   no method of the facade takes a tenancy key or a document scope, and every primitive that accepts a scope
   is reachable *only* through the facade.
3. **The prompts.** One per tier, plus the workers' skills.

**The facade is not a tool, deliberately.** If the manifest is derived from the operation set in exact
equality, a facade move exposed as a tool would be a tool with no operation behind it and the ratchet would
rightly refuse it. The facade is for composition - what the orchestrator runs in code and what a worker
recombines - not for prompt-directed calling. Both properties hold because they are about different surfaces.

> **Caveat, and it is an honest one: in the harness this came from, the facade was never wired.** It was
> built, specified against two requirements, covered by eight tests -- and across the whole project nothing
> ever called it. Its bridge function had no production caller at all; the tests were the only exerciser,
> which is why that went unnoticed for months.
>
> **So build the facade when a tier actually needs to recombine moves in code, not because this skill lists
> it.** And if you build it early, put one assertion somewhere that a production path reaches it: a
> green test suite over an unwired component looks exactly like a working feature.

## Tenancy the model cannot reach

**The tenant never appears in a tool signature.** Pass it through the framework's injected runtime, which is
excluded from the schema sent to the model:

```
search(question, runtime: ToolRuntime)    model sees -> ['question']
search(question, workspace_id: str)       model sees -> ['question', 'workspace_id']   <- the leak
```

That is not stylistic. **In a product like this the documents are adversarial by origin** - somebody outside
your company wrote them - so an identifier the model can name is one an injected instruction inside an
uploaded document can name too. The same rule covers the system prompt and any event you stream to a client:
a workspace id in a prompt is a workspace id a document can quote back.

**Ids, not rows.** The context is serialized into the graph on every invocation. A run has no business
carrying a customer's billing status or a user's password hash past the model.

### Memory: four scopes, and the factory must REFUSE rather than fall back

| path | namespace | writable |
|---|---|---|
| per-workspace | `(customer, workspace, "memories")` | by the run |
| per-customer | `(customer, "memories")` | yes: facts true of the customer |
| product-authored | shipped with the product | **no** |
| anything else | ephemeral, for the life of the run | n/a |

**Read your library's fallback before trusting it.** The one here, given no namespace factory, falls back to
a legacy path returning either an agent-scoped namespace or **a single global one**. Both are catastrophic
and neither raises: one global namespace is every customer's memory merged silently, and an agent-scoped one
is shared across every user of the agent by construction - the shape library docs warn about, *"if one user
can write to memory that another user reads, a malicious user could inject instructions into shared state."*

**So forgetting the factory is not a crash; it is a cross-tenant merge that looks like everything working.**
Make the factory read the run context and raise when there is none.

**Authored knowledge and learned memory live in separate namespaces.** A skill is a file you wrote and
reviewed. Memory is what an agent noticed. Promoting one customer's observation into another's run is the
failure that separation exists to prevent, and it is a mechanism, not a tidiness preference.

## One entry point, because context is PER-INVOCATION

Runtime context is per-invocation, not per-thread: **a resume that omits it wakes every tool with a null
context.** Combine that with the memory fallback above and a forgetful resume reads and writes another
tenant's memory, silently, at the moment a human approved an action.

A rule saying "always pass the context" is obeyed until the first refactor. **Make the call site incapable of
omitting it:** one or two functions that take the authenticated principal, build the context themselves, and
expose no parameter through which a caller could supply a different one. Then guard it - nothing else may
invoke an agent, and nothing outside the package may build one.

## Tiers: three, and the direct surface is a requirement

```
user  <->  CHAT-MANAGER      conversation, threads, UI context, direct tools for unambiguous asks
             |
             +-- ORCHESTRATOR    planning, decomposition, fan-out, re-planning
                   |
                   +-- WORKERS   one skill, few tools each
```

**The chat-manager's direct tools are not an optimisation.** Three tiers means three agents making model
calls before any tool runs, and routing *"list my documents"* through all three is latency and cost on the
commonest path. A model must never sit in the path of an unambiguous request.

**The split is a SUBSET, never a partition.** The orchestrator holds everything; the chat-manager holds the
half that needs no planning. *An operation the orchestrator cannot see is a plan it cannot make.*

**And the split is a judgement, so write it down rather than dressing it up as a derivation.** The tempting
rule - "an operation that names a model or a retrieval graph is a planning operation" - separates the set
correctly today and is a **proxy**: it misclassifies an operation that takes a judge under another name, and
one that traverses the graph through injected callables while belonging on the direct path. Write the split
down, ratchet that every exposed operation lands in exactly one half, and keep the proxy as a cross-check.

**Name the limitation out loud: the routing decision is itself a model call and there is no structural way to
make it.** One model call then a direct tool is what you want; whether the model takes the direct path is
measured, not asserted.

## Take the tool away. Do not ask the model not to use it.

**This is the single most repeated lesson in this harness**, and it has four faces:

- **A prompt saying "do not ask" is a suggestion; a tool the worker was never given is a fact.** Workers do
  not hold the escalation tool at all, so escalating is structurally the orchestrator's decision.
- **Remove the parameter rather than warn about it.** One warning repeated in four places was obeyed zero
  times in four runs; taking the parameter away fixed what a fifth instruction would not.
- **A bound is a count, not a request.** A tier that re-delegates the same work reads back *"complete,
  nothing further needed"* and delegates again: measured **3 times with reasoning on, 24 times with it off**.
  The right answer is 1, occasionally 2. Both numbers are the model's judgement, so choosing between
  reasoning settings only picks which failure you prefer. A dispatch past the bound never runs - no model
  call, no subagent, no second copy of a reply the tier already has. Twenty-three of those twenty-four were
  pure waste.
- **Withholding is a MIDDLEWARE concern, not a model-profile one.** A provider profile keys on the *model*,
  and every tier runs the same model - so excluding a tool there strips it from the tier that needs it. A
  middleware is attached to a tier, which is the only seam that can say "this agent cannot do X".

Two further details that look optional and are not. **Hide the schema AND refuse the call**: filtering this
turn's tool list does not cover a call arriving from replayed history or a resumed run. And **a refusal is a
signpost, not a wall**: a bare "not found" invited seven more wrong guesses in one run; hand the tier back
what the earlier attempt returned and tell it to answer from that. Return a tool message, never an
exception - an exception reaches the error middleware, finds nothing in the taxonomy, and kills a run that
had merely taken a wrong turn.

## When a step does not finish: a bound says STOP, a policy says WHAT INSTEAD

Four observed failures, and the sequence of getting them wrong is the lesson:

| observed | what bounded it | what is left afterwards |
|---|---|---|
| a worker re-dispatched after it had answered and stated its coverage: 2 dispatches, 325.9s | nothing; the run was working | **not a completion failure at all** - a wrong DECISION |
| the whole-turn deadline killed a run that had already produced correct results | our own clock | **the finished work is in the checkpoint** |
| the caller's ceiling fired at 555s of a 900s budget | the CALLER's clock | **the finished work is in the checkpoint** |
| the grading deadline burned re-asking a model that had just timed out | retries exhausted | **nothing at all** |

**First conclusion: "these are all one bug" was wrong.** Grouping a decision failure with three completion
failures made one policy look sufficient for all four. Drive each one with a fault injector and inspect the
aftermath separately.

**Second conclusion, and it saves you a component: the finished work is UNREAD, not LOST.** Two of the four
leave the worker's correct answer verbatim in the checkpoint as a tool message. So "proceed with what
returned, report the gap" is a **read-and-report over state you already persist**, not a progress store that
needs designing.

**Third: the row that leaves nothing needs its own answer.** A policy that assumed partial progress would
render an empty result as a partial answer, which is worse than failing. "Nothing came back" must be
reportable as exactly that.

**Error levels, by who fixes it.** Transient failures retry; tool failures reach the model as a message it
can act on; missing information interrupts for a human; anything unexpected reaches a developer. Runaway
loops are capped - and cap model calls and tool calls per run, ending with what you have rather than raising,
so a partial answer still reaches the user.

**Composition order is load-bearing.** Retry middleware goes INNER, configured so exceptions reach the
error middleware. The other way round, a tool failure is converted to a message before the retry ever sees
it, and the transient level quietly never fires. Read your library's own note on ordering; it is usually
documented and usually ignored.

**Which failures are recoverable is product knowledge.** Keep the taxonomy in the product and the wrapping in
the harness.

## Human in the loop: the replay rule governs everything

**Interrupting suspends the run, and resuming REPLAYS THE NODE FROM THE START.** Pin it in a test: a node
that prints before and after its interrupt yields `["before", "before", "after"]`, on a fake tier and a live
one.

So a tool that spends money, writes a row or sends a message before its question does it **once on the way in
and again on the way back**, silently, because the second pass looks identical to the first.

**The rule: gather the arguments, interrupt, act only afterwards.** Take it to its conclusion - make the
asking tool do nothing but ask, so there is no "afterwards" to get wrong, and read the function body in a
test to keep it that way.

**A question is a tool, not a middleware gate.** Gating a tool the model already chose is right for "may I do
this?" and wrong for "which of these did you mean?". A clarification is something the model decides it needs,
so it must be something the model can reach for.

**Batch the questions one tier up.** Five workers each asking directly is *correct* and also terrible: five
interruptions arrive one at a time, each stopping everything, with no sign another is behind it. A worker
that would ask instead reports "blocked, I need X" through its structured result, and the orchestrator asks
once. The less obvious benefit is bigger: a need that reaches the orchestrator can often be answered *by* the
orchestrator, which already knows what the conversation settled on, and never troubles the user at all.

**Never bridge the asking tool into a code interpreter.** A question raised from inside a loop re-runs the
whole program on resume - every tool call it had already made, again, with nothing on screen to say so.

**And a changed subject is confirmed deterministically, never adopted silently.** The user runs one query,
navigates to another, types "what about this one?", and every word of the answer is fluent, cited, and about
the wrong object. **Asking the model to notice it is asking the failure to detect itself.** Compare the
incoming view against the one the thread last confirmed, before the model runs, with the last-confirmed value
in the checkpoint so it survives the process that started the thread.

**A continuation payload is a claim, not permission.** *"A sweep over documents A, B and C"* says what the
user was looking at; it does not authorise reading A, B and C. It arrives from a browser and is then read by
a model, so it is untrusted twice. Re-ask every identifier of the workspace through the choke point you
already have, keep what survives, and treat the rest as **absent rather than refused** - a stale bookmark is
not an error, and raising would put a foreign identifier on the model's path inside a recoverable error.

## Context management, four mechanisms

1. **One per-run record of what was already asked.** Across every recorded run - 194 tool calls - **25 were
   byte-identical repeats**: same tool, same arguments, a second real retrieval for an answer already in the
   transcript. Answer an identical call from the record so the operation never runs and the payload never
   re-enters the context. Three rules: **opt-IN only** (a job's status changes while the run watches it, and
   an asking tool must reach a person every time); key it on the **run**, not the dispatch (a worker
   re-dispatched asks the same question in different words, and a dispatch-scoped record matches nothing);
   and remember that what prevents a wrong answer is the **subject** check, not which brief did the asking.
2. **Progressive disclosure for the rare procedure.** What a tier needs on every turn belongs in its prompt
   and must stay decisive at the margin. What it needs occasionally - how to batch a clarification, which
   arises only when a worker comes back blocked - belongs in a skill it sees as a name and one line until it
   applies. **The distinction is frequency, not importance.**
3. **Reference over value.** A surviving identifier is re-fetched by the agent, not snapshotted into the
   prompt: fresh, re-scoped on every fetch, and an id rather than a results array.
4. **Structural caps, labelled as such.** Size limits on a payload exist so an oversized one is refused
   visibly rather than trimmed silently. How big a summary *should* be is measured, not guessed - say which
   kind of number you have written down.

## Reasoning is a per-TIER decision, and the measurement is stark

Same question, same tools, same model, only the reasoning flag differing:

| | duration | output tokens | reasoning tokens | tool chosen |
|---|---|---|---|---|
| reasoning ON | **34.8s** | 1,812 | **1,905** | the same one |
| reasoning OFF | **4.3s** | 42 | 0 | the same one |

**Identical decision, 8x the wall clock, 43x the output.** With no cap the reasoning is unbounded, so one
turn can outlive its own attempt deadline - and the retry reasons just as long, so it fails again. That is
how one evaluation item burned three consecutive attempts and died, and why **68% of a run's wall clock** was
attempts abandoned before they returned. The cost was invisible until abandoned attempts appeared in the
trace at all.

**Again a middleware, not a profile**, for the same reason as withholding: the profile keys on the model and
every tier shares it. And **merge the model settings rather than replacing them**: binding replaces the whole
value, so writing the reasoning flag alone drops the endpoint pin and the usage reporting that came with it.
Nothing fails. The variance simply comes back wearing the pin's name.

**Do not over-claim from one arm.** One question, one sample, one tier justifies turning reasoning off for
*that* tier. The planning tier's job is decomposition and its decisions are the hardest; leave it thinking
until you have measured it.

## Durability and metering

**Checkpoint into the database you already run, in its own schema.** A suspended run must outlive the
process that started it, and a deploy is a reload the user did not ask for - so an in-memory saver loses
every run awaiting a human approval. The real cost is not the driver, it is **schema ownership**: a
checkpointer that creates unqualified tables where your migration autogenerator is looking will produce a
`drop_table` for them, and a routine migration deletes every suspended run. Their own schema makes them
unreflected.

**Zero is a measurement; absent is not.** Record a measured zero - an agent that answered from its own
context still spent planning tokens. Never record an **unmeasured** run as zero: a row of zeros asserts free
work. **Record wall-clock duration even when cost is unmeasurable**, because a self-hosted deployment bills
by the second and duration is the one input that makes the run apportionable later - and it cannot be
backfilled. **A ledger failure never fails the run.**

**One session identifier per run**, so usage can be correlated back by it. Without that the reader falls back
to summing a time window and attributes anything else running at that moment to this caller - unusable the
moment two people ask a question at once. Gate tracing on real **configuration**, never on a package being
importable, and make every telemetry touch degrade to a no-op.

**The event contract is yours, not the framework's.** Forwarding raw chunks makes your client depend on node
names, and every middleware change becomes a front-end change. Translate into a small named set of events.
Put no tenancy in them - a streamed frame is more exposed than a prompt.

## Decisions: enumerate them, and say what decides each one TODAY

**A wrong decision is a stop-the-line event, and rarity is not a defence.** A decision that is wrong once in
fifty runs is a decision you do not control, and it will be wrong in front of a customer on the run that
matters. Routing, classification, dispatch, escalation and guardrail verdicts are all decisions.

So keep a registry of decision points, and give each one **four distinguishable states**, because collapsing
them is how gaps get miscounted in both directions:

| state | meaning |
|---|---|
| recorded | something decides it and we can see what it chose |
| **a real recording gap** | something decides it and nothing records it |
| left out on purpose | recording it here would duplicate a fact owned elsewhere - say so, with the reason |
| **no decider yet** | nothing makes this decision at all; name what would have to exist first |

**The last state is the one worth the effort.** An observer for a decision nobody takes records the model's
implicit guess as though a guardrail had ruled on it. In this harness the content-safety point was attributed
to "a guardrail" that measurably did not exist: every mention of injection was prose in a system prompt, the
only typed exception was a boundary backstop whose own docstring said it should be unreachable, and the
behaviour was exercised only by a test outside the harness. **Build the decider first; the observation is
then trivial.**

**Say plainly what decides each point today, and resist the flattering answer.** In this harness, after a
full phase of work: no decision model anywhere in the loop, so most points are plain model turns where *a
51% call and a 99% call are indistinguishable in the output*. Exactly one point returned a real confidence,
because a tool decided it. That asymmetry is the argument for a decision model, and it only becomes visible
if the registry is honest.

**Where a decision model pays, and where it must never go.** Points that are a model's implicit judgement
with a small fixed option space - delegate or answer, which workers, accept or re-dispatch, escalate or
proceed - are candidates, and the registry doubles as the labelled corpus for training one, provided the
decisions are recorded. **A point already decided by a typed allow-list stays deterministic: replacing a
deterministic check with a probabilistic one is a regression however fast it is.** Shadow-mode first, always.

**Derive a decision from what the tier DID, never from what the model announces.** A model asked to report
its own decision reports an intention.

## Grading your own runs

**Keep each rule as ONE object holding both the sentence the model reads and the question the grader is
asked.** They are deliberately different sentences for different readers - the model is told what to do, the
grader is asked what happened - and keeping them on one object is what stops them drifting into two copies of
one decision. Record on the same object where the instruction is actually delivered, because it is not always
a prompt: one rule here is delivered as a signpost returned on the wrong call, and a registry assuming
"instruction equals prompt line" would have misrepresented that.

**The detectors are the grader's TOOLS.** A grader asked *"was the worker dispatched once per question?"* can
be wrong; one handed a function that counts cannot. Of six rules here, **five have a deterministic detector**
and the sixth - whether a dispatch restated the user's request faithfully - is the only genuine judgement.
Keep detector-less rules rare, and know which they are.

**Write your own grading middleware rather than subclassing a framework's.** Ours used about a quarter of the
library's and fought the rest: the rubric lived on a shared graph key that two parallel workers cannot both
write (a hard concurrent-update error that presented as a **hang** and cost three live runs to name), the
grader was built with a bare factory so none of our middleware reached it - no retry policy, no deadline, no
one-answer-per-turn, which is how one grader emitted **307 identical detector calls in a single response** -
and a quarter of its body was revision machinery we deliberately did not use.

**Grade on a bounded clock, and a failed grade abandons the GRADE, never the run.** Give the grading pass its
own wall clock *and* a per-attempt clock inside the retry: without the inner one a slow call is
unabandonable, eats the whole budget, and reports as "passed its deadline".

**Your transcripts are adversarial by origin**, because they quote documents somebody else wrote. Fence the
rules, the transcript and the verdict with a per-pass nonce and escape the closer: a rubric a document can
impersonate is worse than no rubric.

## Framework contract drift is silent, and behaviour is the only detector

Four observed, each with a green structural suite at the time:

- **structured output became an optional tool the model may decline.** A minor version change wrapped it in a
  strategy with no forcing; all three graders ended normally with no structured response, and the run scored
  zero on an answer that was correct. **Asking for a nonce-fenced block and parsing it yourself removes the
  failure by construction** - a model always emits text, there is no optional tool left to decline - and it
  drops a model call. Interpolate the shape into the prompt *from the same constant the parser reads*, so the
  instruction and the parser cannot become two copies of one decision.
- **a middleware left the base stack** and the planning tier silently lost its task-list tool.
- **a retry middleware returned its failure as message content**, indistinguishable to a parser from a badly
  formatted answer - which is how a measured "81% parse rate" was entirely an artefact, and how a grader
  re-asked a model that had just timed out. Configure retries to raise, and count "never answered"
  separately from "wrong shape".
- **every generation emitted had a duration of zero** for the project's whole life.

**So: pin the behaviour you depend on with a test that DRIVES it**, not one that asserts a symbol imports.
Importing fine was never the question. And where a framework decision can change under you, prefer a format
you own.

## Instruments lie, and they lie confidently

Four wrong calls in one phase, each stated with confidence: a stall blamed on the provider from a static
heartbeat; workers said to hold a shell by reading a compiled tool node; a parse rate that was entirely the
retry artefact above; and a time split attributed to the engine that was 767.7s ours against 318s theirs.

**A guard or a number that has not been made to fail is not evidence.** Plant the violation, watch it fire,
restore. And when you report a cause, label it: mechanism observed, consistent-with, or timing-only.

## Build order

0. **Check whether the engine ships the substrate** (the first section above). If it does, steps 1-5 become
   *pin its surface, then verify it against your second product*, and you resume at step 6.
1. **The context object and the single entry point**, with the guard that nothing else may invoke an agent.
   **Give the context an opaque `scope: tuple[str, ...]` from the start**, not named tenancy ids -- it is
   the hardest thing to change later, because every agent is built with it.
2. **The import guard**: no harness module may import the retrieval primitives.
3. **The three-axis measurement as a committed SCRIPT**, not a one-off. Axis 3 (deployment, vendor,
   tenancy) is the one that finds what the others cannot, and declarations must match as well as calls.
4. **The second product**, ~200 lines, hermetic, strict xfails. Before the seams exist, so each seam is
   designed against an outside implementer rather than audited afterwards. **This is the step that was
   missing**, and the one that would have saved the most.
5. **The facade**, with its two guards -- when a tier needs it (see the caveat above).
6. **The derived tool surface**, with exact-equality against the operation registry.
7. **One tier, end to end**, holding the read surface directly. Three tiers before anything has run is a
   hierarchy designed against a prediction.
8. **Error levels and the caps**, in the documented composition order.
9. **Tracing and metering**, before you tune anything - the reasoning cost above was invisible until
   abandoned attempts appeared in the trace.
10. **The durable checkpointer**, in its own schema.
11. **The interrupt protocol**, with the replay test pinned first.
12. **The remaining tiers**, the roster, and the withholding middleware.
13. **The decision registry and the rule registry**, then grading.
14. **Then**, and only with runs recorded, consider a decision model on a point that has one.

## What goes in your project's own child skill

- your tier roster and which operations each tier holds, by name
- your prompts' locked rules, and where each is actually delivered
- the commands: how to run one agent locally, how to replay a recorded run, how to drive a fault
- your decision registry's current four-way split, and which gaps are open
- the defects that recurred in **your** harness, with the run that showed each one

## Verification checklist

**Written for BUILDING the substrate. If you are consuming the engine's tier (the first section), about a
third of these stop being yours** -- the import rule, the three-axis measurement, the vendor/driver/tenancy
rule, the derive-on-the-substrate-side rule, the entry point's context, the middleware order and the
namespace factory's refusal are all properties of code you no longer own. **Verify them rather than
building them, and treat a failure as a finding to raise, not a task.** Everything about your tools, your
prompts, your tenancy shape, your interrupts, your decisions and your grading stays yours in both worlds,
as does the last line.

- [ ] No harness module imports the retrieval primitives (tested)
- [ ] The three-axis measurement is a committed script, and axis 3 matches FIELD DECLARATIONS as well as
      calls -- a context type declaring `customer_id` is the case that hides
- [ ] A second product, in an unrelated domain, builds and runs an agent on the substrate (tested,
      hermetic). Its gaps are `xfail(strict=True)` so one that closes without its exemption being removed
      fails
- [ ] Every value the substrate could DERIVE is derived there, not passed in -- a derived value crossing the
      boundary becomes a convention the next product can break silently
- [ ] Nothing in the substrate names a vendor, a model, a driver or a tenancy key (tested, axis 3)
- [ ] When a seam changed, the second product was changed FIRST
- [ ] The tool manifest is derived from the operation set in exact equality (tested)
- [ ] No tool signature, prompt or streamed event carries a tenancy identifier (tested)
- [ ] The memory namespace factory RAISES without a run context; no fallback path is reachable
- [ ] An agent can only be invoked through the entry point, which cannot omit the context (tested)
- [ ] The replay behaviour of your interrupt is pinned by a test, fake and live
- [ ] Every tool that interrupts does nothing before its question
- [ ] A bound exists for model calls, tool calls and re-dispatch, and each is a count rather than a request
- [ ] Retry middleware is INNER of the error middleware, and failures raise rather than returning as content
- [ ] A measured zero is recorded; an unmeasured run is not recorded as zero
- [ ] Each decision point is in exactly one of the four states, and a test asserts the partition
- [ ] Each graded rule carries both its instruction and its criterion on one object
- [ ] Every new guard was proved by planting its violation and watching it fail
