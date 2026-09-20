# Engine issue 0047: an exact `deepagents` pin transitively pins every consumer

**Raised by:** RuleWright (product), 2026-09-20, while upgrading the product's agent harness (Phase 5a).
**Severity:** not a defect. The pin is deliberate and well-reasoned; the issue is its *reach* — an `==`
in a library's dependency list is not a local decision, it is binding on everything downstream.
**Path:** `pyproject.toml`, `deepagents==0.6.12` (and `langchain-quickjs==0.3.2`), engine ADR-0015.

---

## What we see

The product declared `deepagents>=0.6.12` — a floor, not a pin — and asked uv to upgrade. Nothing moved.
`uv lock --upgrade-package deepagents` reported success and changed nothing, which reads like "already
latest". Forcing the version produced the real reason:

```
Because only rag-wright==0.1.0 is available and rag-wright==0.1.0 depends on deepagents==0.6.12,
we can conclude that all versions of rag-wright depend on deepagents==0.6.12.
And because your project depends on deepagents==0.7.15 and rag-wright, we can conclude that
your project's requirements are unsatisfiable.
```

So the product was held on `deepagents` 0.6.12 while 0.7.15 was current, and **the silent no-op is the
worst part**: a consumer that does not think to interrogate the resolver concludes its floor is being
honoured.

## Why this matters to us specifically

0.7 is where the middleware stack became *composable* rather than fixed, and the product's harness needs
exactly that:

| | 0.6.12 | 0.7.15 |
|---|---|---|
| Passing a `SubAgentMiddleware` to override the built-in | `AssertionError: Please remove duplicate middleware instances.` | replaces it in place by `.name` (measured: our `task_description` wins) |
| `TodoListMiddleware` | forced onto every agent | moved to `langchain.agents.middleware.todo`, opt-in |
| Assembled orchestrator system prompt | 24,620 chars | 17,919 chars, with guidance relocated into tool descriptions |

The first row is the blocking one. On 0.6.12 there is **no supported way** to replace the subagent
middleware's prompt or the `task` tool description; the documented `.name`-matching override is explicitly
`deepagents>=0.7`.

## What the pin is defending, and why we think a floor holds it

ADR-0015's reasoning is recorded in the pin itself — *"dynamic sub-agents are beta, floating = silent
breakage"* — and that is a real hazard, not a hypothetical one. We hit its cousin during the upgrade:
0.7 stopped auto-adding the todo middleware, which silently removed `write_todos` from our planning tier
and would have deleted a product surface had a guard not been watching for it.

The observation is that **the `==` did not prevent that class of breakage for us; a test did.** What the
`==` did prevent was the engine's *consumers* choosing their own upgrade schedule. The engine's own
exposure is small and bounded: two modules import `deepagents` (`capabilities/okf_navigate.py` and
`skills/rlm/agent.py`), neither of which sits on a path the product calls.

## This may reopen a design decision, not just move a version number

The most useful thing we can tell you is that the pin is not only holding back a number. `skills/rlm/agent.py`
records a design choice **caused by** the pinned version:

> a self-referential sub-agent is not constructible on the pinned `deepagents==0.6.12` (its
> `SubAgentMiddleware.__init__` compiles its roster eagerly, so a config whose roster contains itself
> recurses at construction). See ADR-0015 (Q2, corrected — design B') for the grounded reason and why
> interpreter-driven recursion is the faithful realization, not a workaround.

Interpreter-driven recursion was chosen because eager roster compilation made the direct form impossible.
**0.7 is precisely the release that reworked how subagent middleware is composed** — it is the version where
a caller-supplied `SubAgentMiddleware` merges with the built-in stack by `.name` instead of being rejected as
a duplicate, which is the behaviour we needed and could not have on 0.6.12 (see the table above).

We have not tested whether eager compilation still bites on 0.7.15, and it is not ours to test — the RLM skill
is engine code and design B' is an engine decision. But the constraint the decision rests on sits in the exact
area 0.7 changed, so it is worth re-checking the premise before assuming the ADR still holds. If it no longer
bites, ADR-0015 Q2 has a cheaper option available than it did when it was written; if it still bites, the ADR
gets a version-dated confirmation instead of an inherited assumption, which is worth something on its own.

## What we would like

Relax the exact pin to a floor with an upper bound the engine controls, e.g.:

```toml
"deepagents>=0.6.12,<0.8",   # beta; see ADR-0015 for why the ceiling exists
```

A ceiling still stops a surprise major from floating in, but lets a consumer move inside the range.
If the engine would rather hold `==` until it has validated 0.7 against `okf_navigate` and the RLM skill,
that is a legitimate call — in that case the ask is narrower: **say so in the ADR**, so the next consumer
reads "this pin is binding on you and here is when it lifts" instead of debugging a silent no-op.

## What the product did meanwhile

Added a uv override in the **product's** `pyproject.toml` (the engine repo is untouched, the one-way
boundary holds):

```toml
[tool.uv]
override-dependencies = ["deepagents>=0.7.15"]
```

This moves the product's environment only. It is a stopgap, and it carries a real cost worth naming: the
engine's two `deepagents` modules now import a version the engine has not validated inside the product's
venv. We accept that because neither is on a product path — measured, not assumed — but the override
should be deleted the moment the engine's own constraint allows the upgrade.

Product-side test evidence after the move: 1141 fast tests and 378 `-m "db and not ingest"` tests green.

**One upgrade hazard worth passing on, because a pin does not protect against it.** 0.7 deprecated
`BASE_AGENT_PROMPT` (*"Deep Agents no longer provides an authored base prompt"*, removal in 0.9) and stopped
auto-adding the todo middleware, which moved to `langchain.agents.middleware.todo`. The second one silently
removed `write_todos` from our planning tier — a tool one of our product surfaces renders — and every
structural test stayed green because the tool simply was not there to assert on. A behavioural guard caught
it, not the pin and not the type checker. Neither engine call site uses `write_todos`, so this is FYI rather
than a migration step, but it is the shape of breakage to expect from this particular bump.
