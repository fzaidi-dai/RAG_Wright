# Handoff to RuleWright — engine issue 0013 fixed: the actor gate is recall-first (ADR-0068)

Date: 2026-09-01. From: RAG_Wright engine. Re: your issue 0013 ("the actor gate silently drops a real violation on
a role disagreement"). **Resolved** — implemented all three of your suggested properties (1 gate on disjointness
not inequality, 2 model the roles in the ontology, 3 report what the gate dropped). Live-verified on your exact
reproduction. Detail in `docs/adr/0068-*.md`.

## What changed in the engine you consume

1. **The actor gate is recall-first (behavior change, no API change).** `actor_matches` no longer requires role
   *equality*. A specific rule actor now matches a subject actor unless the ontology makes them **disjoint**;
   generic/absent roles still never gate. Your case — rule `advertiser` vs assertion `seller` — now pairs and is
   judged, because both are advertising-domain roles. The judge (not the gate) decides applicability.
   - **Effect you'll see:** the dropped pricing violation is caught again; more generally, a few **more** judge
     calls per document (recall traded for cost, deliberately — bounded by the same semantic top-k). No finding is
     silently missing on a role-word disagreement anymore.

2. **`ComplianceReport.gated_pairs` — a new additive field (default `[]`).** Every `(assertion|document, rule)`
   pair the actor gate skipped, each `{requirement_id, citation, actor, subject_actors, scope
   ("assertion"|"document"), claim_id}`. Empty is the norm (nothing among the advertising roles is disjoint). This
   is the honest-coverage hook you asked for: **your coverage line can now state "checked N rules; K pairs gated by
   role" instead of overclaiming.** With the recall-first gate the only entries are ontology-declared cross-domain
   skips (correctly inapplicable), so an empty list genuinely means "no pair was withheld."

3. **Role disjointness is ontology knowledge (`cmp:roleDomain`).** Each `cmp:ActorRole` in `compliance_bridge.ttl`
   declares a domain; all advertising roles share `"advertising"`. Two roles are disjoint iff both declare a domain
   and the domains differ; an unmodelled role is compatible with everything. **To get cross-domain narrowing** (e.g.
   a labor `employer` rule not judged against an advertising assertion), a domain pack declares its roles under a
   different `roleDomain` — no engine edit. The advertising roles are intentionally left all-compatible (no
   defensible disjoint pair exists among them).

## Actions on your side

- [ ] **Bump the engine version.** No breaking API/identifier/schema change.
- [ ] **Make your coverage statement honest using `gated_pairs`** (you flagged this as yours to fix): report pairs
      *evaluated*, not just rules *consulted*; surface a non-empty `gated_pairs` (rare) as "K pairs not evaluated
      (role scope)".
- [ ] **Re-check any golden expectations tied to which pairs get judged** — recall-first means same-domain role
      pairs that were previously excluded are now judged (e.g. an advertiser prohibition is now also checked
      against an endorser/seller assertion). This is the intended fix, not a regression.
- [ ] Heads-up only: no change needed to your entrypoint (`check_document`, generic path, `sources=[...]`).
