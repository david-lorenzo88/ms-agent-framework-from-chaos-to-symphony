"""Pattern 7 - Fan-out / fan-in (map-reduce).

New material this year. Twenty shipments, three scoring dimensions, one ranked
worklist. The map stage is three plain Python executors and the reduce stage is
a fourth; only the final narration is an agent.

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
from ..memory import STORE, Severity

TABLE_KEY = "ranked_table"
_SEVERITY_WEIGHT = {Severity.CRITICAL: 40, Severity.HIGH: 25, Severity.MEDIUM: 12, Severity.LOW: 4}


@dataclass
class Batch:
    """The work handed to every mapper."""

    shipment_ids: list[str]


@dataclass
class Scores:
    """One mapper's contribution: a score per shipment, plus its dimension name."""

    dimension: str
    by_shipment: dict[str, int] = field(default_factory=dict)


@executor(id="dispatch")
async def dispatch(prompt: str, ctx: WorkflowContext[Batch]) -> None:
    """Load every open exception and hand the same batch to all three mappers."""
    ids = [s.id for s in STORE.open_exceptions()]
    STORE.record("map-reduce:dispatch", "fan-out", f"{len(ids)} shipments", pattern="map-reduce")
    await ctx.send_message(Batch(ids))


class SeverityScorer(Executor):
    """Map stage 1: score by how bad the exception is."""

    @handler
    async def score(self, batch: Batch, ctx: WorkflowContext[Scores]) -> None:
        out = Scores("severity")
        for sid in batch.shipment_ids:
            shipment = STORE.shipments[sid]
            out.by_shipment[sid] = _SEVERITY_WEIGHT[shipment.severity]
        await ctx.send_message(out)


class ValueScorer(Executor):
    """Map stage 2: score by money at risk."""

    @handler
    async def score(self, batch: Batch, ctx: WorkflowContext[Scores]) -> None:
        out = Scores("value")
        for sid in batch.shipment_ids:
            shipment = STORE.shipments[sid]
            out.by_shipment[sid] = min(40, shipment.declared_value_eur // 8_000)
        await ctx.send_message(out)


class RelationshipScorer(Executor):
    """Map stage 3: score by how much the customer is worth to us."""

    @handler
    async def score(self, batch: Batch, ctx: WorkflowContext[Scores]) -> None:
        tier_points = {"gold": 20, "silver": 10, "bronze": 3}
        out = Scores("relationship")
        for sid in batch.shipment_ids:
            shipment = STORE.shipments[sid]
            customer = STORE.customers.get(shipment.customer_id)
            out.by_shipment[sid] = tier_points.get(customer.tier, 0) if customer else 0
        await ctx.send_message(out)


class Reducer(Executor):
    """Reduce stage: sum the three dimensions and rank."""

    @handler
    async def reduce(self, results: list[Scores], ctx: WorkflowContext[AgentExecutorRequest]) -> None:
        totals: dict[str, int] = {}
        for result in results:
            for sid, points in result.by_shipment.items():
                totals[sid] = totals.get(sid, 0) + points

        ranked = sorted(totals.items(), key=lambda kv: -kv[1])[:5]
        STORE.record("map-reduce:reduce", "fan-in", f"{len(results)} dimensions", pattern="map-reduce")

        lines = []
        for rank, (sid, score) in enumerate(ranked, start=1):
            shipment = STORE.shipments[sid]
            customer = STORE.customers.get(shipment.customer_id)
            lines.append(
                f"{rank}. {sid} score {score} - {shipment.goods} on {shipment.lane}, "
                f"{shipment.severity.value}, EUR {shipment.declared_value_eur:,}, "
                f"{customer.name if customer else '?'} ({customer.tier if customer else '?'})"
            )
        table = "\n".join(lines)
        ctx.set_state(TABLE_KEY, table)
        await ctx.send_message(
            AgentExecutorRequest(
                messages=[
                    Message(
                        "user",
                        contents=[
                            "Here is today's ranked exception worklist, scored on severity, value and "
                            f"relationship:\n{table}\n\nWrite the two-sentence stand-up summary for the desk."
                        ],
                    )
                ],
                should_respond=True,
            )
        )


@executor(id="publish")
async def publish(response: AgentExecutorResponse, ctx: WorkflowContext[Never, str]) -> None:
    """Terminal: emit the ranked table together with its narration."""
    table = ctx.get_state(TABLE_KEY) or ""
    await ctx.yield_output(f"TODAY'S WORKLIST\n{table}\n\nStand-up summary: {response.agent_response.text}")


def build():
    """dispatch -> 3 scorers in parallel -> reducer -> narrator."""
    severity = SeverityScorer(id="severity-scorer")
    value = ValueScorer(id="value-scorer")
    relationship = RelationshipScorer(id="relationship-scorer")
    reducer = Reducer(id="reducer")
    narrator = AgentExecutor(
        Agent(
            client=chat_client("summariser-agent"),
            name="summariser-agent",
            description="Narrates the ranked worklist for the morning stand-up.",
            instructions="Summarise the ranked worklist in two sentences. Lead with the single worst case.",
        ),
        id="summariser-agent",
    )

    return (
        WorkflowBuilder(start_executor=dispatch, name="MapReduce")
        .add_fan_out_edges(dispatch, [severity, value, relationship])
        .add_fan_in_edges([severity, value, relationship], reducer)
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
    scenario="Sixteen open exceptions this morning. Which five does the desk work first?",
    case=CaseBrief(
        about=(
            "It is Monday morning. Sixteen shipments on the book are carrying an unresolved exception and the desk "
            "cannot work sixteen. Somebody has to score every open case on the same criteria and hand the team a "
            "ranked worklist - the five that matter most, in order, with the reason each one is there."
        ),
        why=(
            "This is a collection, not a case, and that changes the shape of the graph: one source fans a message out "
            "over many scorers, and a fan-in joins their results into a single list for one reducer. The thing to "
            "point at is the node count. Only one agent appears in this entire pattern, the summariser at the end. "
            "Every mapper is ordinary Python, because value times severity weighting is arithmetic and arithmetic does "
            "not need a language model. Contrast it with Concurrent, two patterns back: that fans agents over one "
            "input, this fans work over a collection."
        ),
        facts=(
            CaseFact("Scope", "Every open exception on the book"),
            CaseFact("Open exceptions", "16 of 20 shipments"),
            CaseFact("Scored on", "Declared value, severity, customer tier, days overdue"),
            CaseFact("Output", "The ranked five the desk works first"),
            CaseFact("Agents involved", "One - the rest is Python"),
        ),
    ),
    default_prompt="Build today's ranked exception worklist for the Baltic desk.",
    nodes=(
        DiagramNode("disp", "dispatch", "executor"),
        DiagramNode("sev", "severity-scorer", "executor"),
        DiagramNode("val", "value-scorer", "executor"),
        DiagramNode("rel", "relationship-scorer", "executor"),
        DiagramNode("red", "reducer", "executor"),
        DiagramNode("nar", "summariser-agent", "agent"),
    ),
    edges=(
        DiagramEdge("disp", "sev", "map"),
        DiagramEdge("disp", "val", "map"),
        DiagramEdge("disp", "rel", "map"),
        DiagramEdge("sev", "red", "reduce"),
        DiagramEdge("val", "red", "reduce"),
        DiagramEdge("rel", "red", "reduce"),
        DiagramEdge("red", "nar", "ranked list"),
    ),
    devui_name="MapReduce",
    build=build,
    new_this_year=True,
)
