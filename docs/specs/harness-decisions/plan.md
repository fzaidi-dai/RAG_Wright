# Harness decisions: plan and ledger (HD)

Status: HD-1 and HD-2 done 2026-11-13; HD-3 decided (ADR-0146); HD-4..HD-8 the build. Scope: the agent harness only (`rag_wright.harness`): the decisions the chat-manager,
orchestrator and workers take themselves, and their handoffs. Tools and their internals are the product's (RuleWright,
TexWright), which already use Jev inside their own capabilities; nothing here touches them.

## Why

Every harness decision is a Qwen turn today, and several are classifications in all but name. The harness says so
itself: `chat_thinking` (`harness/agent.py`) records D1 as "a classification" (reasoning off picked the same tool in
4.3 s as reasoning on in 34.8 s), and `Decision.confidence` is `None` for every model decision, with the note that a
classifier returning a probability per option "is what would buy a threshold to escalate below". Jev (ADR-0119)
answers a yes/no, a choice or a score, calibrated, in about 70-500 ms for about $0.00002 a call, and writes no text.

This workstream measures, before building anything, whether Jev can take the routing decisions, starting with the
two the user chose: **D1** (the chat-manager: answer, call a direct tool, delegate, or ask the user) and **D2** (the
orchestrator: which workers), plus the gate that decides whether a brief can be assembled verbatim.

## The decisions measured first

| Id | Who decides today | Question | Options | Jev shape |
|---|---|---|---|---|
| D1 | chat-manager, one Qwen turn | what does this message need? | each direct tool, `delegate`, `ask_the_user`, `answer` | one `choice`, the options described by the tools' own descriptions |
| D2 | orchestrator, a reasoning-on Qwen turn | which workers does this brief need? | each worker, or none | one `noul` per worker, its `description` as the criterion (multi-label) |
| B1 | (nobody: the chat-manager writes the brief in its own words) | is this message self-contained, or does it refer back to earlier turns? | `self_contained`, `refers_back` | one `noul` |

B1 is what the brief rests on: a self-contained message is passed to the orchestrator verbatim with the `<viewing>`
block (no model call); one that refers back ("what about the second one?") is rewritten by Qwen, as today.

## Method

- **Gold sets on the engine's own product.** Loom (`tests/harness/gold/loom.py`) is the fabric product with a
  roster wide enough to route on: three workers and twelve direct tools with distinct descriptions, so D1 and D2 have
  real option spaces. It sits beside `tests/harness/fabric.py` rather than changing it, because about three hundred
  tests are built on the fabric product's two operations and one worker. Requests are authored, never taken from a client's or RuleWright's data. Each item lists every
  acceptable answer (a request can be served by a direct tool or by delegating), and the set is symmetric per
  option. Multi-turn items carry the earlier turns for B1.
- **Measured with the engine's evals**: `arun_eval` with repeats (Jev flips about 3% of answers on identical calls;
  `flips` is reported), gated on each option's floor, not overall accuracy. D1 and B1 are scored by the routing
  evals' own `score_choice` (any of an item's accepted labels is correct); D2 per worker, a yes or no each.
- **The baseline is today's Qwen tier on the same items**: the chat-manager as built by `build_chat_agent`
  (reasoning off) for D1, the orchestrator's first turn for D2, D1 read from the chat-manager's first tool call
  (the ledger records D1 only as `delegate` or `answer_directly`), D2 from the recorded dispatches
  (`WhatTheRunDecided`). Jev is called through `ainvoke_model("jev_decision")`, never directly.
- **Paid calls get a pre-flight**: the exact count and cost before running, and approval.

## Open questions (decide before HD-1)

1. **Option-space size.** A product's chat-manager can hold a dozen direct tools (RuleWright: about nine). The gold
   set includes a twelve-option variant of D1 to see whether a single `choice` holds up, or whether D1 must split into
   `delegate or not` then `which tool`. **Recommendation: measure both shapes in HD-2.**
2. **What Jev sees as `state`.** The message alone, or the message plus the `<viewing>` block plus the last turns.
   **Recommendation: measure message+view against message+view+last two turns.**

## Ledger

| Id | Task | Acceptance (verify) | Status |
|---|---|---|---|
| HD-1 | Gold sets for D1, D2 and B1 on Loom, a product beside the fabric one (three workers, a four-tool and a twelve-tool chat-manager), the items authored with every acceptable answer, symmetric per option, committed under `tests/harness/gold/` | the sets load and validate (hermetic test); per-option counts printed and balanced; no item names a client or another product | done (2026-11-13: Loom; D1 90 items, 6 per option over 15 options (42 on the small surface); D2 30 briefs, each worker needed in 12 and not in 18, 4 need none; B1 20, 10 per label; D1 and B1 scored by `tests/harness/gold/scoring.py`'s `score_choice` (one choice per item, any accepted label correct; the engine's `score_classification` reads several labels as multi-label), 3 D1 items list alternatives; `tests/harness/test_harness_gold.py` 13 tests, a planted mislabel and a scorer that ignores alternatives each fail them) |
| HD-2 | The baseline and the comparison, live: today's Qwen tiers and Jev on the same items, three repeats each, `[eval] i/N` progress; pre-flight count and cost approved first | `EvalReport`s for both (under the gitignored `data/eval/harness-decisions/`); a table per decision: per-option floor, flips, latency, cost; the two open questions answered from the numbers | done (2026-11-13: run `20261113-175341`, 10 runs, 3 repeats, $0.62 (+ $0.03 smoke); `tests/harness/gold/deciders.py`, `run.py`, `score_workers`; pooled over repeats: D1 4 tools Qwen 86.5% (floor `delegate` 0.39), Jev single 85.7% (0.17), Jev split 88.1% (0.67); D1 12 tools Qwen 90.0% (0.22), Jev single 91.1% (0.33), Jev split 84.1% (0.33); D2 per worker Qwen 75.6% (16 of 90 not decided in 6 calls), Jev 98.1% (floor 0.94, exact sets 94.4%); B1 Qwen 95.0%, Jev 95.0%; flips per D1 run Qwen 6-10, Jev 0-1; Jev about $0.00002 and 0.75-0.85 s a call. **Caveat:** the Qwen readers take a tier's FIRST action (the chat-manager's first tool, the orchestrator's first dispatch round, at most 6 calls), the gold describes the whole turn, so Qwen is understated on `delegate` and on multi-worker briefs; the user chose to keep these numbers (2026-11-13). Open questions: one `choice` holds up at twelve options (the split helps at four, hurts at twelve); state = message and view (D1 has no turns)) |
| HD-3 | The decision: for each of D1, D2 and B1, adopt Jev (with a threshold below which Qwen decides as today), or not; an ADR records the numbers and the choice. If any is adopted, the next tasks (the decider hook on `DecisionPoint`, the per-run cache, the probability on `Decision.confidence`) are written here then | the ADR; this ledger extended or closed | done (2026-11-13: ADR-0146, all three adopted: D1 one `choice`, Jev at 0.7 or more, Qwen below; D2 one `noul` per worker, Jev alone, Qwen only when Jev is unavailable; B1 one `noul`, verbatim brief at 0.6 or more, else the rewrite; thresholds from Jev's saved probabilities against Qwen's answers; HD-4..HD-8 below) |
| HD-4 | The decider: a harness module that asks the decision model one routing question through `ainvoke_model("jev_decision")` (options and their meanings from the product's declarations), returns the choice with its probability, caches it per run, and reports unavailable (no key, not registered, an error) so the caller falls back; the probability lands on `Decision.confidence` | hermetic: a fake decision model; the cache answers a repeat without a call; unavailable falls back; the ledger records the probability; live: one call per question shape | todo |
| HD-5 | D1 in the chat-manager: before its model call, the decider routes the turn; a direct tool, `ask_the_user` or `delegate` at 0.7 or more is taken without a Qwen routing turn (the tool's arguments and every reply stay Qwen's), below it the turn runs as today | hermetic on Loom with scripted models: a confident route skips the routing call, a doubtful one does not; the existing harness suite green | todo |
| HD-6 | B1 and the brief: on `delegate`, a self-contained message at 0.6 or more goes to the orchestrator as the user's words with the `<viewing>` block, built in code; otherwise the chat-manager writes the brief as today | hermetic: the brief is verbatim for a self-contained item and rewritten for one that refers back; `the-brief-is-faithful` holds by construction on the verbatim path | todo |
| HD-7 | D2 in the orchestrator: the decider picks the workers from their descriptions and the orchestrator dispatches them; Qwen still reads, re-plans on a failure and compiles the result | hermetic on Loom; the existing dispatch, batching and tier tests green | todo |
| HD-8 | Live end to end on Loom and the products' note: the HD-1 sets through the built tiers with the decider on, against HD-2's numbers; what RuleWright and TexWright change (nothing required; descriptions become criteria, thresholds overridable) | live report; no regression against HD-2; the guide section written | todo |

Each task is one approval gate; one commit per approved task. Later candidates, unmeasured and not scheduled: D3
(accept or re-dispatch, with a per-worker bound), D4 and batching, D7 (a worker's done or blocked), the `seen.py`
similarity band, the `ConfirmTheView` reply, D8 at the harness's own surfaces, and D10 (the grader).
