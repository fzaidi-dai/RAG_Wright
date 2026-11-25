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


def test_A_SYMPTOM_AS_A_PERSON_SAYS_IT_FINDS_THE_SIMILAR_TICKETS() -> None:
    """**PS-81**: requiring every word of the symptom found nothing for "pump P-3 leaking at the seal", and a model
    told "nothing" searched again and again: six worker dispatches in one live run, fourteen rephrasings in the next."""
    example = _example()
    resources = example.DeskResources(tickets={"site-1": ["pump P-3 leaking", "door sensor stuck"],
                                               "site-2": ["pump P-7 leaking"]})
    (search,) = example.DeskSurface().build(example.DeskContext(organisation="acme", site="site-1"), resources,
                                           only=["desk.find_similar_faults"])

    found = search.invoke({"symptom": "Pump P-3 leaking at the seal"})

    assert found["similar"] == ["pump P-3 leaking", "pump P-7 leaking"]


def test_THE_WORKERS_REPORT_KEEPS_WHICH_TICKET_MATCHED_WHICH_SYMPTOM() -> None:
    """**PS-86**: the report is all the orchestrator sees of a worker. A flat list for an eight-symptom question lost
    which ticket matched which symptom, and the orchestrator re-dispatched one worker five times for one symptom."""
    example = _example()

    report = example.FaultFindings(status="done", tickets={"pump leaking": ["pump P-3 leaking", "pump P-7 leaking"],
                                                          "belt slipping": []})

    assert report.tickets["pump leaking"] == ["pump P-3 leaking", "pump P-7 leaking"]
    assert report.tickets["belt slipping"] == []
