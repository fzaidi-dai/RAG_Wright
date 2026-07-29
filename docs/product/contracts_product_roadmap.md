# Contract Intelligence for SMEs & Boutique Firms — Product Roadmap, Delivery Surfaces & MVP

**Status:** strategy draft (2026-07-29), for discussion. Grounded in market research (see the earlier brief) and
our actual RAG_Wright capabilities. Nothing here is committed to the build ledger yet.

> Scope note. RAG_Wright is the **capability half**; **GraphWright** is the orchestration/compiler half; **ARD**
> (Agentic Resource Discovery, `urn:air` identifiers) is the **capability registry** that bridges them. This
> document keeps that boundary: RAG_Wright registers capabilities in ARD; GraphWright compiles them into static
> and on-demand workflows; MCP + Slack/Teams are the **delivery surfaces**. We do not build orchestration inside
> RAG_Wright.

---

## 1. The one-paragraph thesis

Bring **enterprise-grade contract *analysis and risk* down-market** to SME in-house counsel, boutique firms,
and solo attorneys — priced at a no-brainer level, **grounded** (every answer cites its clause), and **private**
(runs local/self-hosted). The wedge is the gap between cheap storage/e-sign tools (Concord-tier, shallow on AI)
and six-figure enterprise CLM (Ironclad/Icertis/Lexion). Deliver it **inside the tools users already live in**
(their AI client via MCP, and Slack/Teams/Outlook), because for this segment the failure mode is *not adopting
the tool they bought*. Our neuro-symbolic stack (typed KG + small/local LLMs + classifiers) is unusually
well-matched to that segment's two blockers — **trust** and **privacy** — at a price it can bear.

---

## 2. Why this market (grounded, condensed)

| Signal | Figure | Implication |
|---|---|---|
| CLM market (2026) | ~$1.8–3.4B, 12–15% CAGR | Large, growing, not winner-take-all |
| AI-in-legal | $5.59B (2026) → $12.49B (2030), 22.3% CAGR | Fastest-growing slice; tailwind |
| Contract mismanagement loss | **9.2% of revenue** (missed renewals, untracked obligations) | Quantified, visceral ROI story |
| SME/solo AI adoption (Clio 2026) | 71% solo / 75% small firms adopt… | Market is *already* buying AI |
| …value capture | …only ~32% see revenue lift; 86% haven't repriced | ROI must be **self-evident**, not "smart" |
| Global | India 13.4% CAGR, EU = 25% of market (GDPR) | Privacy/local angle travels well |

**Pricing gap we occupy:** Enterprise CLM $30K–$200K+/yr · mid-market Juro/Lexion $15–80K/yr · affordable Concord
~$399/mo (shallow) · Spellbook ~$99/user/mo (single-doc drafting copilot). **Nobody owns "cheap + deep +
private + portfolio-level."**

**Differentiators, and why our architecture *is* them (not a bolt-on):**
- **Grounded / citation-first** — "no claim without a citation" + the grounding judge → answers the #1 adoption
  blocker (accuracy/hallucination; signed AI-disclosure certificates now required in many jurisdictions).
- **Private / local** — local granite + local KG/embeddings → on-prem/ZDR mode that enterprises charge a premium
  for, we give cheaply (protects privilege).
- **Cheap-but-deep** — classifiers + small LLMs + symbolic KG → Concord-tier pricing with Lexion-tier analysis.
  **KG-6 proved the advantage is *largest on hard, multi-constraint portfolio queries*** — the expensive tools'
  turf.

---

## 3. Product architecture (layered)

```
 Delivery surfaces:   MCP server  ·  Slack / Teams / Outlook app  ·  thin web (optional)
                              │   (in-chat UI via MCP Apps / SEP-1865)
 Orchestration:        GraphWright — compiled STATIC query/ingestion graphs
                              │        + ON-DEMAND / dynamic workflows
 Registry:             ARD (Agentic Resource Discovery, urn:air) — capabilities registered here
                              │
 Capabilities:         RAG_Wright — parse · chunk · embed · hybrid-search · rerank ·
                              │        KG-extract · resolve · reason/generate · RLM
 Store & models:       ONE store (ArcadeDB: hybrid index + typed contract KG) ·
                       local/low-cost models (granite, BGE, LegalBERT) ; OpenRouter optional
```

The three retrieval **legs** are scoped queries over one typed contract KG:
- **Leg A** — intra-contract (cited Q&A, key terms, clause disambiguation).
- **Leg B** — cross-portfolio (multi-constraint filters, risk scans, clause search) — *our moat*.
- **Leg C** — relational (counterparty exposure, party-role questions).

**Why the ARD + GraphWright layer matters for the roadmap:** once RAG_Wright's capabilities are registered in
ARD on the standard pattern, the "advanced/complex queries, background periodic agents, on-demand analysis"
items stop being bespoke code and become **GraphWright-compiled workflows** — static graphs for the fixed
products (the Portfolio X-ray), dynamic/on-demand graphs for ad-hoc analyst questions. That is the leverage:
new analytical products become *compositions*, not new engineering.

---

## 4. Use-case catalog (mapped to capability status)

Legend: ✅ engine exists · 🟡 modest delta · 🔴 new build · 🧩 GraphWright workflow (composition, not code)

**Per-contract (Leg A):**
- ✅ Cited Q&A on one contract · ✅ key-term / term-sheet extraction (parties, dates, governing law, caps,
  indemnity, renewal, termination, payment) · ✅ clause disambiguation ("the *mutual* cap") · 🟡 deviation from a
  playbook / missing-clause flags.

**Portfolio (Leg B — the moat):**
- ✅ Multi-constraint filter ("uncapped indemnity AND governed by NY AND auto-renews in 60d") · 🟡 risk / blind-spot
  **scan** across the whole portfolio (checklist library is the new part) · ✅ cross-corpus clause search.

**Relational (Leg C):**
- ✅ Counterparty exposure ("all contracts with Acme + our exposure") · 🟡 party-role questions.

**Obligations & renewals (the #1 SMB pain):**
- 🟡 Extract deadlines/obligations/renewals from typed temporal dims → 🔴 calendar + 🔴 ambient alerts.

**Background & on-demand (GraphWright):**
- 🧩 Periodic risk sweeps · 🧩 "risky new contract ingested" alert · 🧩 portfolio-drift monitoring · 🧩 ad-hoc
  complex analysis composed on demand · 🟡 impact analysis ("if this term/law changes, which contracts are hit?").

**Downstream / generation (later):**
- 🔴 Cited risk memos/summaries (big billable-hour saver) · 🔴 grounded redline suggestions from *your own* clause
  library · 🔴 negotiation-playbook checks.

---

## 5. The MVP — "Contract Portfolio X-ray"

The tightest thing that (a) runs on today's building blocks, (b) hits the quantified pain, (c) shows all three
differentiators, and (d) *is* the unified three-leg demo:

1. **Ingest** — drop a folder of contracts → parse → chunk → typed KG (per matter/workspace).
2. **Read** (Leg A) — cited Q&A + term-sheet per contract.
3. **Scan** (Leg B) — run a **risk/blind-spot checklist** across the portfolio → cited findings per contract,
   ranked by severity.
4. **Track** — obligation/renewal list + alerts (the visibility fix for missed renewals).
5. **Relate** (Leg C) — counterparty exposure view.
6. **Trust & privacy** — everything cited; runs locally.

Delivered as an **MCP server** (instant reach into Claude/ChatGPT/Copilot, near-zero UI build) + a **Slack/Teams
alert bot** (ambient renewal/risk notifications). This single artifact demonstrates Legs A/B/C and the whole
KG-0→KG-6 investment as a product.

**MVP success criteria:** a design partner ingests their real contract pile; the risk scan surfaces ≥1 finding
they didn't know about, each finding opens to its cited clause; a renewal alert fires in Slack/Teams before a
deadline. (Value proven = "found something + before a deadline + I could trust it because it cited the clause.")

---

## 6. Delivery-surface plan

| Surface | Priority | Role | Notes |
|---|---|---|---|
| **MCP server** (Claude / ChatGPT / Copilot / Cursor) | **P0** | Demo + prosumer + design-partner on-ramp | Near-free given our MCP-native stack; UI-in-chat via MCP Apps |
| **Slack + Teams + Outlook** app | **P1** | Where in-house/boutique work; **ambient alerts** + quick Q&A | Incumbents (Docusign/Lexion/Summize) already here → table stakes |
| **Thin web console** | **P2** | Admin, ingestion, tenant management, audit log | Not the primary UX; keep light |
| **Discord / Telegram** | **P3** | Solo/indie/international long-tail, community motion | Not where the core buyer lives; defer |

**In-chat UI (MCP Apps / SEP-1865):** render the **risk dashboard**, **cited findings table**, **clause
highlight**, and **renewal timeline** as interactive components — trust-preserving UI (clickable citations)
without building a full frontend.

---

## 7. MCP server — tool schema (concrete)

Server name: `contract-xray`. All tools return **structured data + citations** `{contract_id, clause_id,
span_id, confidence}`; tools marked **[App]** also return an MCP-Apps UI resource. Inputs shown as JSON Schema
sketches.

### Ingestion / workspace
```jsonc
// ingest_contracts — build/refresh the typed KG for a matter (idempotent, content-hash gated)
{ "name": "ingest_contracts",
  "input": { "matter_id": "string", "source": "string (folder path | upload id)",
             "mode": "enum[local,hosted] default local" },
  "returns": { "ingested": "int", "clauses": "int", "entities": "int", "skipped_unchanged": "int" } }
```

### Leg A — per contract
```jsonc
// ask_contract — cited Q&A on ONE contract                                            [App: clause highlight]
{ "name": "ask_contract",
  "input": { "matter_id": "string", "contract_id": "string", "question": "string" },
  "returns": { "answer": "string", "citations": "Citation[]", "in_taxonomy": "bool", "low_confidence": "bool" } }

// extract_key_terms — structured term sheet, each field cited                          [App: term-sheet card]
{ "name": "extract_key_terms",
  "input": { "matter_id": "string", "contract_id": "string",
             "fields": "string[]? (default: parties,dates,governing_law,liability_cap,indemnity,renewal,termination,payment)" },
  "returns": { "terms": "{field: {value, citation, confidence}}" } }

// disambiguate_clause — the specific clause matching a condition (e.g. the mutual cap)
{ "name": "disambiguate_clause",
  "input": { "matter_id": "string", "contract_id": "string", "function": "string", "condition": "string" },
  "returns": { "clause": "Clause", "citation": "Citation" } }
```

### Leg B — portfolio (the moat)
```jsonc
// filter_contracts — multi-constraint typed filter across the portfolio               [App: results table]
{ "name": "filter_contracts",
  "input": { "matter_id": "string",
             "constraints": "Constraint[]  // e.g. [{dimension:'jurisdiction',value:'New York'},
                                          //       {dimension:'liability_cap',op:'absent'},
                                          //       {dimension:'renewal',op:'auto_within',value:'60d'}]" },
  "returns": { "matches": "{contract_id, matched_constraints, citations}[]" } }

// scan_portfolio_risk — run a risk/blind-spot checklist across all contracts          [App: risk dashboard]
{ "name": "scan_portfolio_risk",
  "input": { "matter_id": "string", "checklist_id": "string default 'sme_default'",
             "perspective": "enum[buyer,seller,either] default either" },
  "returns": { "findings": "{contract_id, rule_id, severity, explanation, citation}[]",
               "summary": "{by_severity, by_rule}" } }

// search_clauses — cross-corpus clause retrieval (KG-primary llm_union pipeline)
{ "name": "search_clauses",
  "input": { "matter_id": "string", "query": "string", "k": "int default 20" },
  "returns": { "clauses": "{contract_id, clause_id, text, function, score, citation}[]" } }
```

### Leg C — relational
```jsonc
// counterparty_exposure — all contracts with a party + aggregate exposure             [App: relationship view]
{ "name": "counterparty_exposure",
  "input": { "matter_id": "string", "party": "string" },
  "returns": { "contracts": "{contract_id, role, exposure_terms, citations}[]", "aggregate": "object" } }
```

### Obligations & renewals
```jsonc
// renewal_calendar — upcoming renewals/deadlines                                       [App: timeline/calendar]
{ "name": "renewal_calendar",
  "input": { "matter_id": "string", "horizon_days": "int default 90" },
  "returns": { "events": "{contract_id, type, due_date, notice_window, citation}[]" } }

// list_obligations — obligations due before a date
{ "name": "list_obligations",
  "input": { "matter_id": "string", "due_before": "date?" },
  "returns": { "obligations": "{contract_id, party, obligation, due, citation}[]" } }
```

**Registry note:** each tool is a thin adapter over an **ARD-registered RAG_Wright capability** (`urn:air:...`);
the *composite* tools (`scan_portfolio_risk`, background sweeps) are **GraphWright-compiled workflows** over
those capabilities, not hand-written orchestration. New checklists / analytical products = new compiled
workflows, same tool surface.

---

## 8. Hosted vs. local — the deployment split (privacy is the product)

| Mode | Inference | KG / index | MCP client | Data boundary | For whom |
|---|---|---|---|---|---|
| **Hosted (SaaS)** | OpenRouter (small LLMs) | our ArcadeDB (tenant-isolated) | Claude/ChatGPT | leaves boundary; ZDR + no-train posture, SOC 2 on roadmap | prosumer, fast start, non-sensitive |
| **Private / self-hosted** | **local granite + BGE + LegalBERT** | **on-prem ArcadeDB** | Claude Desktop / VS Code / Cursor w/ **local MCP** | **never leaves** | privilege-sensitive firms, EU/India data-sovereignty |
| **Hybrid** | local inference + local KG | on-prem | hosted client | prompts to client only | middle ground |

**Honest capability note (from our measurements):** local granite is viable for **query-time** and small SME
portfolios; **bulk ingestion** of large piles is output-token-bound and slow locally, so the private mode uses
a local box for queries and either overnight local batch or a customer-VPC GPU (Modal-style) for large initial
ingests. Lead the privacy pitch with the fully-local query experience; be candid about ingest throughput.

---

## 9. Phased roadmap

### Phase 0 — MVP demo (now)
**Goal:** the unified "Portfolio X-ray" demo on existing corpora (CUAD/ACORD) + a small real SME set.
**Build:** MCP server with the Leg-A/B/C + ingest tools; the `sme_default` risk checklist; ingestion CLI →
per-matter KG; cited outputs; run in Claude/ChatGPT.
**Deltas:** 🔴 MCP server + tool adapters · 🔴 risk-checklist library · 🟡 term-sheet + disambiguate wiring ·
🟡 renewal/obligation extraction from existing temporal dims.
**Exit:** live demo; a risk scan surfaces cited findings; a renewal list renders.

### Phase 1 — productized MVP + design partners
**Goal:** 2–3 boutique firms / SME legal depts using it on their own contracts.
**Build:** multi-tenant ingestion (isolated KGs) + auth + audit log; **Slack/Teams alert bot** (ambient
renewal/risk); **local self-hosted mode**; register capabilities in **ARD**; compile the X-ray as a **GraphWright
static query graph**.
**Deltas:** 🔴 tenancy/auth · 🔴 Slack/Teams app · 🔴 local deploy packaging · 🧩 ARD registration + first
compiled graph.
**Exit:** design partners renew/track live; ≥1 "found something before a deadline" testimonial.

### Phase 2 — v1 GA (paid)
**Goal:** self-serve paid product for SME/boutique.
**Build:** playbook/deviation checks; **MCP-Apps dashboards**; **background periodic agents** and **on-demand
complex analysis** as **GraphWright dynamic workflows**; impact analysis; billing.
**Deltas:** 🟡 playbook engine · 🔴 MCP-Apps UI · 🧩 dynamic workflows (compose from ARD) · 🔴 billing/onboarding.
**Exit:** paying SMEs; ambient agents in Slack/Teams driving retention.

### Phase 3 — platform (multi-domain)
**Goal:** prove the same stack in a second domain.
**Build:** templatize the capability pattern for **one** of {financial-doc analysis, RFP-from-past-projects,
construction-standard compliance} — new ontology + extraction schema, **same ARD + GraphWright + MCP stack**.
**Pitch:** "agentic platform, proven first in legal." Go-to-market stays single-domain per launch.

---

## 10. Positioning & GTM (summary)

- **Beachhead:** portfolio **risk & renewal analysis** (read/analyze — lower liability than draft/file), for SME
  in-house counsel + boutique firms. Less crowded at SME pricing than Word drafting copilots.
- **Message:** *grounded + private + affordable* — every answer cites the clause, runs on your own machine, at a
  fraction of enterprise CLM.
- **Distribution:** meet users in their AI client (MCP) and Slack/Teams/Outlook; land ambient renewal alerts as
  the habit-forming hook. Partner via bar associations, boutique-firm networks, legal-tech marketplaces.
- **Pricing:** no-brainer vs the 9.2% leakage; land through firms who bill it through to clients. Because our
  stack is cheap, we can undercut mid-market and still be accurate.
- **Global:** EU (GDPR) and India (13.4% CAGR) reward the local/private mode; start where design partners exist.

---

## 11. Risks (so we don't fool ourselves)

- **Willingness-to-pay is soft** (86% of small firms haven't repriced around AI) → price low, tie to leakage
  ROI, land through firms.
- **Adoption/UX is the real bottleneck**, not tech → the in-tool surfaces (MCP/Slack/Teams) are the mitigation,
  not a nice-to-have.
- **In-workflow is table stakes** (Docusign/Lexion/Summize already embedded) → defensibility is the *combination*
  grounded+private+cheap+portfolio, not any single feature.
- **Legal liability** → stay assistive + cited + human-verified; never autonomous advice.
- **Privacy vs. hosted-client convenience** → keep the two deployment modes clean; never blur what leaves the
  boundary.
- **Domain depth** (jurisdiction coverage, playbook curation) is real work → scope narrow at MVP; expand with
  design partners.

---

## 12. Immediate next steps (proposed, not started)

1. Freeze the **`sme_default` risk checklist** (10–15 rules mapped to typed KG constraints).
2. Stand up the **`contract-xray` MCP server** with the Section-7 tools over existing capabilities.
3. Wire **renewal/obligation extraction** from existing temporal dims → `renewal_calendar`.
4. Register the first capabilities in **ARD**; compile the X-ray query path as a **GraphWright static graph**.
5. Recruit 2–3 **design partners** (SME legal dept / boutique firm) for Phase 1.

> Sources for the market figures are in the strategy brief (CLM/AI-legal market size, 9.2% leakage, Clio 2026
> adoption, competitor pricing, MCP Apps SEP-1865, Slack/Teams distribution, legal in-workflow incumbents).

---

## 13. Adjacency: the Compliance Check module (validated by a design-partner exec)

**Concept.** Compliance checking = **two-sided retrieval + entailment**: extract the checkable elements
(claims / facts / assertions) from a *subject document* (marketing campaign, ad claim, brand messaging, support
incident, dispute report, agreement), retrieve the *applicable* clauses from a *regulatory corpus*, and judge
each element **compliant / violation / needs-review** — cited on **both** sides. It is the same neuro-symbolic
machine as the contract legs, pointed at two corpora with a judgment node instead of a rank node. Same buyer
(legal/compliance), same ingestion, same trust/privacy, same MCP/Slack surfaces → a **second module of the same
product**, not a second product. Market context: RegTech ~$25B (2026) → $34.6B (2030); AI-in-RegTech ~36%/yr;
enterprise GRC $23.6B → $42.1B by 2031 — a larger, adjacent TAM, underserved below PerformLine/Saifr/enterprise-GRC.

### 13.1 Requirements KG schema (the regulatory corpus side)

Grounded on the **deontic** ontology family — **LKIF** (Legal Knowledge Interchange Format: obligations /
permissions / prohibitions), **LegalRuleML**, and **ODRL** — i.e. the *same* deontic backbone we already use in
`contract_bridge.ttl`. A regulation ingests into a typed KG of **requirement** nodes (reusing the
kg-extraction-recipe, a new template):

```jsonc
// Requirement node — a single regulatory rule (requirement_id = <source_reg>:<section>:<hash>, like clause_id)
{ "requirement_id": "string",
  "source": "string  // e.g. 'FTC Endorsement Guides 16 CFR 255'",
  "citation": "string  // section / paragraph (provenance, always cited)",
  "deontic_type": "enum[obligation, prohibition, permission]  // LKIF/ODRL closed vocab",
  "actor": "string  // who it binds: advertiser, endorser, data-controller, ...",
  "applicability_scope": "Constraint[]  // typed conditions the rule applies under — e.g.
                          //   [{dimension:'claim_type', value:'health'},
                          //    {dimension:'medium', value:'social'}]   <- matched against claims",
  "requirement_text": "string  // what must / must not be done",
  "evidence_standard": "string?  // e.g. 'competent and reliable scientific evidence' (health/green)",
  "trigger_condition": "string?  // predicate that activates the rule",
  "severity": "enum[low,med,high]?" }
```

```jsonc
// Claim node — a checkable element extracted from the SUBJECT document (claim_id + span = provenance)
{ "claim_id": "string", "source_doc": "string", "span": "Span  // provenance, cited",
  "claim_type": "enum[efficacy, comparative, pricing, health, environmental, endorsement, performance, guarantee, ...]",
  "actor": "string?", "subject_product": "string?",
  "assertion_text": "string", "quantitative_value": "string?",
  "disclosures_present": "string[]  // qualifiers/disclaimers found",
  "evidence_referenced": "bool", "medium": "string?" }
```

The **applicability match** (claim → applicable requirements) is exactly our **Leg-B multi-constraint retrieval +
function routing** (claim_type/medium/actor ↔ `applicability_scope`), reusing the `llm_union` router — the
capability KG-6 proved strongest on multi-constraint queries. **Reuse, not new.**

### 13.2 Judgment node design (the genuinely new capability)

Per `(claim, applicable_requirement)` pair → a verdict, extending the **grounding judge (ADR-0028)** from "is X
supported by cue Y" to "does claim X **satisfy / violate** requirement Y":

- **Output (structured, via the model-profile seam):** `verdict ∈ {compliant, violation, needs-review}`,
  `rationale`, `citation_claim` (subject span), `citation_requirement` (reg clause), `confidence`.
- **Cascade (reuse ADR-0028 Flash→Pro):** cheap model first; escalate ambiguous/borderline to a stronger model;
  deterministic lexical checks first where a rule is lexically anchored (e.g. "disclosure X present?").
- **Conservative default + human gate:** default to `needs-review` under uncertainty (never silently conclude a
  violation — false-negatives = liability, false-positives = alert fatigue); **every `violation` requires human
  confirmation** before it leaves the tool. Assistive + cited + human-in-the-loop, always.
- **Both-sided citation** is the trust product: an auditor sees the exact ad span *and* the exact reg clause.

### 13.3 MCP tools (add to the `contract-xray`/`compliance` server)

```jsonc
"ingest_regulations"  { input:{corpus_id, source, standard_id}, returns:{requirements, sections} }        // reg → requirement KG
"extract_claims"      { input:{doc_id}, returns:{claims:Claim[]} }                                         // subject doc → claims  [App: claim list]
"check_compliance"    { input:{doc_id, standard_id, perspective?}, returns:{                               // the composite check     [App: gap matrix]
                          findings:{claim_id, requirement_id, verdict, severity, rationale,
                                    citation_claim, citation_requirement}[], summary } }
"compliance_report"   { input:{doc_id, standard_id}, returns:{gap_matrix, cited} }                          // requirement × coverage matrix
```

`check_compliance` and the periodic re-scan are **GraphWright-composed workflows** (`extract_claims →
retrieve_applicable_requirements → judge → report`) over ARD-registered capabilities — a composition, not a
bespoke build. New standards = new ingested requirement-KGs; same tool surface.

### 13.4 Gold-set & eval plan (two-track — de-risk the judgment node before domain investment)

**Track 1 — bootstrap the machine on existing *public* gold (no annotation cost):**
- **ContractNLI** (607 docs, 17 hypotheses; document-level NLI: entailment / contradiction / neutral **+ evidence
  spans**) — the *exact* shape of our judgment node (verdict + citation). Primary de-risking benchmark.
- **LegalBench** (162 legal-reasoning tasks incl. rule-application/compliance) — few-shot judgment benchmark.
- **Privacy sub-domain** (best-resourced): **GDPR120Q** (39,834 annotations / 120 policies / 108 labels),
  **OPP-115** (115 policies, span-annotated), **CLAUDETTE** (GDPR clause/violation detection), **PolicyQA**.
  Prior neuro-symbolic work (**ComplianceNLP**, **PolicyGuard**, **PrivComp-KG**) validates our KG+RAG+judge
  approach and offers design references.
- **Metric:** verdict accuracy + evidence-span/citation correctness; precision (alert fatigue) and recall
  (missed violations) reported **separately**; conservative-default calibrated. GATE-style bar before shipping.

**Track 2 — build the beachhead gold (advertising/marketing claims):**
- Mine **NAD / BBB National Programs case decisions** — public, *reasoned* adjudications ("claim was/wasn't
  substantiated / was misleading") = ready-made `(claim → rule → verdict + rationale)` examples — plus **FTC**
  enforcement actions / closing letters. Assemble a small **expert-anchored gold set** (the CUAD/ACORD analog for
  ad claims), graded like ACORD (verdict + evidence). This is the real domain work; everything else is reuse.

### 13.5 Which standard to support first (beachhead)

| Standard family | Public machine-readable regs | Gold-data availability | Market / crowding | Verdict |
|---|---|---|---|---|
| **Advertising/marketing claims** — FTC substantiation + Endorsement (16 CFR 255) / Green / Health Guides; **FINRA 2210 / SEC Marketing Rule** for financial | ✅ eCFR, FTC guides | 🟡 build from **NAD/FTC** decisions | funded but **enterprise-priced** (PerformLine, Saifr, Red Marker) → **SME gap open** | **BEACHHEAD** — matches the exec's data (campaigns/ad claims/brand messaging), public regs, SME gap |
| **Privacy** — GDPR / CCPA / PIPEDA | ✅ | ✅ **best** (OPP-115, GDPR120Q, CLAUDETTE, PolicyQA) | crowded (Spellbook, OneTrust) + commoditizing | **Not first GTM** — but use its gold to **bootstrap the judgment node**, and keep as a **fast-follow module** (data is there) |
| **Financial regulation** — FINRA/SEC/AML | ✅ (FIBO/FRO ontologies) | 🟡 | Saifr/RegTech incumbents | Second vertical if a financial-services design partner appears |

**Recommendation:** ship the **general engine** ("extracted elements vs a regulatory corpus's applicable
clauses"), **beachhead advertising/marketing-claims compliance** (FTC/NAD; FINRA 2210 if the partner is
financial), and **do NOT lead with GDPR/CCPA** (crowded/commoditized) — instead exploit privacy's rich public
datasets to *validate the machine cheaply* and keep privacy as a fast-follow. Grounding reuses our ODRL deontic
machinery (LKIF/LegalRuleML family). Privacy angle is **even stronger** here (customer artifacts, disputes,
incidents are highly sensitive) → lead the local/self-hosted mode.

### 13.6 Phase slotting & TODO

Slots as **Phase 2.5 / a parallel module** — after the Contract Portfolio X-ray MVP proves the engine and the
ARD/GraphWright composition, the compliance module is mostly "two new schemas + a judgment node + a gold set,"
reusing ingestion, retrieval, citation, reporting, and surfaces.

- [ ] **C-1** Requirements KG schema + `Requirement`/`Claim` templates (ground on LKIF/LegalRuleML/ODRL; reuse `contract_bridge` deontic edges). 🟡
- [ ] **C-2** Ingest one standard as a requirement-KG (start: FTC Endorsement Guides 16 CFR 255 — small, well-structured). 🟠
- [ ] **C-3** Claims-extraction template + closed `claim_type` vocab. 🟡
- [ ] **C-4** Applicability retrieval = reuse `llm_union` router (claim scope ↔ `applicability_scope`). 🟡
- [ ] **C-5** **Judgment node** (extends grounding judge + Flash→Pro cascade + conservative default + human gate). 🟠
- [ ] **C-6** **Track-1 eval** on ContractNLI (+ a privacy set) — verdict accuracy + citation correctness; set the GATE bar. 🟠
- [ ] **C-7** **Track-2 gold** from NAD/FTC decisions for ad-claims; measure precision/recall separately. 🟠
- [ ] **C-8** MCP tools `ingest_regulations` / `extract_claims` / `check_compliance` / `compliance_report` + GraphWright workflow. 🟡
- [ ] **C-9** Cited gap-matrix report + MCP-Apps UI. 🟡

**Risks:** judgment precision is legally sensitive (keep assistive + cited + human-gated, eval-gated); regulatory
corpora drift (re-ingest on change — a GraphWright periodic workflow); scope creep ("general compliance" is a
trap — one standard, deep, first).

### 13.7 Full landscape reference (research capture — secondary items)

The decision-relevant items are in §13.1–13.5; these additional resources surfaced in research and are kept so
nothing is lost. Tagged by likely relevance.

**Additional ontologies / rule formalisms:**
- **PolicyLR** — a *logic representation* for privacy policies; a reference for the judgment node's rule-logic form. (relevant to C-5)
- **DAOnt** — formal ontology for **EU Data Act** compliance. (reference; EU data-sharing vertical)
- **FinRegOnt family** — **FRO** (Financial Regulation Ontology), **Solvency II**, bank/insurance ontologies, XBRL ontology; ground a financial/insurance vertical. (reference; second vertical)

**Additional datasets / benchmarks:**
- **Polisis** (USENIX 2018) — deep-learning privacy-policy analysis; prior art for the privacy fast-follow.
- **PrivacyGLUE** — language-understanding benchmark over privacy policies; privacy eval.
- **CHANCERY** — corporate-governance reasoning benchmark for LLMs; adjacent governance-compliance eval.
- **"Better Call CLAUSE"** — a *discrepancy* benchmark auditing LLM legal reasoning; useful for **adversarial** stress-testing the judgment node (false-positive/negative probing).
- **MLEB (Massive Legal Embedding Benchmark)** — validate our BGE/embedding leg on legal text.
- **Open Terms Archive** — longitudinal corpora of terms/privacy documents; a ready subject-document source.

**Additional standards:**
- **EU Green Claims Directive** — EU counterpart to the FTC Green Guides for environmental/greenwashing claims; the EU arm of the ad-claims beachhead.
- **FTC Advertising Substantiation policy statement** — the "reasonable basis before a claim" doctrine underpinning the whole ad-claims requirement set.

> Compliance-module sources: [LKIF / legal rule ontologies](https://finregont.com/reference-ontologies/),
> [FIBO](https://www.semanticpartners.com/learn/what-is-fibo),
> [ContractNLI](https://www.gabormelli.com/RKB/ContractNLI_Benchmark),
> [LegalBench](https://github.com/HazyResearch/legalbench),
> [OPP-115](https://cmu.flintbox.com/technologies/9900a353-1bac-4d65-b197-d785cdda85bc),
> [GDPR120Q](https://link.springer.com/chapter/10.1007/978-3-032-12795-2_3),
> [CLAUDETTE meets GDPR](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3208596),
> [ComplianceNLP (KG-augmented RAG)](https://arxiv.org/pdf/2604.23585),
> [PolicyGuard (neuro-symbolic compliance)](https://arxiv.org/pdf/2606.32004),
> [FTC advertising substantiation](https://www.ftc.gov/legal-library/browse/ftc-policy-statement-regarding-advertising-substantiation),
> [FTC Endorsement Guides 16 CFR 255](https://www.ecfr.gov/current/title-16/chapter-I/subchapter-B/part-255),
> [FTC Green Guides](https://www.ftc.gov/business-guidance/resources/environmental-claims-summary-green-guides),
> [NAD / BBB National Programs](https://www.ftc.gov/business-guidance/resources/advertising-faqs-guide-small-business),
> [PerformLine pre-publication scanner](https://www.prweb.com/releases/performline-launches-pre-publication-scanner-bringing-ai-powered-compliance-review-to-marketing-creative-302823191.html),
> [Saifr](https://www.financialcontent.com/article/bizwire-2024-5-7-saifr-expands-ai-powered-offerings-for-regulatory-compliance),
> [PolicyLR](https://arxiv.org/pdf/2408.14830),
> [DAOnt (EU Data Act ontology)](https://arxiv.org/pdf/2604.16386),
> [FinRegOnt / Solvency II](https://finregont.com/solvency-2-ontology/),
> [PolicyQA](https://arxiv.org/pdf/2010.02557),
> [PrivacyGLUE](https://www.mdpi.com/2076-3417/13/6/3701),
> [Polisis / PrivComp-KG](https://arxiv.org/pdf/2404.19744),
> [CHANCERY (governance reasoning)](https://arxiv.org/pdf/2506.04636),
> [Better Call CLAUSE (LLM legal-reasoning audit)](https://arxiv.org/pdf/2511.00340),
> [MLEB (legal embedding benchmark)](https://arxiv.org/html/2510.19365v1),
> [Open Terms Archive datasets](https://opentermsarchive.org/en/datasets/),
> [EU Green Claims Directive / FTC Green Guides](https://www.ftc.gov/business-guidance/resources/environmental-claims-summary-green-guides).
