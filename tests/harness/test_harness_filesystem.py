"""The subject documents are not files: a wrong filesystem call gets a signpost, the agent's own files do not.

Ported from RuleWright under ADR-0139's handover (PS-48g); the substrate is `rag_wright.harness.filesystem`, driven
through the fabric product's signposts. RuleWright's tests of its own listing tool (its description, the field it
leads with) stay in RuleWright: that tool is the product's.

**A namespace collision, not a description problem.** `create_deep_agent` always installs `FilesystemMiddleware`,
whose prompt says *"You have access to a filesystem"*, and a product's listing hands back things with names. Opening
them is the obvious next move, and a bare `not found` invites the next guess: seven wasted calls in one run.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from rag_wright.harness.agent import build_chat_agent
from rag_wright.harness.filesystem import MEMORY_ROOTS, OWN_ROOTS, TheSubjectIsNotAFilesystem, redirect_for
from rag_wright.harness.memory import MEMORY_SOURCES
from rag_wright.harness.rules import OWN_FILESYSTEM
from rag_wright.harness.skills import SKILLS_ROOT, skill_files
from rag_wright.harness.tiers import build_orchestrator
from tests.harness.fabric import FABRIC_PROMPTS, _a_context, _fabric, _resources, _settings, scripted


def _calls(name: str, args: dict, call_id: str = "1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


# --- the signpost -------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/cotton-poplin.pdf", "/compositions/cotton-poplin", "/mill/x.txt", "/"])
def test_A_PATH_OUTSIDE_THE_AGENTS_OWN_SPACE_IS_REDIRECTED(path: str) -> None:
    """Every shape the recorded run tried: a name it was shown, under a guessed folder, and the root."""
    redirect = redirect_for("read_file", {"file_path": path}, signposts=FABRIC_PROMPTS)

    assert redirect is not None, f"{path} still returns a bare 'not found'"
    assert "match_compositions" in redirect, "the redirect says what is wrong and not what to do instead"


@pytest.mark.parametrize("path", ["/memories/scope/note.md", "/memories/shared/", "/large_tool_results/abc"])
def test_THE_AGENTS_OWN_FILES_ARE_NOT_REDIRECTED(path: str) -> None:
    """**The half that matters most.** Recall is `glob`/`grep` over `/memories/`, and DeepAgents offloads an
    oversized result to `/large_tool_results/` and tells the model to `read_file` it back."""
    assert redirect_for("read_file", {"file_path": path}, signposts=FABRIC_PROMPTS) is None


@pytest.mark.parametrize("call", [("read_file", {"file_path": "/skills/weaving/SKILL.md"}),
                                  ("ls", {"path": "/skills/"}), ("glob", {"pattern": "/skills/**/SKILL.md"})])
def test_THE_AGENTS_OWN_SKILLS_ARE_NOT_REDIRECTED(call: tuple[str, dict]) -> None:
    """**A skill is read with `read_file`, by design** (PS-48g). The skills middleware advertises each skill by
    its path and tells the model to read it; the signpost answered that read with "compositions are not files",
    so the orchestrator could never read the procedures authored for it."""
    tool, args = call

    assert redirect_for(tool, args, signposts=FABRIC_PROMPTS) is None


def test_THE_OWN_ROOTS_ARE_THE_MEMORY_ROOTS_AND_THE_SKILLS() -> None:
    """**Derived, not mirrored.** A second hand-kept list drifted: the rule that grades filesystem calls counted
    `/skills` as the agent's own while the signpost did not."""
    assert set(OWN_ROOTS) == {*MEMORY_ROOTS, f"{SKILLS_ROOT}/"}
    assert set(OWN_FILESYSTEM) == {root.rstrip("/") for root in OWN_ROOTS}


def test_THE_MEMORY_ROOTS_COVER_EVERY_REAL_MEMORY_PATH() -> None:
    """A path the memory module writes that the roots miss is recall redirected into a signpost."""
    for source in MEMORY_SOURCES:
        assert any(source.startswith(root) for root in MEMORY_ROOTS), f"{source} is not covered"


@pytest.mark.parametrize("tool", ["ls", "glob", "grep"])
def test_THE_DISCOVERY_TOOLS_ARE_REDIRECTED_TOO(tool: str) -> None:
    """`ls("/")` and `glob("**/*")` both appeared in the trace, and `[]` reads as "the scope is empty". **`grep`
    wanders by its `path`, not its `pattern`**, which is a regex and no location at all."""
    assert redirect_for(tool, {"pattern": "**/*"} if tool == "glob" else {"path": "/"}) is not None


@pytest.mark.parametrize("tool", ["ls", "glob", "grep"])
def test_THE_DISCOVERY_TOOLS_STILL_WORK_ON_MEMORY(tool: str) -> None:
    assert redirect_for(tool, {"pattern": "/memories/**/*.md"} if tool == "glob" else {"path": "/memories/scope/"}) is None


def test_A_GREP_INTO_THE_OFFLOADED_RESULTS_IS_NOT_REDIRECTED_BY_ITS_REGEX() -> None:
    """The route DeepAgents itself tells the model to take for an oversized result."""
    assert redirect_for("grep", {"path": "/large_tool_results", "pattern": "x"}, signposts=FABRIC_PROMPTS) is None


def test_AN_UNRELATED_TOOL_IS_NEVER_REDIRECTED() -> None:
    """The middleware must not sit in front of the product's own tools."""
    assert redirect_for("match_compositions", {"requirements": "/cotton"}, signposts=FABRIC_PROMPTS) is None
    assert redirect_for("task", {"description": "go", "subagent_type": "composition_worker"}) is None


def test_WITHOUT_SIGNPOSTS_THE_REFUSAL_NAMES_NO_TOOL() -> None:
    """The substrate knows no product's tools; a refusal that invented one would send the model nowhere."""
    said = redirect_for("read_file", {"file_path": "/x.pdf"})

    assert said is not None and "not files" in said
    assert "match_compositions" not in said


# --- the signpost answers the reason, which differs by cause --------------------------------------------


def test_A_SEARCH_FOR_A_SUBJECT_EXTENSION_IS_TOLD_WHAT_IT_IS_LOOKING_FOR() -> None:
    """**It wants the subjects it was just shown**, and telling it where they are NOT leaves it to guess again."""
    assert redirect_for("glob", {"pattern": "**/*.weave"}, signposts=FABRIC_PROMPTS) == FABRIC_PROMPTS.looking_for_subjects


@pytest.mark.parametrize("pattern", ["**/*.weave", "*.pattern", "/compositions/*.WEAVE"])
def test_ANY_OF_THE_PRODUCTS_SUBJECT_ENDINGS_IS_RECOGNISED(pattern: str) -> None:
    """Not one extension special-cased: the product names its own, in any case."""
    assert redirect_for("glob", {"pattern": pattern}, signposts=FABRIC_PROMPTS) == FABRIC_PROMPTS.looking_for_subjects


def test_A_WANDERING_LS_IS_TOLD_THE_PLAIN_THING() -> None:
    """**`ls('/')` is orientation, not a search for subjects**, so it does not get the subject answer."""
    assert redirect_for("ls", {"path": "/"}, signposts=FABRIC_PROMPTS) == FABRIC_PROMPTS.not_a_filesystem


# --- the middleware -----------------------------------------------------------------------------------


def _request(name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(tool_call={"name": name, "args": args, "id": "1", "type": "tool_call"})


def test_THE_MIDDLEWARE_RETURNS_A_TOOL_MESSAGE_NOT_AN_EXCEPTION() -> None:
    """A raise would reach the error middleware, find nothing in the taxonomy and halt the run: a dead session
    in place of a recoverable wrong turn."""
    def handler(_request: object) -> None:
        raise AssertionError("the real tool was called despite the redirect")

    result = TheSubjectIsNotAFilesystem(signposts=FABRIC_PROMPTS).wrap_tool_call(
        _request("read_file", {"file_path": "/cotton-poplin.pdf"}), handler)

    assert isinstance(result, ToolMessage) and "not files" in str(result.content)


def test_A_MEMORY_PATH_REACHES_THE_REAL_TOOL() -> None:
    result = TheSubjectIsNotAFilesystem().wrap_tool_call(
        _request("read_file", {"file_path": "/memories/scope/note.md"}),
        lambda _r: ToolMessage(content="the note", tool_call_id="1", name="read_file"))

    assert result.content == "the note"


@pytest.mark.parametrize("tier", ["orchestrator", "chat-manager"])
def test_THE_MIDDLEWARE_IS_ON_BOTH_TIERS(tier: str) -> None:
    """**Asserted by DRIVING each tier**: a guard reading source asserts how something is written, and the
    refactor it blocks is usually the one that made things better. Asked `ls('/')`, each answers with the
    product's signpost rather than an empty listing."""
    build = build_orchestrator if tier == "orchestrator" else build_chat_agent
    context = _a_context()
    agent = build(context, _resources(), _settings(), definition=_fabric(),
                  model=scripted(_calls("ls", {"path": "/"}), AIMessage(content="done")))
    state = agent.invoke({"messages": [{"role": "user", "content": "go"}]}, context=context,
                         config={"configurable": {"thread_id": f"guard-{tier}"}})

    answered = [m for m in state["messages"] if getattr(m, "name", "") == "ls"]
    assert answered and str(answered[-1].content) == FABRIC_PROMPTS.not_a_filesystem


def test_THE_ORCHESTRATOR_CAN_READ_ITS_OWN_SKILL() -> None:
    """**The consequence, on the tier the skills are for.** Seeded the way `start_run` seeds them and read the
    way the skills middleware tells the model to read them."""
    context = _a_context()
    agent = build_orchestrator(context, _resources(), _settings(), definition=_fabric(),
                               model=scripted(_calls("read_file", {"file_path": "/skills/weaving/SKILL.md"}),
                                              AIMessage(content="done")))
    state = agent.invoke({"messages": [{"role": "user", "content": "go"}],
                          "files": skill_files(FABRIC_PROMPTS.skills)}, context=context)

    read = [m for m in state["messages"] if getattr(m, "name", "") == "read_file"]
    assert read and "How to batch a mill enquiry." in str(read[-1].content), (
        f"the orchestrator could not read its own skill: {str(read[-1].content)[:160] if read else None!r}"
    )
