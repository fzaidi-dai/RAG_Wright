---
name: okf_navigate
description: >
  The embedding-free knowledge-navigation method: find the concepts in an Open Knowledge Format (OKF)
  bundle that answer a question by progressive disclosure, not by vector similarity. Keep the bundle in
  interpreter variables, read index signposts and frontmatter with tools, dispatch a selector sub-agent
  to choose which signposts to explore and a reader sub-agent to judge each candidate body, and return
  the shortlist of concept ids. Applied by the okf_navigate capability (FR-K.6).
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
into interpreter variables (never into your context) and dispatches sub-agents to make the two judgments
that need a model:

1. **Selection.** For a directory, `tools.readIndex(...)` returns its signposts (each a `path`, a
   `description`, and `is_dir`). You cannot judge them yourself (they are interpreter data, not in your
   context), so you dispatch an **`okf_selector`** sub-agent with the list and it returns which to explore.
   Descend into chosen subdirectories; collect chosen concept files as candidates.
2. **Reading.** For each candidate concept, `tools.readBody(...)` returns its document text. You dispatch
   an **`okf_reader`** sub-agent with that one body; it returns whether the document is relevant. A
   relevant concept's id goes on the shortlist, and you push its cross-links onto the frontier to expand.

The bundle stays in interpreter variables; a model is only ever called (via `task()`) on a focused list
of signposts or one document body. Both sub-agents already know the question (it is in their system
prompt) — you pass them only the signposts / the body.

### The canonical workflow (write it this way)

**Your ONLY action is to emit this workflow to the `eval` tool in one call, then return its JSON result and
stop.** The bundle is reachable ONLY through the `tools.*` calls inside `eval` — there is NO filesystem, so
never call `ls`, `glob`, `read_file`, or `write_file`; they find nothing and waste the turn. Do not answer
from your own knowledge; the answer only comes from running the workflow.

Call the tools with an **object argument** exactly as their signatures show (for example
`tools.readIndex({ rel_dir: dir })`, `tools.readBody({ rel_path: path })`). Use `task({...,
responseSchema})` so a sub-agent returns a typed value (no string parsing).

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
  const { keep } = await task({
    description: "Signposts to choose from:\n" +
      JSON.stringify(signposts.map((s, i) => ({ i, name: s.path, description: s.description, isDir: s.is_dir }))),
    subagentType: "okf_selector",
    responseSchema: { type: "object", properties: { keep: { type: "array", items: { type: "number" } } },
                      required: ["keep"] },
  });
  return (keep || []).filter((i) => Number.isInteger(i) && i >= 0 && i < signposts.length).map((i) => signposts[i]);
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
const log = [];   // one entry per body read: { path, relevant } — the traversal's decision trace
// READING: read each candidate body once, judge it with the reader, keep the relevant, expand its links.
while (frontier.length && shortlist.length < BUDGET) {
  const path = frontier.shift();
  if (visited.has(path)) continue;
  visited.add(path);
  const body = await tools.readBody({ rel_path: path });
  if (!body) continue;
  const { relevant } = await task({
    description: "Document:\n" + body,
    subagentType: "okf_reader",
    responseSchema: { type: "object", properties: { relevant: { type: "boolean" } }, required: ["relevant"] },
  });
  log.push({ path, relevant });
  if (relevant) {
    shortlist.push(await tools.conceptId({ rel_path: path }));
    for (const link of await tools.related({ rel_path: path })) frontier.push(link.replace(/^\//, ""));
  }
}
JSON.stringify({ shortlist, considered: log.length, log });
```

**These rules are not optional:**

1. **Never compute similarity.** There is no embedding step and no query-to-document scoring; relevance
   is judged by the reader sub-agent reading the document, and selection by the selector reading signposts.
2. **The judgments are the sub-agents' job.** Which signposts to explore comes from `okf_selector`; whether
   a document is relevant comes from `okf_reader`. You do not decide these yourself — you cannot, the
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
