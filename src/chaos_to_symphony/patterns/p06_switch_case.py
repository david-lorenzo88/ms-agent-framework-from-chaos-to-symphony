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
    incident_kind: Literal["flight_cancelled", "flight_delayed", "overbooked", "not_as_booked", "activity_cancelled"]
    reason: str


@dataclass
class Routed:
    """The typed payload the switch group evaluates."""

    severity: str
    incident_kind: str
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
    await ctx.send_message(Routed(parsed.severity, parsed.incident_kind, parsed.reason))


@executor(id="duty_desk")
async def duty_desk(routed: Routed, ctx: WorkflowContext[Never, str]) -> None:
    """Critical and high go to the duty desk, and page the duty manager."""
    STORE.record("switch:duty_desk", "route", routed.incident_kind, pattern="switch-case")
    await ctx.yield_output(
        f"DUTY DESK - severity {routed.severity}, kind {routed.incident_kind}. "
        f"Duty manager paged. Rationale: {routed.reason}"
    )


@executor(id="standard_queue")
async def standard_queue(routed: Routed, ctx: WorkflowContext[Never, str]) -> None:
    """Medium severity joins the standard resolution queue."""
    STORE.record("switch:standard_queue", "route", routed.incident_kind, pattern="switch-case")
    await ctx.yield_output(
        f"STANDARD QUEUE - severity {routed.severity}, kind {routed.incident_kind}. "
        f"Target resolution 48h. Rationale: {routed.reason}"
    )


@executor(id="watchlist")
async def watchlist(routed: Routed, ctx: WorkflowContext[Never, str]) -> None:
    """The Default branch. Anything unmatched lands here rather than vanishing."""
    STORE.record("switch:watchlist", "route", routed.incident_kind, pattern="switch-case")
    await ctx.yield_output(
        f"WATCHLIST (default branch) - severity {routed.severity}, kind {routed.incident_kind}. "
        f"No action, monitored daily. Rationale: {routed.reason}"
    )


def build():
    """intake -> classifier -> switch(critical|high / medium / default)."""
    classifier = AgentExecutor(
        Agent(
            client=chat_client("triage-classifier"),
            name="triage-classifier",
            description="Grades a travel incident into a severity and a kind.",
            instructions=(
                "Grade the travel incident. If the request names a booking reference, look it up first: "
                "the record carries the incident kind and the severity on file, and a grade invented "
                "without them is a guess. Then return JSON with 'severity' (critical, high, medium or "
                "low), 'incident_kind' and a one-sentence 'reason'. Return nothing but JSON."
            ),
            # Read-only access to the case file. Without it a real model asked to
            # "triage incident BTA-26109" knows nothing except that the string
            # looks like a reference, and grades every case the same safe middle
            # way - which lands every run on the medium branch and makes the
            # routing look broken. The offline client never showed this: it reads
            # the booking out of the in-memory store itself, so it has facts the
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
                Case(condition=severity_is("critical", "high"), target=duty_desk),
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
        "The silent default. Add a sixth incident kind upstream and it quietly falls through to Default "
        "forever - no error, no alert, just a branch nobody reads. Make the default branch noisy: log it, "
        "count it, and alert when its share moves."
    ),
    scenario="An incident arrives with no triage grade. Route it to the right desk in one hop.",
    case=CaseBrief(
        about=(
            "Return flight BT716 from Tenerife has been cancelled for a technical fault, and the next seats are two "
            "days away. A family of four is at the airport with nowhere to sleep tonight. The incident has been "
            "logged, but nobody has graded it yet - and until it is graded it sits in no queue at all. It has to land "
            "in exactly one of three places - the duty desk, the standard queue, or the watchlist - in one hop, now."
        ),
        why=(
            "This is the division of labour that makes graph routing trustworthy: the model classifies, the graph "
            "decides. A single agent looks the booking up and produces a typed, validated grade - severity and kind, "
            "nothing more. Ordinary Python predicates then test that payload in order and deliver it to the first "
            "match, or to Default: critical and high to the duty desk, medium to the standard queue, anything else to "
            "the watchlist. The routing decision is unit-testable and cannot drift, because no model is anywhere near "
            "the control flow. Try all three example prompts: each carries a different severity and each lands "
            "somewhere different."
        ),
        facts=(
            CaseFact("Booking", "BTA-26110"),
            CaseFact("Flight", "airBaltic BT716 Tenerife → Riga, cancelled - a technical fault"),
            CaseFact("Travellers", "Liepiņš family, 4, silver tier"),
            CaseFact("Next seats", "25 Sep - two nights stranded"),
            CaseFact("Arrives as", "An incident with no triage grade"),
            CaseFact("Desks", "Duty desk, standard queue, watchlist (Default)"),
        ),
    ),
    default_prompt=(
        "Booking BTA-26110 - return flight from Tenerife cancelled, family of four with no hotel tonight. Grade and "
        "route this incident."
    ),
    nodes=(
        DiagramNode("intake", "intake", "executor"),
        DiagramNode("clf", "triage-classifier", "agent"),
        DiagramNode("routed", "to_routed", "executor"),
        DiagramNode("duty", "duty_desk", "gate"),
        DiagramNode("std", "standard_queue", "gate"),
        DiagramNode("watch", "watchlist (Default)", "gate"),
    ),
    edges=(
        DiagramEdge("intake", "clf", "case"),
        DiagramEdge("clf", "routed", "JSON"),
        DiagramEdge("routed", "duty", "critical | high"),
        DiagramEdge("routed", "std", "medium"),
        DiagramEdge("routed", "watch", "Default", "dashed"),
    ),
    prompt_examples=(
        PromptExample(
            ending="DUTY DESK",
            prompt="Triage incident BTA-26109 and route it to the right desk.",
            why=(
                "A school group of 36 with no flight to Rome: graded critical, so it matches the first Case and the "
                "duty manager is paged."
            ),
        ),
        PromptExample(
            ending="STANDARD QUEUE",
            prompt="Triage incident BTA-26107 and route it to the right desk.",
            why=(
                "A kayak trip cancelled for high water, refund already confirmed: graded medium - past the first Case, "
                "matched by the second."
            ),
        ),
        PromptExample(
            ending="WATCHLIST (default branch)",
            prompt="Triage incident BTA-26108 and route it to the right desk.",
            why=(
                "A 40-minute delay the traveller shrugged off: graded low, so no Case matches and Default catches it "
                "rather than it vanishing."
            ),
        ),
    ),
    devui_name="SwitchCase",
    build=build,
    new_this_year=True,
)
