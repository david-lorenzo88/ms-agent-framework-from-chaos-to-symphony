"""Pattern 9 - Sub-workflow composition.

New material this year. The payment-risk gate - a fraud flag and an open
chargeback, checked before any money moves - is built once as its own workflow
and then *embedded* in the refund workflow through a ``WorkflowExecutor``.

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

from ..base import CaseBrief, CaseFact, DiagramEdge, DiagramNode, PatternSpec, PromptExample
from ..clients import chat_client
from ..memory import BOOKING_REF, STORE
from ..tools import estimate_compensation, lookup_booking, screening

BOOKING_KEY = "booking_id"


@dataclass
class GateVerdict:
    """What the payment-risk gate returns to whoever embedded it."""

    booking_id: str
    cleared: bool
    detail: str


# --------------------------------------------------------------------------
# The reusable sub-workflow
# --------------------------------------------------------------------------


@executor(id="gate_screen")
async def gate_screen(booking_id: str, ctx: WorkflowContext[Never, GateVerdict]) -> None:
    """Screen the payment for a fraud flag and an open chargeback. Pure Python, no model.

    A payment gate is exactly the kind of decision that should never be a
    model call: the rule is written down, the answer must be reproducible, and
    a card scheme may ask you to demonstrate it.
    """
    booking = STORE.bookings.get(booking_id.strip().upper())
    if booking is None:
        await ctx.yield_output(GateVerdict(booking_id, False, "Unknown booking reference."))
        return

    verdict = screening(booking)
    cleared = verdict["cleared"]
    detail = "Payment cleared: no fraud flag, no open chargeback." if cleared else "Blocked: " + verdict["reason"]
    STORE.record("sub-workflow:gate", "screen", f"{booking.id} cleared={cleared}", pattern="sub-workflow")
    await ctx.yield_output(GateVerdict(booking.id, cleared, detail))


def payment_risk_gate():
    """The gate as a standalone workflow. Runnable, testable and shippable on its own."""
    return WorkflowBuilder(start_executor=gate_screen, name="PaymentRiskGate").build()


# --------------------------------------------------------------------------
# The parent workflow that embeds it
# --------------------------------------------------------------------------


@executor(id="extract_reference")
async def extract_reference(request: str, ctx: WorkflowContext[str]) -> None:
    """Find the booking reference in the request and hand it to the gate."""
    match = BOOKING_REF.search(request)
    reference = match.group(0) if match else ""
    ctx.set_state(BOOKING_KEY, reference)
    await ctx.send_message(reference or "UNKNOWN")


@executor(id="on_gate_verdict")
async def on_gate_verdict(verdict: GateVerdict, ctx: WorkflowContext[AgentExecutorRequest, str]) -> None:
    """Branch on the embedded gate's answer.

    A blocked payment terminates here without ever reaching the billing agent.
    That ordering is the whole value of a gate: the expensive, chatty part of
    the system never sees a case it is not allowed to act on.
    """
    if not verdict.cleared:
        await ctx.yield_output(
            f"HELD BY PAYMENT RISK GATE - {verdict.booking_id}. {verdict.detail} "
            "No refund assessment performed; risk review required first."
        )
        return
    await ctx.send_message(
        AgentExecutorRequest(
            messages=[
                Message(
                    "user",
                    contents=[
                        f"Payment cleared for {verdict.booking_id}. {verdict.detail} "
                        "Assess the refund and propose the amount."
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
    """extract -> [payment-risk gate sub-workflow] -> branch -> billing agent."""
    gate = WorkflowExecutor(payment_risk_gate(), id="payment-risk-gate")
    billing = AgentExecutor(
        Agent(
            client=chat_client("billing-specialist"),
            name="billing-specialist",
            description="Assesses a cleared refund and proposes the amount.",
            instructions=(
                "Assess the refund for this booking and propose the amount in EUR, with the date it reaches "
                "the card and a one-line justification."
            ),
            tools=[lookup_booking, estimate_compensation],
        ),
        id="billing-specialist",
    )

    return (
        WorkflowBuilder(start_executor=extract_reference, name="SubWorkflow")
        .add_edge(extract_reference, gate)
        .add_edge(gate, on_gate_verdict)
        .add_edge(on_gate_verdict, billing)
        .add_edge(billing, finalise)
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
    scenario="BTA-26104 again - but this time a payment-risk gate stops it before any refund work begins.",
    case=CaseBrief(
        about=(
            "The stolen-card refund from the handoff demo - but this time it never reaches a billing specialist at "
            "all. A payment-risk gate screens the booking first, finds the card reported stolen, and blocks the case "
            "before anybody starts working out a refund."
        ),
        why=(
            "Re-running a case you have already watched is the point. In the handoff pattern the audience saw the "
            "stolen card caught by a triage agent reading the case. Here the same fact stops it at the door, in a "
            "single node, before a single token is spent on the refund. That gate is a complete workflow of its own - "
            "own graph, own state, own tests - wrapped in a WorkflowExecutor so the parent sees nothing but a booking "
            "reference going in and a typed verdict coming out. It checks two things, a fraud flag and a chargeback "
            "already open, and the example prompts show both. Build it once, embed it in refunds, rebookings and new "
            "bookings, and let one team own it."
        ),
        facts=(
            CaseFact("Booking", "BTA-26104 - the handoff case, again"),
            CaseFact("Parent workflow", "Refund assessment"),
            CaseFact("Child workflow", "The payment-risk gate, embedded as one node"),
            CaseFact("Gate checks", "Fraud flag, open chargeback"),
            CaseFact("Gate result", "Blocked - card reported stolen"),
            CaseFact("What the parent sees", "A typed GateVerdict, never an exception"),
        ),
    ),
    default_prompt="Assess the refund on booking BTA-26104 and propose the amount.",
    nodes=(
        DiagramNode("ext", "extract_reference", "executor"),
        DiagramNode("gate", "payment-risk-gate (sub-workflow)", "gate"),
        DiagramNode("screen", "gate_screen", "executor"),
        DiagramNode("branch", "on_gate_verdict", "executor"),
        DiagramNode("billing", "billing-specialist", "agent"),
        DiagramNode("out", "Held | Assessed", "store"),
    ),
    edges=(
        DiagramEdge("ext", "gate", "booking id"),
        DiagramEdge("gate", "screen", "inner graph", "dashed"),
        DiagramEdge("screen", "branch", "GateVerdict"),
        DiagramEdge("branch", "out", "blocked", "dashed"),
        DiagramEdge("branch", "billing", "cleared"),
        DiagramEdge("billing", "out", "refund"),
    ),
    prompt_examples=(
        PromptExample(
            ending="HELD BY PAYMENT RISK GATE",
            prompt="Screen BTA-26104 through the payment-risk gate, then assess the refund.",
            why="The card was reported stolen. The billing agent is never invoked at all.",
        ),
        PromptExample(
            ending="HELD BY PAYMENT RISK GATE",
            prompt="Screen BTA-26112 through the payment-risk gate, then assess the refund.",
            why=(
                "A clean card - but the customer's bank already has a chargeback open, and refunding too would pay "
                "twice."
            ),
        ),
        PromptExample(
            ending="CLEARED AND ASSESSED",
            prompt="Screen BTA-26107 through the payment-risk gate, then assess the refund.",
            why="No fraud flag, no chargeback, so the gate passes it to the billing agent.",
        ),
    ),
    devui_name="SubWorkflow",
    build=build,
    new_this_year=True,
)
