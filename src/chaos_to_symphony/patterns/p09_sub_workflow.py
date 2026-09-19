"""Pattern 9 - Sub-workflow composition.

New material this year. The compliance gate - sanctions screening plus tariff
classification - is built once as its own workflow and then *embedded* in the
claim-handling workflow through a ``WorkflowExecutor``.

The point on stage: workflows compose. The gate has its own tests, its own
owner and its own release cadence, and the parent treats it as one node. This
is how a multi-agent system stops being one enormous graph that nobody dares
change.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_framework import (
    Agent,
    AgentExecutor,
    AgentExecutorRequest,
    AgentExecutorResponse,
    Message,
    WorkflowBuilder,
    WorkflowContext,
    WorkflowExecutor,
    executor,
)
from typing_extensions import Never

from ..base import DiagramEdge, DiagramNode, PatternSpec
from ..clients import chat_client
from ..memory import STORE
from ..tools import CUSTOMS_TOOLS

SHIPMENT_KEY = "shipment_id"


@dataclass
class GateVerdict:
    """What the compliance gate returns to whoever embedded it."""

    shipment_id: str
    cleared: bool
    detail: str


# --------------------------------------------------------------------------
# The reusable sub-workflow
# --------------------------------------------------------------------------


@executor(id="gate_screen")
async def gate_screen(shipment_id: str, ctx: WorkflowContext[Never, GateVerdict]) -> None:
    """Screen the consignee and check the licence requirement. Pure Python, no model.

    A compliance gate is exactly the kind of decision that should never be a
    model call: the rule is written down, the answer must be reproducible, and
    a regulator may ask you to demonstrate it.
    """
    shipment = STORE.shipments.get(shipment_id.strip().upper())
    if shipment is None:
        await ctx.yield_output(GateVerdict(shipment_id, False, "Unknown shipment reference."))
        return

    customer = STORE.customers.get(shipment.customer_id)
    tariff = STORE.tariff_for(shipment.hs_code)
    problems: list[str] = []
    if customer and not customer.sanctions_cleared:
        problems.append(f"consignee {customer.name} fails sanctions screening")
    if tariff and tariff.requires_licence:
        problems.append(f"HS {shipment.hs_code} requires an import licence")

    cleared = not problems
    detail = "All compliance checks passed." if cleared else "Blocked: " + "; ".join(problems) + "."
    STORE.record("sub-workflow:gate", "screen", f"{shipment.id} cleared={cleared}", pattern="sub-workflow")
    await ctx.yield_output(GateVerdict(shipment.id, cleared, detail))


def compliance_gate():
    """The gate as a standalone workflow. Runnable, testable and shippable on its own."""
    return WorkflowBuilder(start_executor=gate_screen, name="ComplianceGate").build()


# --------------------------------------------------------------------------
# The parent workflow that embeds it
# --------------------------------------------------------------------------


@executor(id="extract_reference")
async def extract_reference(request: str, ctx: WorkflowContext[str]) -> None:
    """Find the shipment reference in the request and hand it to the gate."""
    reference = next((sid for sid in STORE.shipments if sid in request), "")
    ctx.set_state(SHIPMENT_KEY, reference)
    await ctx.send_message(reference or "UNKNOWN")


@executor(id="on_gate_verdict")
async def on_gate_verdict(verdict: GateVerdict, ctx: WorkflowContext[AgentExecutorRequest, str]) -> None:
    """Branch on the embedded gate's answer.

    A blocked consignment terminates here without ever reaching the claims
    agent. That ordering is the whole value of a gate: the expensive, chatty
    part of the system never sees a case it is not allowed to act on.
    """
    if not verdict.cleared:
        await ctx.yield_output(
            f"HELD BY COMPLIANCE GATE - {verdict.shipment_id}. {verdict.detail} "
            "No claim assessment performed; legal review required first."
        )
        return
    await ctx.send_message(
        AgentExecutorRequest(
            messages=[
                Message(
                    "user",
                    contents=[
                        f"Compliance cleared {verdict.shipment_id}. {verdict.detail} "
                        "Assess the claim and propose a settlement."
                    ],
                )
            ],
            should_respond=True,
        )
    )


@executor(id="finalise")
async def finalise(response: AgentExecutorResponse, ctx: WorkflowContext[Never, str]) -> None:
    """Terminal for the cleared path."""
    await ctx.yield_output(f"CLEARED AND ASSESSED\n{response.agent_response.text}")


def build():
    """extract -> [compliance gate sub-workflow] -> branch -> claims agent."""
    gate = WorkflowExecutor(compliance_gate(), id="compliance-gate")
    claims = AgentExecutor(
        Agent(
            client=chat_client("claims-specialist"),
            name="claims-specialist",
            description="Assesses a cleared claim and proposes a settlement.",
            instructions="Assess the claim and propose a settlement figure with a one-line justification.",
            tools=CUSTOMS_TOOLS,
        ),
        id="claims-specialist",
    )

    return (
        WorkflowBuilder(start_executor=extract_reference, name="SubWorkflow")
        .add_edge(extract_reference, gate)
        .add_edge(gate, on_gate_verdict)
        .add_edge(on_gate_verdict, claims)
        .add_edge(claims, finalise)
        .build()
    )


SPEC = PatternSpec(
    slug="sub-workflow",
    number=9,
    name="Sub-workflow composition",
    tier="graph",
    tagline="A workflow is a node. Build the gate once, embed it everywhere.",
    summary=(
        "``WorkflowExecutor`` wraps an entire workflow so a parent can use it as a single node. The child "
        "keeps its own graph, its own state and its own tests; the parent only sees a message going in and a "
        "result coming out. That boundary is what lets a shared capability - a compliance gate, a pricing "
        "engine, a redaction step - be owned by one team and reused by several without anyone editing a "
        "thousand-node graph."
    ),
    use_when=(
        "A capability is reused across several workflows and should have one owner.",
        "A sub-process deserves its own tests and its own release cadence.",
        "You want a hard boundary so the parent cannot reach into the child's internals.",
    ),
    avoid_when=(
        "The child is three nodes used once - inlining is clearer than indirection.",
        "Parent and child must share fine-grained state continuously.",
        "The nesting would get deep enough that nobody can trace a message end to end.",
    ),
    maf_api=(
        "WorkflowExecutor(child_workflow, id='...')",
        ".add_edge(parent_node, sub_workflow_executor)",
        "child yields via ctx.yield_output, parent receives it as a message",
    ),
    failure_mode=(
        "Error opacity. When the child fails, the parent sees a node fail - not the step inside it. Propagate "
        "a typed result (this demo returns GateVerdict, never an exception), give the child its own "
        "instrumentation, and never let a child's silent success mean 'nothing happened'."
    ),
    scenario="BFG-24086 again - but this time compliance blocks it before any claims work happens.",
    default_prompt="Assess the claim on shipment BFG-24086 and propose a settlement.",
    nodes=(
        DiagramNode("ext", "extract_reference", "executor"),
        DiagramNode("gate", "compliance-gate (sub-workflow)", "gate"),
        DiagramNode("screen", "gate_screen", "executor"),
        DiagramNode("branch", "on_gate_verdict", "executor"),
        DiagramNode("claims", "claims-specialist", "agent"),
        DiagramNode("out", "Held | Assessed", "store"),
    ),
    edges=(
        DiagramEdge("ext", "gate", "shipment id"),
        DiagramEdge("gate", "screen", "inner graph", "dashed"),
        DiagramEdge("screen", "branch", "GateVerdict"),
        DiagramEdge("branch", "out", "blocked", "dashed"),
        DiagramEdge("branch", "claims", "cleared"),
        DiagramEdge("claims", "out", "settlement"),
    ),
    build=build,
    new_this_year=True,
)
