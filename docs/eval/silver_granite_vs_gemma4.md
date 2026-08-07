# Silver-set: Granite-8B vs Gemma 4 (the premise of lever A), 2026-08-08

Validates the premise of lever A (escalate to a stronger model) BEFORE building any escalation machinery: is
Gemma 4 actually better than Granite-8B at Leg-A generation? Both run the single-call `generate_answer` through
the seam on OpenRouter (RAG_SERVING=openrouter) over the FROZEN silver evidence (no KG, no A100) -- a clean
same-backend model comparison. `scripts/measure_silver.py`, 3 repeats over the 13-record silver set.

## Results

| strategy | RECALL (answered / answerable) | PRECISION (abstained / unanswerable) | flips |
|---|---|---|---|
| granite-8b | 8/27 (**30%**) | 9/12 (75%) | 2/13 |
| gemma-4    | 27/27 (**100%**) | 6/12 (50%) | **0/13** |

Latency: granite ~2-30s/call; **gemma ~60-255s/call (~120s avg), ~10x slower.**

## Reading (with the answer spot-check)

- **Recall: Gemma 100% vs Granite 30%.** Gemma answered every record Granite falsely abstained on (Cap,
  Uncapped, Governing Law, Audit, Anti-Assignment, Exclusivity), with rich citations AND correct, nuanced
  content -- e.g. the Governing Law answer captured "Texas, EXCEPT covenants unenforceable under Texas law use
  the state where the Franchised Business is located." Granite abstained on it 3/3.
- **Consistency: Gemma 0 flips vs Granite 2.** Gemma is decisive; Granite nondeterministically flips.
- **Precision is better than 50% looks.** Gemma's two "misses" are the two SOFT negatives, and its behaviour is
  reasonable: on Insurance it OPENED with "The provided evidence does not specify general insurance requirements
  for each party, but it outlines requirements for clients seeking lines of credit..." -- flagging the gap and
  grounding only what is present (not fabrication; the strict binary silver key under-credits this). On the two
  CLEAN off-topic negatives it abstained 3/3 (perfect). Granite's 75% includes its OWN fabrication (it answered
  the redacted Minimum-Commitment work-plan 3/3).
- **The real cost is latency:** Gemma cannot run on every query (~120s avg).

## Conclusion -> lever A design (simplified by the data)

Granite's abstain is itself the escalation signal: Granite falsely abstains on ~70% of answerable queries; Gemma
answers them; on clean negatives BOTH abstain (so escalating an abstain -> Gemma also correctly abstains). So:

  A = run Granite; if it abstains, escalate that query to Gemma 4.

Lifts recall 30% -> ~100% while paying Gemma's latency only on the Granite-abstain minority -- no cheap-probe
disagreement machinery needed (that proposal is now unnecessary). The engineering problem A must manage is
BOUNDING Gemma's cost/latency (async, a per-query timeout, maybe a bounded concurrency budget), not detecting
borderline cases.

Caveats: silver (not SME-vetted); OpenRouter-Granite abstains somewhat MORE than the A100-vLLM-Granite on the
same evidence (a serving difference) -- but the Granite-vs-Gemma comparison is same-backend and valid. Recall
"answered" is abstain+valid-citation; fact-coverage was spot-checked, not scored. The strict binary precision
label should later allow a caveated partial (Gemma's Insurance answer) as acceptable.
