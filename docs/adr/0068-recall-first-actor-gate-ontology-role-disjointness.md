# ADR-0068: The compliance actor gate is recall-first — compatible unless the ontology declares roles disjoint; skipped pairs are reported

Date: 2026-09-01
Status: Accepted (implemented; engine issue 0013, filed by RuleWright)

Amends **ADR-0065** (deontic + actor applicability gates, query side). Builds on **ADR-0066** (the ontology `.ttl`
is the single runtime source of truth for domain KNOWLEDGE) and **ADR-0067** (domain-pack retargeting): the new
role-disjointness knowledge is authored in the ontology, not code, and a customer domain extends it in its pack.

## Context

ADR-0065 made a rule's `actor` a symbolic gate: `actor_matches` normalized both sides to a canonical role and
required **exact equality** (`ra in subject_actors`), recall-first only when a side was **absent or generic**. But
the two sides come from **two independent LLM extractions over an open vocabulary** — the rule's actor at policy
ingest, the assertion's actor at check time. When both confidently pick *different-but-equally-defensible* words
for the same real-world party, the exact-equality gate dropped the pair **before any judge call**, with no
finding, no `needs_review`, and no trace in coverage — a **silent recall loss**, and the *likeliest* failure,
because a confident extractor is exactly what produces two different specific words.

RuleWright hit it on the first document measured (issue 0013): a rule bound to `advertiser` ("must not describe a
price as a discount…") never paired with a pricing assertion whose extracted actor was `seller` — even though a
seller advertising **is** the advertiser. The pricing violation, caught before the ADR-0065 upgrade, silently
disappeared while the gap matrix still reported the rule as "checked". A recall hole that reports full coverage is
the exact failure mode ADR-0065's own context warned about ("silent recall loss at scale"), reintroduced one
layer down.

## Decision

Invert the gate to **recall-first**, drive its narrowing from the **ontology**, and make it **never silent**.

1. **Compatible unless ontology-disjoint.** `actor_matches` no longer requires role equality. A specific rule
   actor matches unless it is **disjoint from every** subject actor; a generic/absent role on either side still
   never gates. This inverts the failure direction: an unmodelled or merely-different-but-overlapping role costs a
   **judge call**, never a missed violation. The same predicate (`roles_compatible`) also replaces the DEON-9
   defense-linker's `_actor_compatible`, so a same-domain permission can now defend an O/F rule.

2. **Disjointness is ontology knowledge (ADR-0066).** Each `cmp:ActorRole` declares a `cmp:roleDomain`; two
   canonical roles are disjoint **iff both declare a domain and the domains differ**. All the advertising-ecosystem
   roles (advertiser, endorser, expert, consumer, seller) share domain `"advertising"`, so they are mutually
   compatible — the silent-drop fix. A role with **no** declared domain is compatible with everything
   (recall-first). Cross-DOMAIN narrowing (a labor `employer` rule not judged against an advertising assertion) is
   a **customer domain pack** declaring its roles under a different `roleDomain` — never an engine edit. Loaded at
   runtime by `load_role_domains`; there is no hardcoded role relationship in Python.

3. **The gate is never silent (report gated pairs).** `_actor_gated_pairs` recomputes, from the *same* module
   gate, every `(assertion|document, rule)` pair the actor gate skipped, and the check surfaces them on
   `ComplianceReport.gated_pairs` (`{requirement_id, citation, actor, subject_actors, scope, claim_id}`). Empty is
   the recall-first norm (nothing among the advertising roles is disjoint); a non-empty list lets a consumer state
   honest coverage — "checked N rules; K pairs gated by role" — so a symbolic drop is always visible and a
   precision/recall trade-off is tunable, never invisible.

The advertising roles were deliberately left **all mutually compatible** (no defensible disjoint pair exists among
them — an endorser's or seller's claim can bear on an advertiser rule), so the shipped disjoint-set is empty by
design and the gate's per-domain value is *not* excluding overlapping roles but (a) collapsing synonyms
(ADR-0065, unchanged) and (b) offering safe cross-domain narrowing when a customer models it.

## Consequences

- **Issue 0013 fixed, live-verified.** Real ArcadeDB KG + real BGE embedder + real OpenRouter judge on the issue's
  exact case: §3 (advertiser) now pairs with the `seller` pricing assertion and the judge returns `violation`
  ("$99 → $49 without indicating $99 was the usual selling price"), while §3 is correctly `compliant` on the cure
  and endorsement assertions — the wider candidacy widens *who is judged*, the judge still discriminates.
  `gated_pairs` is empty.
- **More judge calls, by design.** Recall-first widens per-assertion prohibition candidacy (bounded by the
  semantic top-k, unchanged) and keeps more obligations. Cost is traded for recall deliberately (RuleWright:
  "an unmodelled role pair costs a judge call rather than a missed violation").
- **Behavior change for existing tests.** Same-domain role pairs that the exact-equality gate excluded are now
  judged; the DEON-6/7/9/10 tests that asserted exclusion were rewritten to reflect recall-first + cross-domain
  disjointness (the assertions encoded the bug).
- **New engine-facing field.** `ComplianceReport.gated_pairs` is additive (default `[]`); RuleWright reads it to
  state honest coverage. No identifier or schema change.
- **Retargetable.** A customer domain gets cross-domain actor narrowing by declaring `cmp:roleDomain` on its roles
  in its pack — mechanism in code, knowledge in the ttl (ADR-0066/0067).
