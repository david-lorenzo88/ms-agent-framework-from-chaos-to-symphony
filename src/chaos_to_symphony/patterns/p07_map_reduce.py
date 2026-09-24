"""Pattern 7 - Fan-out / fan-in (map-reduce).

New material this year. Sixteen supplier invoices, three checks, one dispute
list. The map stage is three plain Python executors and the reduce stage is a
fourth; only the final memo is an agent.

The point on stage: in an agent workflow, most nodes should not be agents. A
deterministic executor is faster, free, and testable - spend model calls on
judgement, not on arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_framework import (
    Agent,
    AgentExecutor,
    AgentExecutorRequest,
    AgentExecutorResponse,
    Executor,
    Message,
    WorkflowBuilder,
    WorkflowContext,
    executor,
    handler,
)
from typing_extensions import Never

from ..base import CaseBrief, CaseFact, DiagramEdge, DiagramNode, PatternSpec
from ..clients import chat_client
from ..memory import STORE

TABLE_KEY = "dispute_table"


@dataclass
class Batch:
    """The work handed to every mapper."""

    invoice_ids: list[str]


@dataclass
class Findings:
    """One checker's contribution: EUR over-billed per invoice, plus the check's name."""

    check: str
    by_invoice: dict[str, int] = field(default_factory=dict)


@executor(id="dispatch")
async def dispatch(prompt: str, ctx: WorkflowContext[Batch]) -> None:
    """Load this month's supplier invoices and hand the same batch to all three checkers."""
    ids = sorted(STORE.invoices)
    STORE.record("map-reduce:dispatch", "fan-out", f"{len(ids)} invoices", pattern="map-reduce")
    await ctx.send_message(Batch(ids))


class RateChecker(Executor):
    """Map stage 1: billed above the contracted rate, on what was actually delivered."""

    @handler
    async def check(self, batch: Batch, ctx: WorkflowContext[Findings]) -> None:
        out = Findings("rate")
        for iid in batch.invoice_ids:
            inv = STORE.invoices[iid]
            over = max(0, inv.rate_invoiced_eur - inv.rate_contracted_eur) * inv.qty_delivered
            if over:
                out.by_invoice[iid] = over
        await ctx.send_message(out)


class QuantityChecker(Executor):
    """Map stage 2: billed for nights or places that were never delivered."""

    @handler
    async def check(self, batch: Batch, ctx: WorkflowContext[Findings]) -> None:
        out = Findings("quantity")
        for iid in batch.invoice_ids:
            inv = STORE.invoices[iid]
            over = max(0, inv.qty_invoiced - inv.qty_delivered) * inv.rate_invoiced_eur
            if over:
                out.by_invoice[iid] = over
        await ctx.send_message(out)


class CommissionChecker(Executor):
    """Map stage 3: commission deducted at less than the agreed percentage."""

    @handler
    async def check(self, batch: Batch, ctx: WorkflowContext[Findings]) -> None:
        out = Findings("commission")
        for iid in batch.invoice_ids:
            inv = STORE.invoices[iid]
            gross = inv.qty_delivered * inv.rate_contracted_eur
            short = max(0, inv.commission_contracted_pct - inv.commission_invoiced_pct) * gross // 100
            if short:
                out.by_invoice[iid] = short
        await ctx.send_message(out)


class Reducer(Executor):
    """Reduce stage: sum the three checks per invoice and rank what to dispute."""

    @handler
    async def reduce(self, results: list[Findings], ctx: WorkflowContext[AgentExecutorRequest]) -> None:
        totals: dict[str, int] = {}
        detail: dict[str, list[str]] = {}
        for result in results:
            for iid, amount in result.by_invoice.items():
                totals[iid] = totals.get(iid, 0) + amount
                detail.setdefault(iid, []).append(f"{result.check} EUR {amount:,}")

        ranked = sorted(totals.items(), key=lambda kv: -kv[1])
        STORE.record("map-reduce:reduce", "fan-in", f"{len(results)} checks", pattern="map-reduce")

        lines = []
        for rank, (iid, amount) in enumerate(ranked, start=1):
            inv = STORE.invoices[iid]
            lines.append(
                f"{rank}. {iid} EUR {amount:,} - {inv.supplier}, {inv.booking_id}: {', '.join(detail[iid])}"
            )
        total = sum(totals.values())
        table = "\n".join(lines) + f"\nTOTAL EUR {total:,} across {len(ranked)} invoices."
        ctx.set_state(TABLE_KEY, table)
        await ctx.send_message(
            AgentExecutorRequest(
                messages=[
                    Message(
                        "user",
                        contents=[
                            "Here is this month's supplier invoice reconciliation, checked on rate, quantity and "
                            f"commission:\n{table}\n\nWrite the two-sentence memo to the finance lead."
                        ],
                    )
                ],
                should_respond=True,
            )
        )


@executor(id="publish")
async def publish(response: AgentExecutorResponse, ctx: WorkflowContext[Never, str]) -> None:
    """Terminal: emit the dispute list together with the memo."""
    table = ctx.get_state(TABLE_KEY) or ""
    await ctx.yield_output(f"DISPUTE LIST\n{table}\n\nMemo to finance: {response.agent_response.text}")


def build():
    """dispatch -> 3 checkers in parallel -> reducer -> memo."""
    rate = RateChecker(id="rate-checker")
    quantity = QuantityChecker(id="quantity-checker")
    commission = CommissionChecker(id="commission-checker")
    reducer = Reducer(id="reducer")
    narrator = AgentExecutor(
        Agent(
            client=chat_client("summariser-agent"),
            name="summariser-agent",
            description="Writes the dispute memo for the finance lead.",
            instructions=(
                "Write a two-sentence memo to the finance lead: how many invoices to dispute and for how much "
                "in total, then the largest one and why."
            ),
        ),
        id="summariser-agent",
    )

    return (
        WorkflowBuilder(start_executor=dispatch, name="MapReduce")
        .add_fan_out_edges(dispatch, [rate, quantity, commission])
        .add_fan_in_edges([rate, quantity, commission], reducer)
        .add_edge(reducer, narrator)
        .add_edge(narrator, publish)
        .build()
    )


SPEC = PatternSpec(
    slug="map-reduce",
    number=7,
    name="Fan-out / fan-in",
    tier="graph",
    tagline="Map across many items, reduce to one answer. Most nodes are not agents.",
    summary=(
        "Explicit graph parallelism: one source edge-group fans a message out to several executors, and a "
        "fan-in group joins their results into a single list delivered to one reducer. Unlike the Concurrent "
        "orchestration, which fans out agents over one input, this fans work over a *collection* - and the "
        "mappers here are ordinary Python, which is the point. Arithmetic does not need a language model."
    ),
    use_when=(
        "The same computation applies across many items and the results combine.",
        "Several independent dimensions must be scored and then merged.",
        "You want parallelism whose join point is explicit in the graph.",
    ),
    avoid_when=(
        "The mappers must see each other's output - that is a chain, not a map.",
        "There is one input rather than a collection - the Concurrent orchestration is simpler.",
        "The reduce step has no defined merge rule.",
    ),
    maf_api=(
        ".add_fan_out_edges(source, [a, b, c])",
        ".add_fan_in_edges([a, b, c], reducer)",
        "class Reducer(Executor) with @handler taking list[T]",
    ),
    failure_mode=(
        "The straggler and the half-join. Fan-in waits for every branch, so the slowest mapper sets the "
        "latency and a mapper that throws can strand the join. Give mappers timeouts and make the reducer "
        "explicit about a short result list rather than assuming all N arrived."
    ),
    scenario="Month-end: sixteen supplier invoices against what we actually booked. Which do we dispute?",
    case=CaseBrief(
        about=(
            "It is the last week of September, and sixteen supplier invoices are waiting to be paid - hotels in seven "
            "countries, and GetYourGuide for the activities. Most are right. Some are not: a rate above the contract, "
            "nights billed for a family the hotel walked to another hotel, a commission deducted at the wrong "
            "percentage. Finance needs the list of invoices to dispute, with the amount on each, before anything is "
            "paid."
        ),
        why=(
            "This is a collection, not a case, and that changes the shape of the graph: one source fans the invoices "
            "out to three checkers, and a fan-in joins their findings into a single list for one reducer. The thing to "
            "point at is the node count. Only one agent appears in this entire pattern, the one that writes the memo "
            "at the end. Every checker is ordinary Python - rate against the contract, nights and places against what "
            "was delivered, commission against the agreed percentage - because comparing two numbers is arithmetic, "
            "and arithmetic does not need a language model. Contrast it with Concurrent, five patterns back: that fans "
            "agents over one input, this fans work over a collection."
        ),
        facts=(
            CaseFact("Scope", "Every supplier invoice for September"),
            CaseFact("Invoices", "16, from hotels and GetYourGuide"),
            CaseFact("Checked on", "Rate, quantity, commission - three checkers, summed"),
            CaseFact("Output", "The invoices to dispute, largest first"),
            CaseFact("Agents involved", "One - the rest is Python"),
        ),
    ),
    default_prompt="Reconcile this month's supplier invoices and tell finance which ones to dispute.",
    nodes=(
        DiagramNode("disp", "dispatch", "executor"),
        DiagramNode("rate", "rate-checker", "executor"),
        DiagramNode("qty", "quantity-checker", "executor"),
        DiagramNode("comm", "commission-checker", "executor"),
        DiagramNode("red", "reducer", "executor"),
        DiagramNode("nar", "summariser-agent", "agent"),
    ),
    edges=(
        DiagramEdge("disp", "rate", "map"),
        DiagramEdge("disp", "qty", "map"),
        DiagramEdge("disp", "comm", "map"),
        DiagramEdge("rate", "red", "reduce"),
        DiagramEdge("qty", "red", "reduce"),
        DiagramEdge("comm", "red", "reduce"),
        DiagramEdge("red", "nar", "dispute list"),
    ),
    devui_name="MapReduce",
    build=build,
    new_this_year=True,
)
