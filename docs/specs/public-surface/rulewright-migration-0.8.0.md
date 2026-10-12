# RuleWright: moving to rag-wright 0.8 (in progress)

What the release after 0.7.0 changes for RuleWright. Do the 0.3 to 0.7 guides first. Nothing here is edited from
the engine repo. This guide grows with the release; each section names its task.

## 1. A "not in the evidence" answer is now an abstention (PS-28, ADR-0137)

**What changed.** `agenerate_answer` used to return `answer_kind="answered"` (or `"partial"`), with citations, when
the model's answer was that the evidence does not contain what was asked. It now returns `answer_kind="abstained"`
(`abstained=True`, no citations) and keeps the model's one-sentence reason in `answer`, e.g. "The provided evidence
does not contain any provisions regarding a minimum commitment."

**What to do in RuleWright.**
- Where the UI shows an abstention, show `answer` as the reason when it is not the generic abstention text, instead
  of a fixed "no answer" message.
- Any count of answered questions, or any check that treats "answered" as "found", now excludes these: re-baseline
  such numbers after upgrading.

## 2. PDFs can be ingested concurrently again (PS-29, ADR-0138; your engine issue 0052)

**What changed.** Reproduced here (15 CUAD PDFs, 4 in flight: 3 of 3 runs crashed in libpdfium). Two causes: the
engine's own pdfium calls took no lock and left documents for the garbage collector, and pdfium objects created
during a docling conversion can be freed later, outside any lock, beside another document's parse. The engine now
parses one PDF at a time (a process-wide lock, ending with a garbage collection inside it), and its own pdfium use
goes through one locked helper. Everything after the parse -- chunking, embedding, extraction, model calls -- stays
concurrent. Measured: 0 of 5 runs crashed, and the parse of the 15 PDFs took 127 s against 143 s concurrent
(concurrent parses never helped on that machine: docling's CPU-bound models contend).

**What to do in RuleWright.** After upgrading, put `scripts/t710_corpus_run.py` back to several documents in flight
(and remove the comment at the knob), and close engine issue 0052. Also replace RuleWright's own pdfium call: `src/rulewright/sizing.py` counts an upload's pages with
`pypdfium2.PdfDocument(data)` (no lock, never closed), which can run beside a worker's PDF parse in the same process
-- the same race. Use `rag_wright.api.pdf_page_count(data)` (PS-31): it reads under the engine's PDF lock and closes
before releasing it, and raises `ValueError` on an unreadable PDF, so `estimated_pages` keeps returning
`UNKNOWN_PAGES` there. After that no RuleWright code should import pypdfium2. A log line now says when a PDF waited more than
10 s for another PDF's parse. If a pdfium crash ever reappears, send the crash report.

## 3. An eval toolkit (EV-1 to EV-6; nothing to change now)

**What changed.** `rag_wright.api` gains a toolkit for evals: gold sets from expert files (`load_gold_set`), a scorer
per capability kind, the runner `arun_eval` (repeats, errors kept apart, progress) returning a JSON-ready
`EvalReport`, `rescore_eval`, and an optional Langfuse backend (`langfuse=True`). `evaluate_ingestion` gains an
async form for a backend (`aevaluate_ingestion`, with progress) and `IngestionEvaluation.to_dict()`. Nothing existing
was removed or changed; the reference is `docs/evals.md`.

**What to do in RuleWright.** Nothing now. When you put evals behind your backend, follow the consumer skill's
section 5 ("Evals behind your product's backend"); your own eval scripts can move onto `arun_eval` and the scorers
when you next touch them.

## 4. A wrong database name can fail loudly instead of opening an empty graph (PS-27)

**What changed.** Opening a store on a database that does not exist still creates it by default -- your
provisioning relies on that (`provisioning.py`: a customer's derived name is created on first open) -- and the engine
now logs `created database '<name>' (it did not exist)` whenever it does. New: `open_workspace(..., create=False)`
(and `ArcadeDBStore.from_env` / `from_config(..., create=False)`) raises `LookupError` naming the databases that
exist, and creates nothing. The engine's reference MCP servers now open their corpus with `create=False`.

**What to do in RuleWright.** Keep the default where you provision a customer. Pass `create=False` wherever the
database must already exist: an explicit `customer.contract_database` override, and the query-only services
(your copies of the MCP servers, any read path). That turns `tenancy/routing.py`'s "a wrong but UNIQUE name creates an
empty database" case into an error at the point of the mistake. Watch the log for unexpected `created database`
lines after you deploy.

