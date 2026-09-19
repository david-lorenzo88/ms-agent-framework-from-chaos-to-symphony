"""Pattern 1 - Sequential.

A fixed pipeline: intake enriches the case, customs classifies it, the drafter
writes the customer reply. Each participant sees everything said before it.

The point on stage: this is the cheapest, most auditable pattern, and most
"multi-agent" problems are really this one. Reach for anything fancier only
when a linear chain genuinely cannot express the work.
"""

from __future__ import annotations

from agent_framework import Agent
from agent_framework.orchestrations import SequentialBuilder

from ..base import DiagramEdge, DiagramNode, PatternSpec
from ..clients import chat_client
from ..tools import CASE_TOOLS, CUSTOMS_TOOLS


def build():
    """Intake -> Customs -> Drafter, sharing one growing conversation."""
    intake = Agent(
        client=chat_client("intake-agent"),
        name="intake-agent",
        description="Pulls the consignment record and states the facts of the exception.",
        instructions=(
            "You are freight exception intake at Baltic Freight Group. Retrieve the shipment, "
            "state what went wrong in two sentences, and name the customer and their tier. "
            "Do not propose a remedy."
        ),
        tools=CASE_TOOLS,
    )
    customs = Agent(
        client=chat_client("customs-agent"),
        name="customs-agent",
        description="Classifies the goods and flags licence or duty problems.",
        instructions=(
            "You are a customs classification specialist. Using the facts above, classify the HS code, "
            "state the duty rate, and flag any licence requirement that could hold the consignment."
        ),
        tools=CUSTOMS_TOOLS,
    )
    drafter = Agent(
        client=chat_client("writer-agent"),
        name="writer-agent",
        description="Writes the customer-facing reply.",
        instructions=(
            "You write the customer reply. Acknowledge the problem, state the cause plainly, "
            "commit to a dated next step. Never admit legal liability."
        ),
    )
    return SequentialBuilder(
        name="Sequential",
        participants=[intake, customs, drafter],
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
    scenario="BFG-24084: vaccine cartons held at Vaalimaa customs for a missing import licence reference.",
    default_prompt="Shipment BFG-24084 is held at customs. Work the exception and draft the customer reply.",
    nodes=(
        DiagramNode("user", "Case", "store"),
        DiagramNode("intake", "intake-agent", "agent"),
        DiagramNode("customs", "customs-agent", "agent"),
        DiagramNode("writer", "writer-agent", "agent"),
        DiagramNode("out", "Customer reply", "store"),
    ),
    edges=(
        DiagramEdge("user", "intake", "exception"),
        DiagramEdge("intake", "customs", "facts"),
        DiagramEdge("customs", "writer", "+ classification"),
        DiagramEdge("writer", "out", "draft"),
    ),
    build=build,
)
