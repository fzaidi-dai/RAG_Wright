# Harness decisions: plan and ledger (HD)

Status: PROPOSED 2026-11-13. Scope: the agent harness only (`rag_wright.harness`): the decisions the chat-manager,
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
| HD-2 | The baseline and the comparison, live: today's Qwen tiers and Jev on the same items, three repeats each, `[eval] i/N` progress; pre-flight count and cost approved first | `EvalReport`s committed for both; a table per decision: per-option floor, flips, latency, cost; the two open questions answered from the numbers | todo |
| HD-3 | The decision: for each of D1, D2 and B1, adopt Jev (with a threshold below which Qwen decides as today), or not; an ADR records the numbers and the choice. If any is adopted, the next tasks (the decider hook on `DecisionPoint`, the per-run cache, the probability on `Decision.confidence`) are written here then | the ADR; this ledger extended or closed | todo |

Each task is one approval gate; one commit per approved task. Later candidates, unmeasured and not scheduled: D3
(accept or re-dispatch, with a per-worker bound), D4 and batching, D7 (a worker's done or blocked), the `seen.py`
similarity band, the `ConfirmTheView` reply, D8 at the harness's own surfaces, and D10 (the grader).
