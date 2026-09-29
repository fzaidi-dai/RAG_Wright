# How RAG_Wright Handles Contracts — Ingestion & Querying (plain-English)

*Scope: contracts only. Purpose: let a business reader follow exactly what happens to a contract when we load it, and what kinds of questions we can then answer. Every step below is a real node/function in the pipeline; we note why each exists and flag every point where the system makes a decision.*

Grounded 2026-09-27 against the code (`src/rag_wright/`). Two running examples are used throughout:
- **Example A (a contract we load):** a signed Master Services Agreement (MSA) PDF between *Acme Corp* and *Beta LLC*, containing *"Section 8. Limitation of Liability — In no event shall either party's aggregate liability exceed the total fees paid in the twelve (12) months preceding the claim, except for breaches of confidentiality."*
- **Example B (a question we ask):** *"What is Acme's liability cap in this MSA?"* and, corpus-wide, *"Find every contract that caps liability at 12 months' fees."*

---

## Part 1 — Ingestion: turning a contract into searchable knowledge

We store two things per contract in **one database (ArcadeDB)**: a **search index** (so we can find the exact wording) and a **knowledge graph / KG** (structured facts — "this clause caps liability at 12 months' fees", "Acme contracts with Beta"). Ingestion builds both.

The work runs as a small assembly line (a LangGraph graph), one contract at a time:

```
PARSE ─► CHUNK ─► SEGMENT ─►┬─► EXTRACT CLAUSES ─┐
(before the graph)          ├─► INDEX SPANS      ├─► RESOLVE ─► WRITE
                            └─► EXTRACT GRAPH    ┘
```

The three middle steps run **in parallel**. A **content-hash gate** guards every expensive step (parse, chunk, clause-extraction, KG-write): if the exact same input was processed before, we reuse the result and do zero new work. Re-loading a contract is therefore cheap and produces the identical graph (no duplicates).

### Step 0 — Parse (read the document)
*What:* turn the PDF/DOCX into clean text, preserving structure (headings, tables, figures, page numbers). *Why:* everything downstream needs faithful text with provenance.
*Decision points:* **(a)** if we've parsed this file before, reuse it. **(b) OCR/VLM escalation** — we OCR fast first; only pages that come back low-quality get escalated to a vision model; pages still unreadable are reported honestly, never silently dropped. Tables and figures are captured as atomic items (a table is one block; a figure gets a caption + vision description) so nothing is lost.
*Example A:* the scanned MSA becomes structured text; "Section 8. Limitation of Liability" and its paragraph are preserved with their page number.

### Step 1 — Chunk (cut into coherent pieces)
*What:* split the document into right-sized, self-contained pieces. *Why:* a whole contract is too big to reason over at once; chunks are the unit we summarize and index.
*How / decision points:* **structure-first and deterministic** — a new chunk starts at every heading, with **no AI call at all** for a normally-structured contract. An AI call fires **only** for a section that is longer than the size cap (it's asked where to cut that one section). Over-long pieces are hard-split; lone headings are folded into the text that follows.
*Example A:* Section 8's heading + paragraph become one chunk.

### Step 2 — Segment (find the individual clauses)
*What:* cut each chunk into **operative spans** (the actual sentences/provisions) and attach page/coordinates. *Why:* clauses, not chunks, are what customers ask about. This step is **deterministic (rules/regex), no AI**, so it's fast and repeatable.
*Two key decisions here (both binary):*
- **"Is this worth turning into a clause?"** (`is_extractable_span`) — real prose = yes; furniture = no. Signature blocks ("By: __ Name: __"), notice addresses, table-of-contents dotted lines, and bare ALL-CAPS headings are filtered out. *Note: filtered text is still put in the search index — we only decline to mint a structured clause from it, so nothing becomes unsearchable.*
- **"Does this start a new clause/provision?"** (`starts_new_provision`) — decided by the section number ("8." or "8.1." starts one; deeper numbering folds in) or a heading. This is how we group sentences into provisions instead of treating every sentence as its own clause.
*Classification (soft) here:* an AI **clause-type classifier** runs once per chunk and labels each span (e.g. "limitation of liability") with a confidence. **This label is a soft tag only — never a filter.** It rides along to help later ranking; it can never hide a clause. *(This is the classifier you asked about — see "The classifier, today" below.)*
*Example A:* the liability sentence is one span, tagged "limitation_of_liability"; the signature page is filtered from clause-minting.

### Step 3a — Extract Clauses (turn a provision into structured facts)
*What:* read each provision and fill in a typed **Clause** record (~35 fields: clause type, liability-cap basis/amount, carve-outs, governing law, term/notice, IP ownership, etc.). *Why:* this is the structured knowledge that powers precise, filterable answers.
*How:* the ~35 fields are split into **8 themed groups**, and we make **one AI call per group (~8 per provision), run in parallel**; for list-type fields we union a second model's output to avoid under-listing. *(We measured that pruning groups or batching to save calls costs ~15–18% recall, so we don't.)*
*Classification + decisions here:* each field is mapped onto our **controlled vocabulary** — e.g. "capped at fees paid" → `liability_cap_basis = fees_paid`. A value we can't map cleanly becomes **OTHER** (treated as "not asserted"). Free-form carve-outs (e.g. "except confidentiality") are kept **verbatim** rather than dropped. Every extracted fact is then **quality-gated**: if its wording isn't actually present in the text it's downgraded from **EXTRACTED** to **AMBIGUOUS**; an applicability check and an optional AI judge run too. Every fact carries a confidence tag (**EXTRACTED / INFERRED / AMBIGUOUS**) and a citation. Failures are recorded, never silently dropped.
*Example A:* → `{clause_type: limitation_of_liability, liability_cap_basis: fees_paid, liability_cap_amount: "12 months' fees", carve_out: "confidentiality"}`, each with a citation to Section 8.

### Step 3b — Index Spans (make the wording searchable) — *this is where embeddings + the vector DB happen*
*What:* convert **every span** into vectors and store them in the search index. *Why:* so a later question can find the exact clause by meaning and by keyword.
*How:* one batched call to the **BGE-M3 embedding model** produces, per span, a **dense vector** (1024 numbers capturing meaning) **and** a **sparse vector** (keyword weights). Both are written to ArcadeDB's vector indexes (`LSM_VECTOR` for dense, `LSM_SPARSE_VECTOR` for sparse). This is best-effort and runs in parallel with clause extraction.
*Example A:* the liability span is embedded and stored, so "cap on damages" will later match it even though those exact words don't appear.

### Step 3c — Extract Graph (find the parties and relationships)
*What:* pull the **parties** (and any affiliates) from the contract. *Why:* to answer "who contracts with whom" and to link clauses to real entities.
*How:* one AI call over the preamble, once per contract (cached). *Example A:* extracts *Acme Corp* and *Beta LLC* and the relationship "Acme contracts-with Beta".

### Step 4 — Resolve (match names to canonical entities) — *your "entity similarity" question*
*What:* map extracted names to canonical entity IDs. *Why:* so "Acme", "Acme Corp." and "Acme Corporation" become one entity.
*Important:* this is **exact, normalized-name matching against a registry — deliberately NO fuzzy / embedding / AI similarity matching for entities.** An unknown name is left **unlinked**, never guessed. (We judged a wrong automatic merge worse than an honest "unlinked".) So: *similarity search is used for finding clause text at query time — not for merging entities.*

### Step 5 — Write (populate the knowledge graph)
*What:* persist everything in one transaction. *Why:* one durable, queryable source of truth.
*How:* each Clause becomes a **Clause node + shared property-value nodes + one typed edge per fact** (e.g. `CAPS`, `EXCEPTS`, `GOVERNED_BY`), each edge carrying its confidence and citation. Parties become entity nodes with `Mentions` and `Relationship` edges. A content-hash gate makes re-writes idempotent (no duplicate edges).
*Example A:* the KG now holds *Clause(Section 8) —CAPS→ fees_paid(12 months)*, *—EXCEPTS→ confidentiality*, and *Acme —contracts_with→ Beta*, all cited.

---

## Part 2 — Querying: the four question types we support

There are **four** contract query types (each is a registered capability and an MCP tool):

| # | Query type | The question it answers | Example |
|---|---|---|---|
| 1 | **Answer a question about one contract** (`intra_document_qa`) | "What does *this* contract say about X?" — cited answer | "What is Acme's liability cap in this MSA?" |
| 2 | **Find clauses across all contracts** (`typed_property_retrieval`) | "Across the whole portfolio, find clauses matching this description" | "Find every contract that caps liability at 12 months' fees" |
| 3 | **Relationship question** (`relational_qa`) | "Who is connected to whom?" — answered from the graph | "Which companies does Acme contract with?" |
| 4 | **Compliance check** (`compliance_check`) | "Does this document comply with the stored rules/policy?" — cited findings + verdict | "Does this ad comply with FTC endorsement rules?" |

**How search works underneath (shared by 1 & 2):** the question is embedded with the **same BGE-M3 model** (dense + sparse). ArcadeDB runs a **hybrid search** — dense (meaning) and sparse (keywords) combined server-side by a fusion ranking (RRF) — plus a **dense-floor guarantee** that a very strong meaning-match can't be crowded out by common keywords. **The clause-type classifier is NOT used to filter here** (see below); the search pool is the whole index.

### 1) One-contract Q&A — `intra_document_qa`  (nodes: serve → assemble → generate)
- **serve** — pull *this contract's* clauses from the KG (including untyped spans, so recall doesn't depend on labels); if there are many, **rerank them by meaning** against the question using a **BGE cross-encoder reranker** and keep the top ~12. *(This semantic rerank replaced the old function-label filter.)* Any "cap" clause's linked carve-outs are pulled in too.
- **assemble** — rehydrate the real clause text for citation; attach the structured properties out-of-band.
- **generate** — an AI writes a grounded answer using only the retrieved evidence, in a light tagged format we parse. **No claim without a citation:** any answer that ends up without a valid citation is turned into an honest "I can't answer from this document." *Example B →* "Liability is capped at the fees paid in the 12 months before the claim [Section 8], except for confidentiality breaches [Section 8]."

### 2) Corpus-wide clause search — `typed_property_retrieval`  (nodes: extract_constraints → retrieve → judge_relevance → assemble)
- **extract_constraints** — read the query for typed conditions (e.g. `cap_basis = fees_paid`). *Boosts* ranking; never a hard filter.
- **retrieve** — whole-index hybrid search + dense floor, then a **deterministic property boost** (spans matching the extracted constraints rank higher; ties keep the search order; zero-match spans keep their place so recall is safe).
- **judge_relevance** — when a specific clause-type is requested, an AI judges each candidate span relevant / not / uncertain, so "not found" is a real, honest answer.
- **assemble** — return the ranked, cited spans. *Example B (corpus) →* the liability spans from every MSA that caps at 12 months' fees.

### 3) Relationship questions — `relational_qa`  (nodes: traverse → assemble → generate)
- **traverse** the entity graph from a starting company along `contracts_with` edges (bounded hops); **assemble** each reached entity as cited evidence (cited by the contract the edge came from); **generate** the answer. *Example →* "Acme contracts with Beta LLC [MSA]."

### 4) Compliance check — `compliance_check`  (nodes: extract_claims → retrieve_applicable → judge → assemble)
- **extract_claims** from the subject document/ad; **retrieve_applicable** rules from a requirements KG and **route by rule type** (obligations judged once over the whole document; prohibitions checked per claim; permissions treated as defenses; actor/applicability gates skip irrelevant pairs honestly); **judge** each relevant (claim, rule) pair with an AI; **assemble** a report with findings, a per-requirement gap matrix, and an overall verdict. *(This is the compliance engine; it reuses the same parsing/chunking front-end as ingestion.)*

---

## Part 3 — The classifier, today (your specific question)

- We **removed the clause-type classifier as a retrieval *filter*** (ADR-0047). Measurement showed a label-based pre-filter matched whole-index recall within ±0.02 while imposing a ceiling on what could ever be found; so retrieval now searches the whole index and lets meaning-based ranking do the work.
- It is **still used at ingestion as a *soft tag*** — an AI classifier labels each span's clause type (with confidence); the label is stored and helps light-touch ranking, but it can never hide a clause.
- **At query time it is not used at all.** (The trained LegalBERT model still exists and is used by the bulk back-fill scripts that built the shipped corpus; the default live pipeline uses the AI tag classifier.)

---

## Part 4 — How good is retrieval today (measured)

Honest benchmark: **ACORD, 57 attorney-graded queries** (relevance grade ≥ 2, unit = clause), on the production contract KG. *(CUAD's questions are content-free templates, so we don't treat CUAD as a retrieval benchmark.)*

| Stage | recall@10 | recall@20 | recall@50 |
|---|---|---|---|
| **Raw hybrid pool** (the "initial retrieval", no rerank) | 0.06 | 0.09 | 0.17 |
| **Full pipeline + semantic rerank** (measured on the judged set) | **0.68** | **0.90** | — |
| Best possible (reachability ceiling) | — | — | **0.97** |

Reading it plainly: the relevant clause is **almost always somewhere in the index (0.97 ceiling)** — this is a *ranking* problem, not a *findability* problem. The **raw** hybrid pool puts few relevant clauses in the top slots (short legal queries embed weakly), so **the reranker is load-bearing**: with it we reach **~68% in the top 10 and ~90% in the top 20**. (The raw and reranked rows are measured on different universes — full pool vs. judged set — so read them as "the rerank does the heavy lifting," not as one clean before/after.) nDCG@10 ≈ 0.70.

---

## One-line glossary
- **Clause / provision** — a single contractual term (e.g. the liability cap).
- **Span** — the exact sentence(s) of a clause, stored for citation and search.
- **Embedding** — a numeric fingerprint of meaning (BGE-M3: a 1024-number "dense" vector + a keyword "sparse" vector).
- **Knowledge graph (KG)** — the structured facts and their links (clauses, properties, parties, relationships), each with a citation and a confidence tag.
- **Reranker** — a model that reorders search hits by how well they actually answer the question (BGE cross-encoder).
- **Recall@k** — of all the clauses that *should* be found, the fraction that appear in the top *k* results.
