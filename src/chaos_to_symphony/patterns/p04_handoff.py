"""Pattern 4 - Handoff.

Triage reads the case and transfers ownership to the right specialist. The
routing decision is made by the agent holding the case, not by a central
manager - that is the whole distinction from Group Chat.

The point on stage: handoff tools are ordinary tools. The model routes by
calling one, which means routing shows up in the trace like any other tool call.
"""

from __future__ import annotations

from agent_framework import Agent
from agent_framework.orchestrations import HandoffBuilder

from ..base import DiagramEdge, DiagramNode, PatternSpec
from ..clients import chat_client
from ..tools import CASE_TOOLS, CUSTOMS_TOOLS


def build():
    """Triage fans out to four specialists; specialists can bounce back to triage."""
    triage = Agent(
        client=chat_client("triage-agent"),
        name="triage-agent",
        description="First contact. Decides which specialist owns the case.",
        instructions=(
            "You triage freight exceptions. Read the case, then hand off to the specialist who should own it. "
            "Do not attempt to resolve it yourself."
        ),
        # Handoff short-circuits on the routing tool call, so each agent must keep
        # its own history in step with the service. The builder refuses without this.
        require_per_service_call_history_persistence=True,
        tools=CASE_TOOLS,
    )
    customs = Agent(
        client=chat_client("customs-specialist"),
        name="customs-specialist",
        description="Owns customs holds, tariff classification and import licences.",
        instructions="Resolve customs holds. Classify the code, identify the missing document, state the release path.",
        # Handoff short-circuits on the routing tool call, so each agent must keep
        # its own history in step with the service. The builder refuses without this.
        require_per_service_call_history_persistence=True,
        tools=CUSTOMS_TOOLS,
    )
    compliance = Agent(
        client=chat_client("compliance-specialist"),
        name="compliance-specialist",
        description="Owns sanctions screening failures and frozen consignments.",
        instructions=(
            "Handle sanctions and compliance failures. If the consignee fails screening, state plainly that "
            "the consignment stays frozen and legal must review. Never authorise release yourself."
        ),
        # Handoff short-circuits on the routing tool call, so each agent must keep
        # its own history in step with the service. The builder refuses without this.
        require_per_service_call_history_persistence=True,
        tools=CUSTOMS_TOOLS,
    )
    claims = Agent(
        client=chat_client("claims-specialist"),
        name="claims-specialist",
        description="Owns damage, loss and temperature-excursion claims.",
        instructions="Handle damage, loss and temperature claims. Quantify the loss and propose a settlement.",
        # Handoff short-circuits on the routing tool call, so each agent must keep
        # its own history in step with the service. The builder refuses without this.
        require_per_service_call_history_persistence=True,
        tools=CASE_TOOLS,
    )
    ops = Agent(
        client=chat_client("ops-specialist"),
        name="ops-specialist",
        description="Owns delays, re-routing and recovery.",
        instructions="Handle delays. Give a recovery plan with a revised delivery date.",
        # Handoff short-circuits on the routing tool call, so each agent must keep
        # its own history in step with the service. The builder refuses without this.
        require_per_service_call_history_persistence=True,
        tools=CASE_TOOLS,
    )

    builder = (
        HandoffBuilder(
            name="Handoff",
            participants=[triage, customs, compliance, claims, ops],
            description="Freight exception desk with specialist routing.",
        )
        .with_start_agent(triage)
        .add_handoff(triage, [customs, compliance, claims, ops])
    )
    # Specialists can return a case they do not own. Without this the desk is a
    # one-way street and a mis-triage is unrecoverable.
    for specialist in (customs, compliance, claims, ops):
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
    scenario="BFG-24086: a CNC machine from Kaliningrad whose consignee fails sanctions screening.",
    default_prompt="Shipment BFG-24086 is stuck. Route it to the right specialist and resolve it.",
    nodes=(
        DiagramNode("triage", "triage-agent", "orchestrator"),
        DiagramNode("customs", "customs-specialist", "agent"),
        DiagramNode("compliance", "compliance-specialist", "agent"),
        DiagramNode("claims", "claims-specialist", "agent"),
        DiagramNode("ops", "ops-specialist", "agent"),
    ),
    edges=(
        DiagramEdge("triage", "customs", "handoff_to_customs"),
        DiagramEdge("triage", "compliance", "handoff_to_compliance"),
        DiagramEdge("triage", "claims", "handoff_to_claims"),
        DiagramEdge("triage", "ops", "handoff_to_ops"),
        DiagramEdge("compliance", "triage", "return", "dashed"),
        DiagramEdge("customs", "triage", "return", "dashed"),
    ),
    devui_name="Handoff",
    build=build,
)
