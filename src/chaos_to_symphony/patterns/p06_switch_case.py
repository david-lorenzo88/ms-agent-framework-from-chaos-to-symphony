"""Pattern 6 - Conditional routing (switch / case).

New material this year. Below the five named orchestrations sits the workflow
graph, and this is the primitive that answers the commonest question after the
pattern tour: *what if none of the five fit?*

One agent classifies; a switch-case edge group routes to exactly one of three
terminal branches. The routing decision is deterministic Python evaluated over
a typed payload - the model classifies, the graph decides.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from agent_framework import (
    Agent,
    AgentExecutor,
    AgentExecutorRequest,
    AgentExecutorResponse,
    Case,
    ChatOptions,
    Default,
    Message,
    WorkflowBuilder,
    WorkflowContext,
    executor,
)
from pydantic import BaseModel
from typing_extensions import Never

from ..base import CaseBrief, CaseFact, DiagramEdge, DiagramNode, PatternSpec, PromptExample, parse_structured
from ..clients import chat_client
from ..memory import STORE
from ..tools import TRIAGE_TOOLS

CASE_KEY = "case_text"


class Triage(BaseModel):
    """What the classifier must return. Structured output, not prose."""

    severity: Literal["critical", "high", "medium", "low"]
    exception_kind: Literal["damage", "customs_hold", "delay", "lost", "temperature_excursion"]
    reason: str


@dataclass
class Routed:
    """The typed payload the switch group evaluates."""

    severity: str
    exception_kind: str
    reason: str


def severity_is(*values: str):
    """Predicate factory: matches a Routed carrying one of these severities."""

    def condition(message: Any) -> bool:
        return isinstance(message, Routed) and message.severity in values

    return condition


@executor(id="intake")
async def intake(case_text: str, ctx: WorkflowContext[AgentExecutorRequest]) -> None:
    """Store the case once, then ask the classifier to grade it."""
    ctx.set_state(CASE_KEY, case_text)
    await ctx.send_message(AgentExecutorRequest(messages=[Message("user", contents=[case_text])], should_respond=True))


@executor(id="to_routed")
async def to_routed(response: AgentExecutorResponse, ctx: WorkflowContext[Routed]) -> None:
    """Validate the classifier's JSON, then emit the typed payload the switch reads."""
    parsed = parse_structured(Triage, response.agent_response.text)
    await ctx.send_message(Routed(parsed.severity, parsed.exception_kind, parsed.reason))


@executor(id="major_incident")
async def major_incident(routed: Routed, ctx: WorkflowContext[Never, str]) -> None:
    """Critical and high go to the major-incident desk, and page a duty manager."""
    STORE.record("switch:major_incident", "route", routed.exception_kind, pattern="switch-case")
    await ctx.yield_output(
        f"MAJOR INCIDENT DESK - severity {routed.severity}, kind {routed.exception_kind}. "
        f"Duty manager paged. Rationale: {routed.reason}"
    )


@executor(id="standard_queue")
async def standard_queue(routed: Routed, ctx: WorkflowContext[Never, str]) -> None:
    """Medium severity joins the standard resolution queue."""
    STORE.record("switch:standard_queue", "route", routed.exception_kind, pattern="switch-case")
    await ctx.yield_output(
        f"STANDARD QUEUE - severity {routed.severity}, kind {routed.exception_kind}. "
        f"Target resolution 48h. Rationale: {routed.reason}"
    )


@executor(id="watchlist")
async def watchlist(routed: Routed, ctx: WorkflowContext[Never, str]) -> None:
    """The Default branch. Anything unmatched lands here rather than vanishing."""
    STORE.record("switch:watchlist", "route", routed.exception_kind, pattern="switch-case")
    await ctx.yield_output(
        f"WATCHLIST (default branch) - severity {routed.severity}, kind {routed.exception_kind}. "
        f"No action, monitored daily. Rationale: {routed.reason}"
    )


def build():
    """intake -> classifier -> switch(critical|high / medium / default)."""
    classifier = AgentExecutor(
        Agent(
            client=chat_client("triage-classifier"),
            name="triage-classifier",
            description="Grades a freight exception into a severity and a kind.",
            instructions=(
                "Grade the freight exception. If the request names a shipment reference, look it up "
                "first: the record carries the exception kind and the severity on file, and a grade "
                "invented without them is a guess. Then return JSON with 'severity' (critical, high, "
                "medium or low), 'exception_kind' and a one-sentence 'reason'. Return nothing but JSON."
            ),
            # Read-only access to the case file. Without it a real model asked to
            # "triage exception BFG-24084" knows nothing except that the string
            # looks like a reference, and grades every case the same safe middle
            # way - which lands every run on the medium branch and makes the
            # routing look broken. The offline client never showed this: it reads
            # the shipment out of the in-memory store itself, so it has facts the
            # model it stands in for was never given.
            tools=TRIAGE_TOOLS,
            default_options=ChatOptions(response_format=Triage),
        ),
        id="triage-classifier",
    )

    return (
        WorkflowBuilder(start_executor=intake, name="SwitchCase")
        .add_edge(intake, classifier)
        .add_edge(classifier, to_routed)
        .add_switch_case_edge_group(
            to_routed,
            [
                Case(condition=severity_is("critical", "high"), target=major_incident),
                Case(condition=severity_is("medium"), target=standard_queue),
                Default(target=watchlist),
            ],
        )
        .build()
    )


SPEC = PatternSpec(
    slug="switch-case",
    number=6,
    name="Conditional routing",
    tier="graph",
    tagline="The model classifies. The graph decides. Exactly one branch runs.",
    summary=(
        "A switch-case edge group evaluates ordered predicates against a typed message and delivers it to the "
        "first matching target, or to Default. Because the predicates are plain Python over a validated "
        "payload, the routing decision is unit-testable and cannot drift - the language model contributes the "
        "classification, not the control flow. This is the primitive to reach for when a pattern needs to "
        "branch rather than chain."
    ),
    use_when=(
        "One of N mutually exclusive branches should run, chosen from data.",
        "You need routing you can test without invoking a model.",
        "An unmatched case must go somewhere explicit rather than stalling.",
    ),
    avoid_when=(
        "Several branches should run - use fan-out instead.",
        "The branch depends on work only an agent can do mid-flight; consider Handoff.",
        "There are only two outcomes and a simple edge condition would read better.",
    ),
    maf_api=(
        "WorkflowBuilder(start_executor=...)",
        ".add_switch_case_edge_group(source, [Case(condition=..., target=...), Default(target=...)])",
        "@executor(id=...) / WorkflowContext[T]",
    ),
    failure_mode=(
        "The silent default. Add a sixth exception kind upstream and it quietly falls through to Default "
        "forever - no error, no alert, just a branch nobody reads. Make the default branch noisy: log it, "
        "count it, and alert when its share moves."
    ),
    scenario="A new exception arrives with no triage grade. Route it to the right desk in one hop.",
    case=CaseBrief(
        about=(
            "A forklift went through three of twelve pallets of rack PDUs at the Klaipeda hub. The exception has been "
            "logged but nobody has graded it yet, and until it is graded it sits in no queue at all. Baltic Freight "
            "has four desks - major incident, claims, customs, standard - and this needs to reach exactly one of them, "
            "in one hop, now."
        ),
        why=(
            "This is the division of labour that makes graph routing trustworthy: the model classifies, the graph "
            "decides. A single agent looks the shipment up and produces a typed, validated grade - severity and kind, "
            "nothing more. Ordinary Python predicates then test that payload in order and deliver it to the first "
            "match, or to Default. The routing decision is unit-testable and cannot drift, because no model is "
            "anywhere near the control flow. Try all three example prompts: each one carries a different fact and each "
            "lands on a different desk."
        ),
        facts=(
            CaseFact("Shipment", "BFG-24099"),
            CaseFact("Lane", "Vilnius to Hamburg (LT-DE)"),
            CaseFact("What happened", "Forklift strike; 3 of 12 pallets compromised"),
            CaseFact("Declared value", "EUR 119,000"),
            CaseFact("Customer", "Vilnius Electronics UAB, gold tier"),
            CaseFact("Arrives as", "An exception with no triage grade"),
        ),
    ),
    default_prompt=(
        "Shipment BFG-24099 - forklift strike at the Klaipeda hub, 3 of 12 pallets of rack PDUs compromised. "
        "Grade and route this exception."
    ),
    nodes=(
        DiagramNode("intake", "intake", "executor"),
        DiagramNode("clf", "triage-classifier", "agent"),
        DiagramNode("routed", "to_routed", "executor"),
        DiagramNode("major", "major_incident", "gate"),
        DiagramNode("std", "standard_queue", "gate"),
        DiagramNode("watch", "watchlist (Default)", "gate"),
    ),
    edges=(
        DiagramEdge("intake", "clf", "case"),
        DiagramEdge("clf", "routed", "JSON"),
        DiagramEdge("routed", "major", "critical | high"),
        DiagramEdge("routed", "std", "medium"),
        DiagramEdge("routed", "watch", "Default", "dashed"),
    ),
    prompt_examples=(
        PromptExample(
            ending="MAJOR INCIDENT DESK",
            prompt="Triage exception BFG-24084 and route it to the right desk.",
            why="Graded critical, so it matches the first Case and a duty manager is paged.",
        ),
        PromptExample(
            ending="STANDARD QUEUE",
            prompt="Triage exception BFG-24085 and route it to the right desk.",
            why="Graded medium: past the first Case, matched by the second.",
        ),
        PromptExample(
            ending="WATCHLIST (default branch)",
            prompt="Triage exception BFG-24094 and route it to the right desk.",
            why="Graded low, so no Case matches and Default catches it rather than it vanishing.",
        ),
    ),
    devui_name="SwitchCase",
    build=build,
    new_this_year=True,
)
