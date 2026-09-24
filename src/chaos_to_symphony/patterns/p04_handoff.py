"""Pattern 4 - Handoff.

Triage reads the case and transfers ownership to the right specialist. The
routing decision is made by the agent holding the case, not by a central
manager - that is the whole distinction from Group Chat.

The point on stage: handoff tools are ordinary tools. The model routes by
calling one, which means routing shows up in the trace like any other tool call.
"""

from __future__ import annotations

from agent_framework import Agent, Message
from agent_framework.orchestrations import HandoffBuilder

from ..base import CaseBrief, CaseFact, DiagramEdge, DiagramNode, PatternSpec, PromptExample
from ..clients import chat_client
from ..tools import (
    FLIGHT_TOOLS,
    HANDOFF_TRIAGE_TOOLS,
    HOTEL_TOOLS,
    PAYMENT_TOOLS,
    lookup_booking,
    record_decision,
    screen_payment,
)


def resolved(conversation: list[Message]) -> bool:
    """End once a participant has actually answered the case.

    Handoff is conversational by design: left alone the workflow asks the user
    for another turn after every reply, so a single-shot demo loops - specialist
    answers, workflow asks, we answer, specialist answers again - until the
    runner gives up at a hundred iterations. That is this pattern's own
    hot-potato failure mode, met from the outside rather than in theory.

    The test is "the last message is a non-empty assistant reply" rather than
    the more obvious "a handoff tool was called". The conversation handed to a
    termination condition has already been through
    ``clean_conversation_for_handoff``, so the routing tool calls are stripped
    out of it and simply cannot be seen from here - measured, not assumed.
    Triage's own turn is one of those stripped tool calls, which is why this
    does not fire before the case has been routed.
    """
    if not conversation:
        return False
    last = conversation[-1]
    return str(last.role) == "assistant" and bool((getattr(last, "text", "") or "").strip())


def build():
    """Triage fans out to four specialists; specialists can bounce back to triage."""
    triage = Agent(
        # Two tool calls offline: one to screen the payment, one to route on the
        # answer - the same two a live model makes when it follows the prompt.
        client=chat_client("triage-agent", tool_budget=2),
        name="triage-agent",
        description="First contact. Decides which specialist owns the case.",
        instructions=(
            "You triage incidents at Baltic Travel Agency. Look the booking up and screen its payment before "
            "routing anything: a payment that fails screening goes to risk, whatever the incident is. "
            "Otherwise route by the incident - flights, hotels, or billing for refunds. Hand off to the "
            "specialist who should own it; do not attempt to resolve it yourself."
        ),
        # Handoff short-circuits on the routing tool call, so each agent must keep
        # its own history in step with the service. The builder refuses without this.
        require_per_service_call_history_persistence=True,
        tools=HANDOFF_TRIAGE_TOOLS,
    )
    flights = Agent(
        client=chat_client("flights-specialist"),
        name="flights-specialist",
        description="Owns cancellations, delays and the airline's EU261 claim.",
        instructions=(
            "Resolve flight disruption. Check the flight against EU261, state what the airline owes and the "
            "care it must provide, and the traveller's rights. We file the claim; we do not pay it."
        ),
        # Handoff short-circuits on the routing tool call, so each agent must keep
        # its own history in step with the service. The builder refuses without this.
        require_per_service_call_history_persistence=True,
        tools=FLIGHT_TOOLS,
    )
    hotels = Agent(
        client=chat_client("hotels-specialist"),
        name="hotels-specialist",
        description="Owns overbookings, downgrades and rehousing.",
        instructions=(
            "Resolve hotel problems. Establish what happened from the booking and the channel-manager log, "
            "and find where the travellers sleep if the hotel cannot house them."
        ),
        # Handoff short-circuits on the routing tool call, so each agent must keep
        # its own history in step with the service. The builder refuses without this.
        require_per_service_call_history_persistence=True,
        tools=HOTEL_TOOLS,
    )
    billing = Agent(
        client=chat_client("billing-specialist"),
        name="billing-specialist",
        description="Owns refunds and invoices.",
        instructions=(
            "Handle refunds. Screen the payment before any refund, refund only to the card that paid, and "
            "give the amount and the date it will arrive."
        ),
        # Handoff short-circuits on the routing tool call, so each agent must keep
        # its own history in step with the service. The builder refuses without this.
        require_per_service_call_history_persistence=True,
        tools=PAYMENT_TOOLS,
    )
    risk = Agent(
        client=chat_client("risk-specialist"),
        name="risk-specialist",
        description="Owns payments that fail screening: fraud flags and open chargebacks.",
        instructions=(
            "Handle payments that fail screening. If the card is flagged, state plainly that the booking is "
            "frozen and that no refund goes to any card until risk has reviewed it. Never release it yourself."
        ),
        # Handoff short-circuits on the routing tool call, so each agent must keep
        # its own history in step with the service. The builder refuses without this.
        require_per_service_call_history_persistence=True,
        tools=[lookup_booking, screen_payment, record_decision],
    )

    builder = (
        HandoffBuilder(
            name="Handoff",
            participants=[triage, flights, hotels, billing, risk],
            description="Travel incident desk with specialist routing.",
            termination_condition=resolved,
        )
        .with_start_agent(triage)
        .add_handoff(triage, [flights, hotels, billing, risk])
    )
    # Specialists can return a case they do not own. Without this the desk is a
    # one-way street and a mis-triage is unrecoverable.
    for specialist in (flights, hotels, billing, risk):
        builder = builder.add_handoff(specialist, [triage])
    return builder.build()


SPEC = PatternSpec(
    slug="handoff",
    number=4,
    name="Handoff",
    tier="core",
    tagline="The agent holding the case decides who should hold it next.",
    summary=(
        "Ownership of the conversation transfers between specialists. Each agent gets a generated "
        "handoff_to_* tool per permitted target, so routing is a tool call the model makes with full "
        "context - not a decision a router made before anyone read the case. That is what lets the right "
        "owner emerge mid-conversation, as requirements become clear."
    ),
    use_when=(
        "The right expert is not knowable up front: triage, then billing, then technical.",
        "Escalation and fallback are normal, and ownership should move with the case.",
        "You want a human able to step in at the switching point.",
    ),
    avoid_when=(
        "You need parallel or ensemble analysis - handoff is inherently one-at-a-time.",
        "A central manager should control the flow; that is Group Chat's model.",
        "The pipeline is fixed and deterministic - Sequential is cheaper and clearer.",
    ),
    maf_api=(
        "HandoffBuilder(participants=[...]).with_start_agent(a)",
        ".add_handoff(source, [targets])",
        ".with_autonomous_mode() / .with_termination_condition(...)",
    ),
    failure_mode=(
        "Hot-potato routing: two specialists each believe the case belongs to the other and hand it back and "
        "forth until the budget dies. Bound the return path - a depth limit or a termination condition - and "
        "never let a mesh topology be the default just because it was less typing."
    ),
    scenario="BTA-26104: a refund request on a cancelled food tour - paid with a card reported stolen.",
    case=CaseBrief(
        about=(
            "A customer booked two nights at Hotel Neiburgs in Riga and a Riga Central Market food tour. The operator "
            "cancelled the tour, and the customer now wants the whole booking refunded, EUR 508 - to a different card. "
            "On the surface this is the most routine refund on the desk. It is not: the card that paid was reported "
            "stolen three days ago, and the right outcome is a frozen booking and a risk review, not a refund."
        ),
        why=(
            "The surface of this case points at the wrong owner, and that is the whole argument for the pattern. It is "
            "filed as a cancelled activity, so a router working from that one field would send it to billing, who "
            "handle refunds. Triage does not: it reads the booking, screens the payment before routing - its "
            "instructions say to - sees the stolen card, and hands the case straight to the risk specialist. The "
            "routing is a tool call the model makes with the full case in view, not a decision a router made from one "
            "field before anyone had read the file. The example prompts make the point: two cancelled activities, two "
            "different owners. Note the topology in the diagram: specialists hand back to triage rather than sideways "
            "to each other, which is what stops two of them volleying the case between them."
        ),
        facts=(
            CaseFact("Booking", "BTA-26104"),
            CaseFact("Booked", "Hotel Neiburgs, 2 nights, and a Riga Central Market food tour"),
            CaseFact("Incident", "Food tour cancelled by the operator"),
            CaseFact("Request", "Refund EUR 508 - to a different card"),
            CaseFact("Customer", "J. Miller, bronze tier, first booking with us"),
            CaseFact("The twist", "Screening: card reported stolen - freeze and escalate to risk"),
        ),
    ),
    default_prompt=(
        "Booking BTA-26104: the customer wants a refund for a cancelled tour, paid to a different card. Route it to "
        "the right specialist and resolve it."
    ),
    nodes=(
        DiagramNode("triage", "triage-agent", "orchestrator"),
        DiagramNode("flights", "flights-specialist", "agent"),
        DiagramNode("hotels", "hotels-specialist", "agent"),
        DiagramNode("billing", "billing-specialist", "agent"),
        DiagramNode("risk", "risk-specialist", "agent"),
    ),
    edges=(
        DiagramEdge("triage", "flights", "handoff_to_flights"),
        DiagramEdge("triage", "hotels", "handoff_to_hotels"),
        DiagramEdge("triage", "billing", "handoff_to_billing"),
        DiagramEdge("triage", "risk", "handoff_to_risk"),
        DiagramEdge("risk", "triage", "return", "dashed"),
        DiagramEdge("billing", "triage", "return", "dashed"),
    ),
    prompt_examples=(
        PromptExample(
            ending="risk-specialist",
            prompt="Incident on BTA-26104. Route it to whoever owns it and resolve it.",
            why=(
                "A cancelled activity and a refund request - but the card was reported stolen, and a failed screening "
                "outranks the incident kind."
            ),
        ),
        PromptExample(
            ending="billing-specialist",
            prompt="Incident on BTA-26107. Route it to whoever owns it and resolve it.",
            why="Also a cancelled activity, on a payment that clears screening. Refunds are billing's.",
        ),
        PromptExample(
            ending="flights-specialist",
            prompt="Incident on BTA-26105. Route it to whoever owns it and resolve it.",
            why="A flight that landed four hours late. Flights owns disruption and the airline's EU261 claim.",
        ),
        PromptExample(
            ending="hotels-specialist",
            prompt="Incident on BTA-26106. Route it to whoever owns it and resolve it.",
            why="A family walked by an oversold hotel. Hotels owns overbookings and downgrades.",
        ),
    ),
    devui_name="Handoff",
    build=build,
)
