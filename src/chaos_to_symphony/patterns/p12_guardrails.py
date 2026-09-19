"""Pattern 12 - Guardrails and graceful degradation.

New material this year, and the section the abstract promises but last year's
deck never delivered. Three production failure modes, reproduced on demand,
each with the mechanism that contains it:

1. **The runaway loop** - a committee that never agrees, stopped by a round cap.
2. **The dead specialist** - an agent whose provider is down, contained by a
   fallback path rather than a stack trace.
3. **The hung call** - a tool that never returns, contained by a timeout.

The point on stage: every one of these is invisible in a demo and inevitable in
production. None of the mitigations are exotic; all three are missing from most
multi-agent code.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from agent_framework import Agent, ChatResponse, ChatResponseUpdate, Message
from agent_framework.orchestrations import GroupChatBuilder, GroupChatState

from ..base import DiagramEdge, DiagramNode, PatternSpec
from ..clients import chat_client
from ..memory import STORE
from ..scripted import ScriptedChatClient
from ..tools import CASE_TOOLS

HARD_ROUND_CAP = 4
TOOL_TIMEOUT_SECONDS = 1.5


class OutageChatClient(ScriptedChatClient):
    """A client whose provider is down. Stands in for a real 503.

    Deliberately a *client* failure rather than a tool failure: a provider
    outage is the one thing no amount of prompt engineering survives, and it is
    the failure mode teams discover in production rather than in testing.
    """

    def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> Any:
        raise RuntimeError("Upstream model provider returned 503 Service Unavailable")


class SlowChatClient(ScriptedChatClient):
    """A client that answers correctly, eventually. Too late to be useful."""

    async def _slow(self, messages: Sequence[Message], options: Mapping[str, Any]) -> ChatResponse:
        await asyncio.sleep(30)
        return self._respond(messages, options)

    def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> Any:
        if stream:
            async def _gen():
                await asyncio.sleep(30)
                yield ChatResponseUpdate(role="assistant", contents=[])
            return self._build_response_stream(_gen())
        return self._slow(messages, options)


def never_agrees(state: GroupChatState) -> str:
    """A selection function with no exit condition - the runaway case, on purpose."""
    names = list(state.participants.keys())
    return names[state.current_round % len(names)]


def build():
    """A committee that cannot converge, held by a hard round cap.

    There is deliberately no termination_condition here. The cap is the only
    thing between this workflow and an unbounded bill - which is exactly the
    point being made.
    """
    a = Agent(
        client=chat_client("legal-counsel"),
        name="legal-counsel",
        description="Will never concede the point.",
        instructions="Argue that the settlement is too generous. Never concede.",
        tools=CASE_TOOLS,
    )
    b = Agent(
        client=chat_client("ops-account-lead"),
        name="ops-account-lead",
        description="Will also never concede.",
        instructions="Argue that the settlement is too mean. Never concede.",
        tools=CASE_TOOLS,
    )
    return GroupChatBuilder(
        name="Guardrails",
        participants=[a, b],
        selection_func=never_agrees,
        orchestrator_name="deadlock-chair",
        max_rounds=HARD_ROUND_CAP,  # the backstop
        output_from="all",
    ).build()


async def _assess_with_fallback(prompt: str) -> tuple[str, str]:
    """Try the specialist; fall back to the generalist when the provider is down.

    Returns (path_taken, answer). The caller always gets an answer - degraded,
    labelled, but an answer. A multi-agent system whose specialist outage takes
    the whole request down has traded reliability for cleverness.
    """
    specialist = Agent(
        client=OutageChatClient("pricing-specialist"),
        name="pricing-specialist",
        description="The preferred, more capable path.",
        instructions="Price the exposure precisely.",
    )
    try:
        response = await specialist.run(prompt)
        return "primary", response.text
    except Exception as exc:  # provider outage, not a bug in our graph
        STORE.record("guardrail:fallback", "degrade", str(exc)[:80], pattern="guardrails")
        generalist = Agent(
            client=chat_client("cost-assessor"),
            name="cost-assessor",
            description="The degraded path: less specialised, still useful.",
            instructions="Give a conservative cost band for this exception. Flag it as an estimate.",
            tools=CASE_TOOLS,
        )
        response = await generalist.run(prompt)
        return "fallback", response.text


async def _assess_with_timeout(prompt: str) -> tuple[str, str]:
    """Bound a call that may never return."""
    slow = Agent(
        client=SlowChatClient("risk-scorer"),
        name="risk-scorer",
        description="Correct, but far too slow.",
        instructions="Score the residual risk.",
    )
    try:
        response = await asyncio.wait_for(slow.run(prompt), timeout=TOOL_TIMEOUT_SECONDS)
        return "in-time", response.text
    except asyncio.TimeoutError:
        STORE.record("guardrail:timeout", "abandon", f">{TOOL_TIMEOUT_SECONDS}s", pattern="guardrails")
        return "timeout", (
            f"Risk scoring abandoned after {TOOL_TIMEOUT_SECONDS}s. Proceeding without a risk score and "
            "flagging the case for manual review."
        )


async def demo(prompt: str) -> list[str]:
    """Run all three failure modes and narrate what contained each one."""
    notes: list[str] = []

    # 1 - the runaway loop
    workflow = build()
    rounds = 0
    async for event in workflow.run(prompt, stream=True):
        if event.type == "executor_completed":
            rounds += 1
    notes.append(
        f"[1] Runaway loop: two agents instructed never to concede, with no termination condition. "
        f"Stopped by max_rounds={HARD_ROUND_CAP} after {rounds} executor completions, not by agreement."
    )

    # 2 - the dead specialist
    path, answer = await _assess_with_fallback(prompt)
    notes.append(
        f"[2] Provider outage: the specialist's client raised 503. Took the '{path}' path. "
        f"The caller still got an answer: {answer[:120]}"
    )

    # 3 - the hung call
    outcome, message = await _assess_with_timeout(prompt)
    notes.append(f"[3] Hung call: bounded at {TOOL_TIMEOUT_SECONDS}s -> '{outcome}'. {message[:120]}")

    notes.append(
        "All three failed. None of them took the request down - which is the only definition of "
        "production-ready that matters."
    )
    return notes


SPEC = PatternSpec(
    slug="guardrails",
    number=12,
    name="Guardrails & degradation",
    tier="production",
    tagline="Three ways multi-agent systems die, and the three lines that stop them.",
    summary=(
        "Not an orchestration pattern but the discipline that makes the other eleven survivable. This demo "
        "reproduces the three failures that only appear under real load - a loop with no exit, a model "
        "provider returning 503, and a call that never comes back - and shows each one contained: a hard "
        "round cap, a labelled fallback path, and a timeout. The system degrades in all three cases. It "
        "does not fall over in any of them."
    ),
    use_when=(
        "Always. Every pattern in this session needs all three of these.",
        "Especially where a model decides how many turns the work takes.",
        "Especially where one specialist is a single point of failure for the request.",
    ),
    avoid_when=(
        "Never - though tune the numbers per workflow rather than copying these.",
        "A cap set so low it truncates legitimate work is its own failure mode.",
        "A fallback silently as good as the primary means you did not need the primary.",
    ),
    maf_api=(
        "max_rounds= / max_round_count= / max_stall_count=",
        "try/except around agent.run() with a labelled fallback agent",
        "asyncio.wait_for(...) to bound a call that may never return",
    ),
    failure_mode=(
        "The guardrail nobody watches. A cap that fires constantly is a workflow that never completes, and a "
        "fallback that serves most traffic is an outage nobody has noticed. Emit a metric every time one of "
        "these fires, and alert on the *rate* - the mitigation working is not the same as the system being well."
    ),
    scenario="The same settlement question, run through a deadlocked committee, a dead provider and a hung call.",
    default_prompt="Agree the settlement for shipment BFG-24085, where two pallets of brake discs were crushed.",
    nodes=(
        DiagramNode("cap", "max_rounds cap", "gate"),
        DiagramNode("loop", "Deadlocked committee", "orchestrator"),
        DiagramNode("primary", "pricing-specialist (503)", "agent"),
        DiagramNode("fb", "cost-assessor (fallback)", "agent"),
        DiagramNode("slow", "risk-scorer (hangs)", "agent"),
        DiagramNode("to", "timeout 1.5s", "gate"),
        DiagramNode("out", "Degraded answer", "store"),
    ),
    edges=(
        DiagramEdge("loop", "cap", "round 5 blocked"),
        DiagramEdge("cap", "out", "truncated, logged"),
        DiagramEdge("primary", "fb", "on 503", "dashed"),
        DiagramEdge("fb", "out", "labelled estimate"),
        DiagramEdge("slow", "to", "no reply"),
        DiagramEdge("to", "out", "abandon + flag", "dashed"),
    ),
    devui_name="Guardrails",
    build=build,
    demo=demo,
    new_this_year=True,
)
