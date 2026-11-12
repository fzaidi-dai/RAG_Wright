"""A product's skill: seeded, readable, and not mistaken for the run's documents.

Ported from RuleWright under ADR-0139's handover (PS-48a); the substrate is `rag_wright.harness.skills`,
`runs._opening_turn`, `rules.reached_for_the_filesystem` and `tiers.build_orchestrator`. RuleWright's
tests of its own skill's text and prompts stay in RuleWright.

**The failure these are written against is a skill that is advertised but cannot be read.** That is worse
than no skill: the model spends a `read_file` discovering the gap, and on this harness it would also have
scored a rule against the tier that tried, because reading anything outside the agent's own paths read as
treating the run's documents as a filesystem.
"""

from __future__ import annotations

from typing import Any

from tests.harness.fabric import FABRIC_PROMPTS, _a_context, _fabric, _resources, _settings, scripted

#: The fabric product's one skill, as its definition publishes it.
[WEAVING_PATH] = FABRIC_PROMPTS.skills


def test_THE_SKILL_IS_SEEDED_AT_THE_ONE_DOOR_SO_A_RUN_CANNOT_MISS_IT() -> None:
    """**`StateBackend` serves the virtual filesystem out of graph state.** A skill absent from the
    opening turn does not exist for that run, however correctly the middleware is wired."""
    from rag_wright.harness.runs import _opening_turn

    turn = _opening_turn("which blends suit outerwear?", None, FABRIC_PROMPTS.skills)

    assert WEAVING_PATH in (turn.get("files") or {}), (
        f"the opening turn carries no skill at {WEAVING_PATH}; the orchestrator would be advertised a "
        "file it cannot open"
    )


def test_READING_THE_SKILL_IS_NOT_REACHING_FOR_THE_DOCUMENTS() -> None:
    """**The rule that would have fired, and the reason `/skills` is on the agent's own paths.**

    `reached_for_the_filesystem` flags a filesystem call whose path is not one of the agent's own. The
    criterion it enforces is "no filesystem tool against the run's DOCUMENTS" -- a skill the product
    authored is not one of them.
    """
    from rag_wright.harness.rules import reached_for_the_filesystem

    def a_run_that_read(path: str) -> dict:
        """**The recording shape the detector really reads.** A hand-rolled `{"calls": [...]}` yields
        NOTHING from `_calls`, so both halves of this test would pass for the wrong reason -- a fixture the
        production code cannot parse proves nothing at all."""
        return {"turns": [{"n": 1, "said": [{"tool_calls": [
            {"name": "read_file", "args": {"file_path": path}}]}]}]}

    assert reached_for_the_filesystem(a_run_that_read(WEAVING_PATH)) is None, (
        "reading the agent's own skill is scored as treating the documents as a filesystem"
    )
    assert reached_for_the_filesystem(a_run_that_read("/aw26-outerwear.weave")) is not None, (
        "the rule no longer catches a real filesystem reach -- widening it went too far"
    )


async def test_THE_ORCHESTRATOR_CAN_ACTUALLY_READ_ITS_OWN_SKILL() -> None:
    """**The end-to-end property, and the one the others cannot stand in for.** Wiring, seeding and
    naming can each be right while the file is still unreadable at run time."""
    from langchain.agents.middleware.types import AgentMiddleware
    from langchain_core.messages import AIMessage

    from rag_wright.harness.runs import _opening_turn
    from rag_wright.harness.tiers import build_orchestrator

    offered: dict[str, Any] = {}

    class Watch(AgentMiddleware):
        @property
        def name(self) -> str:
            return "Watch"

        async def awrap_model_call(self, request, handler):
            offered["tools"] = {getattr(t, "name", str(t)) for t in (request.tools or [])}
            message = request.system_message
            content = getattr(message, "content", message) or ""
            offered["prompt"] = content if isinstance(content, str) else "\n".join(
                b.get("text", "") for b in content if isinstance(b, dict))
            return await handler(request)

    context = _a_context()
    agent = build_orchestrator(context, _resources(), _settings(), model=scripted(AIMessage(content="done")),
                               extra_middleware=[Watch()], definition=_fabric())
    state = await agent.ainvoke(_opening_turn("which blends suit outerwear?", None, FABRIC_PROMPTS.skills),
                                context=context)

    assert "weaving" in offered["prompt"], "the skill is seeded but never advertised to the model"
    assert WEAVING_PATH in offered["prompt"], "the model is told the skill exists but not where"
    assert "read_file" in offered["tools"], "the skill is advertised but the tool to open it is withheld"
    assert WEAVING_PATH in (state.get("files") or {}), "the skill did not survive into run state"


# --- a skill the library would skip is refused when the product declares it ---------------------------


def _prompts_with(skills: dict[str, str]) -> Any:
    from dataclasses import replace

    return replace(FABRIC_PROMPTS, skills=skills)


def test_A_SKILL_WITHOUT_A_DESCRIPTION_IS_REFUSED_AT_DECLARATION() -> None:
    """**Found by this port**: the fabric product's own skill had a `name` and no `description`, so
    deepagents skipped it with a logged warning and the orchestrator was never told it existed. The
    product finds out when it declares the skill, not by reading a log."""
    import pytest

    with pytest.raises(ValueError, match="/skills/weaving/SKILL.md"):
        _prompts_with({"/skills/weaving/SKILL.md": "---\nname: weaving\n---\nHow to batch a mill enquiry."})


def test_A_SKILL_WITHOUT_FRONTMATTER_IS_REFUSED() -> None:
    import pytest

    with pytest.raises(ValueError, match="frontmatter"):
        _prompts_with({"/skills/weaving/SKILL.md": "How to batch a mill enquiry."})


def test_A_SKILL_OUTSIDE_THE_SKILLS_FOLDER_IS_REFUSED() -> None:
    """The middleware lists `/skills/<name>/SKILL.md` and nothing else, so a skill published anywhere
    else is seeded into the run and never advertised."""
    import pytest

    text = FABRIC_PROMPTS.skills[WEAVING_PATH]
    for path in ("/weaving/SKILL.md", "/skills/SKILL.md", "/skills/weaving/notes.md", "/skills/a/b/SKILL.md"):
        with pytest.raises(ValueError, match="/skills/<name>/SKILL.md"):
            _prompts_with({path: text})


def test_A_VALID_SKILL_AND_NO_SKILLS_ARE_BOTH_ACCEPTED() -> None:
    assert _prompts_with(dict(FABRIC_PROMPTS.skills)).skills == FABRIC_PROMPTS.skills
    assert _prompts_with({}).skills == {}
