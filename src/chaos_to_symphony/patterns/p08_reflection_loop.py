"""Pattern 8 - Reflection loop.

New material this year. A writer drafts the customer letter, a reviewer judges
it against the letter policy, and a rejected draft goes back round with the
objection attached. The graph contains a genuine cycle.

The point on stage: a cycle is the first thing in this whole tour that can run
forever. The loop counter is not a nicety - it is the only reason this
terminates when the reviewer is never satisfied.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent_framework import (
    Agent,
    AgentExecutor,
    AgentExecutorRequest,
    AgentExecutorResponse,
    ChatOptions,
    Message,
    WorkflowBuilder,
    WorkflowContext,
    executor,
)
from pydantic import BaseModel

from ..base import CaseBrief, CaseFact, DiagramEdge, DiagramNode, PatternSpec, PromptExample, parse_structured
from ..clients import chat_client
from ..memory import STORE
from ..tools import LETTER_TOOLS

MAX_REVISIONS = 3
BRIEF_KEY = "brief"
ROUND_KEY = "revision"
DRAFT_KEY = "draft"


class Verdict(BaseModel):
    """The reviewer's structured judgement. Prose here would be unparseable."""

    decision: Literal["approve", "revise"]
    reason: str


@dataclass
class Draft:
    """A candidate letter on its way to review."""

    text: str
    revision: int


@executor(id="start")
async def start(brief: str, ctx: WorkflowContext[AgentExecutorRequest]) -> None:
    """Record the brief and ask for revision 1."""
    ctx.set_state(BRIEF_KEY, brief)
    ctx.set_state(ROUND_KEY, 1)
    await ctx.send_message(AgentExecutorRequest(messages=[Message("user", contents=[brief])], should_respond=True))


@executor(id="capture_draft")
async def capture_draft(response: AgentExecutorResponse, ctx: WorkflowContext[AgentExecutorRequest]) -> None:
    """Keep the draft, then put it in front of the reviewer."""
    text = response.agent_response.text
    revision: int = ctx.get_state(ROUND_KEY) or 1
    ctx.set_state(DRAFT_KEY, Draft(text, revision))
    STORE.record("reflection:drafter", "draft", f"revision {revision}", pattern="reflection-loop")
    await ctx.send_message(
        AgentExecutorRequest(
            messages=[
                Message(
                    "user",
                    contents=[
                        f"Review this customer letter against policy. Revision {revision} of "
                        f"{MAX_REVISIONS}.\n\n{text}"
                    ],
                )
            ],
            should_respond=True,
        )
    )


@executor(id="judge")
async def judge(response: AgentExecutorResponse, ctx: WorkflowContext[AgentExecutorRequest, str]) -> None:
    """Approve, or send it round again - but never more than MAX_REVISIONS times.

    The ceiling check comes *before* the decision is honoured. An approval loop
    whose exit depends only on the reviewer agreeing is an approval loop that
    can run until the budget is gone.
    """
    verdict = parse_structured(Verdict, response.agent_response.text)
    draft: Draft = ctx.get_state(DRAFT_KEY)
    revision: int = ctx.get_state(ROUND_KEY) or 1
    STORE.record("reflection:reviewer", verdict.decision, f"revision {revision}", pattern="reflection-loop")

    if verdict.decision == "approve":
        await ctx.yield_output(f"APPROVED at revision {revision}.\n\n{draft.text}\n\nReviewer: {verdict.reason}")
        return

    if revision >= MAX_REVISIONS:
        # The ceiling. Ship the best draft with the objection recorded rather
        # than looping; an unresolved disagreement is a fact, not a failure.
        await ctx.yield_output(
            f"ESCALATED after {revision} revisions - reviewer never approved.\n\n{draft.text}\n\n"
            f"Outstanding objection: {verdict.reason}\nRouted to a human editor."
        )
        return

    ctx.set_state(ROUND_KEY, revision + 1)
    brief: str = ctx.get_state(BRIEF_KEY) or ""
    await ctx.send_message(
        AgentExecutorRequest(
            messages=[
                Message(
                    "user",
                    contents=[
                        f"{brief}\n\nYour previous draft was rejected: {verdict.reason}\n"
                        f"Rewrite it addressing that objection. This is revision {revision + 1}."
                    ],
                )
            ],
            should_respond=True,
        )
    )


def build():
    """start -> drafter -> capture -> reviewer -> judge -> (loop back to drafter | out)."""
    drafter = AgentExecutor(
        Agent(
            client=chat_client("letter-writer"),
            name="letter-writer",
            description="Writes and rewrites the customer letter.",
            instructions=(
                "Write the customer letter. Look the booking up first, and check any disrupted flight against "
                "EU261. State what happened and what the traveller is owed, and by whom, with a date on every "
                "commitment. Never promise compensation the airline does not owe, and never admit liability. "
                "If given an objection, address it directly."
            ),
            # The writer speaks first: nobody before it has put the facts in
            # the conversation, so without its own lookup a live model drafts
            # a letter about a trip it has never seen. The offline client never
            # showed this - it reads the store itself.
            tools=LETTER_TOOLS,
        ),
        id="letter-writer",
    )
    reviewer = AgentExecutor(
        Agent(
            client=chat_client("review-agent"),
            name="review-agent",
            description="Judges a draft against the letter policy.",
            instructions=(
                "Judge the letter against the letter policy. Return JSON with 'decision' ('approve' or "
                "'revise') and 'reason'. Revise it if it: (1) promises EU261 compensation the airline does not "
                "owe - check the booking with check_eu261; (2) leaves out the traveller's rights on a cancelled "
                "flight - rerouting or a full refund, and meals while they wait where those are owed; (3) makes "
                "any promise without a date; or (4) admits liability."
            ),
            tools=LETTER_TOOLS,
            default_options=ChatOptions(response_format=Verdict),
        ),
        id="review-agent",
    )

    return (
        WorkflowBuilder(start_executor=start, name="ReflectionLoop")
        .add_edge(start, drafter)
        .add_edge(drafter, capture_draft)
        .add_edge(capture_draft, reviewer)
        .add_edge(reviewer, judge)
        .add_edge(judge, drafter)  # the cycle
        .build()
    )


SPEC = PatternSpec(
    slug="reflection-loop",
    number=8,
    name="Reflection loop",
    tier="graph",
    tagline="Draft, critique, rewrite - with a hard ceiling on the rewriting.",
    summary=(
        "A cyclic graph: a producer emits work, a critic judges it, and a rejection routes back to the "
        "producer with the objection attached. Quality climbs for two or three passes and then plateaus, "
        "which is why the interesting part of this pattern is not the loop but the exit - an iteration "
        "ceiling that escalates to a human instead of spending the budget proving the critic wrong."
    ),
    use_when=(
        "Output quality measurably improves under critique: copy, code, analysis.",
        "You can state the acceptance criteria well enough for a critic to apply them.",
        "A disagreement that will not resolve should reach a person, with context.",
    ),
    avoid_when=(
        "There is no objective acceptance test - the loop becomes taste, and taste does not converge.",
        "One pass is good enough; each extra round is another full model call.",
        "Latency is tight - worst case here is MAX_REVISIONS times the single-pass cost.",
    ),
    maf_api=(
        ".add_edge(judge, drafter) to close the cycle",
        "ctx.set_state / ctx.get_state to carry the iteration count",
        "ChatOptions(response_format=Verdict) for a parseable judgement",
    ),
    failure_mode=(
        "The loop that never exits, and its quieter cousin - the critic that approves everything to end the "
        "conversation. Cap the iterations, make the exit path escalate rather than silently accept, and log "
        "the approve/revise ratio: a critic approving 100% of first drafts has stopped reviewing."
    ),
    scenario=(
        "BTA-26111: a flight cancelled by an air traffic control strike. The letter must not promise money the airline "
        "does not owe."
    ),
    case=CaseBrief(
        about=(
            "Laura Vītola spent six hours at Riga airport before airBaltic cancelled her flight to Paris: French air "
            "traffic control was on strike. She is owed something, but not what most people think. An air traffic "
            "control strike is an extraordinary circumstance, so the EUR 400 of EU261 compensation she will have read "
            "about is not owed - by anyone. What she is owed is the choice of another flight or a refund, and meals "
            "for the hours she waited. The letter has to say exactly that."
        ),
        why=(
            "Quality here is testable, which is the precondition for the pattern working at all. The reviewer applies "
            "four rules: no promise of compensation that is not owed, the traveller's rights on a cancelled flight "
            "stated, a date on every commitment, and no admission of liability. So the graph has a cycle: the writer "
            "drafts, the reviewer judges, and a rejection goes straight back to the writer with the objection "
            "attached. On this case the first draft promises the EUR 400, the second takes it out and forgets her "
            "rights, and the third passes. Both agents look the booking up and check EU261 themselves - the writer "
            "speaks first, so nobody before it has put the facts in the conversation. The interesting part is not the "
            "loop but the exit: try the third example, whose refund waits on a hotel that has not answered, so no "
            "draft can ever date it."
        ),
        facts=(
            CaseFact("Booking", "BTA-26111"),
            CaseFact("Flight", "airBaltic BT691 Riga → Paris, cancelled on the day"),
            CaseFact("Cause", "French air traffic control strike - an extraordinary circumstance"),
            CaseFact("EU261 compensation", "None - the 1,672 km band is EUR 400, but not for a strike"),
            CaseFact("Still owed", "Rerouting or a refund, and meals for the wait"),
            CaseFact("Customer", "Laura Vītola, silver tier"),
        ),
    ),
    default_prompt=(
        "Draft the customer letter for BTA-26111, a flight to Paris cancelled by an air traffic control strike."
    ),
    nodes=(
        DiagramNode("start", "start", "executor"),
        DiagramNode("writer", "letter-writer", "agent"),
        DiagramNode("cap", "capture_draft", "executor"),
        DiagramNode("rev", "review-agent", "agent"),
        DiagramNode("judge", "judge", "gate"),
        DiagramNode("out", "Approved / escalated", "store"),
    ),
    edges=(
        DiagramEdge("start", "writer", "brief"),
        DiagramEdge("writer", "cap", "draft"),
        DiagramEdge("cap", "rev", "review?"),
        DiagramEdge("rev", "judge", "verdict"),
        DiagramEdge("judge", "writer", "revise (max 3)", "loop"),
        DiagramEdge("judge", "out", "approve | escalate"),
    ),
    prompt_examples=(
        PromptExample(
            ending="APPROVED at revision 3",
            prompt="Draft the customer letter for BTA-26111 and review it until it passes policy.",
            why=(
                "The first draft promises the EUR 400 the strike cancels; the second forgets her rights; the third "
                "passes."
            ),
        ),
        PromptExample(
            ending="APPROVED at revision 1",
            prompt="Draft the customer letter for BTA-26107 and review it until it passes policy.",
            why="A cancelled kayak trip with a refund date already confirmed. The loop is capable of not looping.",
        ),
        PromptExample(
            ending="ESCALATED after 3 revisions",
            prompt="Draft the customer letter for BTA-26106 and review it until it passes policy.",
            why=(
                "The refund waits on a hotel that has not answered, so no draft can put a date on it. MAX_REVISIONS "
                "ends it and a person takes over."
            ),
        ),
    ),
    devui_name="ReflectionLoop",
    build=build,
    new_this_year=True,
)
