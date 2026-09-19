"""Pattern 5 - Magentic.

An open-ended question with no known solution path: *why has the Klaipeda lane
degraded this quarter?* A manager agent builds a task ledger, delegates, reads
what comes back, and re-plans until it can answer.

The point on stage: this is the most capable and the most expensive pattern,
and it is the one that most needs its three ceilings set deliberately -
max_round_count, max_stall_count and max_reset_count.
"""

from __future__ import annotations

from agent_framework import Agent
from agent_framework.orchestrations import MagenticBuilder

from ..base import DiagramEdge, DiagramNode, PatternSpec
from ..clients import chat_client
from ..tools import ANALYSIS_TOOLS, CASE_TOOLS

MAX_ROUNDS = 8
MAX_STALLS = 3


def build():
    """A manager over three specialists, with every loop ceiling set explicitly."""
    researcher = Agent(
        client=chat_client("researcher-agent"),
        name="researcher-agent",
        description="Pulls shipment history and finds the affected consignments.",
        instructions=(
            "Find the shipments relevant to the question. List them with lane, exception and severity. "
            "State facts only - no interpretation."
        ),
        tools=ANALYSIS_TOOLS,
    )
    analyst = Agent(
        client=chat_client("analyst-agent"),
        name="analyst-agent",
        description="Finds the pattern across shipments and names a root cause.",
        instructions=(
            "Given the shipments found, identify what they have in common. Distinguish a lane-level "
            "systemic cause from unrelated one-off incidents. Name one root cause."
        ),
        tools=ANALYSIS_TOOLS,
    )
    costing = Agent(
        client=chat_client("cost-agent"),
        name="cost-agent",
        description="Totals the financial impact.",
        instructions="Total the financial exposure across the shipments identified. Give one figure in EUR.",
        tools=CASE_TOOLS,
    )
    manager = Agent(
        client=chat_client("magentic-manager"),
        name="magentic-manager",
        description="Plans, delegates and synthesises the final answer.",
        instructions=(
            "You coordinate specialists on an open-ended investigation. Build a plan, delegate one step at "
            "a time, and stop as soon as you can answer the question."
        ),
    )

    return MagenticBuilder(
        name="Magentic",
        participants=[researcher, analyst, costing],
        manager_agent=manager,
        # The three ceilings. Left unset, an open-ended plan is an open-ended bill.
        max_round_count=MAX_ROUNDS,
        max_stall_count=MAX_STALLS,
        max_reset_count=2,
        output_from="all",
    ).build()


SPEC = PatternSpec(
    slug="magentic",
    number=5,
    name="Magentic",
    tier="core",
    tagline="A manager that plans, delegates, re-plans, and knows when to stop.",
    summary=(
        "Built for problems with no predetermined approach. A manager agent maintains a task ledger - facts "
        "known, facts needed, a plan - delegates one step at a time to whichever specialist fits, reads the "
        "result, and revises the ledger. When it stops making progress it replans; when replanning stops "
        "helping it resets. It is the closest thing here to an autonomous researcher, with the cost profile "
        "to match."
    ),
    use_when=(
        "The problem is open-ended and the solution path genuinely is not known up front.",
        "The answer needs several rounds of research, computation and reasoning over shared context.",
        "You want progress tracking and an optional human review of the plan before work starts.",
    ),
    avoid_when=(
        "The task is linear or deterministic - Sequential is faster and an order of magnitude cheaper.",
        "You only need independent parallel outputs - Concurrent fits.",
        "Your environment forbids dynamic multi-round orchestration.",
    ),
    maf_api=(
        "MagenticBuilder(participants=[...], manager_agent=...)",
        "max_round_count / max_stall_count / max_reset_count",
        ".with_plan_review() for human approval of the plan",
    ),
    failure_mode=(
        "Unbounded spend. The manager decides how many rounds the work takes, so without ceilings the budget "
        "is decided by a language model at runtime. Set all three - rounds, stalls, resets - and turn on "
        "plan review for anything that touches money or customers."
    ),
    scenario="Three separate exceptions on the Klaipeda ferry route in a fortnight. Is the lane the cause?",
    default_prompt=(
        "Several Vilnius shipments are late this month. Investigate whether the Klaipeda lane is the root "
        "cause and total the financial exposure."
    ),
    nodes=(
        DiagramNode("mgr", "magentic-manager", "orchestrator"),
        DiagramNode("ledger", "Task ledger", "store"),
        DiagramNode("res", "researcher-agent", "agent"),
        DiagramNode("ana", "analyst-agent", "agent"),
        DiagramNode("cost", "cost-agent", "agent"),
    ),
    edges=(
        DiagramEdge("mgr", "ledger", "plan"),
        DiagramEdge("mgr", "res", "delegate"),
        DiagramEdge("mgr", "ana", "delegate"),
        DiagramEdge("mgr", "cost", "delegate"),
        DiagramEdge("res", "mgr", "result", "dashed"),
        DiagramEdge("ana", "mgr", "result", "dashed"),
        DiagramEdge("cost", "mgr", "result", "dashed"),
        DiagramEdge("ledger", "mgr", "re-plan", "loop"),
    ),
    devui_name="Magentic",
    build=build,
    new_this_year=False,
)
