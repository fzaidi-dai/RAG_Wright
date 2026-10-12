"""EV-6: the `creating-evals` skill's complete example RUNS as written. The test lifts its one Python block, points
`GOLD_FILE` and `REPORT` at a messy expert workbook and a temp path, supplies `my_judge` (the product's capability
call), and executes it: gold import, `arun_eval` with repeats, the report saved and scored again. Hermetic."""
from __future__ import annotations

import json
import re
from pathlib import Path

import openpyxl

_SKILL = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "creating-evals" / "SKILL.md"


def _block() -> str:
    blocks = re.findall(r"```python\n(.*?)```", _SKILL.read_text(encoding="utf-8"), re.S)
    assert len(blocks) == 1, "the skill should show exactly one python block"
    return blocks[0]


def _workbook(path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in [["NLI labels -- reviewed by the legal team"], [],
                [" ID ", "Premise", "hypothesis", "LABEL", "Contract"],
                ["n1", "Either party may end it on 30 days' notice.", "Termination needs notice.", "entailment", "c1"],
                ["n2", "No fee is payable.", "A fee is payable.", "contradiction", "c1"],
                [],
                ["n3", "The term is two years.", "Renewal is automatic.", "neutral", "c2"],
                ["n4", "Assignment needs consent.", "Assignment is free.", "contradiction", "c2"]]:
        ws.append(row)
    wb.save(path)
    return path


def test_the_example_constants_are_the_ones_the_test_rebinds():
    for name in ("GOLD_FILE", "REPORT"):
        assert re.search(rf"^{name} = ", _block(), re.M), name


def test_the_skill_example_runs_as_written(tmp_path, capsys):
    gold_file = _workbook(tmp_path / "labels.xlsx")
    report = tmp_path / "runs" / "nli.json"
    code = re.sub(r'^GOLD_FILE = .*$', f'GOLD_FILE = {str(gold_file)!r}', _block(), flags=re.M)
    code = re.sub(r'^REPORT = .*$', f'REPORT = {str(report)!r}', code, flags=re.M)
    answers = {"Termination needs notice.": "entailment", "A fee is payable.": "contradiction",
               "Renewal is automatic.": "neutral", "Assignment is free.": "contradiction"}

    async def my_judge(premise, hypothesis):
        return answers[hypothesis]

    exec(compile(code, "creating-evals SKILL.md", "exec"), {"my_judge": my_judge, "__name__": "example"})
    out = capsys.readouterr().out
    assert "[eval] start N=8 (4 items x 2 repeats)" in out
    saved = json.loads(report.read_text(encoding="utf-8"))
    assert saved["passed"] is True and saved["errors"] == 0 and len(saved["items"]) == 4
    assert "at 0.9 precision: True" in out
