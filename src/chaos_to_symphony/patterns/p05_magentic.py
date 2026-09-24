"""Pattern 5 - Magentic.

An open-ended question with no known solution path: *three hotels have failed
our guests in ten days - is that bad luck, or is it us?* A manager agent builds
a task ledger, delegates, reads what comes back, and re-plans until it can answer.

The point on stage: this is the most capable and the most expensive pattern,
and it is the one that most needs its three ceilings set deliberately -
max_round_count, max_stall_count and max_reset_count.
"""

from __future__ import annotations

from agent_framework import Agent
from agent_framework.orchestrations import MagenticBuilder

from ..base import CaseBrief, CaseFact, DiagramEdge, DiagramNode, PatternSpec
from ..clients import chat_client
from ..tools import ANALYSIS_TOOLS, estimate_compensation, lookup_booking

MAX_ROUNDS = 8
MAX_STALLS = 3


def build():
    """A manager over three specialists, with every loop ceiling set explicitly."""
    researcher = Agent(
        client=chat_client("researcher-agent"),
        name="researcher-agent",
        description="Finds the incidents relevant to the question.",
        instructions=(
            "Find the incidents relevant to the question. List each with its booking, hotel, incident kind "
            "and when it was booked. State facts only - no interpretation."
        ),
        tools=ANALYSIS_TOOLS,
    )
    analyst = Agent(
        client=chat_client("analyst-agent"),
        name="analyst-agent",
        description="Finds what the incidents have in common and names a root cause.",
        instructions=(
            "Given the incidents found, identify what they have in common. Read the channel-manager sync log "
            "for each hotel involved, and distinguish one systemic cause from unrelated one-off failures. "
            "Name one root cause."
        ),
        tools=ANALYSIS_TOOLS,
    )
    costing = Agent(
        client=chat_client("cost-agent"),
        name="cost-agent",
        description="Totals the financial impact.",
        instructions="Total the agency's exposure across the bookings identified. Give one figure in EUR.",
        tools=[lookup_booking, estimate_compensation],
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
    scenario="Three hotels have walked or downgraded our guests since mid-September. Is our channel manager the cause?",
    case=CaseBrief(
        about=(
            "This one is not about a single booking. In ten days three hotels have failed our guests: the Baltic Beach "
            "Hotel & SPA cannot house a corporate group, Hotel Excelsior Dubrovnik sold a honeymoon suite twice, and "
            "Pullman Riga Old Town walked a family on arrival. Three hotels in three cities, and no complaint mentions "
            "the others - it looks like bad luck. Somebody has to find out whether it is, and put a number on what it "
            "has cost."
        ),
        why=(
            "There is no procedure for this. You do not know in advance whether the answer is in the incidents, the "
            "bookings, the hotel contracts or the channel-manager logs - and that is precisely the situation the "
            "manager agent exists for. It keeps a ledger of what it knows and what it still needs, delegates one step "
            "at a time to whichever specialist fits, reads the result, and revises the plan. The answer is not in any "
            "single booking, so watch the analyst's turn: it is the one that looks where no guest complained. Two of "
            "the three incidents are cases the audience has already watched, in patterns 2 and 3. And watch the round "
            "count: a language model is deciding how much work this takes, which is why all three ceilings are set."
        ),
        facts=(
            CaseFact("The question", "Bad luck, or one cause? And what has it cost?"),
            CaseFact("Incidents", "BTA-26102 (Jūrmala), BTA-26103 (Dubrovnik), BTA-26106 (Riga)"),
            CaseFact("Hotels", "Baltic Beach Hotel & SPA, Hotel Excelsior Dubrovnik, Pullman Riga Old Town"),
            CaseFact("Window", "Ten days, three cities, three different guests"),
            CaseFact("Where to look", "Incidents, bookings, contracts, channel-manager logs - nobody knows yet"),
            CaseFact("Must produce", "A root cause and the total exposure"),
        ),
    ),
    default_prompt=(
        "Three hotels have walked or downgraded our guests since mid-September. Investigate whether our channel "
        "manager connection is the root cause and total the exposure."
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
