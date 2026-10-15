# RuleWright: moving to rag-wright 0.12

What 0.12.0 changes for RuleWright (PS-43, from your note of 2026-10-15; unreleased until its release PR is
merged). Do the 0.11 guide first. Nothing here is edited from the engine repo, and nothing breaks an existing
call: every item is new or optional.

## 1. Build workers with `worker_spec(..., definition=...)` (PS-43)

**What changed.** `worker_spec` takes an optional `definition=`, the product's `AgentDefinition`. Each policy
parameter left at its inert default is taken from it: `retry_on`, `message_for`, `repeatable`,
`question_argument`, `framings`, `carry_forward` and `read_framing` from the fields of the same names, and
`signposts` from `definition.prompts`. A value passed explicitly still wins; `ledger` is still the caller's. Without
`definition=`, `worker_spec` behaves exactly as in 0.10.

**What to do in RuleWright.** The three `worker_spec(...)` calls in `src/rulewright/agent/workers.py` thread
those values by hand. Replace the threaded arguments with `definition=rulewright_agent()` (or whatever holds your
definition), keeping any argument that deliberately differs from it. Your guard on the built middleware keeps
proving the consequence either way.

## 2. The harness's prose no longer speaks RuleWright (PS-43)

The harness's docstrings, comments and the reason strings in `NOT_ON_WORKERS`, `WITHHELD_FROM_CHAT` and
`NEVER_BRIDGED` no longer cite RuleWright, its task, requirement or ADR ids, `AgentContext`, `for_tenant` or your
tool names. The keys of those three dicts are unchanged and every reason is still longer than 40 characters, so
your tests on them hold. If a test of yours asserts on the exact text of a harness message (the filesystem rule's
detector now says "against the subject documents"; the memory refusal no longer cites `AR-6`), update it. The
engine now guards this (`test_THE_SUBSTRATE_SPEAKS_NO_PRODUCTS_LANGUAGE`).

## 3. The harness has a design reference: `docs/harness.md` (PS-43)

Your 2026-10-15 draft, taken in: the prose follows your eight sections, and the tables are generated from the
installed tier by `scripts/build_harness_doc.py` (adapted from your `draft_harness_design_doc.py`), with a drift
test. You can retire your script and point at the engine's document. The `building-an-agent-harness` skill now
says fifteen fields, not four.

## Verify

Your fast and db suites green on `>=0.12.0`; the worker-middleware guard green after item 1; bump the
floor and `engine-state.toml`'s pin (the 42 harness names are unchanged).
