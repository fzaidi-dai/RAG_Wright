"""The orchestrator's skill: the procedure it needs OCCASIONALLY, kept out of every turn.

**Why a skill and not more prompt.** `AGENT-SPEC.md` §13 names "the orchestrator's skill" as the
mechanism for `AR-16` (batched clarifications) and `AR-10` (authored product knowledge), and nothing had
ever built one -- `SkillsMiddleware` was unused anywhere in the repo. The distinction that earns it is
frequency, not importance: the decomposition rules in `ORCHESTRATOR_PROMPT` are needed on EVERY turn and
must stay there, because T-5a.12aa's whole point was that those rules have to be decisive at the margin.
Batching a clarification is needed only when a worker comes back `blocked`, which is rare. Progressive
disclosure means the model sees a name and one line until then, and reads the procedure when it applies.

**`AR-10` is why this is authored rather than learned.** Product knowledge is written down and reviewed;
it is never promoted out of one customer's run into another's. A skill is a file we wrote. `/memories` is
what an agent noticed. Keeping them in separate namespaces is the mechanism, not a tidiness preference.

**The content is a Python constant, not a packaged `.md` file.** `StateBackend` serves the virtual
filesystem out of graph state, so the skill has to be WRITTEN into state either way (the library's own
documented path for this backend is `invoke(files=...)`). Sourcing it from a constant means there is no
package-data story to get wrong in a wheel, and one place to review the text.
"""

from __future__ import annotations

from typing import Any

#: Where the skill lives in the agent's own virtual filesystem.
#:
#: **Not under `/memories`, deliberately.** That is the memory namespace (`AR-9`), and `AR-10` draws the
#: line this path keeps: authored knowledge and learned notes are different things. `rules.py` lists this
#: prefix among the agent's OWN paths so reading it is not mistaken for treating the matter as a
#: filesystem -- see `OWN_FILESYSTEM`.
SKILLS_ROOT = "/skills"
ORCHESTRATING_PATH = f"{SKILLS_ROOT}/orchestrating/SKILL.md"




def skill_files(skills: dict[str, str]) -> dict[str, Any]:
    """The skill, shaped for the virtual filesystem `StateBackend` serves.

    Seeded through the one door (`start_run`), so a run cannot reach an agent without it.
    """
    from deepagents.backends.utils import create_file_data

    return {path: create_file_data(text) for path, text in skills.items()}


def orchestrator_skill(backend: Any = None, *, preamble: str = "", label: str = "skills") -> Any:
    """`SkillsMiddleware` over the authored skill, carrying OUR prompt rather than the library's.

    The backend defaults to a `StateBackend`, which is what `create_deep_agent` builds when no backend is
    passed -- it is a view over graph state rather than a store of its own, so reading the skill reads the
    same `files` the run was seeded with.
    """
    from deepagents.backends import StateBackend
    from deepagents.middleware.skills import SkillsMiddleware

    return SkillsMiddleware(
        backend=backend if backend is not None else StateBackend(),
        # **The label is the PRODUCT's name, passed in** (T-7.58). It was the literal `"RuleWright"`,
        # which the library shows the model as the source of every skill it reads.
        sources=[(f"{SKILLS_ROOT}/", label)],
        system_prompt=preamble,
    )
