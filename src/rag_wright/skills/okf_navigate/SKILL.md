---
name: okf_navigate
description: >
  The embedding-free knowledge-navigation method: find the concepts in an Open Knowledge Format (OKF)
  bundle that answer a question by progressive disclosure, not by vector similarity. Keep the bundle in
  interpreter variables, read index signposts and frontmatter with tools, dispatch a selector sub-agent
  to choose which signposts to explore and use the judgeBodies tool to judge candidate bodies in parallel,
  and return the shortlist of concept ids. Applied by the okf_navigate capability (FR-K.6).
---

# OKF navigation: progressive disclosure over a knowledge bundle, without similarity

This skill teaches a **method**, not a behavior. An OKF bundle is a directory tree of markdown files
(one concept per file) with an `index.md` in each directory listing its entries with a one-line
description, and cross-links between related concepts. The bundle is large; you cannot load it all into
your context, and you must **never** compute an embedding similarity. You navigate it the way a person
skims a table of contents: read the signposts, follow the promising ones, read only the documents worth
reading.

## The method: interpreter holds the bundle → a workflow selects and reads via sub-agents

You do not see the bundle. You write a **workflow** in the `eval` tool that reads the bundle with `tools.*`
into interpreter variables (never into your context) and makes the two judgments that need a model:

1. **Selection.** For a directory, `tools.readIndex(...)` returns its signposts (each a `path`, a
   `description`, and `is_dir`). You cannot judge them yourself (they are interpreter data, not in your
   context), so you dispatch an **`okf_selector`** sub-agent with the list and it returns which to explore.
   Descend into chosen subdirectories; collect chosen concept files as candidates.
2. **Reading.** You judge candidate bodies with **`tools.judgeBodies({ rel_paths })`** — hand it a *batch* of
   candidate paths and it reads and judges them **in parallel**, returning one boolean per path (in order). A
   relevant concept's id goes on the shortlist, and you push its cross-links onto the frontier to expand.
   Reading is a tool, not a sub-agent, on purpose: the interpreter runs one JS engine, so dispatching readers
   with `task()` would serialize them; `tools.judgeBodies` fans them out in the tool instead.

The bundle stays in interpreter variables; the selector is only ever called (via `task()`) on a focused list
of signposts, and body judging happens inside `tools.judgeBodies`. The selector and the judge both already
know the question — you pass the selector only the signposts, and the judge only the batch of paths.

### The canonical workflow (write it this way)

**Your ONLY action is to emit this workflow to the `eval` tool in one call, then return its JSON result and
stop.** The bundle is reachable ONLY through the `tools.*` calls inside `eval` — there is NO filesystem, so
never call `ls`, `glob`, `read_file`, or `write_file`; they find nothing and waste the turn. Do not answer
from your own knowledge; the answer only comes from running the workflow.

Call the tools with an **object argument** exactly as their signatures show (for example
`tools.readIndex({ rel_dir: dir })`, `tools.judgeBodies({ rel_paths: batch })`). The `okf_selector` sub-agent
returns a JSON **string** in its text; `JSON.parse` it (extract the object with a `{ ... }` match first). Do
NOT pass a `responseSchema` to `task()` — forcing structured output makes a reasoning model reject the call;
the selector is instructed to reply with JSON, so parse its text. `tools.judgeBodies` returns a real array of
booleans (not text) — use it directly, no parsing.

```javascript
// Navigate an OKF bundle by progressive disclosure. The bundle is read via tools.* into interpreter
// variables, never into your context; a model is only ever called (task()) on a list of signposts or one
// document body. No query-to-document similarity is ever computed.
const BUDGET = await tools.frontierBudget();   // max document bodies to read
const shortlist = [];                          // concept ids judged relevant
const visited = new Set();                     // concept paths already read

// SELECTION: hand a list of signposts to the selector sub-agent; it returns the indices worth exploring.
// Send BOTH the name (`path`) and the `description`: a directory's description may be just a count, so the
// name carries the signal; a concept's description is its summary. The selector needs both to choose well.
async function pick(signposts) {
  const raw = await task({
    description: "Signposts to choose from:\n" +
      JSON.stringify(signposts.map((s, i) => ({ i, name: s.path, description: s.description, isDir: s.is_dir }))),
    subagentType: "okf_selector",
  });
  let keep = [];
  try { keep = JSON.parse(raw.match(/\{[\s\S]*\}/)[0]).keep || []; } catch (e) { keep = []; }
  return keep.filter((i) => Number.isInteger(i) && i >= 0 && i < signposts.length).map((i) => signposts[i]);
}

// Descend the tree to a depth bound. The selector gates DIRECTORIES (which subtrees are worth exploring),
// but once a directory is chosen, collect ALL of its concept files -- do not sub-select concepts. This
// maximizes recall: a chosen category's clauses are all read (the reader is the precision filter). A
// signpost's `path` is already resolved (root-relative) by the tool -- pass it straight to readIndex/readBody.
async function collect(dir, depth) {
  if (depth > 3) return [];
  const signposts = await tools.readIndex({ rel_dir: dir });
  if (!signposts.length) return [];
  const dirs = signposts.filter((s) => s.is_dir);
  const out = signposts.filter((s) => !s.is_dir).map((s) => s.path);  // ALL concepts in this directory
  if (dirs.length) {
    const chosenDirs = await pick(dirs);  // the selector chooses only which subdirectories to descend
    for (const d of chosenDirs) out.push(...(await collect(d.path, depth + 1)));
  }
  return out;
}

const frontier = await collect("", 0);
const log = [];            // one entry per body judged: { path, relevant } — the traversal's decision trace
const BATCH = 16;          // paths handed to judgeBodies at once; the tool judges them in parallel internally
// READING: hand batches of candidate paths to judgeBodies (it reads + judges them in PARALLEL and returns a
// boolean per path), keep the relevant, expand relevant concepts' links. Do NOT read/judge one body at a
// time -- that serializes the slow step. The tool owns the fan-out; you just batch and combine.
while (frontier.length && shortlist.length < BUDGET) {
  const batch = [];
  while (frontier.length && batch.length < BATCH) {
    const p = frontier.shift();
    if (!visited.has(p)) { visited.add(p); batch.push(p); }
  }
  if (!batch.length) break;
  const verdicts = await tools.judgeBodies({ rel_paths: batch });  // parallel [bool], one per path, in order
  for (let k = 0; k < batch.length && shortlist.length < BUDGET; k++) {
    log.push({ path: batch[k], relevant: verdicts[k] });
    if (verdicts[k]) {
      shortlist.push(await tools.conceptId({ rel_path: batch[k] }));
      for (const link of await tools.related({ rel_path: batch[k] })) frontier.push(link);  // lateral expansion
    }
  }
}
JSON.stringify({ shortlist, considered: log.length, log });
```

**These rules are not optional:**

1. **Never compute similarity.** There is no embedding step and no query-to-document scoring; relevance
   is judged by `tools.judgeBodies` reading each document, and selection by the selector reading signposts.
2. **The judgments are not yours to make.** Which signposts to explore comes from `okf_selector`; whether a
   document is relevant comes from `tools.judgeBodies`. You do not decide these yourself — you cannot, the
   signposts and bodies are interpreter data, not in your context.
3. **Read bodies only after the signpost sift.** Select from `index.md` descriptions first; read a body
   only for a concept the selector kept. Bodies read must stay a small fraction of concepts considered.
4. **Pass tool arguments as an object**, matching each tool's signature (`{ rel_dir }`, `{ rel_path }`).
5. **Return `JSON.stringify({ shortlist })`** as the final expression, and nothing else.

## What this skill does NOT own (deferred to the applying capability)

- **Which model each sub-agent uses** — the model-profile seam (the navigation judgments run on the strong
  model), never a hardcoded flag.
- **The bounds** (depth, frontier budget) — supplied as run parameters via `tools.*`.
- **The bundle itself** — compiled by `okf_compile` (FR-K.1-K.4); this method only reads it.

The method is the shape of the computation; the capability supplies the tools, the sub-agents, the bounds,
and the tests. Keep this file about the shape.
