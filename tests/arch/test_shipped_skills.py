"""PS-10: the engine-authored Claude Code skills ship in the wheel, version-matched to the engine.

Each skill lives once, under `src/rag_wright/.agents/skills/<name>/` (the convention docling and fastapi use), and the
engine repo's `.claude/skills/<name>` is a symlink to it, so there is no copy to drift. A product links them from the
installed package. A shipped skill is read in a product repo, so any engine-repo path it names is marked as such.
"""
from __future__ import annotations

import re
import subprocess
import tarfile
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PACKAGED = _ROOT / "src" / "rag_wright" / ".agents" / "skills"
_LINKED = _ROOT / ".claude" / "skills"

SHIPPED = (
    "using-the-rag-wright-engine",
    "authoring-a-capability",
    "building-an-agent-harness",
    "building-a-product-backend",
    "building-a-product-frontend",
    "specifying-a-product-on-the-engine",
    "building-an-ingestion-capability",
    "creating-evals",
    "classifier-opportunity-analysis",
    "setfit",
    "laya",
    "qwen-vllm-modal",
)
QWEN_SCRIPT = "qwen-vllm-modal/scripts/modal_qwen3_vllm_server.py"
# PS-51: the frontend skill ships its shell scaffold, since a source scan cannot produce a layout.
FRONTEND_SCAFFOLD = tuple(f"building-a-product-frontend/scaffold/{f}"
                          for f in ("surfaces.tsx", "ShellLayout.tsx", "ShellLayout.test.tsx"))

# The sentence a shipped skill carries when it names a path in the engine repository.
ENGINE_REPO_NOTE = "are in the engine repository"
_REPO_PATH = re.compile(r"(?<![\w./~-])(docs|eval|scripts|tests|src)/[\w./-]+")


def test_each_skill_lives_once_in_the_package_and_the_repo_links_to_it():
    assert sorted(p.name for p in _PACKAGED.iterdir() if p.is_dir()) == sorted(SHIPPED)
    for name in SHIPPED:
        assert (_PACKAGED / name / "SKILL.md").is_file(), name
        link = _LINKED / name
        assert link.is_symlink(), f".claude/skills/{name} must link to the packaged skill"
        assert link.resolve() == (_PACKAGED / name).resolve(), name


def test_the_qwen_skill_bundles_its_deploy_script():
    script = _PACKAGED / QWEN_SCRIPT
    assert script.is_file()
    repo_path = _ROOT / "scripts" / "modal_qwen3_vllm_server.py"
    assert repo_path.is_symlink() and repo_path.resolve() == script.resolve()


def test_the_frontend_skill_bundles_its_shell_scaffold():
    skill = (_PACKAGED / "building-a-product-frontend" / "SKILL.md").read_text(encoding="utf-8")
    for path in FRONTEND_SCAFFOLD:
        assert (_PACKAGED / path).is_file(), path
        assert path.split("/", 1)[1] in skill, f"the skill must name {path}"


def test_engine_repo_paths_in_a_shipped_skill_are_marked():
    unmarked = []
    for name in SHIPPED:
        text = (_PACKAGED / name / "SKILL.md").read_text(encoding="utf-8")
        if _REPO_PATH.search(text) and ENGINE_REPO_NOTE not in text:
            unmarked.append(name)
    assert unmarked == []


def test_the_release_build_ships_the_skills(tmp_path):
    # Built the way publish.yml builds (`uv build`: the sdist first, then the wheel FROM the sdist). A wheel built
    # straight from the tree (`--wheel`) had the skills while the 0.3.0 release, built from its sdist, did not.
    # Offline: the build backend comes from uv's cache, so the test never reaches the network.
    subprocess.run(["uv", "build", "--offline", "--out-dir", str(tmp_path)], cwd=_ROOT, check=True, capture_output=True)
    (wheel,) = tmp_path.glob("rag_wright-*.whl")
    (sdist,) = tmp_path.glob("rag_wright-*.tar.gz")
    shipped = [f"rag_wright/.agents/skills/{n}/SKILL.md" for n in SHIPPED] + [f"rag_wright/.agents/skills/{QWEN_SCRIPT}"]
    shipped += [f"rag_wright/.agents/skills/{path}" for path in FRONTEND_SCAFFOLD]
    assert set(shipped) - set(zipfile.ZipFile(wheel).namelist()) == set()
    with tarfile.open(sdist) as tar:
        in_sdist = {n.split("/", 1)[1] for n in tar.getnames()}
    assert {f"src/{s}" for s in shipped} - in_sdist == set()


# ADR-0134: skills for product developers are linked into a repo's `.claude/skills/`, never into the global one.
_GLOBAL_LINK = re.compile(r"\bln\b[^\n]*(~|\$HOME|\$\{HOME\})/\.claude/skills")
_LINK_GUIDES = (
    "README.md", "CLAUDE.md", "docs/installation.md", "docs/playbook.md",
    "docs/templates/product-starter/README.md", "docs/templates/product-starter/CLAUDE.md.template",
    "docs/templates/product-starter/playbook.md.template",
)


def test_no_guide_links_skills_into_the_global_claude_skills():
    texts = {p: (_ROOT / p).read_text(encoding="utf-8") for p in _LINK_GUIDES}
    texts |= {f"skill {n}": (_PACKAGED / n / "SKILL.md").read_text(encoding="utf-8") for n in SHIPPED}
    assert [name for name, text in texts.items() if _GLOBAL_LINK.search(text)] == []


def test_the_global_link_check_bites():
    assert _GLOBAL_LINK.search('for d in "$SKILLS"/*/; do ln -sfn "$d" ~/.claude/skills/; done')
    assert _GLOBAL_LINK.search('ln -s "$d" "$HOME/.claude/skills/x"')
    assert not _GLOBAL_LINK.search('ln -sfn "${d%/}" ".claude/skills/$(basename "$d")"')
