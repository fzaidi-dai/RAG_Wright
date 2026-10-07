"""ING-5: the `building-an-ingestion-capability` skill's worked example RUNS as written. The test lifts the skill's
Turtle and Python blocks verbatim, points the example's four constants at a temp pack, a sample document and a
scratch database, and executes it end to end (evaluate, ingest, cited records) against the local ArcadeDB. Two
documents run through the default `document_concurrency` of 2, so concurrent writes are exercised too (ING-5: ArcadeDB
write conflicts are retried by the store)."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

_SKILL = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "building-an-ingestion-capability" / "SKILL.md"
_DB = "ragwright_test_ingest_skill"
_SAMPLE = """# Quality report

## Scope

This report covers the spring production batches. Each batch was tested twice.

## Results

| Batch | Tensile strength | Verdict |
|---|---|---|
| B-1001 | 412 | pass |
| B-1002 | 398 | pass |

## Actions

The supplier will repeat the test on any batch below 400.
"""


def _block(lang: str) -> str:
    blocks = re.findall(rf"```{lang}\n(.*?)```", _SKILL.read_text(encoding="utf-8"), re.S)
    assert len(blocks) == 1, f"the skill should show exactly one {lang} block"
    return blocks[0]


def test_the_example_constants_are_the_ones_the_test_rebinds():
    code = _block("python")
    for name in ("PACK_TTL", "SAMPLES", "CORPUS", "CACHE"):
        assert re.search(rf"^{name} = ", code, re.M), name


@pytest.mark.store
@pytest.mark.embed
@pytest.mark.parse
def test_the_skill_example_runs_end_to_end(tmp_path, capsys):
    from dotenv import load_dotenv

    load_dotenv(".env")
    os.environ.setdefault("ARCADEDB_HOST", "localhost")
    pack = tmp_path / "pack.ttl"
    pack.write_text(_block("turtle"), encoding="utf-8")
    samples = [tmp_path / "quality_report.md", tmp_path / "quality_report_q2.md"]
    samples[0].write_text(_SAMPLE, encoding="utf-8")
    samples[1].write_text(_SAMPLE.replace("spring", "summer").replace("B-10", "B-20"), encoding="utf-8")
    code = _block("python")
    code = re.sub(r'^PACK_TTL = .*$', f"PACK_TTL = {str(pack)!r}", code, flags=re.M)
    code = re.sub(r'^SAMPLES = .*$', f"SAMPLES = {[str(x) for x in samples]!r}", code, flags=re.M)
    code = re.sub(r'^CORPUS = .*$', f"CORPUS = {_DB!r}", code, flags=re.M)
    code = re.sub(r'^CACHE = .*$', f"CACHE = {str(tmp_path / 'cache')!r}", code, flags=re.M)
    ns: dict = {}
    try:
        exec(compile(code, str(_SKILL), "exec"), ns)  # noqa: S102 - the skill's own example, verbatim
        report, ws = ns["report"], ns["ws"]
        assert report.failed == 0 and report.succeeded == 2
        for doc in report.documents:
            assert doc.units >= 3 and doc.records == doc.units
            assert not doc.extraction_failures and not doc.span_failures  # nothing lost to a write conflict
        from rag_wright.api import kg_read

        rows = kg_read(ws, "Section", fields=["section_id", "title", "span_id"])
        assert len(rows) == sum(d.records for d in report.documents) and all(r["span_id"] for r in rows)
    finally:
        if "ws" in ns:
            ns["ws"]._store.drop()
