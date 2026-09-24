"""Pattern 1 - Sequential.

A fixed pipeline: intake states what changed, the trip planner rebuilds the
trip around it, the writer tells the family. Each participant sees everything
said before it.

The point on stage: this is the cheapest, most auditable pattern, and most
"multi-agent" problems are really this one. Reach for anything fancier only
when a linear chain genuinely cannot express the work.
"""

from __future__ import annotations

from agent_framework import Agent
from agent_framework.orchestrations import SequentialBuilder

from ..base import CaseBrief, CaseFact, DiagramEdge, DiagramNode, PatternSpec
from ..clients import chat_client
from ..tools import INTAKE_TOOLS, PLANNER_TOOLS


def build():
    """Intake -> Trip planner -> Writer, sharing one growing conversation."""
    intake = Agent(
        client=chat_client("intake-agent"),
        name="intake-agent",
        description="Pulls the booking and states what changed.",
        instructions=(
            "You are incident intake at Baltic Travel Agency. Look the booking up, state what went wrong "
            "in two sentences, and name the customer and their tier. Do not propose a remedy."
        ),
        tools=INTAKE_TOOLS,
    )
    planner = Agent(
        client=chat_client("trip-planner"),
        name="trip-planner",
        description="Rebuilds the itinerary around a changed flight.",
        instructions=(
            "You rebuild trips around disruption. Using the facts above, work out when the travellers now "
            "arrive, which hotel nights to release so they are not charged as a no-show, and when each "
            "activity they would miss can move to - check live slots, and only offer one with room for the "
            "whole party. Check the flight against EU261 and say who owes what."
        ),
        tools=PLANNER_TOOLS,
    )
    writer = Agent(
        client=chat_client("writer-agent"),
        name="writer-agent",
        description="Writes the customer letter.",
        instructions=(
            "You write the customer letter. State what happened and what has been done, put a date on "
            "every commitment, and say who owes any compensation. Never admit legal liability."
        ),
    )
    return SequentialBuilder(
        name="Sequential",
        participants=[intake, planner, writer],
        output_from="all",
    ).build()


SPEC = PatternSpec(
    slug="sequential",
    number=1,
    name="Sequential",
    tier="core",
    tagline="A pipeline. Each agent refines what the last one produced.",
    summary=(
        "Chains agents in a fixed, linear order. Each participant receives the shared conversation so far "
        "and appends to it, creating a pipeline of specialised transformations. It is the Pipes and Filters "
        "cloud pattern with language models in the filters - deterministic in structure, reproducible, and "
        "trivially auditable because the transcript is the audit trail."
    ),
    use_when=(
        "Steps have genuine linear dependencies: draft, then review, then polish.",
        "You need a predictable, reproducible pipeline you can point a compliance officer at.",
        "Progressive refinement improves quality, and the stages cannot be parallelised.",
    ),
    avoid_when=(
        "The work is embarrassingly parallel - use Concurrent and cut the latency.",
        "One agent could do all the stages; the orchestration is then pure overhead.",
        "You need routing or backtracking on intermediate results - a chain cannot branch.",
    ),
    maf_api=("SequentialBuilder(participants=[...], output_from='all')", "workflow.run(prompt)"),
    failure_mode=(
        "Error amplification. A wrong fact introduced at stage one is treated as established truth by every "
        "later stage, and the confident final answer hides it. Mitigate with a verification participant, or "
        "structured output between stages so a malformed hand-off fails loudly instead of silently."
    ),
    scenario="BTA-26101: airBaltic cancels a family's flight to Barcelona two days out. Everything after it moves.",
    case=CaseBrief(
        about=(
            "Two days before the Kalniņš family fly to Barcelona, airBaltic cancels their Saturday flight and moves "
            "them to the same flight on Sunday. That one change runs through the whole trip: unless someone tells the "
            "hotel, their first night at Catalonia Plaza Catalunya becomes a no-show they pay for, and the Sagrada "
            "Família tour booked for Saturday afternoon happens without them. The family needs one letter that says "
            "what has been fixed, what they are owed, and by whom."
        ),
        why=(
            "The work has a real order to it. You cannot move the tour before you know when the family lands, and you "
            "cannot write to them before the plan exists. Three agents, one after another: intake pulls the booking "
            "and states what changed; the trip planner rebuilds the trip around the new flight - releases the first "
            "hotel night, finds a tour slot after landing with room for four, and checks what EU261 says; the writer "
            "turns that into one letter. Watch the transcript rather than the answer - each agent appends to the same "
            "conversation, which is why this pattern is the easy one to put in front of a compliance officer."
        ),
        facts=(
            CaseFact("Booking", "BTA-26101"),
            CaseFact("Trip", "Barcelona family holiday, 26 Sep - 3 Oct"),
            CaseFact("Flight", "airBaltic BT651 Riga → Barcelona, cancelled - moved to 27 Sep"),
            CaseFact("Hotel", "Catalonia Plaza Catalunya, 2 Family Rooms, 7 nights"),
            CaseFact("Customer", "Kalniņš family, gold tier"),
            CaseFact("EU261", "EUR 400 each, owed by airBaltic - 2,337 km, cancelled two days out"),
        ),
    ),
    default_prompt=(
        "Booking BTA-26101: airBaltic has cancelled the family's outbound flight to Barcelona. Work the knock-on and "
        "write to the family."
    ),
    nodes=(
        DiagramNode("user", "Booking", "store"),
        DiagramNode("intake", "intake-agent", "agent"),
        DiagramNode("planner", "trip-planner", "agent"),
        DiagramNode("writer", "writer-agent", "agent"),
        DiagramNode("out", "Customer letter", "store"),
    ),
    edges=(
        DiagramEdge("user", "intake", "incident"),
        DiagramEdge("intake", "planner", "facts"),
        DiagramEdge("planner", "writer", "+ new plan"),
        DiagramEdge("writer", "out", "letter"),
    ),
    devui_name="Sequential",
    build=build,
)
