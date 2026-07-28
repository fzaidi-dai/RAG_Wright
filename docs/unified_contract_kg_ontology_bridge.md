# KG-0 — Unified Contract KG: the ontology bridge (design for the schema-review gate)

Status: **APPROVED at the schema-review gate — 2026-07-28** (ask-first data-model change; Q1–Q5 + the KG-2
model choice resolved in §9). Build proceeds to KG-1.
Implements FR-Q (structured, cited, typed clause facts) + FR-S (one store, provenance on everything).
Plan: `docs/unified_contract_kg_plan.md` (KG-0). Decision record: ADR-0033. Follows the `kg-extraction-recipe`
Skill. This document is the artifact under review; **nothing is written to the store and no dependency is
added until this schema is approved.**

---

## 1. What KG-0 decides (and what it does not)

KG-0 fixes the **schema of the one unified contract KG** and the **standards it is grounded in** — nothing
runtime. It answers: which node classes, which typed edges, which closed-vocabulary value nodes, how each of
today's 19 flat `PropertyDimension`s becomes a typed relationship, and how the graph cites FOLIO / ODRL /
PROV-O. It is deliberately a **small, closed-vocabulary bridge**, not a full clause ontology and not a
LegalRuleML deontic-reasoning engine (that is compliance/orchestration scope, near the two-halves boundary —
ADR-0033).

**Non-goals (scope discipline):** no new open vocabulary; no reasoning/inference rules; no change to
`chunk_id` or `entity_id` (load-bearing, ask-first — FR-S.2/.3); no store migration or `rdflib` add in KG-0
(both are downstream — KG-3 and KG-1 respectively).

---

## 2. Grounding (confirmed against code + standards, not guessed)

**2a. `docling-graph template from-ontology` (KG-1's compiler)** — reader `docling_graph/templategen/ontology/
owl.py` (now first-class in the framework graph, 2402 `docling_graph` nodes). It consumes exactly the OWL/RDFS
constructs the bridge uses: `owl:Class`, `rdfs:subClassOf`, `owl:equivalentClass`, `owl:ObjectProperty`,
`owl:DatatypeProperty`, `rdfs:domain`/`rdfs:range`, **`owl:oneOf` (closed enumerations)**, `owl:Restriction`
with `owl:onProperty` + cardinality/qualifiedCardinality (scalar vs multi-valued), `owl:hasKey`, `owl:union
Of`, and SKOS/RDFS labels+comments. CLI: `docling-graph template from-ontology SOURCE [--root] [--format owl]
[--include/--exclude glob] [-o out]`; OWL path needs the `templategen` extra (`rdflib>=7,<8`) — deterministic,
zero-LLM. So every construct below is one the compiler can read.

**2b. Existing store schema to upgrade** — `src/rag_wright/store/arcadedb.py`:
- `Clause` vertex `{clause_id, function, folio_iri}`; `PropertyValue` vertex `{value_key = "dimension:value",
  dimension, value, folio_iri}`; **one generic** `HasProperty` edge (Clause→PropertyValue) carrying
  `{confidence, span_id, chunk_id, source_doc_id}`. Value nodes deduped by canonical `(dimension,value)`;
  write is idempotent via the `clause_id` content-hash gate.
- `Contract` vertex already exists `{contract_id, name, agreement_type, parties_json, …}`.
- `Entity` vertex (the Leg-C Party) `{entity_id=CIK/`PRIVATE:<key>`, name, entity_type, …}` with
  `CONTRACTS_WITH`/`AFFILIATE_OF` edges — **done** (GP-1B, recall 0.991).

**2c. Standards** — FOLIO base `https://folio.openlegalstandard.org/` (already aligned in
`contracts/property.py`, naming-only). ODRL 2.2 (W3C Rec) namespace `http://www.w3.org/ns/odrl/2/`:
`odrl:Permission`/`Prohibition`/`Duty`, `odrl:action`/`target`/`assignee`/`assigner`/`constraint`. PROV-O
`http://www.w3.org/ns/prov#`: `prov:wasDerivedFrom`, `prov:Entity`. **Exact ODRL/PROV term IRIs are
transcribed from the published vocabularies and re-verified when we download the OWL at KG-1** (the ask-first
dependency step); KG-0 only commits to *adopting* them.

---

## 3. The three grounding layers (what each contributes)

| Layer | Source | Role in the bridge |
|---|---|---|
| **FOLIO** | live IRIs, already in `property.py` | clause-type + subject **type** vocabulary — promoted from naming-only maps to real `rdfs:subClassOf` / `owl:equivalentClass` |
| **ODRL 2.2** | W3C Rec OWL (downloadable at KG-1) | the **rights/duties spine** — deontic clauses become `Permission`/`Prohibition`/`Duty` with `action`/`target`/`assignee`, **and `odrl:constraint` (leftOperand/operator/rightOperand) carries the cap and temporal bounds** (Q5, approved for depth) |
| **Custom bridge OWL** | authored here (~a few dozen terms) | promotes the flat `PropertyDimension`s into **typed object properties to closed-vocab value nodes**, declares the clause-type `subClassOf` FOLIO, wires PROV-O provenance. The only thing we author; kept small + closed. |

---

## 4. FOLIO alignment audit (today → proposed)

Today `property.py` holds two **naming-only** maps (string→IRI, no OWL): `FOLIO_CLAUSE_IRI` (15 of 16 clause
types) and `FOLIO_SUBJECT_IRI` (4 subjects). Audit result:

- **15/16 clause types have a FOLIO home** → promote each to `cbr:<ClauseType> rdfs:subClassOf folio:<IRI>`
  (or `owl:equivalentClass` where the match is exact, e.g. Governing Law, Cap on Liability).
- **2 clause types have no clean FOLIO class** — *Joint IP Ownership* and *Revenue/Profit Sharing* — stay
  **native** (a `cbr:` class with no FOLIO parent, SKOS-labelled). Honest gap, unchanged from T57a.
- **4 carve-out subjects** (`fraud`, `gross_negligence`, `willful_misconduct`, `confidentiality`) have FOLIO
  concept IRIs → their value individuals get `owl:sameAs`/`skos:exactMatch folio:<IRI>`; the rest are native.
- **No FIBO** (rejected T57a as out-of-domain) and **no full FOLIO OWL import** — we cite IRIs, we do not
  vendor the whole ontology (scope discipline).

---

## 5. Proposed unified schema

### 5a. Node classes (small, closed)

| Class | Identity key | Notes |
|---|---|---|
| `Contract` | `contract_id` | exists; the Leg-A scope anchor |
| `Party` | `entity_id` (CIK / `PRIVATE:<key>`) | = today's `Entity`; Leg-C, done |
| `Clause` | `clause_id` (content-hash gated) | exists; `subClassOf` its FOLIO type via `function`→`cbr:` class |
| `ValueNode` (abstract) | `value_key = "dimension:value"` | today's `PropertyValue`; **specialized below** |
| ↳ closed-vocab value classes | ″ | `Mutuality`, `Favorability`, `Exception`, `Subject`, `PartyScope`, `Asymmetry`, `CapBasis`, `DamageType`, `WarrantyScope`, `ClaimScope`, `ProceduralDuty`, `LawMultiplicity`, `IpOwnership`, `NonsolicitTarget`, `RenewalMechanism` — each `owl:oneOf` its `CLOSED_VOCAB` individuals |
| ↳ open-valued value classes | ″ | `Jurisdiction`, `Amount`, `TemporalBound`, `NoticePeriod` — literal-bearing, **no `oneOf`** (open dims) |

**Value-node identity is unchanged** (`value_key`), so the existing dedup/upsert and every populated row stay
valid — this is an *additive typing* of `PropertyValue`, not a re-key.

### 5b. Typed edges (the ADR-0033 sanctioned set) + which dimensions ride each

The design tension the gate should confirm: **separate edge type** vs **qualifier property on an edge** vs
**value node**. Proposal below keeps the sanctioned edge set (~13) small and folds qualifiers onto edges as
properties, rather than exploding into 19 edge types.

| Edge (domain → range) | Grounding | Dimensions carried | Shape |
|---|---|---|---|
| `PARTY_TO` (Party → Contract) | — | role | edge prop `role` |
| `HAS_CLAUSE` (Contract → Clause) | — | clause type | edge prop `clause_type`/`function` |
| `CONTRACTS_WITH`, `AFFILIATE_OF` (Party ↔ Party) | — | **done (Leg C)** | as-is |
| `COVERS` (Clause → Subject / PartyScope) | ODRL `target` | `covered_subject`, `covered_parties` | value node |
| `CAPS` (Clause → CapBasis, + `CapConstraint`) | FOLIO Cap + ODRL | `cap_basis` (value node), `cap_quantum` (open) via `odrl:constraint` | node + `odrl:constraint` |
| `GRANTS` (Clause → ProceduralDuty/Right) | ODRL `Permission` | license/ROFR grants, `procedural` | ODRL `action`/`assignee` on edge |
| `PROHIBITS` (Clause → NonsolicitTarget / DamageType) | ODRL `Prohibition` | `nonsolicit_target`, `damage_type` (waivers) | ODRL `action`/`target` |
| `REQUIRES` (Clause → ProceduralDuty) | ODRL `Duty` | `procedural` (duty-to-defend) | ODRL `action` |
| `HAS_MUTUALITY` (Clause → Mutuality) | — | `mutuality`; `favorability`, `party_asymmetry` as sibling `HAS_*` OR edge props | **gate Q1** |
| `GOVERNED_BY` (Clause/Contract → Jurisdiction) | FOLIO Governing Law | `jurisdiction` (open), `law_multiplicity` (edge prop) | node + qualifier |
| `REFERENCES` (Clause → Clause) | — | cross-clause refs | edge |
| `EXCEPTS` (Clause → Exception) | — | `carve_out` | value node |
| `BOUNDED_BY` (Clause → TemporalBound) | ODRL | `temporal_bound`, `notice_period` (open) via `odrl:constraint` | `odrl:constraint` |
| (residual `HAS_*`) | — | `warranty_scope`, `claim_scope`, `ip_ownership`, `renewal_mechanism` (all closed, scalar) | value node |

Every edge keeps today's provenance (`confidence` ∈ EXTRACTED/INFERRED/AMBIGUOUS, `span_id`, `chunk_id`,
`source_doc_id`) via PROV-O `prov:wasDerivedFrom` — **no claim without a citation (FR-Q.6)**, unchanged.

### 5c. Dimension → relationship map (all 19)

| PropertyDimension | Vocab | Proposed edge → value class | Open? |
|---|---|---|---|
| mutuality | mutual/unilateral | `HAS_MUTUALITY` → `Mutuality` | closed |
| favorability | buyer/seller_favorable | `HAS_FAVORABILITY` → `Favorability` | closed |
| carve_out | 8 vals | `EXCEPTS` → `Exception` (4 FOLIO-tagged) | closed |
| covered_subject | 6 vals | `COVERS` → `Subject` (ODRL target) | closed |
| covered_parties | 3 vals | `COVERS` → `PartyScope` | closed |
| party_asymmetry | symmetric/different | `HAS_ASYMMETRY` → `Asymmetry` | closed |
| cap_basis | fixed_fee/multiple/other | `CAPS` → `CapBasis` | closed |
| cap_quantum | open | `CAPS` → `odrl:constraint` (`CapConstraint`, operator + rightOperand literal) | **open** |
| damage_type | 5 vals | `PROHIBITS` (waiver) → `DamageType` | closed |
| warranty_scope | 4 vals | `HAS_WARRANTY_SCOPE` → `WarrantyScope` | closed |
| claim_scope | 3 vals | `HAS_CLAIM_SCOPE` → `ClaimScope` | closed |
| procedural | 2 vals | `REQUIRES`/`GRANTS` → `ProceduralDuty` (ODRL) | closed |
| jurisdiction | open | `GOVERNED_BY` → `Jurisdiction` | **open** |
| law_multiplicity | single/multiple | `GOVERNED_BY` edge prop | closed |
| ip_ownership | assigned/joint/retained | `HAS_IP_OWNERSHIP` → `IpOwnership` | closed |
| nonsolicit_target | employees/customers | `PROHIBITS` → `NonsolicitTarget` (ODRL) | closed |
| temporal_bound | open | `BOUNDED_BY` → `odrl:constraint` (`TemporalConstraint`, operator + duration literal) | **open** |
| renewal_mechanism | auto/requires_notice | `HAS_RENEWAL` → `RenewalMechanism` | closed |
| notice_period | open | `BOUNDED_BY` → `odrl:constraint` (`TemporalConstraint`, notice duration) | **open** |

---

## 6. Proposed bridge OWL (Turtle skeleton — representative slice, for review)

Namespaces: `cbr:` = `https://ragwright.local/ontology/contract-bridge#` (our terms), `folio:`, `odrl:`,
`prov:`, `skos:`. Full file authored + parse-validated at KG-1 (when `rdflib` lands); this slice shows the
exact construct pattern for every category.

```turtle
@prefix cbr:  <https://ragwright.local/ontology/contract-bridge#> .
@prefix folio: <https://folio.openlegalstandard.org/> .
@prefix odrl: <http://www.w3.org/ns/odrl/2/> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .

cbr:Contract a owl:Class .
cbr:Clause   a owl:Class .
cbr:Party    a owl:Class .

# clause type -> FOLIO (subClassOf; equivalentClass where exact)
cbr:CapOnLiability a owl:Class ; rdfs:subClassOf folio:RD0R9lAU0GYr2Rm3CDcMWQn ;
    skos:prefLabel "Cap on Liability" .
cbr:GoverningLaw   a owl:Class ; owl:equivalentClass folio:RCinm0jvGGkzcHth7AnasRI .
cbr:JointIPOwnership a owl:Class ; skos:prefLabel "Joint IP Ownership" .   # native: no FOLIO parent

# closed-vocab value class via owl:oneOf (the compiler emits a Pydantic Literal/Enum)
cbr:Mutuality a owl:Class ; owl:oneOf ( cbr:mutual cbr:unilateral ) .
cbr:mutual a cbr:Mutuality . cbr:unilateral a cbr:Mutuality .

# typed edge = ObjectProperty with domain/range (+ cardinality restriction for scalar dims)
cbr:hasMutuality a owl:ObjectProperty, owl:FunctionalProperty ;
    rdfs:domain cbr:Clause ; rdfs:range cbr:Mutuality .
cbr:Clause rdfs:subClassOf [ a owl:Restriction ;
    owl:onProperty cbr:hasMutuality ; owl:maxQualifiedCardinality 1 ;
    owl:onClass cbr:Mutuality ] .

# ODRL deontic slice: a non-solicit clause PROHIBITS an action on a target
cbr:prohibits a owl:ObjectProperty ; rdfs:subPropertyOf odrl:prohibition ;
    rdfs:domain cbr:Clause ; rdfs:range cbr:NonsolicitTarget .
cbr:NonsolicitTarget a owl:Class ; owl:oneOf ( cbr:employees cbr:customers ) .

# open-valued dim: literal-bearing, NO oneOf
cbr:governedBy a owl:ObjectProperty ; rdfs:domain cbr:Clause ; rdfs:range cbr:Jurisdiction .
cbr:Jurisdiction a owl:Class .   # value carries an rdfs:label literal; open vocabulary

# ODRL constraint (Q5 depth): cap + temporal bounds carry leftOperand/operator/rightOperand
cbr:caps a owl:ObjectProperty ; rdfs:domain cbr:Clause ; rdfs:range cbr:CapBasis .
cbr:CapConstraint a owl:Class ; rdfs:subClassOf odrl:Constraint .   # e.g. "12 months' fees"
cbr:boundedBy a owl:ObjectProperty ; rdfs:domain cbr:Clause ; rdfs:range cbr:TemporalConstraint .
cbr:TemporalConstraint a owl:Class ; rdfs:subClassOf odrl:Constraint .   # cap_quantum / temporal_bound / notice_period
# each constraint individual: odrl:leftOperand (what is bounded), odrl:operator (lteq/…), odrl:rightOperand (the open literal)

# provenance (PROV-O) — every assertion cites its span/chunk
cbr:Assertion a owl:Class ; rdfs:subClassOf prov:Entity .
```

---

## 7. Store build path (KG-3) — typed, not a flat-graph parallel (Q3 decided)

**Decision (Q3): build the typed graph properly; the flat `HasProperty` graph is retired, not kept as a
fallback.** The flat `Clause → HasProperty → PropertyValue(dimension,value)` layer was a stopgap; the whole
point of KG-0..KG-6 is the typed ER graph, so we do it right rather than carry a dead parallel structure.

- **Node identities preserved (this is the only "additive" part).** `Clause.clause_id`, `Party.entity_id`,
  `Contract.contract_id`, and the value-node `value_key = "dimension:value"` are kept, so **no re-key**, no
  `chunk_id`/`entity_id` change (FR-S.2/.3), and the `clause_id` content-hash idempotency gate is untouched.
  A value node simply gains its typed class + optional FOLIO `sameAs`.
- **Edge layer is rebuilt typed.** Emit the typed edge set (`HAS_CLAUSE`, `PARTY_TO`, `COVERS`, `CAPS`,
  `GRANTS`/`PROHIBITS`/`REQUIRES`, `HAS_MUTUALITY`, `GOVERNED_BY`, `REFERENCES`, `EXCEPTS`, `BOUNDED_BY`,
  residual `HAS_*`) with `odrl:constraint` on cap/temporal, from the **KG-2 granite extraction**. The generic
  `HasProperty` edge type is dropped (or, if a clean deterministic mapping exists, regenerated once into typed
  edges — whichever is cleaner at KG-3; "additive or regenerate as appropriate").
- **Provenance schema unchanged** on every typed edge (`confidence` ∈ EXTRACTED/INFERRED/AMBIGUOUS, `span_id`,
  `chunk_id`, `source_doc_id`), now read as PROV-O `prov:wasDerivedFrom` — no claim without a citation (FR-Q.6).
- **The existing flat population (~3,886 clauses, DeepSeek V4 Pro) is superseded**, not migrated: KG-2
  re-extracts under the typed schema with granite (§8). It may serve at most as a throwaway comparison baseline
  at KG-6, nothing load-bearing.

---

## 8. Ask-first / downstream boundary (not in KG-0)

- **`rdflib` (+ `linkml-runtime`) via the `templategen` extra** — added at **KG-1** to compile the OWL. Ask-first
  dependency; not added now.
- **Store DDL change** (new typed edge types; drop the flat `HasProperty`) — landed at **KG-3**; ask-first
  schema change; not run now.
- **Extraction model — no A/B (decided).** KG-2 extracts clause properties with **granite-4.1-8b**, the model
  already chosen for Leg C (GP-1B entity KG, real relational recall 0.991). We do **not** re-run a granite-vs-
  DeepSeek A/B: the same extractor built a near-perfect entity KG, so it is expected to carry clause-property
  extraction too. DeepSeek is a **contingency only** — invoked at KG-6 *iff* granite falls significantly below
  par in the final analysis (unexpected). The property-grounding judge (ADR-0028) remains the quality gate.

---

## 9. Gate resolutions (approved 2026-07-28)

- **Q1 — edge granularity → DECIDED: distinct `HAS_*` edges.** Keep `HAS_MUTUALITY`/`HAS_FAVORABILITY`/… as
  named typed edges (matches ADR-0033; clearer queries), not one `HAS_ATTRIBUTE` + `dimension` discriminator.
- **Q2 — residual dimension naming → DECIDED: confirmed `HAS_*`/`EXCEPTS`/`BOUNDED_BY`.**
- **Q3 — flat vs typed → DECIDED: build typed; retire the flat graph** (§7). Not kept as a fallback; node
  identities preserved, edge layer rebuilt typed. Additive-or-regenerate chosen per-case at KG-3.
- **Q4 — the `.ttl` → DECIDED: author + parse-validate at KG-1** (keeps `rdflib` at its planned step). KG-0
  ships the reviewed ontology as the Turtle in §6.
- **Q5 — ODRL depth → DECIDED: full depth now.** Adopt the `Permission`/`Prohibition`/`Duty` +
  `action`/`target`/`assignee` core **and** `odrl:constraint` (leftOperand/operator/rightOperand) for the cap
  (`cap_basis`/`cap_quantum`) and temporal (`temporal_bound`/`notice_period`) bounds — modeled in §5b/§5c/§6.

**KG-2 extraction model → DECIDED: granite-4.1-8b, no A/B** (see §8); DeepSeek is a KG-6 below-par contingency
only.
```
