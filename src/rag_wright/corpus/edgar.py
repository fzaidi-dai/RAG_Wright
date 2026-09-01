"""EDGAR entity acquisition logic (T7, docs/Corpus_Acquisition.md).

Pure logic; the throttled/cached fetch and the CLI live in `scripts/acquire_edgar.py`.

`normalize_cik` is the **single** canonical CIK->EntityId normalization point: a CIK is 10-digit
zero-padded, which is exactly the canonical `EntityId` (T1), so normalization is zero-pad-to-10 then
validate against the strict contract. T8's registry loader **reuses this exact function** so the two
cannot drift from the T1 contract.

Name->CIK proposals are mechanical and structurally **UNVERIFIED**: linking a contract party to a
CIK is itself the entity-resolution problem (FR-C.7), so a fuzzy/mechanical proposal is never ground
truth. Each proposal carries an explicit `status` (T7 only ever emits UNVERIFIED; VERIFIED is set by
human verification at T10), and the CLI writes proposals to a separate `proposed/` location. The
`MatchCoverage` records which parties resolved and which did not, so T10 verification starts from a
known map.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel

from rag_wright.contracts.identifiers import EntityId

_CIK_PREFIX = re.compile(r"(?i)^cik[-:_ ]*")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Read this before treating the unresolved set as "not entities" (a note for the T10 verifier).
# Conservative normalized-conformed-name matching systematically leaves two classes unresolved, by
# design, not as a bug: (1) private companies and individuals, who are not in EDGAR at all (only
# public filers are), so a public-company-to-private-counterparty contract resolves one side only;
# and (2) name variants not close to the conformed name (subsidiaries filing under a parent, former
# names, DBAs), some of which the submissions former-names data can later help resolve. Both are
# exactly the cases human verification (T10) exists for. Consequence for the eval: graph entity
# coverage will be public-filer-centric (FR-C.7), which matters when reading multi-hop results.
UNRESOLVED_NOTE = (
    "Unresolved does NOT mean 'not an entity'. Conservative conformed-name matching intentionally "
    "misses private companies / individuals (absent from EDGAR) and name variants (subsidiaries, "
    "former names, DBAs). These are the cases human verification (T10) exists for; graph entity "
    "coverage is therefore public-filer-centric (FR-C.7)."
)


def normalize_cik(raw: int | str) -> EntityId:
    """Normalize a raw EDGAR CIK (int, unpadded, or ``CIK``-prefixed) to a canonical `EntityId`.

    This is the one place messy EDGAR CIK forms become the canonical identifier; T8 reuses it.
    """
    if isinstance(raw, bool):  # bool is an int subclass; reject explicitly
        raise ValueError("CIK must be an int or digit string, not bool")
    if isinstance(raw, int):
        digits = str(raw)
    elif isinstance(raw, str):
        digits = _CIK_PREFIX.sub("", raw.strip())
    else:
        raise ValueError(f"CIK must be an int or str, got {type(raw).__name__}")
    if not digits.isdigit():
        raise ValueError(f"CIK must be numeric, got {raw!r}")
    if len(digits) > 10:
        raise ValueError(f"CIK must be at most 10 digits, got {raw!r}")
    return EntityId.of(digits.zfill(10))


def normalize_name(name: str) -> str:
    """Fold a company/party name to a comparison key (lowercase, alphanumeric runs collapsed)."""
    return _NON_ALNUM.sub(" ", name.lower()).strip()


class MatchStatus(str, Enum):
    """The verification state of a name->CIK match. T7 emits only UNVERIFIED."""

    UNVERIFIED = "UNVERIFIED"  # mechanical proposal; NOT ground truth until human-verified (T10)
    VERIFIED = "VERIFIED"  # set only by human verification at T10


class NameToCikProposal(BaseModel):
    """A mechanical, UNVERIFIED name->CIK proposal (never ground truth until verified at T10)."""

    party_name: str
    proposed_cik: str  # canonical 10-digit EntityId value
    proposed_conformed_name: str  # the EDGAR conformed name matched
    match_method: str  # how the proposal was made, e.g. "normalized_conformed_name"
    status: MatchStatus = MatchStatus.UNVERIFIED


class MatchCoverage(BaseModel):
    """Which parties got a proposed CIK and which did not, so T10 starts from a known map."""

    resolved: list[NameToCikProposal]
    unresolved: list[str]


class LooseCandidate(BaseModel):
    """A looser token-overlap CIK candidate, shown WITH its evidence for human confirmation.

    A proposal to eyeball, never an auto-commit: the registry name and the tokens it matched on are
    surfaced so a common-token collision ("Federated", "Premier", "Excite") is caught on the
    evidence, not confirmed on a bare CIK. Always UNVERIFIED until a human approves it.
    """

    proposed_cik: str
    registry_name: str  # the EDGAR conformed name matched (the evidence)
    matched_tokens: list[str]
    score: float  # Jaccard token overlap
    status: MatchStatus = MatchStatus.UNVERIFIED


_CIK_TAG = re.compile(r"<cik>(\d+)</cik>", re.IGNORECASE)


def parse_browse_edgar_ciks(atom_xml: str) -> list[str]:
    """CIKs from an EDGAR `browse-edgar ...&output=atom` company-search response (delisted filers
    included). Returns canonical 10-digit CIKs, deduped in order; multiple means an ambiguous name."""
    seen: list[str] = []
    for match in _CIK_TAG.finditer(atom_xml):
        try:
            value = normalize_cik(match.group(1)).value
        except ValueError:
            continue
        if value not in seen:
            seen.append(value)
    return seen


class EdgarEvidence(BaseModel):
    """Grounded EDGAR evidence for a CIK, to confirm on (former_names resolve dot-com name changes)."""

    proposed_cik: str
    registry_name: str
    former_names: list[str] = []
    tickers: list[str] = []
    source: str = "edgar_submissions"
    status: MatchStatus = MatchStatus.UNVERIFIED


def parse_submissions_evidence(submissions: dict) -> EdgarEvidence:
    """The conformed name, former names, and tickers from an EDGAR submissions record."""
    return EdgarEvidence(
        proposed_cik=normalize_cik(submissions["cik"]).value,
        registry_name=submissions.get("name", ""),
        former_names=[f["name"] for f in submissions.get("formerNames", []) if f.get("name")],
        tickers=submissions.get("tickers", []),
    )


def former_names(submissions: dict) -> list[str]:
    """Former company names from an EDGAR submissions record (alias handling; reused by T24)."""
    return [f["name"] for f in submissions.get("formerNames", []) if f.get("name")]


def loose_cik_candidates(
    name: str, company_tickers: list[dict], *, top_n: int = 3, min_overlap: float = 0.34
) -> list[LooseCandidate]:
    """Token-overlap CIK candidates for a mention, ranked, each carrying its match evidence."""
    query = set(normalize_name(name).split())
    if not query:
        return []
    scored: list[tuple[float, set[str], dict]] = []
    for row in company_tickers:
        title_tokens = set(normalize_name(row["title"]).split())
        overlap = query & title_tokens
        if not overlap:
            continue
        jaccard = len(overlap) / len(query | title_tokens)
        if jaccard >= min_overlap:
            scored.append((jaccard, overlap, row))
    scored.sort(key=lambda s: (-s[0], s[2]["title"]))
    return [
        LooseCandidate(
            proposed_cik=normalize_cik(row["cik_str"]).value,
            registry_name=row["title"],
            matched_tokens=sorted(overlap),
            score=round(jaccard, 3),
        )
        for jaccard, overlap, row in scored[:top_n]
    ]


def propose_matches(
    party_names: list[str], company_tickers: list[dict]
) -> MatchCoverage:
    """Propose name->CIK matches mechanically against the EDGAR company registry seed.

    A conservative normalized-conformed-name match: it proposes only where a party's folded name
    equals an EDGAR conformed name. Everything else is left unresolved for human verification (T10),
    rather than fuzzy-guessed into a circular answer key.
    """
    by_name: dict[str, dict] = {}
    for row in company_tickers:
        by_name.setdefault(normalize_name(row["title"]), row)

    resolved: list[NameToCikProposal] = []
    unresolved: list[str] = []
    for name in party_names:
        row = by_name.get(normalize_name(name))
        if row is None:
            unresolved.append(name)
            continue
        resolved.append(
            NameToCikProposal(
                party_name=name,
                proposed_cik=normalize_cik(row["cik_str"]).value,
                proposed_conformed_name=row["title"],
                match_method="normalized_conformed_name",
            )
        )
    return MatchCoverage(resolved=resolved, unresolved=unresolved)


def build_edgar_registry(rows, *, aliases_by_cik=None):
    """ADR-0067: the SEC/EDGAR registry BUILDER (moved off the generic EntityRegistry). Build an EntityRegistry
    from `company_tickers.json` rows, keyed by a normalized CIK `EntityId`, with `normalize_name` as the surface
    normalizer. A row whose CIK is invalid is skipped (recorded in `skipped_ids`), never fabricated. The engine's
    EntityRegistry stays domain-neutral; this SEC builder + normalize_cik live in the SEC layer (the plug-in)."""
    from rag_wright.ontology.registry import EntityRegistry, RegistryRecord

    aliases_by_cik = aliases_by_cik or {}
    registry = EntityRegistry(normalize=normalize_name)
    for row in rows:
        raw_cik = row["cik_str"]
        try:
            entity_id = normalize_cik(raw_cik)
        except ValueError:
            registry.skipped_ids.append(str(raw_cik))
            continue
        registry.add(RegistryRecord(
            entity_id=entity_id, canonical_name=row["title"], ticker=row.get("ticker"),
            aliases=aliases_by_cik.get(entity_id.value, [])))
    return registry
