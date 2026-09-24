"""Pattern 2 - Concurrent.

Three assessors read the same case at the same time and answer independently:
cost, liability, rehousing. Fan-out, then fan-in to an aggregator.

The point on stage: latency is the obvious win, but independence is the real
one. Run sequentially and the legal view is contaminated by the cost view -
and here the two genuinely disagree, which is only visible because neither saw
the other's answer.
"""

from __future__ import annotations

from agent_framework import Agent
from agent_framework.orchestrations import ConcurrentBuilder

from ..base import CaseBrief, CaseFact, DiagramEdge, DiagramNode, PatternSpec
from ..clients import chat_client
from ..tools import check_availability, estimate_compensation, get_customer, get_sync_log, lookup_booking


def build():
    """Three independent assessments of one incident, aggregated.

    Each assessor speaks first, with nobody before it in the conversation, so
    each holds its own read access to the facts its question turns on.
    """
    cost = Agent(
        client=chat_client("cost-assessor"),
        name="cost-assessor",
        description="Quantifies what the incident costs the agency.",
        instructions=(
            "Assess only the money: services not delivered, the disruption allowance for this customer's "
            "tier, and the goodwill ceiling. Give a number. Ignore liability and rehousing entirely."
        ),
        tools=[lookup_booking, get_customer, estimate_compensation],
    )
    legal = Agent(
        client=chat_client("legal-assessor"),
        name="legal-assessor",
        description="Assesses liability as package organiser, and who ultimately pays.",
        instructions=(
            "Assess only liability: what we owe as package organiser under the Package Travel Directive, "
            "and whether the cost can be recovered from the hotel - read the channel-manager sync log to see "
            "whose system failed. Ignore cost modelling and rehousing."
        ),
        tools=[lookup_booking, get_customer, get_sync_log],
    )
    ops = Agent(
        client=chat_client("ops-assessor"),
        name="ops-assessor",
        description="Finds rooms for the travellers who have none.",
        instructions=(
            "Assess only rehousing: search live availability near the hotel, say where the travellers who "
            "cannot be housed will sleep, and how they get to the programme. Ignore money and liability."
        ),
        tools=[lookup_booking, check_availability],
    )
    return ConcurrentBuilder(name="Concurrent", participants=[cost, legal, ops], output_from="all").build()


SPEC = PatternSpec(
    slug="concurrent",
    number=2,
    name="Concurrent",
    tier="core",
    tagline="Fan out to specialists at once, fan in one answer.",
    summary=(
        "Runs several agents simultaneously against the same input, then aggregates. Wall-clock time becomes "
        "the slowest participant rather than the sum of all of them. The subtler benefit is independence: "
        "because no participant sees another's output, you get genuinely uncorrelated perspectives, which is "
        "what makes ensemble reasoning and quorum voting work."
    ),
    use_when=(
        "Several specialists can assess the same input from different angles.",
        "You want diverse, uncorrelated views - brainstorming, ensembles, voting.",
        "Latency matters and no participant needs another's output.",
    ),
    avoid_when=(
        "There is a strict order of operations - use Sequential.",
        "Participants would write to shared state or external systems in parallel.",
        "You have no aggregation strategy; merging conflicting answers would lower quality.",
    ),
    maf_api=(
        "ConcurrentBuilder(participants=[...], output_from='all')",
        ".with_aggregator(custom_executor)",
    ),
    failure_mode=(
        "Cost multiplication and silent disagreement. Every participant pays full token price on the same "
        "input, and when two of them contradict each other the aggregator often averages the conflict away "
        "instead of surfacing it. Aggregate with an explicit conflict rule, not a summary prompt."
    ),
    scenario="BTA-26102: a Jūrmala hotel cannot honour 7 of a corporate group's 12 rooms. They arrive tomorrow.",
    case=CaseBrief(
        about=(
            "Nordic Code Labs is sending 24 people to the Baltic Beach Hotel & SPA in Jūrmala for a three-day team "
            "incentive. The hotel has just told us it can honour 5 of the group's 12 rooms: the other seven were sold "
            "to us after the hotel had already closed them. Fourteen people arrive tomorrow with nowhere to sleep, and "
            "three questions have to be answered at once - what this costs, whose cost it is, and where everyone "
            "sleeps."
        ),
        why=(
            "The three assessments genuinely do not depend on each other, which is the whole case for fanning out. "
            "Cost models the bill, legal reads the liability, ops finds rooms - Hotel Jūrmala Spa and Semarah Hotel "
            "Lielupe have nine between them. None needs another's answer, so they run at once and the clock is the "
            "slowest one rather than the sum of all three. Then read the answers side by side: cost assumes the "
            "relocation can be recovered from the hotel, and legal says it cannot, because the sync log shows the "
            "fault was ours. That disagreement is only visible because neither saw the other's answer - and it is "
            "exactly what an aggregator must surface rather than smooth over."
        ),
        facts=(
            CaseFact("Booking", "BTA-26102"),
            CaseFact("Group", "Nordic Code Labs SIA, 24 travellers, gold tier"),
            CaseFact("Hotel", "Baltic Beach Hotel & SPA, Jūrmala - 12 rooms, 25-28 Sep"),
            CaseFact("Shortfall", "7 rooms, 14 guests"),
            CaseFact("Booked", "15 Sep at 06:40"),
            CaseFact("Space nearby", "Hotel Jūrmala Spa (5 rooms), Semarah Hotel Lielupe (4)"),
        ),
    ),
    default_prompt="Booking BTA-26102: the hotel cannot honour 7 of the group's 12 rooms. Assess it from every angle.",
    nodes=(
        DiagramNode("user", "Booking", "store"),
        DiagramNode("dispatch", "dispatcher", "orchestrator"),
        DiagramNode("cost", "cost-assessor", "agent"),
        DiagramNode("legal", "legal-assessor", "agent"),
        DiagramNode("ops", "ops-assessor", "agent"),
        DiagramNode("agg", "aggregator", "orchestrator"),
    ),
    edges=(
        DiagramEdge("user", "dispatch", ""),
        DiagramEdge("dispatch", "cost", "fan-out"),
        DiagramEdge("dispatch", "legal", "fan-out"),
        DiagramEdge("dispatch", "ops", "fan-out"),
        DiagramEdge("cost", "agg", "fan-in"),
        DiagramEdge("legal", "agg", "fan-in"),
        DiagramEdge("ops", "agg", "fan-in"),
    ),
    devui_name="Concurrent",
    build=build,
)
