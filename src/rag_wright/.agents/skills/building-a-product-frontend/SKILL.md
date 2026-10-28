---
name: building-a-product-frontend
description: The architecture and build ORDER for a product front end over a retrieval/agent engine - the core/product seam, the shell's regions (rail, main, detail, a reserved run row), generating the API client from the schema, the URL as state of record, one data-access layer, and the three test tiers in the order you need them. Load at the START of a new product's UI, and before changing the shape of an existing one. Craft (composition, a11y, states) is frontend-ui-engineering, which still applies.
---

# Building a product front end over an engine

Repository paths in this skill (`docs/`, `src/`) are in the engine repository: read them there or on GitHub, at the tag matching your installed engine.

> **This skill was distilled from a shipped product and is written for a DIFFERENT one.** It ships in the
> engine wheel; link it into your repo's `.claude/skills/` (`docs/installation.md`). Its companion craft skill,
> `frontend-ui-engineering`, is one of the shared way-of-working skills. The agent surface it ends with is built
> on the engine's `rag_wright.harness` tier (`building-an-agent-harness`).
>
> **It is PRODUCT-AGNOSTIC on purpose, and every number in it is measured.** Each decision below was paid
> for once, by shipping; the measurements are kept because they are why anybody follows the rule. What is
> specific to a single codebase -- its locked choices, its primitives, its paths -- belongs in a
> project-scoped child skill of your own, never in this file. The last section says what to put there.
>
> **It is the ARCHITECTURE skill, not the craft skill.** `frontend-ui-engineering` covers composition,
> accessibility, spacing, states, and the ways UI defects hide from a green suite. Load both. Where that
> skill already says something, this one points at it rather than repeating it.

## The shape every one of these products has

A front end over a document-retrieval-plus-agent engine is, every time, the same six things:

1. **Identity**: password and federated sign-in, one session, email verification, password reset.
2. **Tenancy**: an account, workspaces inside it, membership and roles, deny-by-default authorization.
3. **A corpus**: upload, ingestion progress, a library of what has been ingested, a per-item detail view
   with the source rendered and its extracted records beside it.
4. **Deterministic operations**: menu-driven, parameterised, reached by a click. Each returns a result that
   cites its evidence.
5. **An agent surface**: chat that can pick up from a deterministic result, ask for more, take feedback, and
   answer open questions.
6. **Housekeeping**: settings, members, usage and spend.

**What changes between products is the entities and the operations. The structure around them does not.**
That is why this skill exists, and it is also the exact limit of what can be shared: see the seam below.

## Decision 1: two directories, and the dependency arrow points one way

```
src/core/      knows NOTHING about the domain. Shell, layout, tables, auth pages, theme,
               data-access plumbing, settings. The thing you carry to the NEXT product.
src/product/   the domain. Entity names, the operation surfaces, the copy, the registries.
```

**The core reads registries the product populates.** Navigation is the first one: the product declares its
surfaces, and ONE entry carries both the route and the navigation, so routes and menu cannot drift into two
lists: `path`, the `element`, and an optional `nav` of `label` and `end`. **`end` is the field that governs
correct highlighting**: without it a parent path (`/intake`) also matches its child (`/intake/saved`) and the
parent stays lit while the child is open. A surface with no `nav` is routed but not advertised (reached from
an account badge, or a link handed out after a job). The shell renders whatever it is given. The core never
imports from the product; the product imports from the core.

**Enforce it with a scan, not with intent.** A test that greps the core directory for the domain's own words
-- comments and docstrings included -- and fails on any hit. Two properties make it work:

- **Comments count.** A core component commented "this is where the invoice total goes" is coupled; the next
  person writes code to match the comment.
- **Strip paths and references before scanning**, or a legitimate pointer to a domain document trips it and
  somebody adds an exemption, and then the exemption list is the guard.

Write this test with the second directory, on the first day. Retrofitting it means auditing everything.

### The seam is a discipline, NOT a framework

**Do not build a plugin loader, dynamic module registration, a theming engine, or config-driven forms.**
Adaptation is **import-then-customize**: the next product copies or depends on the core, declares its own
registries, and writes its own result surfaces.

This is the most tempting decision to get wrong, so here is the reason in full. A result surface looks
generic -- read a parameter from the URL, run a query, render loading/error/empty, group the rows, cite each
one. Most of it is. But a small number of branches in those pages encode **domain truths, not layout**:

- *"Not found is not the same as none."* An unrecognised name must not render like a clean, empty result:
  one says "we have nothing on this", the other says "you are exposed to nothing", and only one of them is
  true. A typo must never read as an all-clear.
- *"These two groups must never be merged."* A parent organisation and its subsidiary are different parties;
  pooling their records into one total misstates who is responsible for what. The separation is a fact about
  the world, enforced in the rendering.

A configuration schema has two options for a rule like that. It cannot express it -- and the generated page
is plausible, well laid out, and wrong, which is the most expensive kind of wrong in a product people make
decisions from. Or it can express it, and the configuration is now a programming language with no type
checker, no tests and no reviewer. **Generalise the shell, the plumbing and the primitives; write the result
surfaces.** They are the cheapest part and the part that carries the meaning.

## Decision 2: the shell has regions, and they follow from the shape

The six things above are functional, but three of them fix the frame's geometry. Draw it before the first
surface:

```
  rail  |  main    detail
  rail  |  run     run
```

- **`main | detail`** is item 3: a per-item view with the source rendered and its extracted records **beside**
  it. The detail panel is a region of the SHELL, not a page's private layout, so every surface that selects
  something shows it in the same place.
- **The rail, not a top bar**, is item 2: workspace switching, the library and the account are persistent
  chrome that **grows with the corpus**, and **a horizontal bar cannot grow.** Full height, beside the content.
- **The run docked BELOW, spanning `main` and `detail`**, is item 5: the agent picks up from a deterministic
  result, so a run must hold a conversation **while the surface it is about stays visible**, and must be able to
  put results into `main` and `detail`, not only into its own transcript. A run that is a mode of `main` fails
  both. A run in its own column fails on width: tool calls need room, and **rail + main + detail + run as four
  columns does not fit at 1440px.** Below, it is a sibling of both.
- **Reserve the run row from the first commit, though nothing renders there.** One grid line; it buys that no
  surface is written assuming nothing can ever dock below it. The regions themselves are a per-product decision
  to record in your child skill; the argument for them is not.

**Mark the current surface, by more than colour, and guard it.** The current navigation item carries
`aria-current="page"` (a router's `NavLink` sets it; a plain `Link` never does), and its visual echo is not a
colour swap alone (a border, a weight), because colour alone is the half of a contrast requirement a palette
cannot satisfy. The guard is a jsdom test: *the current surface has `aria-current="page"`, and a parent with
`end` does not while its child is open.*

**Start from the scaffold; a scan cannot produce a layout.** Every other rule here is checkable by a source
scan; the frame is the one decision a scan cannot make, so it is the one that defaults to the framework's
`<header><nav>` above `<main>`. This skill ships the frame as three files in its own folder, `scaffold/` beside
this file, to copy into your core directory (React, `react-router`, Tailwind with shadcn token names; the tokens
are yours, the structure is the point):

- `scaffold/surfaces.tsx`: the registry type (`path`, `element`, `nav: { label, end }`) and `navigable`
- `scaffold/ShellLayout.tsx`: the frame -- skip link, the rail (`complementary`, named) holding a named `nav`,
  `main` as its own scroll container, `detail` rendered only when something fills it (an empty landmark sends a
  screen-reader user nowhere), the run row reserved
- `scaffold/ShellLayout.test.tsx`: the jsdom guards -- the current surface, `end`, the rail, one `main`, no
  empty detail

Two things only the browser tier can assert, so assert them there: **the detail panel is BESIDE `main`, not
under it** (the grid declares a third column only when `detail` exists; with two columns declared, the panel
auto-places into the run row, full width under `main`, and jsdom computes no grid to notice), and **`main` is
wider than the panel beside it** (the panel's width is capped; uncapped, it measured 608px against `main`'s 576px
at 1440px). Copy, then customise. **Do not make the shell configurable**: Decision 1's argument against
config-driven surfaces applies unchanged one level up.

## Decision 3: GENERATE the API client from the schema

If the backend publishes a machine-readable schema -- and a modern Python or TypeScript backend does, for
free -- **the client is generated, never hand-mirrored.**

Measured, on the product this skill comes from: a hand-written client reached **1,154 lines, 87 exports and
24 endpoint string literals**, and its own header comment admitted the schema was the real source of truth
"if they ever drift". They had already drifted: a comment two-thirds down recorded that the field names
there had gone stale behind the backend **twice**.

The discipline that makes generation work in practice:

- **Commit the generated file.** A diff in a pull request is how a breaking backend change becomes visible
  instead of a runtime surprise.
- **Regenerate in CI and fail on a dirty tree.** Otherwise the generated file is a hand-written file with
  extra steps.
- **Hand-write only the thin layer above it**: auth headers, error normalisation, the query-key factory.
- **Change the client type in the SAME commit as the backend type.** A field that became optional while the
  client still types it as required is a dereference that survives a full green suite and a clean build.

## Decision 4: the URL is the state of record

Page, filters, sort, selection, which panel is open, which tab is active -- all of it in the URL, none of it
in component state.

Three reasons, and the third is the one people forget: it is shareable, the back button is correct by
construction, and **an agent can hand the user a link to what it just did**. In a product where a chat turn
and a click are both first-class ways to reach a surface, state the agent cannot address is state the agent
cannot hand back.

**Write the guard when the first consumer lands.** Deferring it is reasonable while there is nothing to
guard; what fails is the deferral with a named trigger that nobody checks. If you defer, write the trigger
down where the next person will look, not only in the ticket.

## Decision 5: one data-access layer, two guards

A query-key factory is the only place keys are built; no component calls `fetch` directly. Both guards are a
source scan, both take minutes, and both are provable by planting their violation:

- no inline key array outside the factory
- no `fetch(` in a component

A library's own `refetch()` goes *through* the layer and is the opposite of a violation -- make sure the
guard knows the difference, or it will be disabled by the first person it annoys.

## Decision 6: one table primitive, and the allow-list that must not grow

Build the table once, and allow a raw `<table>` element in exactly that one file. Everything else that needs
a table uses the primitive; if the primitive cannot do it, extend the primitive.

**Adding a file to the allow-list without it BEING the primitive is how a rule becomes an exemption list.**
The guard should make that edit conspicuous: one line, in a file whose name says what it is for.

Make pagination props optional. An unpaginated listing that renders "N rows" is better than a pager over a
single page -- and see the arithmetic warning below before you let a page size anywhere near a count.

## The three test tiers, and the ORDER is the lesson

| tier | answers | structurally blind to |
|---|---|---|
| **unit / jsdom** | does this component behave? | **anything visual -- jsdom computes no CSS** |
| **browser, against the BUILT artifact** | does what we already know to check still hold? | anything nobody thought to assert |
| **a person or agent looking at it** | **what did nobody think to check?** | regressions -- it runs once |

`frontend-ui-engineering` has the detail on what each tier cannot see and how to pick between them. What
belongs here is the **sequencing**, because this is the thing a new product gets wrong:

**Stand up the browser tier before your second surface, not after your ninth.** Adopted late, it arrives as
a backlog of surfaces nobody has ever watched render -- and the first run finds defects on all of them at
once, in a batch too large to attribute to the change that caused each one. Measured on the product this
comes from: once the discovery tier existed, it found a real defect on **every single surface built**, and
the majority were invisible to every automated tier in the repo -- an invisible sign-in button, a duplicated
main landmark, every control under the minimum hit area, a document rendered at a third of its intended
size, two adjacent controls reading as one, a content region narrower than the panel beside it, and a white
screen on a page whose component tests were entirely green.

**The browser tier runs against the production build**, not the dev server. The server harness for it:

- its **own** database, created and migrated for the run -- never the development database
- the test account provisioned through the **real** provisioning command, so the path under test is the path
  users take
- a **build**, then serve the build the way production serves it
- **rebuild on every run**, or a planted source change never reaches the browser and a guard you are proving
  cannot fail
- **kill any hand-started dev server first.** A harness that reuses an existing server will serve a stale
  build, and the fix you just made appears to fail

Two traps worth naming in advance: a production build does **not** double-invoke effects, so anything that
only manifests under development-mode strict rendering belongs in the jsdom tier and could never fail in the
browser; and an accessibility sweep over *mocked* pages never renders the real shell, which is how nested
landmarks survive indefinitely.

**Drive the browser by element reference. Never by screen coordinates.** Locator-first frameworks make the
mistake inexpressible; a browser-automation plugin will happily take a pixel, and nothing but this rule
stops you. A stale reference is the signal to re-read the page, never to reach for coordinates. And after a
click that navigates or submits, wait and confirm before the next step -- racing a sign-in request with a
navigation makes a route guard bounce it, which looks exactly like broken authentication.

**Anything the discovery tier finds becomes a browser test in the same change.** A finding left as an
anecdote is a defect that comes back; the repeated pattern is a human finding the same fault three times
because no run of it ever became an assertion.

## Rules that each cost a real defect, in portable form

**A feature ends at the surface.** A backend change whose UI lands "next task" does not land. Ship the
surface in the same commit, and check what neighbouring commits touched before calling a task done. A
structural guard helps -- a new table with no route fails -- but nothing enforces "and a person can reach
it" except looking.

**A failed load must never render as an empty result.** "You have none" and "we could not read what you
have" are different claims, and in a product people act on, one of them is a false assurance. Same shape
everywhere: "no results" versus "no such record"; "not retained" versus "retention expired".

**Every data surface defines loading, empty AND error.** Guard it by scanning for the three states, and make
any exemption a written line in the file that says why -- a surface addressed by an id has no "no results"
case. A silent exemption is how "every data surface" becomes "most of them".

**A page size is a presentation choice and must never become an arithmetic one.** A list capped at 100
feeding a coverage numerator reports "100 of 100 processed" for a 500-item job. If a number means coverage,
it comes from the count, never from the page.

**Never present text the user did not write as theirs.** An assembled excerpt shown under "in your document"
attributes words to somebody in an order they never used. If the backend labels which spans are verbatim and
which are reconstructed, read the label instead of guessing, and never invent a filename for pasted input.

**Hit areas: measure them, do not eyeball them.** Every control in the product this comes from was under the
minimum until one was measured in a real browser. A text input is its own target and cannot borrow an
adjacent button's padding.

**A list the UI owns is a capability the UI will silently lack**, and **any guard with a hand-maintained list
goes stale silently**. Both are in `frontend-ui-engineering` with the mechanics; they are listed here because
they bite hardest in exactly this kind of product, where the backend's capability set grows every week.

## The build order for a new product

Doing these out of order is what costs the rework:

1. **Scaffold, then the seam.** Two directories and the vocabulary scan over the core, before the second
   component exists.
2. **Generate the client.** Before any surface. Everything downstream types against it.
3. **The data-access layer and its two guards.** Cheap now, an audit later.
4. **The shell: the regions (Decision 2, from the scaffold), the navigation registry, theme tokens.** Before
   any surface hangs on it, or the surfaces invent minimal chrome and the shell step reads as already done.
   Resist centring the content region, since a detail panel sits beside it -- make each surface constrain its
   own measure, and assert it.
5. **Identity and tenancy surfaces.** They are the most portable code you will write and they exercise the
   shell, routing and the session end to end.
6. **The table primitive**, with its allow-list guard, before the second listing.
7. **The browser-tier harness** -- own database, real provisioning, build, serve, rebuild per run.
8. **The first corpus surface** (upload, progress, library, detail). This is where ingestion contracts get
   real.
9. **One deterministic operation end to end**, with citations, through all three tiers. Look at it in a
   browser before calling it done.
10. **Then the rest of the operations.** By now the shape is set and each one is cheap.
11. **The agent surface last**, because it needs the operations to exist and the URL state to be addressable.

## What goes in your project's own child skill

This skill carries what is true of any product of this shape. Put in the project-scoped child the things a
reader cannot derive:

- your locked decisions, with what a violation looks like, and the fact that changing one needs a recorded
  decision rather than a judgement call
- your primitives, by path: the table, the shell, the query-key factory, the states helper
- which regions your shell has and what fills `detail` and the run row, as a locked decision
- the commands: how to run each tier, where specs live, what the e2e server script does
- the defects that have actually recurred in **your** codebase, with the evidence that makes them stick
- current debts: a guard that was deferred, a decision made but not yet built

Keep that file in front of the person *before* they write code, not in review. A decision recorded only in
an architecture document is a decision that gets violated with the document already in place.

## Verification checklist

- [ ] The core directory has a vocabulary scan over it, comments included, and it passes
- [ ] Nothing in the core imports from the product
- [ ] The API client is generated from the published schema, committed, and regenerated in CI
- [ ] Any client type mirroring a backend contract changed in the SAME commit as the backend
- [ ] The regions (rail, main, detail, the reserved run row) are the SHELL's, not a page's own layout
- [ ] The current surface carries `aria-current="page"` and is marked by more than colour; a parent with `end`
      is not lit while its child is open
- [ ] Navigation-shaped state is in the URL, not component state
- [ ] Reads go through the query-key factory; no `fetch(` in a component
- [ ] No raw table outside the primitive; the allow-list did not grow
- [ ] Every data surface has loading, empty and error, or a written exemption saying why
- [ ] A failed load renders as a failure, never as an empty result
- [ ] Anything visual is asserted in a browser against the BUILT artifact, not in jsdom
- [ ] The guard for whatever changed was proved by planting its violation and watching it fail
- [ ] A new or substantially changed surface was OPENED and looked at, and anything found became a browser
      test in the same change
- [ ] No screen-coordinate interaction anywhere in the change or its tests
- [ ] The backend change this belongs to ships its surface in the same commit
