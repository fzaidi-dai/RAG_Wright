# RuleWright and TexWright: moving to rag-wright 0.19

What 0.19.0 changes for a product built on the agent harness: the harness routes three decisions with its own
decision model (the harness-decisions workstream, HD-1..HD-10, ADR-0146; reference: `docs/harness.md` section 9).
Nothing here is edited from the engine repo.

**Nothing is required.** No name was removed or renamed, and every addition has a default. But behaviour changes on
upgrade, so read sections 1 and 2 before you bump the floor.

**TexWright:** nothing until you build on the harness (T-6.2). When you do, routing comes with it: read
`docs/harness.md` section 9 and section 3 below on writing descriptions, and decide section 2 for your deployment.

## 1. What you will see change

With the decision model's credentials present (`OPENROUTER_API_KEY`, which RuleWright already sets for its agent
model), the harness routes on its own. You register nothing; the harness registers and calls its decision model.

- **The chat-manager's turn (D1)** is routed on its opening call: a direct tool, `ask_the_user`, `delegate` or
  `answer`, at a probability of 0.7 or more. A direct tool that takes no arguments is called with no model call; a tool
  that takes arguments, `ask_the_user` or `delegate` is offered to your model alone, and on `qwen3.8-27b-modal-or` it is
  also forced (section 5); `answer` is written with no tools. Below 0.7 your chat-manager routes as before.
- **A delegated message that stands on its own is handed over verbatim (B1):** with no earlier turns, or when the
  decision model says at 0.6 or more that it can be understood without them, the brief is the person's own words with
  the `<viewing>` block, built in code. Otherwise your chat-manager writes it as before.
- **The orchestrator's workers (D2)** are chosen on its opening call, one yes/no per worker: no worker means its
  opening call runs without the hand-off; one worker is dispatched in code with the brief; several are planned by your
  orchestrator as before.
- **Without the credentials**, every tier behaves as in 0.18 and the process logs one warning
  (`decision_model_unavailable`).

Measured on the engine's routing gold sets (HD-8): D1 85.7% on four direct tools (your model alone 86.5%) and 92.2%
on twelve (90.0%); D2 90.0% per worker (75.6%). Each routing call costs about $0.00002 and under a second.

## 2. Decide: keep routing on, or turn it off

The person's message and the orchestrator's brief are now also sent to the decision model's provider (TypeSafe's
Jev, through OpenRouter), as your ingestion judge's inputs already are. If your deployment must not send them, turn
routing off on your definition, and nothing is sent:

```python
from rag_wright.harness import AgentDefinition

definition = AgentDefinition(..., routing=None)
```

A self-hosted deployment can keep routing on with an on-premises decision model instead (`register_decision_profile`
with your Laya server's profile).

## 3. If you keep it: your descriptions are the criteria

The decision model reads your direct tools' descriptions, your `prompts.orchestrator_description` (what delegating
means) and each worker's description. Re-read them as a classifier would: each must say what it is FOR, in words that
tell it apart from the others.

- **`orchestrator_description`** should say what needs planning (several documents, comparing, checking against a
  policy, several steps), not only what the orchestrator is. `delegate` is the weakest option on the engine's sets: a
  request that needs planning is mistaken for a direct read of the same subject.
- **Workers' descriptions** decide D2: one sentence each on the kind of request it serves.
- **Thresholds** are yours to change, after measuring on your own gold sets (`arun_eval`; the engine's own sets and
  scorer are in `tests/harness/gold/` as a worked example). Your recorded runs are the place to draw D1 and D2 items
  from:

```python
from rag_wright.harness import AgentDefinition, RoutingThresholds

definition = AgentDefinition(..., routing=RoutingThresholds(d1=0.8))
```

## 4. Your run ledger has more in it

- `D1` now carries the decision model's probability as `confidence` when its route settled the turn, and `route`; for
  a delegation, `brief_by` (`verbatim` or `model`) and `self_contained` (B1's probability).
- Each worker's `D2` row carries `decision_model_yes`, and the probability as `confidence` when the decision model
  dispatched that worker. A brief sent to no worker is a `D2` row `none`.
- **Your `the-brief-is-faithful` rule** can read `brief_by`: a `verbatim` brief is faithful by construction, so only
  `model` briefs need the judge.
- Your D1-D10 table (`agent/decision_points.py`): D1 and D2 now have a decider that returns a confidence, as only D5
  did before. B1 is the harness's own and not in your table.

## 5. Your agent model's profile

The engine's profile for `qwen3.8-27b-modal-or`, your agent model, now says it honours a forced tool choice
(`ModelProfile.forced_tool_choice=True`, measured: 16 of 16 forced calls, and the routed `ask_the_user` items took 0
replies of 18 against 4 unforced). Nothing to do. If you move to another model, it is not forced until measured: test
it and register its profile with `forced_tool_choice=True`. `profile_for(model_id)` (new on `rag_wright.api`) reads a
profile.

## 6. Your tests

**A hermetic test that builds a real tier will now try to call the decision model** if your test session loads
`OPENROUTER_API_KEY` (as `.env` does). Either declare `routing=None` on the definitions your tests build, or fail the
decision model's call in a fixture, which is what the engine's harness tests do (`tests/harness/conftest.py`):

```python
import pytest
import rag_wright.api


@pytest.fixture(autouse=True)
def no_decision_model(monkeypatch):
    invoke = rag_wright.api.ainvoke_model

    async def stub(name, inputs, **kwargs):
        if name == "jev_decision":
            raise RuntimeError("no decision model in hermetic tests")
        return await invoke(name, inputs, **kwargs)

    monkeypatch.setattr(rag_wright.api, "ainvoke_model", stub)
```

A test that scripts the chat-manager's first tool call is unaffected either way: with the decision model unavailable,
the turn is routed by your scripted model as before.

## 7. The skills that changed

Re-read if you link them: **`building-an-agent-harness`** -- its decisions section names the three routed decisions
and that there is nothing to register.

## Verify

Your fast suite green on `>=0.19.0` with section 6's fixture or `routing=None`; one live chat run with the credentials
set, and its ledger's `D1` carrying a `confidence`; then bump the floor.
