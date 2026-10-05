# ADR-0020: Interpreter sessions must not coexist in one process (KI-1); serialize the interpreter step across concurrent document graphs

> **Status: PARKED (ADR-0052).** Part of the GraphWright-era RLM-as-interpreter / dynamic-sub-agent runtime, which is parked. The engine's RLM today is authored SKILL.md content built as ordinary software (used by the `rlm_chunking` / `rlm_synthesis` capabilities); there is no interpreter or dynamic-dispatch runtime in this repo.


Date: 2026-07-16. Status: Accepted. Records a cross-graph correctness constraint from GraphWright's KI-1
resolution that RAG_Wright's ingestion harness must enforce, and closes the interpreter-concurrency
dimension of SPEC OQ8 (ingestion batch sizes and worker counts). Confirms the current harness already
satisfies it and binds any future concurrent-batch path.

## Context (KI-1)

Two QuickJS interpreter runtimes coexisting in **one process** race on shared Rust-side state: about half
the time a run **completes without dispatching, silently, with zero exceptions**. There is no error
signal — the RLM workflow returns "success" while having skipped its sub-agent dispatches.

GraphWright added a **compile-time guard** that rejects two concurrent interpreter nodes within a single
compiled graph. So any one ingestion graph is safe: our ingestion graph has exactly one interpreter node
(`rlm_chunking`), and within it the concurrency is one interpreter session with concurrent sub-agent
workers **inside** it — the reliable single-session case.

What GraphWright's guard **cannot** see: if the ingestion **harness** runs multiple documents' graphs
concurrently in one process, each graph brings up its own interpreter session and those sessions coexist
**across** graphs — the exact coexistence race, from outside any single graph. This is a harness-level
concurrency decision, not a graph-topology property, so it is ours to enforce.

## Decision

**Serialize interpreter-session execution across concurrent document graphs: at most one interpreter
session live per process at a time.** Documents may be processed concurrently in every other respect
(parse, embed, summarize, write); only the **interpreter-bearing step** (`rlm_chunking`'s boundary
discovery, and equally `rlm_synthesis` on the query side — both run an RLM interpreter session) must be
serialized so no two RLM sessions are alive at the same moment in the same process.

- **Current state: SATISFIED.** The ingestion/eval harnesses process one document at a time (plain
  `for d in docs:` loops in `eval/gate1_chunker_ab.py` and `eval/gate2_hybrid_rerank.py`; each `chunk()`
  runs its one interpreter session to completion before the loop advances). No `asyncio.gather`, thread
  pool, or process pool over documents exists anywhere in `eval/`, `scripts/`, or `src/`. So KI-1 cannot
  bite today; this ADR is the recorded constraint, not a change to running behavior.
- **Enforcement is in place now (always-on), not deferred.** A process-wide `BoundedSemaphore(1)` +
  `rlm_interpreter_session()` in `skills/rlm/agent.py` serializes the full interpreter-session lifetime for
  both the chunking discoverer and the synthesis extractor (see "The always-on guard" below). Separate
  **processes** are inherently safe (no in-process coexistence), so a per-process lock is the correct
  scope; the batch design (OQ8) is free on batch size and worker count as long as this holds.
- **Task T35 is the throughput design, not the correctness rescue.** The semaphore is the always-on
  correctness floor; T35 builds the real concurrent batch path (batch sizes, worker counts, OQ8) that
  serializes interpreter sessions **consciously** rather than relying on an uncontended semaphore, and it
  carries a **fail-if-silent** check — that dispatch actually fired under concurrency, not merely that the
  run completed — because the failure has no error signal. The semaphore makes silent degradation not
  happen; the T35 check proves it isn't.

## The always-on guard (a correctness safeguard, NOT performance overhead)

Because the failure is **silent** (~50% dispatch loss, zero exceptions), a "remember to add serialization
before you add concurrency" ledger note (task T35) is insufficient on its own: a note defends against
failures that announce themselves, and this one's whole nature is that it doesn't. So the enforcement is
**always on**, not deferred:

- `skills/rlm/agent.py` holds a process-wide `_INTERPRETER_SEMAPHORE = BoundedSemaphore(1)` and a
  `rlm_interpreter_session()` context manager. Both `SeamBoundaryDiscoverer.discover` (chunking) and
  `SeamSliceExtractor.extract` (synthesis) run their interpreter session inside it: the middleware is
  created **inside** the lock, its QuickJS runtime is built lazily on first `eval` **inside** the lock,
  and the registry is **closed inside** the lock (`_registry.close()`, deterministic teardown, not GC) —
  the full coexistence window, from before build to after teardown, exactly as scoped. A semaphore around
  only the dispatch call would let a second runtime come up while the first waits; this does not.
- This makes the safe behaviour the **default** rather than the **remembered** behaviour. It is
  **uncontended (zero cost)** while ingestion is serial, and engages only if concurrency is ever added —
  converting a silent-**correctness** failure into a visible-**performance** one (slower, debuggable,
  noticed), which is strictly better.

**This lock is load-bearing. Do NOT remove it as an "uncontended lock in a serial path."** A one-line
comment at the semaphore says so, to prevent a well-meaning cleanup from silently reopening the hole. It
lifts only via the exit path below.

## Why now

Cross-graph coexistence would silently drop ~half the RLM dispatches across a concurrent batch and report
success — quietly degrading every document processed concurrently, with no error signal. The whole value
of RLM ingestion is accuracy; this destroys it invisibly. So the constraint must be recorded and binding
**before** any concurrent batching runs, not discovered after a batch produces degraded chunks.

## Exit path (not permanent)

Lifted when the underlying coexistence bug is fixed. GraphWright filed an upstream bug against
`langchain-quickjs` / `deepagents`; recorded exit paths also include a sandbox-based RLM build and
RLM-in-LangGraph via DSPy. Until one lands, serialize interpreter sessions per process. When the fix
arrives, this constraint and GraphWright's in-graph guard lift **together**, verified by the KI-1
regression harness at full dispatch under concurrency.

## Consequences

- **Closes the interpreter-concurrency dimension of OQ8**: whatever batch/worker design is chosen, RLM
  interpreter sessions must not coexist in-process. Recorded here; SPEC OQ8 stays open on its other
  dimensions (wave vs pipelined, model-tier assignment, dead-letter, bulk vs background).
- **T35** (document update/upsert) and any future ingestion-batch task inherit this as a hard constraint:
  the serialization lands with the concurrent path, and a KI-1-style check (dispatch actually fired under
  concurrency) guards it, since the failure is silent.
