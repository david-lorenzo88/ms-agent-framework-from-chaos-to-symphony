"""Pattern 2 - Concurrent.

Three assessors read the same case at the same time and answer independently:
cost, legal, operations. Fan-out, then fan-in to an aggregator.

The point on stage: latency is the obvious win, but independence is the real
one. Run sequentially and the legal view is contaminated by the cost view.
"""

from __future__ import annotations

from agent_framework import Agent
from agent_framework.orchestrations import ConcurrentBuilder

from ..base import CaseBrief, CaseFact, DiagramEdge, DiagramNode, PatternSpec
from ..clients import chat_client
from ..tools import CASE_TOOLS


def build():
    """Three independent assessments of one exception, aggregated."""
    cost = Agent(
        client=chat_client("cost-assessor"),
        name="cost-assessor",
        description="Quantifies the financial exposure.",
        instructions=(
            "Assess only the money: direct loss, penalty exposure, and the goodwill ceiling for this "
            "customer's tier. Give a number. Ignore legal and operational angles entirely."
        ),
        tools=CASE_TOOLS,
    )
    legal = Agent(
        client=chat_client("legal-assessor"),
        name="legal-assessor",
        description="Assesses contractual and regulatory liability.",
        instructions=(
            "Assess only liability: CMR limits, contractual penalty clauses, regulatory exposure. "
            "State whether settling is cheaper than contesting. Ignore cost modelling and operations."
        ),
        tools=CASE_TOOLS,
    )
    ops = Agent(
        client=chat_client("ops-assessor"),
        name="ops-assessor",
        description="Assesses the operational recovery options.",
        instructions=(
            "Assess only recovery: re-route options, added transit hours, and what it takes to stop this "
            "recurring on the lane. Ignore money and legal questions."
        ),
        tools=CASE_TOOLS,
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
    scenario="BFG-24088: a trailer of power converters lost between Poznan and Antwerp, EUR 133,000 declared.",
    case=CaseBrief(
        about=(
            "An entire trailer of power converters disappeared somewhere between Poznan and Antwerp. A police report "
            "is filed; the goods are gone. There is nothing left to inspect, so the only question left is what this "
            "costs the company - and that question has three different answers depending on who you ask."
        ),
        why=(
            "A total loss is the cleanest case for fanning out, because the three assessments genuinely do not depend "
            "on each other. Cost models the write-off, legal reads the liability position, ops works out the recovery "
            "and the replacement. None of them needs another's answer, so they run at once and the clock is the "
            "slowest one rather than the sum of all three. The subtler point is independence: because no assessor sees "
            "the others' reasoning, you get three genuinely uncorrelated views - which is the entire basis of ensemble "
            "reasoning."
        ),
        facts=(
            CaseFact("Shipment", "BFG-24088"),
            CaseFact("Lane", "Vilnius to Antwerp (LT-BE)"),
            CaseFact("Goods", "Power converters, 2,900 kg"),
            CaseFact("Declared value", "EUR 133,000"),
            CaseFact("Customer", "Vilnius Electronics UAB, gold tier"),
            CaseFact("Status", "Lost in transit, police report filed"),
        ),
    ),
    default_prompt="Shipment BFG-24088 has been lost in transit. Assess it from every angle.",
    nodes=(
        DiagramNode("user", "Case", "store"),
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
