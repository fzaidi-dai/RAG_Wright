"""Author the SILVER answer key onto tests/fixtures/leg_a_silver/evidence_snapshot.json (in place).

Silver = a human (me) reading each record's frozen evidence and recording: answerable (from THIS evidence),
the key facts a correct answer must state, and the supporting citations. Not legal-SME-vetted -- that upgrade
edits this same file later. Citations are given by clause INDEX token (the `:<idx>:` in a chunk_id) and resolved
to the full chunk_id here, so the key stays readable.

Notes on the judgement calls (borderline items are deliberately kept -- they test precision):
- answerable-7 (Insurance): the served evidence is CREDIT-risk ratings + acceptable-guarantee rules + a BLANK
  performance-policy form -- it does NOT state what insurance each party must carry. Unanswerable from evidence.
- answerable-10 (Minimum Commitment): a heavily-redacted ([***]) research work-plan, not stated contractual
  minimums. Unanswerable from evidence.
Both are relabelled answerable=false (de-facto negatives) -- a strategy that answers them confidently is
over-reaching (a precision failure).
"""

from __future__ import annotations

import json
from pathlib import Path

_PATH = Path("tests/fixtures/leg_a_silver/evidence_snapshot.json")

# id -> (answerable, must_include, [citation index tokens], notes)
_KEY: dict[str, tuple] = {
    "answerable-0": (
        True,
        ["Liability capped to the fees paid / amounts paid under the agreement",
         "Consequential / indirect / special / incidental damages are excluded",
         "Higher cap (5 years of fees) for breaches of confidentiality (5.2) and IP indemnity (6.4(a))"],
        ["418", "559", "557", "560"],
        "Multiple caps: general damages exclusion + fee-paid aggregate cap + a raised cap for confidentiality/"
        "IP-indemnity breaches. A good answer names the fee-based cap AND the exclusion of indirect damages."),
    "answerable-1": (
        True,
        ["Breach of confidentiality (Article 10) is uncapped",
         "Fraud, gross negligence or willful misconduct is uncapped",
         "Indemnification obligations (Article 12) are uncapped"],
        ["839", "840", "841"],
        "Evidence is fragmentary (many bare section cross-refs) but the three carve-outs from the liability "
        "limit are legible at :839/:840/:841."),
    "answerable-2": (
        True,
        ["Governed primarily by the laws of the State of Texas",
         "State-specific franchise laws override for franchisees in those states (e.g. Illinois)"],
        ["919", "1180", "1221"],
        "Nuanced: many state addenda (Illinois, Minnesota, North Dakota, Washington) create apparent conflicts; "
        "the primary governing law is Texas, with state-specific carve-outs. A correct answer says Texas + the "
        "state-specific exceptions."),
    "answerable-3": (
        True,
        ["Either party may terminate at any time on three (3) months' prior written notice",
         "The parties may also terminate by mutual written agreement"],
        ["501", "588", "948"],
        "Clear termination-for-convenience: 3 months' notice by either party, or mutual agreement."),
    "answerable-4": (
        True,
        ["In-term covenant not to compete, and a post-term (Post-Term Period) covenant",
         "May not own, operate, be employed by, or hold an interest in a Competitive Business",
         "Competitive Business = property and/or casualty insurance distribution business",
         "Non-solicitation of the Franchised Business's customers"],
        ["1052", "1076", "1055", "1053"],
        "In-term + post-term non-compete, no interest in a competing P&C insurance business, non-solicit "
        "customers, geographically scoped to the Approved Location's city/county (:1077)."),
    "answerable-5": (
        True,
        ["Bank of America may audit/inspect the Supplier's records upon request during normal business hours",
         "Supplier must keep accurate records and retain them (seven years)"],
        ["333", "550", "549"],
        "Records kept + retained 7 years; BoA (and its reps / regulators) may inspect/audit/copy during "
        "business hours, excluding other customers' proprietary records."),
    "answerable-6": (
        True,
        ["The franchisee may not assign/transfer without the franchisor's approval (an unapproved transfer is a "
         "breach)",
         "The franchisor's interest is freely transferable",
         "Approved transfers are conditioned (new-owner qualification, general release, franchisor right of "
         "first refusal)"],
        ["632", "628", "662"],
        "Asymmetric: franchisor freely transfers; franchisee needs consent and must satisfy numerous conditions "
        "incl. a ROFR."),
    "answerable-7": (
        False, [], [],
        "UNANSWERABLE from the served evidence: it is about CREDIT-risk ratings, acceptable payment guarantees, "
        "and a BLANK 'insurance only policy of performance' form (fields with no values). It does not state what "
        "insurance each party must carry. Correct behaviour = abstain (or answer only 'a performance policy in "
        "favour of Ecopetrol is referenced, details not in the evidence')."),
    "answerable-9": (
        True,
        ["Fox grants the Licensee a limited exclusive license",
         "The Licensee may not enter an exclusive distribution agreement with a CSP other than VGSL in the "
         "listed VGSL Territories"],
        ["11", "120", "112"],
        "Exclusive license to Licensee + a restriction against granting exclusivity to CSPs other than VGSL in "
        "the enumerated territories."),
    "answerable-10": (
        False, [], [],
        "UNANSWERABLE from the served evidence: a heavily-redacted ([***]) research-project WORK PLAN / task "
        "schedule, not stated contractual minimum commitments (no volumes, dollars, or minimum quantities are "
        "legible). Correct behaviour = abstain."),
    "answerable-11": (
        True,
        ["Economics are shared off a defined profit pool: '365 Gross Profits' = invoice sales less returns/"
         "credits, trade allowances, COGS and distribution costs",
         "Costs are allocated by defined methods (e.g. default as a % of gross sales)"],
        ["103", "1052", "1064"],
        "WEAK-answerable: the evidence defines the shared profit pool and the cost-allocation methodology, but "
        "the actual split RATIO is not in the served evidence. A correct answer describes the pool/allocation "
        "basis and should NOT invent a percentage split."),
    "negative-0": (
        False, [], [],
        "NEGATIVE: evidence is entirely governing-law clauses; there is no product-warranty or warranty-"
        "duration content. Correct behaviour = abstain."),
    "negative-1": (
        False, [], [],
        "NEGATIVE: evidence is entirely audit/records clauses; there is no contract price or payment schedule. "
        "Correct behaviour = abstain."),
}


def _resolve(tokens: list[str], evidence: list[dict], rid: str) -> list[str]:
    out = []
    for tok in tokens:
        matches = [e["chunk_id"] for e in evidence if f":{tok}:" in e["chunk_id"]]
        if len(matches) != 1:
            raise SystemExit(f"[{rid}] citation token {tok!r} resolved to {len(matches)} evidence items (want 1)")
        out.append(matches[0])
    return out


def main() -> None:
    data = json.loads(_PATH.read_text(encoding="utf-8"))
    seen = set()
    for rec in data["records"]:
        rid = rec["id"]
        if rid not in _KEY:
            raise SystemExit(f"no silver key authored for record {rid}")
        answerable, must_include, tokens, notes = _KEY[rid]
        rec["answerable"] = answerable
        rec["must_include"] = must_include
        rec["expected_citations"] = _resolve(tokens, rec["evidence"], rid)
        rec["notes"] = notes
        seen.add(rid)
    _PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    ans = sum(r["answerable"] is True for r in data["records"])
    neg = sum(r["answerable"] is False for r in data["records"])
    print(f"[silver-key] authored {len(seen)} records: {ans} answerable + {neg} unanswerable -> {_PATH}")


if __name__ == "__main__":
    main()
