---
name: building-a-product-backend
description: The architecture of a product backend over a retrieval engine - the four units and the one dependency law, the placement test for every new operation, the operation layer that gives a route and an agent one implementation, tenancy that cannot be forgotten, jobs as a queue of items, and the guards that make each of these fail loudly instead of drifting. Load at the START of a new product's backend and before adding any route, operation, table or engine call. Pairs with building-a-product-frontend and building-an-agent-harness.
---

# Building a product backend over an engine

Repository paths in this skill (`docs/`, `src/`) are in the engine repository: read them there or on GitHub, at the tag matching your installed engine.

> **This skill was distilled from a shipped backend and is written for a DIFFERENT product.** It ships in the
> engine wheel; link it into your repo's `.claude/skills/` (`docs/installation.md`).
>
> **Every rule below was paid for**, and the measurements are kept because they are why anybody follows the
> rule. Its two companions cover the other surfaces: `building-a-product-frontend` and
> `building-an-agent-harness`.
>
> **The one sentence.** Your backend is not thin HTTP over engine calls. It is where **product policy**
> lives, and almost every decision below is about making sure that policy has exactly one home and cannot
> be re-derived, forgotten or bypassed.

## Four units, one dependency law

| unit | owns |
|---|---|
| **engine** | capabilities, the ontology machinery, the generic ingestion and query primitives, the capability registry |
| **agent harness** | the loop, memory, delegation, interrupts, deadlines, run grading: the engine's `rag_wright.harness` tier |
| **UI core** | shell, workspaces, users, roles, the chat surface, the table and state primitives |
| **product** | your domain pack (`.ttl` + capabilities), the routes for your deterministic use cases, the operations behind them, the agent's definition (tools, workers, prompts, rules) |

**Product -> Harness -> Engine, and Product -> Engine. Never upward.** The engine and its harness tier enforce
their half with import contracts; your half is a boundary test in your repo: product code imports only
`rag_wright.api`, `rag_wright.pack_sdk` (pack code) and `rag_wright.harness` (agent code).

**Fix the UI core / product boundary inside your repo on day one**, so a later split is a move rather than an
untangling. The failure mode is "keep it in mind as a requirement": in the product this comes from, zero of
seventeen architecture decisions governed the front end, its conventions lived in recall, and it drifted. **A
convention is only enforced when violating it fails loudly.**

## Where your domain's pipelines live: your pack, on the engine's primitives

The engine is domain-neutral. It ships three kinds of thing, and a new domain uses each differently:

| kind | examples | what you do |
|---|---|---|
| **mechanism** | the store, model profiles, tracing, encoders, the registry, document parsing | use it as is |
| **generic primitives** | `build_ingestion` and its hooks, hybrid search, graph query, answer generation, span judging, the decision model | compose them |
| **the reference pack** | a contract-ingestion pipeline, a compliance check | read it as a worked example; never your domain |

**Your domain's composites are your pack's capabilities.** An ingestion pipeline for your documents is
`build_ingestion` plus your extractor and hooks; a query workflow is your capability composing the engine's
primitives; both are registered with the engine (`register_capability`, `load_pack`) so a route, an agent and
an eval all invoke them by name. The `authoring-a-capability` and `building-an-ingestion-capability` skills
carry the how.

**Do not re-implement a primitive, and do not reach past the public tiers for one.** The measured reason: a
domain pipeline that imported 21 engine internals broke at runtime, on a customer's corpus, when the engine
removed ONE of them, while the product's suite reported 644 passed, 1 failed (a monkeypatch test). Through the
public tiers that change is a named diff in your surface snapshot instead.

**When a primitive does not fit, that is an engine request, not a fork.** Write it in your
`docs/engine-requests/` with the evidence, and meanwhile drop to the next primitive down in your own
capability; never copy engine code into the product.

**IP exposure is a packaging question, not a placement one.** A domain workflow that must stay proprietary is
your closed pack, depending on the open engine; it never needs the engine to know about it.

## The placement test, for every new user operation

> **Product policy -> product. Use-case shape -> engine.**
>
> - Does it depend on **tenancy, roles, workspace selection, billing, or a UI or agent affordance**?
>   **Product.**
> - Is it domain knowledge or a domain pipeline (vocabulary, schema, extraction, a typed query)? **Your
>   pack**: the `.ttl` plus capabilities registered with the engine, invoked by name.
> - Would any product of any domain reuse it unchanged, and does no public primitive cover it? **An engine
>   request.**

**Tenancy is the sharpest discriminator.** The engine owns the mechanism (a store, a document filter); the
product supplies the values (which store, and which documents this workspace selected).

Apply it in both directions and trust the answer. Grading, coverage, corpus-wide sweeps, export and named
conditions all stayed in the product here, because **what counts as a match is a product judgement** and
burying it in the seam would have made it unreviewable. Same test, opposite answer, and it was right.

## The operation layer: one implementation, two callers

**This is the most important decision in this skill.** You have two drivers - a click and an agent turn -
and they must be the same code.

| layer | holds |
|---|---|
| `workflows/` (primitives) | `grade`, `coverage`, `search`, `correct`, `export_*`. **Pure, tenancy-free, unit-testable.** |
| **`operations/`** | **composition and policy.** One function per user operation, taking a tenant and scope context, returning a typed result. |
| `api/routes/` | **HTTP only.** Parse, call the operation, serialize, status codes. |

**The route calls the operation. The agent's tool IS the same operation.** Identical output by construction
rather than by discipline.

**The measurement that forces it.** One route here was 76 lines composing SIX steps: canonicalise the inputs,
run the search against the tenant's graph, **grade** the results against product policy, resolve display
names scoped to the customer, compute **coverage over what was SUBMITTED rather than what retrieval
returned**, and assemble. An agent calling the underlying primitive would get raw results and have to
re-derive four of those - so **the same question would return a different answer depending on whether the
user clicked or typed, and the chat answer would be the one with no grading policy behind it.**

**Enforce it:** a route module may not import the primitives. It reaches them only through an operation.
That one import rule is what makes the layer real.

**Make the operations a registry, and prove the two callers agree.** Give each operation an id, a version, typed
parameters (with the sources of their dropdown values), a result shape and whether it writes; the UI's menu and the
agent's tool surface are both built from it. Then a **parity test** invokes each read-only operation both ways (the
route, the agent's tool) on fixed data and asserts identical results, and a second test asserts no operation that
writes is on the agent's tool surface unless the spec says so: a store write reached from chat is a write no person
confirmed.

## The API is the product, so make the claim CHECKABLE

Saying "the API is the product" changes nothing. Two ratchets do:

1. **Every table has a route.** A data model with no way to reach it is a feature that does not exist.
2. **Every route in the published schema is named by at least one test**, with an **empty** exemption map and
   exact equality.

**Write the second one carefully, because the first draft of ours was vacuous.** It scanned every test file
including itself, and its own docstring named the one untested route - so it passed on exactly the broken
surface it was built to catch. **A guard satisfiable by its own prose proves nothing.** Exclude the guard's
own file, and prove it with an **assembled** sentinel path so the file's text cannot satisfy its own scan.

Fixed, it immediately found a second untested route. And the untested route it was written for had been
returning **500 on every real request for five tasks of green suites**: the pieces either side had fifteen
tests and three tests respectively. **What nobody tested was the wiring between them**, which is all a
handler is.

## Tenancy you cannot forget

**Derive the per-customer store name; never store one.** A pure function of the tenancy key. Ids are unique,
so two customers cannot produce one name, and there is **no stored value to go stale, be copied between
environments, or be left empty**. Route every resolution through **one choke point**.

**If you support a per-customer override, make the column UNIQUE.** Then two customers pointing at one store
is impossible rather than unlikely. (And any test that sets such an override must release it on teardown, or
the next run fails on the constraint rather than on anything it was testing.)

**Deny by default, as a database fact.** Membership is a row in a link table and its absence is a refusal.
Give it a **composite primary key** so "one membership per user per workspace" is enforced by the database
rather than by an `if not already_a_member` somebody has to remember.

### The corpus belongs to the CUSTOMER; a workspace is a SELECTION over it

Ingestion puts an item in the customer's corpus **once**. Membership in a workspace is a separate
many-to-many fact: one item may be selected into several workspaces.

(A single-tenant product with one shared corpus still benefits from the selection model the day a second
team or project arrives; if you defer it, record the decision.)

**This is the decision a new product most often gets wrong**, by attaching documents to a workspace directly.
Get it right and re-use costs nothing, scoping is one join, and "the same document in two workspaces" is not a
duplicate ingest.

### Resolve the scope INSIDE the operation, never take it as an argument

The operation asks the store which items this workspace may search and passes that list into every query. A
caller that forgot would query the whole customer's corpus **with nothing on screen to say so**.

**And short-circuit an empty selection before touching the engine.** This is a correctness fix, not an
optimisation: found by opening the page in a browser, a workspace with nothing ingested returned **HTTP
500**, because the first thing a graph traversal touches is a type that a database nobody has ingested into
does not have. So the **first thing a new customer sees** failed with "Internal Server Error". Short-circuit
in the product rather than catching the engine's error: an empty selection is a legitimate state you already
know about, and swallowing a store exception would also hide a genuinely broken store.

**No route may take a client-supplied handle without scoping it**, and the durable half is a guard that
proves the *next* route will too. A test that two routes call the check is worth much less than a test that
every route must.

## Jobs: product-owned, and the unit of queuing is ONE ITEM

The product owns the job lifecycle end to end: the record, its status, stage, counts and tenancy.

**A bulk operation is not one row.** Make it a **parent** coordinator row that processes nothing and holds
the batch outcome, plus **N child** rows, each carrying one item's storage key and a foreign key back to the
parent. One row per batch cannot be resumed, cannot report per-item progress, and cannot be retried
selectively.

**A lost item is its VERDICT, not its job status.** Define one predicate - with a SQL twin so the API and the
database agree - for "this item was lost": the job failed, **or** the work's own verdict says it failed.
**A partial result is not a loss**, and that boundary belongs to the engine's own outcome type: a partially
ingested item is written and can be queried, it simply lost some extractions, and calling it a failure offers
a retry that re-ingests something already in the graph.

**Lease fields go in typed columns**, because the queue filters and orders on them.

## The data model: typed columns for the spine, JSONB for the volatile

Decide per field with a stated test rather than by taste. **A typed column** if any of these holds: the store
**filters, orders, joins or aggregates** on it; it must be **constrained** (not-null, unique, a foreign key, a
bounded set). **JSONB** for the rest - payloads, reports, volatile nested shapes.

**Then walk the metadata in a test**, so the principle cannot be violated accidentally. A principle nobody
*can* break beats one everybody has to remember.

### Two schemas, one database, one owner each

If a framework creates its own tables (a checkpointer, a queue, a cache), **give it its own schema**.

The failure otherwise is severe and quiet: your migration autogenerator walks your models, finds tables in
its schema with no model behind them, and writes a `drop_table` for each. A routine migration then **deletes
every suspended run**. In a separate schema they are simply not reflected.

## Identity: two paths, one session

Federated sign-in and password sign-in converge on **one server-side, revocable session record**, with a
token in an HttpOnly cookie. Two paths, one session, one revocation story.

- **A signup records an ATTEMPT; confirming the emailed token is what provisions the account.** Hold the
  pending signup with an already-hashed password and its own hashed, single-use, short-lived token.
- **Consume every single-use token atomically**: one `UPDATE ... WHERE used_at IS NULL RETURNING`. Not a read
  then a write.
- **A federated callback that reads an email and issues a session IS an account-takeover primitive** unless
  something asserts the person owns that address. The provider's verified-email claim is the only thing that
  does. Gate linking on it.

**Uploads go client-to-storage through a signed URL.** The API issues the URL and handles control traffic
only; bytes never pass through your process. It removes a whole class of timeout, memory and size problems
before they exist.

## Policy stays in the product, even when the engine could hold it

The pattern to copy: **the engine reports the FACT, the product decides the GROUPING.** The engine judges
each result against a condition and returns a verdict with a rationale and a confidence; the product decides
which verdicts count as matched, possible or not-found, and what that means for the user.

Keep it in the product and it is reviewable, testable and changeable per product. Bury it in the seam and
nobody can see what the number meant.

**Related: do not cache what the layer below already caches correctly.** Read the engine's caching before
adding yours. A second cache is a second answer to one question, which is the failure the single-home rule
above exists to prevent.

## The seam, and reaching for an internal honestly

One module is the engine's door. Import the engine's **public API** and nothing deeper; assert it with a
boundary test.

When you genuinely need something the public tiers do not carry yet, **write the engine request** and keep
your interim on the public surface (a coarser primitive, a product-side composition), recorded with the
request it waits on, and a ratchet that **fails the moment the engine publishes it**. Then adoption is a
*deletion* - drop the fallback, drop the line - rather than something to remember. Never import an engine
internal to bridge the gap. Also snapshot the engine's public surface (`rag_wright.api.__all__`, and the
`pack_sdk` and `harness` tiers you use) so drift is a named diff rather than a mystery failure three layers
into a call site.

**And a moved engine is normal.** Guard the thing that can reach you - the published surface - not the
commit. A guard that fires on every unrelated release is a guard people learn to ignore.

## Testing a product over an engine

**Put a LIVE test on every engine seam.** Fakes prove your mapping; only a live run proves the integration.
The measurement: of four defects in one seam task, **three were invisible to unit tests**, and the worst - a
model of the wrong type passed where another was required - failed as an **opaque error inside the engine's
own guard**, so the fast suite stayed green while the integration was broken.

**Keep the expensive markers out of the default run, and then actually run them.** Three tests here sat
broken across three tasks because a retrieval signature changed while the default suite - which deselects
the database-marked tests - kept reporting green. **Run the database-marked suite before committing anything
that touches a route, a shared fixture or the seam.**

**Separate "needs a database" from "spends money".** Tests carrying both markers perform real ingests and
spend real credits on every run. Exclude the paying ones by default and run them deliberately.

**Salt a live ingest fixture.** Fixed bytes pin a live test to a stale artefact from a previous engine
version, which here cost three wrong conclusions in one day.

**And never run the browser suite against the same database as the integration suite.** There is one queue:
the browser harness starts a real worker, the worker claims the integration tests' jobs, and three of them
fail with the correct code in front of you.

## Build order

1. **The relational store and the migration tool**, with the schema split decided up front.
2. **The data-model guard** that walks your metadata. It is cheap now and an audit later.
3. **Identity and the session**, both paths, with the single-use token consumed atomically.
4. **Tenancy**: the derived name, the one choke point, membership deny-by-default.
5. **The seam**, the public-surface snapshot and the boundary test.
6. **The customer corpus and the workspace-as-selection tables.** Before any ingest.
7. **Jobs**: parent and child rows, leases in typed columns, the lost-item predicate.
8. **Uploads via signed URLs.**
9. **One operation end to end** - primitive, operation, route - and the import rule that keeps routes out of
   the primitives.
10. **The two API ratchets**, each proved by planting its violation.
11. **A live seam test**, before you trust any of the above.
12. **Then the rest of the operations.** The shape is set and each one is cheap.

## What goes in your project's own child skill

- your placement-test decisions, by operation, with the answer and the reason
- your seam's interim list and the engine gaps each waits on
- the commands: migrations, the marked suites, the live seam test, provisioning an account
- the defects that recurred in **your** backend, with the evidence for each
- which policy decisions are deliberately the product's, so nobody "simplifies" one into the seam

## Verification checklist

- [ ] Product code imports the engine only through `rag_wright.api`, `rag_wright.pack_sdk` and `rag_wright.harness` (tested)
- [ ] No route module imports a primitive; it reaches them through an operation (tested)
- [ ] Each read-only operation returns the same result from the route and from the agent's tool (a parity test)
- [ ] No store-writing operation is on the agent's tool surface unless the spec says so (tested)
- [ ] Every table is reachable by a route (tested)
- [ ] Every route in the published schema is named by a test, exemption map EMPTY, and the guard excludes
      its own file
- [ ] The per-customer store name is derived, not stored, and resolved in one place
- [ ] Any per-customer override column is UNIQUE
- [ ] Membership absence is a refusal, with a composite primary key behind it
- [ ] Every operation resolves its own scope and short-circuits an empty selection before the engine
- [ ] No route accepts a client-supplied handle without scoping it (tested for ALL routes, not two)
- [ ] Single-use tokens are consumed by one atomic statement
- [ ] Federated linking is gated on a verified-email claim
- [ ] Every field is in a typed column or JSONB by the stated test, and a guard walks the metadata
- [ ] Any framework-owned tables live in their own schema
- [ ] A partial result is not reported as a loss
- [ ] Every engine seam has a LIVE test
- [ ] The database-marked suite was run before committing a route, fixture or seam change
- [ ] Every new guard was proved by planting its violation and watching it fail
