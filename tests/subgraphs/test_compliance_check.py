"""CC-6 (compliance §13.3): the `compliance_check` subgraph -- the headline composite.

extract_claims (CC-3) -> retrieve_applicable_requirements (claim scope <-> requirement applicability, with a
section->claim_type map for empty-scope requirements) -> judge per (claim, requirement) pair (CC-4, concurrent,
with AD-LEVEL disclosure context) -> assemble (cited findings + gap matrix + summary). Query-side posture:
degrade-to-empty on failure, never crash; conservative default. Hermetic: all seams stubbed, no LLM/DB.
"""

from __future__ import annotations

from rag_wright.capabilities.compliance_judgment import JudgeVerdict
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.compliance import (
    Claim,
    ClaimType,
    ComplianceReport,
    Constraint,
    DeonticType,
    Requirement,
    Verdict,
)
from rag_wright.subgraphs.compliance_check import (
    applies_to,
    build_compliance_check,
    register_compliance_check,
)


def _claim(text="clinically proven to work", ctype=ClaimType.HEALTH, disc=None) -> Claim:
    return Claim(fact_id=Claim.make_id("ad", 0, text), source_doc="ad", claim_type=ctype,
                 assertion_text=text, disclosures_present=disc or [])


def _req(section="255.5", scope=("endorsement",), text="A material connection must be disclosed.") -> Requirement:
    return Requirement(requirement_id=Requirement.make_id("FTC 16 CFR 255", section, text),
                       source="FTC 16 CFR 255", citation=f"§ {section}", deontic_type=DeonticType.OBLIGATION,
                       actor="advertiser",
                       applicability_scope=[Constraint(dimension="claim_type", value=v) for v in scope],
                       requirement_text=text)


# --- applicability: section-based (context), extracted scope only broadens (the ontology enrichment) ---------


def test_context_scoped_guides_apply_broadly():
    # the FTC endorsement guides apply by CONTEXT, not claim_type: an efficacy/health claim in an endorsement
    # IS subject to §255.5 disclosure, so §255.5 applies to any claim type (context-based)
    r = _req(scope=(), text="Disclose material connections.")
    assert applies_to(r, _claim(ctype=ClaimType.HEALTH)) is True
    assert applies_to(r, _claim(ctype=ClaimType.ENDORSEMENT)) is True
    assert applies_to(_req(section="255.1", scope=()), _claim(ctype=ClaimType.PRICING)) is True


def test_definitions_section_applies_to_nothing():
    # §255.0 (Purpose and definitions) is not an operative rule -> excluded (empty section set + no scope)
    assert applies_to(_req(section="255.0", scope=()), _claim(ctype=ClaimType.HEALTH)) is False


def test_extracted_scope_only_broadens_never_excludes():
    # an unmapped section defaults to all; extracted scope can ADD a type but a noisy scope must not EXCLUDE a
    # claim the section covers (§255.5 with a granite 'endorsement' scope still applies to an efficacy claim)
    assert applies_to(_req(section="255.5", scope=("endorsement",)), _claim(ctype=ClaimType.EFFICACY)) is True


# --- the composite: extract -> retrieve -> judge -> assemble -------------------------------------


def _graph(claims, requirements, judge_fn):
    async def _claims_fn(text, source):
        return claims

    return build_compliance_check(
        claims_fn=_claims_fn,
        requirements_fn=lambda: requirements,
        judge_fn=judge_fn,
    )


async def test_produces_cited_findings_and_a_summary():
    claims = [_claim(ctype=ClaimType.ENDORSEMENT)]
    reqs = [_req(scope=("endorsement",))]

    async def _judge(c, r):
        return JudgeVerdict(verdict="violation", rationale="no disclosure", confidence=0.9)

    graph = _graph(claims, reqs, _judge)
    out = await graph.ainvoke({"subject_text": "…ad…", "source_doc": "ad"})
    report = out["report"]
    assert isinstance(report, ComplianceReport)
    assert len(report.findings) == 1 and report.findings[0].verdict is Verdict.VIOLATION
    assert report.summary == {"violation": 1}
    assert report.gap_matrix and report.gap_matrix[0]["citation"] == "§ 255.5"


async def test_only_applicable_pairs_are_judged():
    # a §255.0 (definitions) requirement applies to no claim -> no applicable pair -> no findings
    async def _judge(c, r):
        return JudgeVerdict(verdict="violation", rationale="", confidence=1.0)

    graph = _graph([_claim(ctype=ClaimType.PRICING)],
                   [_req(section="255.0", scope=(), text="Endorsement means…")], _judge)
    out = await graph.ainvoke({"subject_text": "x", "source_doc": "ad"})
    assert out["report"].findings == []


async def test_ad_level_disclosures_reach_the_judge():
    # one claim discloses #ad, another has none; both must be judged WITH the ad-level disclosure set
    seen = {}

    async def judge(claim, req):
        seen[claim.assertion_text] = set(claim.disclosures_present)
        return JudgeVerdict(verdict="compliant", rationale="", confidence=0.9)

    disclosed = _claim(text="two shades whiter", ctype=ClaimType.ENDORSEMENT, disc=["#ad"])
    bare = _claim(text="available now", ctype=ClaimType.ENDORSEMENT, disc=[])
    graph = _graph([disclosed, bare], [_req(scope=("endorsement",))], judge)
    await graph.ainvoke({"subject_text": "x", "source_doc": "ad"})
    assert "#ad" in seen["available now"]  # the bare fragment sees the ad-level #ad (the CC-4 residual fix)


async def test_degrades_to_empty_when_extraction_fails():
    async def boom(text, source):
        raise RuntimeError("extractor down")

    async def _judge(c, r):
        return JudgeVerdict(verdict="violation", rationale="", confidence=1.0)

    graph = build_compliance_check(claims_fn=boom, requirements_fn=lambda: [_req()], judge_fn=_judge)
    out = await graph.ainvoke({"subject_text": "x", "source_doc": "ad"})
    assert out["report"].findings == [] and out["report"].source_doc == "ad"  # never crashes; empty report


def test_registers_as_a_subgraph():
    reg = CapabilityRegistry()
    register_compliance_check(reg)
    entry = reg.get("compliance_check")
    assert entry.kind == "subgraph" and entry.contract is ComplianceReport


# --- CC-8: semantic narrowing (content top-k + always-include context + dedup) -------------------

from rag_wright.contracts.compliance import RuleScope  # noqa: E402
from rag_wright.subgraphs.compliance_check import build_select_fn, rule_scope_of  # noqa: E402


def test_rule_scope_context_for_disclosure_else_content():
    # §255.5 (material connections / disclosure) applies regardless of claim content -> CONTEXT (always-include);
    # §255.1 (objective claims need substantiation) is content-specific -> CONTENT (narrow by similarity)
    assert rule_scope_of(_req(section="255.5")) is RuleScope.CONTEXT
    assert rule_scope_of(_req(section="255.1")) is RuleScope.CONTENT


class _FakeEmbedder:
    """Maps a text to a fixed vector by the first keyword it contains -> controllable cosine ranking."""

    def __init__(self, table):
        self._table = table

    def encode_dense(self, text):
        for key, vec in self._table.items():
            if key.lower() in text.lower():
                return vec
        return [0.0, 0.0, 0.0]


def test_select_keeps_context_and_top_k_content_dropping_irrelevant():
    claim = _claim(text="reduce your risk of a heart attack", ctype=ClaimType.HEALTH)
    health = _req(section="255.1", scope=(), text="Health claims need competent and reliable scientific evidence.")
    kids = _req(section="255.1", scope=(), text="Endorsements to children warrant special care.")
    disclosure = _req(section="255.5", scope=(), text="A material connection must be disclosed.")
    emb = _FakeEmbedder({"heart attack": [1, 0, 0], "scientific evidence": [1, 0, 0],
                         "children": [0, 1, 0], "material connection": [0, 0, 1]})
    select = build_select_fn(emb, [health, kids, disclosure], k=1)

    picked = {r.requirement_id for r in select(claim, [health, kids, disclosure])}
    assert health.requirement_id in picked      # top-1 content (cosine 1.0 with the claim)
    assert disclosure.requirement_id in picked   # CONTEXT rule always included (not by similarity)
    assert kids.requirement_id not in picked     # irrelevant content dropped by top-k (the noise cut)


def test_select_dedupes_near_identical_rules():
    claim = _claim(text="lose weight fast", ctype=ClaimType.EFFICACY)
    a = _req(section="255.1", scope=(), text="Weight-loss claims must be substantiated (variant A).")
    b = _req(section="255.1", scope=(), text="Weight-loss claims must be substantiated (variant B).")
    emb = _FakeEmbedder({"lose weight": [1, 0, 0], "weight-loss": [1, 0, 0]})  # a and b get the SAME vector
    select = build_select_fn(emb, [a, b], k=5, dedup_threshold=0.99)
    picked = select(claim, [a, b])
    assert len(picked) == 1  # near-identical rules collapse to one


async def test_compliance_check_uses_select_fn_when_provided():
    # the subgraph routes through select_fn (narrowing) instead of judging all applicable pairs
    claim = _claim(ctype=ClaimType.ENDORSEMENT)
    r1, r2 = _req(text="rule one"), _req(text="rule two")
    seen = []

    def select(c, reqs):  # select_fn stays sync (embedder narrowing, no model call)
        return [r1]  # narrow to just r1

    async def judge(c, r):
        seen.append(r.requirement_text)
        return JudgeVerdict(verdict="compliant", rationale="", confidence=1.0)

    async def _claims_fn(t, s):
        return [claim]

    graph = build_compliance_check(claims_fn=_claims_fn, requirements_fn=lambda: [r1, r2],
                                   judge_fn=judge, select_fn=select)
    await graph.ainvoke({"subject_text": "x", "source_doc": "ad"})
    assert seen == ["rule one"]  # only the narrowed requirement was judged


# --- COMP-VERDICT-GENERIC: domain-agnostic verdict on a free-text subject (no applicability ontology) ----------


def test_generic_facts_fn_makes_one_checkable_fact_from_free_text():
    from rag_wright.contracts.compliance import CheckableFact
    from rag_wright.subgraphs.compliance_check import generic_facts_fn

    facts = generic_facts_fn("Employer did not record a work-related injury.", "osha_scenario")
    assert len(facts) == 1 and isinstance(facts[0], CheckableFact)
    assert facts[0].assertion_text.startswith("Employer did not record")
    assert facts[0].source_doc == "osha_scenario"
    assert generic_facts_fn("  ", "s") == []  # empty subject -> no fact


def test_semantic_select_without_applicability_filter_keeps_all_then_ranks():
    # filter_applicability=False -> no claim_type pre-filter (a generic CheckableFact has none), pure BGE ranking
    from rag_wright.contracts.compliance import CheckableFact
    from rag_wright.subgraphs.compliance_check import build_select_fn

    class _Emb:  # deterministic DISTINCT 2-D vectors so the two reqs are not near-duplicates (dedup)
        def encode_dense(self, text):
            return [1.0, 0.0] if "short" in text else [0.0, 1.0]

    reqs = [_req("255.1", text="short"), _req("255.5", text="a much longer requirement text here")]
    fact = CheckableFact(fact_id="f0", source_doc="s", assertion_text="x")
    select = build_select_fn(_Emb(), reqs, k=5, context_k=3, filter_applicability=False)
    got = select(fact, reqs)
    assert {r.requirement_id for r in got} == {r.requirement_id for r in reqs}  # all kept (no applicability drop)


async def test_run_generic_compliance_verdict_produces_a_cited_report_without_ontology():
    # end-to-end hermetic: a non-advertising subject + requirements with EMPTY applicability -> a verdict + findings
    from rag_wright.capabilities.compliance_judgment import JudgeVerdict
    from rag_wright.subgraphs.compliance_check import run_generic_compliance_verdict

    class _Store:
        def all_requirements(self):
            return [{"requirement_id": "osha:1904.4:h", "source": "OSHA", "citation": "§ 1904.4",
                     "deontic_type": "obligation", "actor": "employer",
                     "requirement_text": "Employers must record each work-related injury.",
                     "evidence_standard": None, "severity": None, "applicability_json": "[]",
                     "confidence": "EXTRACTED"}]

    class _Emb:
        def encode_dense(self, text):
            return [1.0, 0.0]

    async def _judge(fact, requirement):  # domain-agnostic ASYNC judge stub -> a verdict on text
        return JudgeVerdict(verdict="violation", rationale="not recorded", confidence=0.9)

    import rag_wright.capabilities.compliance_judgment as cj
    orig = cj.build_ageneric_judge_fn
    cj.build_ageneric_judge_fn = lambda model_id: _judge  # inject the stub async judge (source of the local import)
    try:
        report = await run_generic_compliance_verdict(
            "The employer failed to record a work-related injury on the OSHA log.",
            "osha_case", store=_Store(), judge_model_id="stub", embedder=_Emb(),
            aextract_fn=_stub_sentence_extractor())
    finally:
        cj.build_ageneric_judge_fn = orig
    assert report.findings and report.findings[0].verdict.value == "violation"
    assert report.findings[0].citation_requirement.startswith("§ 1904.4")  # both-sided citation preserved
    assert report.summary.get("violation") == 1


# --- COMP-APPLIC-1 Increment 0: the DIMENSION-AGNOSTIC matcher (any domain, no per-domain code) ----------------


def test_constraint_applies_is_dimension_agnostic_and_recall_first():
    from rag_wright.contracts.compliance import Constraint
    from rag_wright.subgraphs.compliance_check import constraint_applies

    C = Constraint
    # requirement constrains a dimension; subject matches on it -> applies
    assert constraint_applies([C(dimension="hazard_type", value="chemical")],
                              [C(dimension="hazard_type", value="chemical")])
    # subject HAS the dimension but a non-matching value -> excluded
    assert not constraint_applies([C(dimension="hazard_type", value="chemical")],
                                  [C(dimension="hazard_type", value="fall")])
    # requirement constrains a dimension the SUBJECT lacks -> recall-first, NOT excluded
    assert constraint_applies([C(dimension="employer_size", value="small")],
                              [C(dimension="industry", value="construction")])
    # requirement with NO constraints -> applies to any subject
    assert constraint_applies([], [C(dimension="anything", value="x")])
    # multi-dimension: all constrained dims the subject carries must match
    req = [C(dimension="industry", value="construction"), C(dimension="employer_size", value="small")]
    assert constraint_applies(req, [C(dimension="industry", value="construction"),
                                    C(dimension="employer_size", value="small")])
    assert not constraint_applies(req, [C(dimension="industry", value="retail"),
                                        C(dimension="employer_size", value="small")])
    # works for the ADVERTISING dimension too (claim_type) -- one matcher, all domains
    assert constraint_applies([C(dimension="claim_type", value="health")],
                              [C(dimension="claim_type", value="health")])


def test_build_select_fn_constraint_scope_mode_routes_by_generic_matching():
    from rag_wright.contracts.compliance import Constraint
    from rag_wright.subgraphs.compliance_check import build_select_fn

    class _Emb:
        def encode_dense(self, text):
            return [1.0, 0.0]

    # two requirements with DIFFERENT (dimension,value) applicability -- a NON-advertising dimension
    r_small = _req("A", text="rule for small employers")
    r_small = r_small.model_copy(update={"applicability_scope": [Constraint(dimension="employer_size", value="small")]})
    r_large = _req("B", text="rule for large employers")
    r_large = r_large.model_copy(update={"applicability_scope": [Constraint(dimension="employer_size", value="large")]})

    from rag_wright.contracts.compliance import CheckableFact
    subject = CheckableFact(fact_id="f0", source_doc="s", assertion_text="a small employer scenario")
    # the domain's subject-scope producer (data, not compliance_check code): this subject is a "small" employer
    def scope_fn(_s):
        return [Constraint(dimension="employer_size", value="small")]
    select = build_select_fn(_Emb(), [r_small, r_large], k=5, context_k=3, constraint_scope_fn=scope_fn)
    got = {r.requirement_id for r in select(subject, [r_small, r_large])}
    assert got == {r_small.requirement_id}  # only the small-employer rule applies -- generic routing, no ad code


# --- issue 0007 (0007-PATH): scope a verdict to named policy `source`s -----------------------------

import pytest  # noqa: E402


def _row(source: str, citation: str, text: str) -> dict:
    return {"requirement_id": f"{source}:{citation}", "source": source, "citation": citation,
            "deontic_type": "obligation", "actor": "party", "requirement_text": text,
            "evidence_standard": None, "severity": None, "applicability_json": "[]", "confidence": "EXTRACTED"}


class _MultiPolicyStore:
    """A hermetic store mirroring the real seam: `all_requirements(sources=...)` filters, `requirement_sources()`
    returns the distinct policy names."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def all_requirements(self, sources=None) -> list[dict]:
        if sources is None:
            return list(self._rows)
        s = set(sources)
        return [r for r in self._rows if r["source"] in s]

    def requirement_sources(self) -> set[str]:
        return {r["source"] for r in self._rows}


class _Emb1:
    def encode_dense(self, text):
        return [1.0, 0.0]


def _inject_generic_violation_judge():
    """Patch the generic judge to a stub that flags every requirement as a violation; returns a restore fn."""
    import rag_wright.capabilities.compliance_judgment as cj

    async def _judge(fact, requirement):
        return JudgeVerdict(verdict="violation", rationale="stub", confidence=0.9)

    orig = cj.build_ageneric_judge_fn
    cj.build_ageneric_judge_fn = lambda model_id: _judge
    return cj, orig


def _stub_sentence_extractor():
    """SEG-7a hermetic assertion extractor: split a chunk into its sentences (VERBATIM) as ExtractedAssertions,
    so the semantic path (chunk -> extract -> attach) runs with NO model. Splits on sentence terminators AND
    newlines and drops short fragments (headings), mimicking a real extractor's clean per-assertion output."""
    import re

    from rag_wright.capabilities.assertion_extraction import ExtractedAssertion, ExtractedAssertions

    async def _extract(text, model, *, template, **kw):
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", text or "") if len(p.strip()) > 15]
        return ExtractedAssertions(subject="s", assertions=[ExtractedAssertion(assertion_text=p) for p in parts])

    return _extract


def _text_doc(*paragraphs, heading=None):
    """A minimal docling-like doc: an optional section heading + body paragraphs as text items (`.texts`)."""
    from types import SimpleNamespace

    items = []
    if heading is not None:
        items.append(SimpleNamespace(text=heading, label="section_header", level=1))
    items.extend(SimpleNamespace(text=p, label="text", level=None) for p in paragraphs)
    return SimpleNamespace(texts=items)


def _doc_of(*items):
    """A docling-like doc from (label, text) tuples -- for arbitrary structure (multi-section, bullets)."""
    from types import SimpleNamespace

    return SimpleNamespace(texts=[SimpleNamespace(text=t, label=lbl, level=None) for lbl, t in items])


async def test_subject_verdict_runs_the_semantic_pipeline_with_locators():
    # SEG-7a: run_subject_compliance_verdict now chunks -> extracts VERBATIM assertions -> attaches locators ->
    # judges. Injected doc + stub extractor (hermetic, no model/parse). Findings cite each assertion's § locator.
    from rag_wright.subgraphs.compliance_check import run_subject_compliance_verdict

    doc = _text_doc("Our product cures arthritis fast. It also reverses aging completely.",
                    heading="4. Advertising")
    store = _MultiPolicyStore([_row("p1", "§ 1", "An advertisement must not claim a cure.")])
    cj, orig = _inject_generic_violation_judge()
    try:
        report = await run_subject_compliance_verdict(
            "ad.pdf", store=store, judge_model_id="stub", embedder=_Emb1(),
            doc=doc, aextract_fn=_stub_sentence_extractor(), sources=["p1"])
    finally:
        cj.build_ageneric_judge_fn = orig
    claims = [f.citation_claim for f in report.findings]
    assert any("§ 4" in c and "cures arthritis" in c for c in claims)   # a semantic assertion cites its § locator
    assert any("reverses aging" in c for c in claims)                    # both sentences became checkable facts


async def test_run_generic_compliance_verdict_scopes_to_named_sources():
    from rag_wright.subgraphs.compliance_check import run_generic_compliance_verdict

    store = _MultiPolicyStore([_row("p1", "§ 1", "P1 rule: a party must record injuries."),
                               _row("p2", "§ 2", "P2 rule: a party must disclose connections.")])
    cj, orig = _inject_generic_violation_judge()
    try:
        report = await run_generic_compliance_verdict(
            "the party did the thing here in the subject.", "doc", store=store, judge_model_id="stub",
            embedder=_Emb1(), sources=["p1"], aextract_fn=_stub_sentence_extractor())
    finally:
        cj.build_ageneric_judge_fn = orig
    cited = [f.citation_requirement for f in report.findings]
    assert cited and all(c.startswith("§ 1") for c in cited)      # only p1's requirement was judged
    assert not any(c.startswith("§ 2") for c in cited)            # p2 was never in scope


async def test_unknown_source_raises_unknown_compliance_source_error():
    from rag_wright.subgraphs.compliance_check import (
        UnknownComplianceSourceError,
        run_generic_compliance_verdict,
    )

    store = _MultiPolicyStore([_row("p1", "§ 1", "P1 rule.")])
    with pytest.raises(UnknownComplianceSourceError) as ei:
        await run_generic_compliance_verdict(
            "subj", "doc", store=store, judge_model_id="stub", embedder=_Emb1(), sources=["p1", "ghost"])
    assert ei.value.unknown == ["ghost"] and ei.value.present == ["p1"]  # distinct names, not a bare ValueError
    assert "ghost" in str(ei.value)


async def test_empty_sources_scopes_to_nothing_no_findings():
    # `[]` = scope to no policy -> zero requirements -> no findings (product maps this to not_checked). NOT an error.
    from rag_wright.subgraphs.compliance_check import run_generic_compliance_verdict

    store = _MultiPolicyStore([_row("p1", "§ 1", "P1 rule.")])
    cj, orig = _inject_generic_violation_judge()
    try:
        report = await run_generic_compliance_verdict(
            "The subject makes a checkable claim here.", "doc", store=store, judge_model_id="stub",
            embedder=_Emb1(), sources=[], aextract_fn=_stub_sentence_extractor())
    finally:
        cj.build_ageneric_judge_fn = orig
    assert report.findings == []


async def test_sources_none_does_not_consult_requirement_sources():
    # back-compat: sources=None must NOT call requirement_sources() -> a store without it still works
    from rag_wright.subgraphs.compliance_check import run_generic_compliance_verdict

    class _NoValidateStore:
        def all_requirements(self, sources=None):
            return [_row("p1", "§ 1", "P1 rule: a party must act.")]
        # deliberately NO requirement_sources()

    cj, orig = _inject_generic_violation_judge()
    try:
        report = await run_generic_compliance_verdict(
            "The party acted in a way that must be recorded.", "doc", store=_NoValidateStore(),
            judge_model_id="stub", embedder=_Emb1(), sources=None, aextract_fn=_stub_sentence_extractor())
    finally:
        cj.build_ageneric_judge_fn = orig
    assert [f.citation_requirement for f in report.findings]  # ran fine, produced findings


# --- issue 0008 (0008-A): check a subject DOCUMENT (upload), segmented per section -----------------

async def test_run_compliance_document_verdict_checks_each_section():
    # SEG-7a: an uploaded document -> semantic pipeline -> assertions cite their § section locator.
    from rag_wright.subgraphs.compliance_check import run_compliance_document_verdict

    store = _MultiPolicyStore([_row("p1", "§ 1", "A party must disclose material connections.")])
    doc = _doc_of(("section_header", "1. Endorsement"),
                  ("text", "The influencer was paid but did not disclose it."),
                  ("section_header", "2. Pricing"),
                  ("text", "The product costs only forty-nine dollars today."))

    cj, orig = _inject_generic_violation_judge()
    try:
        report = await run_compliance_document_verdict(
            "subject.pdf", b"%PDF fake", store=store, judge_model_id="stub", embedder=_Emb1(),
            doc=doc, aextract_fn=_stub_sentence_extractor())
    finally:
        cj.build_ageneric_judge_fn = orig
    assert len(report.findings) == 2                                   # one assertion per section
    claims = " || ".join(f.citation_claim for f in report.findings)
    assert "§ 1" in claims and "paid but did not disclose" in claims   # each assertion cites its section
    assert "§ 2" in claims and "forty-nine dollars" in claims


async def test_document_verdict_headingless_doc_has_no_section_locator():
    # SEG-7a: a headingless (flat) uploaded doc -> assertions with NO § locator (honest, not degraded).
    from rag_wright.subgraphs.compliance_check import run_compliance_document_verdict

    store = _MultiPolicyStore([_row("p1", "§ 1", "A party must disclose material connections.")])
    doc = _text_doc("One flat paragraph, no headings at all present here.")

    cj, orig = _inject_generic_violation_judge()
    try:
        report = await run_compliance_document_verdict(
            "flat.txt", b"...", store=store, judge_model_id="stub", embedder=_Emb1(),
            doc=doc, aextract_fn=_stub_sentence_extractor())
    finally:
        cj.build_ageneric_judge_fn = orig
    assert len(report.findings) == 1 and "§" not in report.findings[0].citation_claim


async def test_document_verdict_scopes_by_sources_and_errors_on_unknown():
    # 0007 integration: the document path honours `sources` and raises on an unknown one, like the text path
    from rag_wright.subgraphs.compliance_check import UnknownComplianceSourceError, run_compliance_document_verdict

    store = _MultiPolicyStore([_row("p1", "§ 1", "rule")])
    with pytest.raises(UnknownComplianceSourceError):
        await run_compliance_document_verdict(
            "s.txt", b"x", store=store, judge_model_id="stub", embedder=_Emb1(),
            doc=_text_doc("body text here for the subject."), aextract_fn=_stub_sentence_extractor(),
            sources=["ghost"])


async def test_run_subject_compliance_verdict_upload_mode_cites_section_and_sentence():
    # SEG-7a: the unified front-end, upload mode -> per-assertion findings citing "§ {section}".
    from rag_wright.subgraphs.compliance_check import run_subject_compliance_verdict

    store = _MultiPolicyStore([_row("p1", "§ 1", "A party must disclose material connections.")])
    doc = _doc_of(("section_header", "2.1 Endorsement"),
                  ("text", "The influencer was paid a substantial fee for this sponsored post. "
                           "She did not disclose the paid relationship to her audience anywhere."))

    cj, orig = _inject_generic_violation_judge()
    try:
        report = await run_subject_compliance_verdict(
            "subject.pdf", store=store, judge_model_id="stub", embedder=_Emb1(),
            name="subject.pdf", data=b"%PDF fake", doc=doc, aextract_fn=_stub_sentence_extractor())
    finally:
        cj.build_ageneric_judge_fn = orig
    assert len(report.findings) == 2                                   # two assertions in the one section
    assert all("§ 2.1" in f.citation_claim for f in report.findings)   # both cite the section locator
    claims = " || ".join(f.citation_claim for f in report.findings)
    assert "was paid a substantial fee" in claims and "did not disclose" in claims  # distinct verbatim spans


async def test_run_subject_compliance_verdict_text_mode_no_locator():
    # SEG-7a: paste mode -> the semantic pipeline over a structureless doc -> NO spurious "§".
    from rag_wright.subgraphs.compliance_check import run_subject_compliance_verdict

    store = _MultiPolicyStore([_row("p1", "§ 1", "A party must not make deceptive claims.")])
    doc = _text_doc("Our new supplement cures arthritis in just two weeks. "
                    "Dr. Miller recommends it to all her patients. Was ninety-nine, now only forty-nine.")

    cj, orig = _inject_generic_violation_judge()
    try:
        report = await run_subject_compliance_verdict(
            "subject.txt", store=store, judge_model_id="stub", embedder=_Emb1(),
            doc=doc, aextract_fn=_stub_sentence_extractor())
    finally:
        cj.build_ageneric_judge_fn = orig
    assert report.findings and all("§" not in f.citation_claim for f in report.findings)  # structureless -> no §
    assert any("cures arthritis" in f.citation_claim for f in report.findings)


async def test_run_subject_compliance_verdict_requires_an_input():
    from rag_wright.subgraphs.compliance_check import run_subject_compliance_verdict

    store = _MultiPolicyStore([_row("p1", "§ 1", "rule")])
    with pytest.raises(ValueError):
        await run_subject_compliance_verdict(
            "s", store=store, judge_model_id="stub", embedder=_Emb1())  # neither text nor data nor doc


# --- issue 0010: per-sentence subject facts (sentence segmentation is the DEFAULT) ----------------

_ADCOPY = ("Our new supplement cures arthritis in just two weeks. Dr. Miller recommends it to all her patients. "
           "Was $99, now only $49 this week.")


def test_sentence_facts_fn_splits_sentences_abbrev_and_price_safe():
    from rag_wright.subgraphs.compliance_check import sentence_facts_fn

    facts = sentence_facts_fn(_ADCOPY, "subject.txt")
    texts = [f.assertion_text.strip() for f in facts]
    assert len(facts) == 3                                              # three sentences, not one blob
    assert any("cures arthritis" in t for t in texts)
    assert any("Dr. Miller recommends" in t for t in texts)            # "Dr." NOT split
    assert any("only $49" in t for t in texts)                         # "$99"/"$49" NOT split
    assert sentence_facts_fn("", "s.txt") == []


_SUBJECT_SECTIONS = [
    {"section": "1", "heading": "Health Claims",
     "text": "Our supplement cures arthritis in two weeks. Dr. Miller recommends it to all her patients."},
    {"section": "2.1", "heading": "Pricing", "text": "Was $99, now only $49 this week."},
]


def test_subject_facts_fn_splits_sections_into_sentences_with_section_locator():
    # UNIFY-B: the unified section->sentence producer. Each parsed section is split into sentences (abbrev/decimal
    # safe); each sentence -> a CheckableFact carrying the SECTION locator + the CLEAN sentence (heading NOT folded
    # in, unlike document_facts_fn), so a finding cites "doc § {section}: sentence".
    from rag_wright.subgraphs.compliance_check import subject_facts_fn

    facts = subject_facts_fn(_SUBJECT_SECTIONS, "subject.txt")
    assert len(facts) == 3                                                  # 2 sentences in §1 + 1 in §2.1
    assert [f.section for f in facts] == ["1", "1", "2.1"]                  # each carries its section locator
    texts = [f.assertion_text.strip() for f in facts]
    assert any("cures arthritis" in t for t in texts)
    assert any("Dr. Miller recommends" in t for t in texts)                # "Dr." NOT split
    assert any("only $49" in t for t in texts)                             # "$99"/"$49" NOT split
    assert all("Health Claims" not in t and "Pricing" not in t for t in texts)  # heading NOT in the cited text


def test_subject_facts_fn_skips_empty_sections_and_blank_sentences():
    from rag_wright.subgraphs.compliance_check import subject_facts_fn

    sections = [{"section": "1", "heading": "", "text": "   "},           # empty body -> skipped
                {"section": "2", "heading": "H", "text": "A real sentence here."}]
    facts = subject_facts_fn(sections, "s.txt")
    assert len(facts) == 1 and facts[0].section == "2"
    assert subject_facts_fn([], "s.txt") == []


def test_subject_facts_fn_ids_are_unique_across_sections():
    # a repeated sentence in two sections -> distinct fact_ids (running index), so nothing collides
    from rag_wright.subgraphs.compliance_check import subject_facts_fn

    sections = [{"section": "1", "text": "Same sentence here."},
                {"section": "2", "text": "Same sentence here."}]
    facts = subject_facts_fn(sections, "s.txt")
    assert len(facts) == 2 and facts[0].fact_id != facts[1].fact_id
    assert facts[0].section == "1" and facts[1].section == "2"


async def test_run_generic_compliance_verdict_cites_each_assertion():
    # SEG-7a: the generic text path parses the paste through docling and runs the SAME semantic pipeline ->
    # a finding per checkable assertion, each citing its own verbatim span, NO § (structureless paste).
    from rag_wright.subgraphs.compliance_check import run_generic_compliance_verdict

    store = _MultiPolicyStore([_row("p1", "§ 1", "A party must not make deceptive or unsubstantiated claims.")])
    paste = ("Our new supplement cures arthritis in just two weeks of use. "
             "Doctor Miller recommends it to all of her patients regularly. "
             "The price was ninety-nine dollars and is now only forty-nine.")
    cj, orig = _inject_generic_violation_judge()
    try:
        report = await run_generic_compliance_verdict(
            paste, "subject.txt", store=store, judge_model_id="stub", embedder=_Emb1(),
            aextract_fn=_stub_sentence_extractor())
    finally:
        cj.build_ageneric_judge_fn = orig
    claims = [f.citation_claim for f in report.findings]
    assert len(report.findings) >= 2                                   # multiple checkable assertions
    assert all("§" not in c for c in claims)                           # structureless paste -> no spurious §
    assert any("cures arthritis" in c for c in claims)


def _seg4_doc():
    from types import SimpleNamespace
    return SimpleNamespace(texts=[
        SimpleNamespace(text="4. Advertising", label="section_header", level=1),
        SimpleNamespace(text="Our supplement cures arthritis fast in most adults.", label="text", level=None),
        SimpleNamespace(text="It reverses the visible signs of aging completely.", label="text", level=None),
        SimpleNamespace(text="Guaranteed results within thirty days or your money back.", label="list_item", level=None),
        SimpleNamespace(text="5. Pricing", label="section_header", level=1),
        SimpleNamespace(text="The price was ninety-nine dollars and is now forty-nine.", label="text", level=None),
    ])


def test_attach_structural_locators_maps_assertions_to_section_and_element():
    # SEG-4: each verbatim assertion is matched to its docling item -> section + element_kind + ¶/bullet ordinal,
    # so locator() renders "§ N ¶M" / "§ N · bullet M". Ordinals count per-kind, reset per section.
    from rag_wright.contracts.compliance import CheckableFact
    from rag_wright.subgraphs.compliance_check import attach_structural_locators

    def _f(t):
        return CheckableFact(fact_id=CheckableFact.make_id("d", 0, t), source_doc="d", assertion_text=t)

    facts = [_f("Our supplement cures arthritis fast in most adults."),
             _f("It reverses the visible signs of aging completely."),
             _f("Guaranteed results within thirty days or your money back."),
             _f("The price was ninety-nine dollars and is now forty-nine.")]
    attach_structural_locators(facts, _seg4_doc())
    assert facts[0].section == "4" and facts[0].element_kind == "text" and facts[0].locator() == "§ 4 ¶1"
    assert facts[1].element_ordinal == 2 and facts[1].locator() == "§ 4 ¶2"                # 2nd paragraph in § 4
    assert facts[2].element_kind == "list_item" and facts[2].locator() == "§ 4 · bullet 1"  # 1st bullet in § 4
    assert facts[3].section == "5" and facts[3].locator() == "§ 5 ¶1"                       # ordinal resets per section


def test_attach_merges_soft_wrapped_lines_so_paragraph_ordinals_are_correct():
    # SEG-4 hardening: docling line-splits a wrapped paragraph; the merge coalesces it back to ONE logical
    # paragraph, so the NEXT real paragraph is ¶2 (not ¶3), and the whole wrapped text is one ¶.
    from types import SimpleNamespace

    from rag_wright.contracts.compliance import CheckableFact
    from rag_wright.subgraphs.compliance_check import attach_structural_locators

    doc = SimpleNamespace(texts=[
        SimpleNamespace(text="4. Claims", label="section_header", level=1),
        SimpleNamespace(text="Our supplement cures arthritis and also reverses the visible", label="text",
                        level=None),                                          # line 1 (no terminal punctuation)
        SimpleNamespace(text="signs of aging in most adults.", label="text", level=None),   # soft-wrap continuation
        SimpleNamespace(text="The manufacturer guarantees a full refund to buyers.", label="text", level=None),
    ])

    def _f(t):
        return CheckableFact(fact_id=CheckableFact.make_id("d", 0, t), source_doc="d", assertion_text=t)

    f_wrapcont = _f("signs of aging in most adults.")              # sits in the merged paragraph 1
    f_refund = _f("The manufacturer guarantees a full refund to buyers.")
    attach_structural_locators([f_wrapcont, f_refund], doc)
    assert f_wrapcont.locator() == "§ 4 ¶1"                        # merged into paragraph 1, not a separate ¶
    assert f_refund.locator() == "§ 4 ¶2"                          # the next real paragraph is ¶2, not ¶3


def test_attach_locates_assertion_that_spans_soft_wrapped_items():
    # SEG-4 robustness: docling can split a soft-wrapped paragraph into consecutive `text` items; an assertion
    # spanning the wrap must still be located (concatenation-based match), not dropped.
    from types import SimpleNamespace

    from rag_wright.contracts.compliance import CheckableFact
    from rag_wright.subgraphs.compliance_check import attach_structural_locators

    doc = SimpleNamespace(texts=[
        SimpleNamespace(text="4. Claims", label="section_header", level=1),
        SimpleNamespace(text="It also completely reverses the visible", label="text", level=None),
        SimpleNamespace(text="signs of aging in most adults over time.", label="text", level=None),
    ])
    f = CheckableFact(fact_id="f0", source_doc="d",
                      assertion_text="It also completely reverses the visible signs of aging in most adults over time.")
    attach_structural_locators([f], doc)
    assert f.section == "4" and f.locator().startswith("§ 4")      # located despite the line-wrap split


def test_attach_flat_doc_has_no_section_locator():
    # SEG-4 / decision 5: a headingless (flat) document -> no section -> no "§" (honest, not degraded).
    from types import SimpleNamespace

    from rag_wright.contracts.compliance import CheckableFact
    from rag_wright.subgraphs.compliance_check import attach_structural_locators

    doc = SimpleNamespace(texts=[SimpleNamespace(text="A flat claim with no heading at all here.", label="text",
                                                 level=None)])
    f = CheckableFact(fact_id="f0", source_doc="d", assertion_text="A flat claim with no heading at all here.")
    attach_structural_locators([f], doc)
    assert f.section is None and f.locator() == ""


def test_attach_unmatched_assertion_stays_unlocated():
    # a paraphrased/absent assertion matches no item -> stays unlocated (still citable by its text).
    from rag_wright.contracts.compliance import CheckableFact
    from rag_wright.subgraphs.compliance_check import attach_structural_locators

    f = CheckableFact(fact_id="f0", source_doc="d", assertion_text="This exact text is not in the document.")
    attach_structural_locators([f], _seg4_doc())
    assert f.section is None and f.element_kind is None and f.element_ordinal is None


async def test_aextract_subject_facts_runs_per_chunk_with_unique_ids():
    # SEG-3: extract checkable assertions from each subject CHUNK (concurrently) -> CheckableFacts, re-indexed
    # globally so fact_ids are unique across chunks. Domain-neutral (no claim_type). Injected extractor.
    from rag_wright.capabilities.assertion_extraction import ExtractedAssertion, ExtractedAssertions
    from rag_wright.subgraphs.compliance_check import aextract_subject_facts

    async def _stub(text, model, *, template, **kw):  # one assertion per chunk = the chunk text
        return ExtractedAssertions(subject="s", assertions=[ExtractedAssertion(assertion_text=text.strip())])

    facts = await aextract_subject_facts(
        ["First chunk assertion.", "Second chunk assertion."], source_doc="d", model=object(), aextract_fn=_stub)
    assert [f.assertion_text for f in facts] == ["First chunk assertion.", "Second chunk assertion."]
    assert facts[0].fact_id != facts[1].fact_id                       # globally unique across chunks
    assert all(getattr(f, "claim_type", None) is None for f in facts)  # domain-neutral


async def test_subject_chunks_uses_the_shared_chunker_seam():
    # SEG-2: subject_chunks semantically chunks a parsed subject via the SAME shared chunker as ingestion
    # (single-call discoverer + finalize; no cache/summarize -- the subject is transient). Injected stub.
    from types import SimpleNamespace

    from rag_wright.capabilities.rlm_chunking import BoundarySpan
    from rag_wright.subgraphs.compliance_check import subject_chunks

    class _Disc:
        def __init__(self):
            self.acalls = 0

        async def adiscover(self, document):
            self.acalls += 1
            return [BoundarySpan(start_index=0, end_index=1), BoundarySpan(start_index=2, end_index=2)]

    doc = SimpleNamespace(texts=[SimpleNamespace(text="First assertion here.", label="text", level=None),
                                 SimpleNamespace(text="Second assertion here.", label="text", level=None),
                                 SimpleNamespace(text="A third, separate claim.", label="list_item", level=None)])
    disc = _Disc()
    texts = await subject_chunks(doc, discoverer=disc)
    assert disc.acalls == 1                                       # went through the shared async chunker
    joined = " ".join(texts)
    assert all(s in joined for s in ("First assertion", "Second assertion", "third, separate claim"))


async def test_aextract_ad_claims_extracts_per_chunk_typed_tail_preserved():
    # SEG-7b: the advertising extractor runs PER CHUNK, re-indexed for unique ids; the typed-Claim tail
    # (claim_type) is preserved. The structural locator is attached separately (SEG-4), not here.
    from rag_wright.contracts.compliance import Claim, ClaimType
    from rag_wright.subgraphs.compliance_check import _aextract_ad_claims

    async def fake_aclaim(text, *, model, source_doc, **kw):  # one claim per chunk
        return [Claim(fact_id=Claim.make_id(source_doc, 0, text), source_doc=source_doc,
                      assertion_text=text.strip(), claim_type=ClaimType.HEALTH)]

    chunks = ["Our product cures arthritis fast.", "It was ninety-nine dollars, now forty-nine.", "   "]
    claims = await _aextract_ad_claims(chunks, "ad.pdf", object(), aclaim_fn=fake_aclaim)
    assert len(claims) == 2                                                        # empty chunk skipped
    assert claims[0].fact_id != claims[1].fact_id                                  # globally unique ids
    assert all(c.claim_type is ClaimType.HEALTH for c in claims)                  # typed-Claim tail preserved


async def test_ad_path_upload_cites_section_locators(monkeypatch):
    # SEG-7b: run_compliance_check runs the SEMANTIC front-end (parse -> chunk -> per-chunk Claim extraction ->
    # attach locators -> judge). An uploaded ad's typed claims cite their "§ {section}" locator.
    import rag_wright.subgraphs.compliance_check as cc
    from rag_wright.contracts.compliance import Claim, ClaimType

    async def fake_aclaim(text, *, model, source_doc, **kw):  # extract the verbatim sentences of the chunk
        import re
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if len(s.strip()) > 15]
        return [Claim(fact_id=Claim.make_id(source_doc, i, s), source_doc=source_doc,
                      assertion_text=s, claim_type=ClaimType.HEALTH) for i, s in enumerate(sents)]

    # the ad judge stub (build_acompliance_judge_fn) -- async, like the real one
    import rag_wright.capabilities.compliance_judgment as cj

    async def _ad_judge(claim, req):
        return JudgeVerdict(verdict="violation", rationale="x", confidence=0.9)

    orig = cj.build_acompliance_judge_fn
    cj.build_acompliance_judge_fn = lambda mid: _ad_judge
    doc = _doc_of(("section_header", "4. Advertising"),
                  ("text", "Our product cures arthritis fast in most adults."))
    store = _MultiPolicyStore([_row("p1", "§ 1", "An ad must not claim a cure.")])
    try:
        report = await cc.run_compliance_check(
            source_doc="ad.pdf", name="ad.pdf", data=b"%PDF", store=store, extract_model=object(),
            judge_model_id="stub", embedder=_Emb1(), doc=doc, aclaim_fn=fake_aclaim, sources=["p1"])
    finally:
        cj.build_acompliance_judge_fn = orig
    assert report.findings and any("§ 4" in f.citation_claim and "cures arthritis" in f.citation_claim
                                   for f in report.findings)


async def test_unified_multi_section_document_maps_each_sentence_to_its_section():
    # UNIFY-E (arc gate): a genuine MULTI-section subject -> each sentence cites ITS OWN "§ {section}", so the
    # cross-section mapping is correct (not just 1-2 sections). Numeric locators come from the section ids.
    from rag_wright.subgraphs.compliance_check import run_subject_compliance_verdict

    store = _MultiPolicyStore([
        _row("p1", "§ A", "An advertisement must not claim a product cures a disease."),
        _row("p2", "§ B", "A strike-through 'was' price must reflect a bona fide former selling price."),
        _row("p3", "§ C", "A material connection between advertiser and endorser must be disclosed."),
    ])

    doc = _doc_of(
        ("section_header", "1. Product Claims"),
        ("text", "Our supplement cures arthritis in just two weeks of daily use."),
        ("section_header", "2. Pricing"),
        ("text", "The regular price was ninety-nine dollars only last month here."),
        ("section_header", "3. Endorsements"),
        ("text", "Doctor Miller personally recommends this product to all of her patients."),
    )

    cj, orig = _inject_generic_violation_judge()
    try:
        report = await run_subject_compliance_verdict(
            "policy_subject.pdf", store=store, judge_model_id="stub", embedder=_Emb1(),
            name="policy_subject.pdf", data=b"%PDF", doc=doc, aextract_fn=_stub_sentence_extractor())
    finally:
        cj.build_ageneric_judge_fn = orig
    claims = {f.citation_claim for f in report.findings}
    assert any("§ 1" in c and "cures arthritis" in c for c in claims)         # each assertion cites its section
    assert any("§ 2" in c and "ninety-nine dollars" in c for c in claims)
    assert any("§ 3" in c and "Miller" in c for c in claims)
    assert {c.split("§")[1].strip().split()[0] for c in claims} == {"1", "2", "3"}   # three distinct sections


def test_compliance_report_has_ocr_unreadable_pages_field():
    # SEG-6: the report carries which pages the tiered OCR could not read, so a verdict is never silently based on
    # half-read text (the ENG-1 principle, compliance side).
    from rag_wright.contracts.compliance import ComplianceReport

    assert ComplianceReport(source_doc="d").ocr_unreadable_pages == []                 # default empty
    assert ComplianceReport(source_doc="d", ocr_unreadable_pages=[3, 4]).ocr_unreadable_pages == [3, 4]


async def test_subject_verdict_propagates_ocr_unreadable_pages(monkeypatch):
    # SEG-6/SEG-7a: the OCR PARTIAL (unreadable pages) from the parse reaches the ComplianceReport.
    import rag_wright.subgraphs.compliance_check as cc

    async def _fake_parse(**kw):  # parse -> (docling doc, unreadable pages)
        return _text_doc("The subject makes a checkable claim here today."), [3, 4]

    monkeypatch.setattr(cc, "_aparse_subject_any", _fake_parse)
    store = _MultiPolicyStore([_row("p1", "§ 1", "A party must not make deceptive claims.")])
    cj, orig = _inject_generic_violation_judge()
    try:
        report = await cc.run_subject_compliance_verdict(
            "scan.pdf", store=store, judge_model_id="stub", embedder=_Emb1(), name="scan.pdf", data=b"x",
            aextract_fn=_stub_sentence_extractor(), sources=["p1"])
    finally:
        cj.build_ageneric_judge_fn = orig
    assert report.ocr_unreadable_pages == [3, 4]                      # surfaced on the report


async def test_paste_and_upload_reach_the_same_producer(monkeypatch):
    # SEG-7a: paste (text mode) AND upload (doc mode) both flow through the SAME semantic_subject_facts producer.
    import rag_wright.subgraphs.compliance_check as cc

    seen: list[int] = []
    real = cc.semantic_subject_facts

    async def spy(parsed_doc, **kw):
        seen.append(1)
        return await real(parsed_doc, **kw)

    monkeypatch.setattr(cc, "semantic_subject_facts", spy)
    store = _MultiPolicyStore([_row("p1", "§ 1", "A party must not make deceptive claims.")])
    cj, orig = _inject_generic_violation_judge()
    try:
        await cc.run_generic_compliance_verdict(
            "A pasted claim sentence here for the test.", "p.txt", store=store, judge_model_id="stub",
            embedder=_Emb1(), aextract_fn=_stub_sentence_extractor())                                # paste
        await cc.run_compliance_document_verdict(
            "d.pdf", b"x", store=store, judge_model_id="stub", embedder=_Emb1(),
            doc=_text_doc("An uploaded claim sentence here for the test."),
            aextract_fn=_stub_sentence_extractor())                                                  # upload
    finally:
        cj.build_ageneric_judge_fn = orig
    assert len(seen) == 2                                              # semantic_subject_facts produced for BOTH
