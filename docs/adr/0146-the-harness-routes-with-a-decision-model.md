# ADR-0146: The harness routes with a decision model, with the LLM below a threshold

**Status:** accepted · **Date:** 2026-11-13 · **Related:** HD-1..HD-3 (`docs/specs/harness-decisions/plan.md`),
ADR-0119 (Jev), ADR-0139 (the harness is engine scope; its routing is a model call), ADR-0040 ING-9 (Jev as the
ingestion judge)

## Context

Every routing decision in the harness is a Qwen turn: D1 (the chat-manager answers, calls a direct tool, asks, or
delegates), D2 (which workers the orchestrator dispatches) and the brief, which the chat-manager writes in its own
words. The harness already calls D1 a classification (reasoning off chose the same tool in 4.3 s as reasoning on in
34.8 s), and `Decision.confidence` is `None` for every model decision. Jev answers a calibrated yes/no or choice with
no text, in under a second, for about $0.00002.

HD-2 measured both on authored gold sets for Loom, the engine's wider test product (run `20261113-175341`, three
repeats, pooled):

| decision | Qwen (today's tier) | Jev |
|---|---|---|
| D1, four direct tools | 86.5%, `delegate` 0.39 | one `choice`: 85.7%, `delegate` 0.17; split: 88.1%, floor 0.67 |
| D1, twelve direct tools | 90.0%, `delegate` 0.22 | one `choice`: 91.1%, `delegate` 0.33; split: 84.1% |
| D2, per worker / exact set | 75.6% / not comparable (16 of 90 undecided) | 98.1% (floor 0.94) / 94.4% |
| B1, is the message self-contained | 95.0% | 95.0% |
| flips across repeats (D1) | 6-10 items per run | 0-1 |
| per decision | 1.6-3.5 s a call; D2 3.4 calls | 0.75-0.85 s, one call |

The Qwen readers take a tier's first action, while the gold describes the whole turn, so Qwen is understated on
`delegate` and on multi-worker briefs. Even so, nothing regresses: Jev matches Qwen on D1 and B1, and is better on D2.

From Jev's saved probabilities, against Qwen's answers on the same items:
- **D1**: Jev deciding at a top probability of at least 0.7 and Qwen below it gives 91.3% (four tools) and 91.1%
  (twelve), with Jev deciding 76-84% of messages.
- **D2**: Jev alone is best; any band handed to Qwen lowers the exact sets.
- **B1**: all three of Jev's wrong answers were within 0.08 of 0.5.

## Decision

- **The harness routes with the decision model** through the capability layer (`ainvoke_model("jev_decision")`),
  and the LLM decides only below a threshold or when the decision model is unavailable (no key, not registered, an
  error), which is today's behaviour.
  - **D1**: one `choice` over the direct tools (by their own descriptions), `delegate`, `ask_the_user` and `answer`.
    Jev decides at a top probability of 0.7 or more; below that, the chat-manager's Qwen turn decides as today. The
    split shape is not adopted: it helped at four options and hurt at twelve.
  - **D2**: one `noul` per worker, its description as what a yes means, in one call; Jev decides at 0.5, and Qwen only
    when Jev is unavailable.
  - **B1**: one `noul`, "can the message be understood without the earlier turns?". At 0.6 or more the brief is the
    user's words verbatim with the `<viewing>` block; otherwise the chat-manager rewrites it, as today (a doubtful case
    costs a rewrite, never a lost reference).
- Each decision is recorded on the run's ledger with Jev's probability as `Decision.confidence`, and cached per run,
  because Jev flips about 3% of answers on identical calls.
- What a product supplies stays a declaration: its tools' and workers' descriptions are the criteria. The thresholds
  are harness defaults a product may override.
- Routing in code supersedes ADR-0139's "the routing decision itself is the chat-manager's model call, and there is no
  structural way to make it" for the decisions above; the LLM remains the fallback and the writer of every text.

## Consequences

- A delegated request no longer needs the chat-manager's Qwen turn to route it, nor the orchestrator's reasoning-on
  turn to choose workers; Qwen is left composing replies, rewriting a brief that refers back, and compiling results.
- `delegate` is the weakest D1 option for both models (confused with a direct read and with asking). Improving it is
  prompting work on the option descriptions, measured on the same gold sets; it does not block adoption.
- The Qwen baseline is first-action. A whole-turn baseline (the orchestrator stubbed for D1, the workers answered
  for D2) is the measurement to run if the comparison is ever questioned.
- User questions and tool results reach the decision model's provider through OpenRouter, as the ingestion judge's
  inputs already do (ADR-0119); a self-hosted deployment points the profile at an on-premises decision server (Laya).
- The build is gated tasks in the plan: the decider hook, D1 in the chat-manager, B1 and the brief, D2 in the
  orchestrator, then a live end-to-end run and the products' guide.

## Addendum (HD-8, 2026-11-13): measured live

The routed tiers on the same gold sets (run `20261113-214024`, read at the first action as HD-2 was): D1 85.7% on
four direct tools (Qwen alone 86.5%, weakest option 0.44 against 0.39) and 92.2% on twelve (90.0%); D2 90.0% per
worker (75.6%), exact sets 78.9%, the misses all multi-worker briefs the orchestrator plans and is read at its first
round. No regression. One gap: a route to `ask_the_user` offers the tool without forcing it, and those items were
answered with a reply 4 of 18 times per D1 run (HD-9, proposed).
