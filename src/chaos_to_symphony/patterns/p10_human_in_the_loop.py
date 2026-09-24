"""Pattern 10 - Human in the loop (approval gate).

New material this year, and the one the abstract's governance promise rests on.
A settlement above the customer's approval threshold stops the workflow dead
and asks a named person. Nothing proceeds until an answer comes back.

The point on stage: the pause is a first-class workflow state, not a callback
you bolted on. The workflow emits a ``request_info`` event, stops, and is
resumed by handing responses back into ``run(responses=...)`` - which means the
pause can outlive the process.
"""

from __future__ import annotations

from agent_framework import Agent
from agent_framework.orchestrations import SequentialBuilder

from ..base import CaseBrief, CaseFact, DiagramEdge, DiagramNode, PatternSpec
from ..clients import chat_client
from ..memory import STORE
from ..tools import CASE_TOOLS, estimate_compensation, lookup_booking, record_decision

#: The agent whose output needs signing off. Everything before it runs freely.
APPROVAL_AGENT = "settlement-agent"


def build():
    """assessor -> settlement (gated) -> letter. The gate is one builder call."""
    assessor = Agent(
        client=chat_client("cost-assessor"),
        name="cost-assessor",
        description="Quantifies what the incident costs the agency.",
        instructions=(
            "Quantify what this booking's incident costs us - services not delivered plus the tier's "
            "allowance - and state the goodwill ceiling and approval threshold for the customer's tier."
        ),
        tools=CASE_TOOLS,
    )
    settlement = Agent(
        client=chat_client("settlement-agent"),
        name=APPROVAL_AGENT,
        description="Proposes the settlement figure that a human must approve.",
        instructions=(
            "Propose a settlement figure in EUR with a one-line justification. "
            "This proposal is subject to human approval before it is sent."
        ),
        tools=[lookup_booking, estimate_compensation, record_decision],
    )
    letter = Agent(
        client=chat_client("writer-agent"),
        name="writer-agent",
        description="Writes the letter once the figure is approved.",
        instructions=(
            "Write the customer letter conveying the approved settlement. "
            "Do not restate the internal reasoning."
        ),
    )

    return (
        SequentialBuilder(
            name="HumanInTheLoop",
            participants=[assessor, settlement, letter],
            output_from="all",
        )
        # One call turns a fully automatic pipeline into a governed one.
        .with_request_info(agents=[APPROVAL_AGENT])
        .build()
    )


def approval_context(prompt: str) -> dict[str, object]:
    """What the approver needs on screen to decide. Read from the in-memory store."""
    booking = STORE.booking_in(prompt)
    if booking is None:
        return {}
    customer = STORE.customers.get(booking.customer_id)
    policy = STORE.policy_for(booking.customer_id)
    return {
        "booking": booking.id,
        "customer": customer.name if customer else "unknown",
        "tier": customer.tier if customer else "unknown",
        "packagePriceEur": booking.package_price_eur,
        "approvalThresholdEur": policy.approval_threshold_eur if policy else None,
        "goodwillCeilingEur": policy.max_goodwill_eur if policy else None,
    }


SPEC = PatternSpec(
    slug="human-in-the-loop",
    number=10,
    name="Human in the loop",
    tier="production",
    tagline="The workflow stops and asks. Nothing moves until a person answers.",
    summary=(
        "``.with_request_info(agents=[...])`` turns a named participant into an approval gate. When it "
        "produces output the workflow emits a request_info event and suspends; it resumes only when "
        "responses are passed back into run(). Because the pause is workflow state rather than a held "
        "coroutine, an approval can take a coffee break or a weekend - and with checkpointing it can "
        "outlive the process that started it."
    ),
    use_when=(
        "A decision has financial, legal or reputational consequences.",
        "Regulation requires a named human in the chain of accountability.",
        "You are rolling out autonomy gradually and want a gate you can widen over time.",
    ),
    avoid_when=(
        "The volume would make a human the bottleneck for every single case.",
        "The decision is fully specifiable - encode the rule instead of asking a person to rubber-stamp it.",
        "There is nobody actually on the other end; an unanswered gate is an outage.",
    ),
    maf_api=(
        ".with_request_info(agents=['settlement-agent'])",
        "event.type == 'request_info' -> workflow suspends",
        "workflow.run(responses={request_id: AgentRequestInfoResponse.approve()}, stream=True)",
    ),
    failure_mode=(
        "Rubber-stamping, and the unanswered gate. A human asked to approve forty settlements an hour "
        "approves forty settlements an hour without reading them - so gate on a threshold, not on every "
        "case, and give the approver the figures, not the transcript. Then alarm the queue: a gate nobody "
        "answers is an outage that looks like a quiet afternoon."
    ),
    scenario=(
        "BTA-26103: EUR 2,000 for a honeymoon suite sold twice. Gold tier - anything over EUR 1,000 needs a name "
        "against it."
    ),
    case=CaseBrief(
        about=(
            "Back to the Dubrovnik honeymoon. The assessment is done and the settlement agent has a figure: EUR 1,400 "
            "for the five nights the couple did not get the suite they paid for, plus the gold tier's allowance of EUR "
            "120 a day - EUR 2,000. Elīna and Mārtiņš Ozols are gold-tier customers, and on the gold tier a person has "
            "to put their name against anything over EUR 1,000. So the workflow stops."
        ),
        why=(
            "Nobody set a flag to make this demo pause. The arithmetic does it: the tool adds the services not "
            "delivered to the tier's allowance, caps the total at the goodwill ceiling, and returns "
            "needs_human_approval because the figure crosses the threshold. What happens next is the pattern - the "
            "workflow emits a request_info event and suspends, and nothing moves until you approve, or send it back to "
            "be re-priced. The architectural point is that the pause is workflow state, not a held coroutine. This "
            "approval could take a weekend. Combined with the next pattern's checkpointing, it can outlive the process "
            "that started it."
        ),
        facts=(
            CaseFact("Booking", "BTA-26103"),
            CaseFact("Customer", "Elīna and Mārtiņš Ozols, gold tier"),
            CaseFact("Estimate", "EUR 2,000 - EUR 1,400 not delivered + EUR 600 allowance"),
            CaseFact("Goodwill ceiling", "EUR 2,500"),
            CaseFact("Approval threshold", "EUR 1,000"),
            CaseFact("Therefore", "needs_human_approval - the run suspends"),
        ),
    ),
    default_prompt="Assess booking BTA-26103 and propose a settlement for approval.",
    nodes=(
        DiagramNode("assess", "cost-assessor", "agent"),
        DiagramNode("settle", "settlement-agent", "agent"),
        DiagramNode("gate", "request_info", "gate"),
        DiagramNode("human", "Duty manager", "human"),
        DiagramNode("letter", "writer-agent", "agent"),
    ),
    edges=(
        DiagramEdge("assess", "settle", "loss figure"),
        DiagramEdge("settle", "gate", "proposal"),
        DiagramEdge("gate", "human", "suspend + ask", "dashed"),
        DiagramEdge("human", "gate", "approve / edit", "dashed"),
        DiagramEdge("gate", "letter", "resume"),
    ),
    devui_name="HumanInTheLoop",
    build=build,
    new_this_year=True,
)
