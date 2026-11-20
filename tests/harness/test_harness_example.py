"""`examples/harness_quickstart.py` is a working product, not a sketch: run here with a scripted model (PS-69)."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

from langchain_core.messages import AIMessage

from tests.harness.fabric import scripted

EXAMPLE = Path(__file__).parents[2] / "examples" / "harness_quickstart.py"


def _example():
    spec = importlib.util.spec_from_file_location("harness_quickstart", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass resolves its annotations through its module
    spec.loader.exec_module(module)
    return module


def test_THE_EXAMPLE_PRODUCT_ANSWERS_FROM_ITS_OWN_DIRECT_TOOL_UNDER_ITS_OWN_SCOPE() -> None:
    example = _example()
    model = scripted(
        AIMessage(content="", tool_calls=[{"name": "list_open_tickets", "args": {}, "id": "c1", "type": "tool_call"}]),
        AIMessage(content="Two tickets are open at site-1."),
    )

    run = asyncio.run(example.ask("Which tickets are open?", organisation="acme", site="site-1", model=model))

    *_, called, answered = run.state["messages"]
    assert '"site": "site-1"' in called.content, "the tool ran under the run's scope, which the model never named"
    assert answered.content == "Two tickets are open at site-1."


def test_THE_EXAMPLE_DECLARES_A_TIER_SPLIT_THE_SUBSTRATE_DERIVES() -> None:
    from rag_wright.harness import direct_names, worker_operations

    example = _example()

    assert set(direct_names(example.DEFINITION)) == {"desk.list_open_tickets"}
    assert worker_operations(example.DEFINITION, example.DeskContext("acme", "site-1")) == {"desk.find_similar_faults"}
