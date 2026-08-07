# Leg-A silver eval set

Ground-truth-ish fixture for measuring the `intra_document_qa` generation strategies (baseline / B reason→emit /
C best-of-N / **A escalation**) WITHOUT the live KG. Each record freezes the evidence `intra_document_qa` serves
for one query (target function → serve clauses → attach ADR-0044 carve-outs → rehydrate span text), plus a
**silver answer key**.

**Silver, not gold.** The answer key was authored by reading each record's frozen evidence (reading comprehension,
not legal-SME judgement). It is provisional. A legal SME upgrades it to gold later by editing this same file — the
schema and the measurement harness do not change.

## Schema (`evidence_snapshot.json` → `records[]`)

| field | meaning |
|---|---|
| `id`, `kind` | `answerable-*` / `negative-*` (intent) |
| `function_served`, `question`, `contract_id` | the query |
| `evidence[]` | frozen `{chunk_id, text, confidence}` — the generator's input |
| `answerable` | **silver truth**: is the question answerable FROM this evidence? (overrides `kind`) |
| `must_include` | key facts a correct answer must state |
| `expected_citations` | chunk_ids that support the answer (subset of evidence) |
| `notes` | judgement rationale, esp. for borderline items |

## Contents

9 answerable + 4 unanswerable. Two records intended as answerable were relabelled `answerable:false` because the
served evidence does not actually support the answer (Insurance-7 = credit-guarantee text + a blank policy form;
Minimum-Commitment-10 = a redacted research work-plan) — kept deliberately, as precision tests: a strategy that
answers them confidently is over-reaching. Two explicit negatives pair real evidence with an off-topic question.

## Regenerating / editing

- Re-snapshot evidence (needs the KG up): `python -m scripts.snapshot_leg_a_eval`
- Re-apply / edit the answer key: `python -m scripts.author_leg_a_silver_key` (edit `_KEY` there), or hand-edit
  the JSON directly (what the SME does).

## Scoring (for the A measurement)

- **Recall** (on `answerable:true`): did the strategy answer (not abstain) with the expected facts + valid
  citations? A false abstain here is the failure A targets.
- **Precision** (on `answerable:false`): did the strategy correctly abstain? Answering here is fabrication.
