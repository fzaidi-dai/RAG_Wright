---
name: rlm-method
description: >
  The general recursive-language-model (RLM) divide-and-conquer method: when a working set is too
  large for one context window, load it into an interpreter, slice and dispatch the work in code, and
  synthesize the results, so the model never attends over the full volume. Applied by the RLM
  chunking capability (ingestion) and the RLM synthesis capability (query).
---

# RLM: divide and conquer over a working set too large for one prompt

This skill teaches a **method**, not a behavior. It is authored software (FR-C.10), not a build-tool
feature. Two capabilities apply it with their own contracts and tests: **RLM chunking** (FR-I.1,
ingestion) and **RLM synthesis** (FR-Q.5, query). This document is deliberately general; every
guarantee an application needs — determinism, boundary validation, gating, idempotence — is the
**applying capability's** job, not the method's (see "What this skill does NOT own").

## The problem

A prompt has a bounded context window. Real working sets — a whole parsed document, a large candidate
set of retrieved chunks — routinely exceed it, or fit but degrade quality when the model must attend
over everything at once. Stuffing the whole volume into one call is the failure mode this method
avoids.

## The method: interpreter → slice/dispatch in code → synthesize

1. **Load the working set into an interpreter as data.** Read the full input into a Python process as
   ordinary values (lists, dicts, dataframes) — *not* into a prompt. The interpreter, not the model,
   holds the state and is not bounded by a context window. Nothing about the whole volume is sent to
   a model yet.

2. **Slice and dispatch the work in code.** The *code* decides how to partition the working set into
   small, focused units (by section, by topic, by a fixed budget, by a computed boundary). Then it
   calls a model **once per unit**, on that unit alone. Because partitioning is code, it is
   inspectable, testable, and cheap; because each model call sees only its slice, quality does not
   decay with total size and cost scales with the work, not the volume. Dispatch may run concurrently
   (the capability owns pooling and backpressure).

3. **Synthesize the results in code, recursively if needed.** Combine the per-slice outputs — again in
   code — into the final result. When the combined intermediate is itself too large, apply the same
   three steps to it (load → slice/dispatch → synthesize): the method is recursive. A model is called
   on the *reduced* material, never on the raw whole.

The invariant across all three steps: **the model is only ever called on a small, focused slice; the
interpreter holds the whole.**

## How the two capabilities apply it

- **RLM chunking (FR-I.1).** Working set = one whole parsed document. Slice = split along topic /
  section / chapter boundaries into semantically coherent chunks (variable size, capped ~20,000
  tokens). Synthesize = a summary per chunk plus a per-document manifest and stable `chunk_id`s.
- **RLM synthesis (FR-Q.5).** Working set = the candidate chunks a query retrieved. Slice/filter in
  code, then recursively call sub-models on the small focused portions, so synthesis never attends
  over the full chunk volume.

## What this skill does NOT own (deferred to the applying capability)

- **Determinism and reproducibility** — temperature zero or structured output, and stable ids. (RLM
  chunking owns this for `chunk_id`s and boundaries.)
- **Boundary validation** — checking that produced slices are well-formed and within caps.
- **Gating** — content-hash gating so unchanged input does no work; incremental, resumable runs.
- **Model choice** — which model each call uses, via the model-profile seam (never a hardcoded flag).

The method is the shape of the computation; the capability supplies the contract, the guarantees, and
the tests. Keep this file about the shape.
