"""Pattern 3 - Group Chat.

A claims committee argues about a goodwill payment in one shared thread, with
an orchestrator deciding who speaks next and a hard round cap ending it.

The point on stage: this is the only core pattern where agents *react* to each
other, and that is exactly why it needs the tightest termination rules.
"""

from __future__ import annotations

import re

from agent_framework import Agent, Message
from agent_framework.orchestrations import GroupChatBuilder, GroupChatState

from ..base import DiagramEdge, DiagramNode, PatternSpec
from ..clients import chat_client
from ..tools import CASE_TOOLS

MAX_ROUNDS = 6


def committee_selector(state: GroupChatState) -> str:
    """Choose the next speaker.

    A plain Python function, not a model call: speaker selection is control
    flow, and control flow you can unit-test beats control flow you have to
    evaluate. Swapping in ``orchestrator_agent=`` makes the choice model-driven
    instead - one keyword, same builder.
    """
    names = list(state.participants.keys())
    # The chair opens and closes; the specialists take the middle rounds.
    if state.current_round == 0:
        return names[0]
    if state.current_round >= MAX_ROUNDS - 1:
        return names[0]
    return names[1 + ((state.current_round - 1) % (len(names) - 1))]


#: A money figure: "EUR 8,000", "8000 EUR", "8.000 euros" or a bare symbol.
_AMOUNT = re.compile(r"(?:eur|euros?|\u20ac)\s*[\d][\d.,]*|[\d][\d.,]*\s*(?:eur|euros?|\u20ac)", re.IGNORECASE)


def settled(conversation: list[Message]) -> bool:
    """Stop early once a settlement figure has actually been stated.

    Deliberately looks for a *number with a currency on it* rather than for the
    word "settle". The scripted client is predictable, but a real model will
    write "I propose a goodwill payment of EUR 8,000" or use a symbol, and a
    condition keyed on particular English words would simply never fire -
    leaving max_rounds as the only thing ending the conversation, which is the
    failure this pattern's own slide warns about.
    """
    if not conversation:
        return False
    text = getattr(conversation[-1], "text", "") or ""
    return bool(_AMOUNT.search(text))


def build():
    """A four-seat claims committee under a chair, capped at six rounds."""
    chair = Agent(
        client=chat_client("claims-manager"),
        name="claims-manager",
        description="Chairs the committee and records the settlement.",
        instructions=(
            "You chair the claims committee. Open by stating the decision to be made. When the specialists "
            "have spoken, state a single settlement figure in EUR and the reason for it."
        ),
        tools=CASE_TOOLS,
    )
    finance = Agent(
        client=chat_client("pricing-specialist"),
        name="pricing-specialist",
        description="Argues the financial position.",
        instructions="Argue the money. Push back on any figure above the goodwill ceiling for this tier.",
        tools=CASE_TOOLS,
    )
    legal = Agent(
        client=chat_client("legal-counsel"),
        name="legal-counsel",
        description="Argues liability and precedent.",
        instructions="Argue liability and precedent. Warn when a settlement would set an expensive precedent.",
        tools=CASE_TOOLS,
    )
    account = Agent(
        client=chat_client("ops-account-lead"),
        name="ops-account-lead",
        description="Argues the commercial relationship.",
        instructions="Argue the relationship. Weigh annual volume and churn risk against the settlement cost.",
        tools=CASE_TOOLS,
    )

    return GroupChatBuilder(
        name="GroupChat",
        participants=[chair, finance, legal, account],
        selection_func=committee_selector,
        orchestrator_name="committee-chair",
        termination_condition=settled,
        max_rounds=MAX_ROUNDS,
        output_from="all",
    ).build()


SPEC = PatternSpec(
    slug="group-chat",
    number=3,
    name="Group Chat",
    tier="core",
    tagline="One thread, several agents, an orchestrator holding the floor.",
    summary=(
        "Several agents collaborate in a single accumulating conversation while an orchestrator decides who "
        "speaks next. Because every participant sees every prior turn, positions can be challenged and "
        "revised - which makes it the right pattern for maker-checker loops, editorial review and any "
        "decision that has to show its reasoning. The accumulating thread is also, conveniently, the audit log."
    ),
    use_when=(
        "The decision genuinely benefits from debate and consensus-building.",
        "You need maker-checker or a structured quality gate with a visible rationale.",
        "You want a human able to read - or join - one coherent thread.",
    ),
    avoid_when=(
        "Simple delegation would do; the conversation overhead buys nothing.",
        "You are under a latency SLA that dialogue rounds would breach.",
        "Nothing can objectively judge 'done', so the chat cannot terminate on merit.",
    ),
    maf_api=(
        "GroupChatBuilder(participants=[...], selection_func=...)",
        ".with_max_rounds(n) / termination_condition=",
        "orchestrator_agent= for model-driven speaker selection",
    ),
    failure_mode=(
        "The infinite agreement loop: participants politely restate each other until the budget runs out. "
        "Two defences, and you want both - a hard max_rounds ceiling as the backstop, and a termination "
        "condition that tests for the artefact you actually wanted rather than for a sentiment."
    ),
    scenario="BFG-24082: a reefer failure on chilled salmon to Hamburg. Gold-tier customer, EUR 12,000 ceiling.",
    default_prompt="Shipment BFG-24082 suffered a temperature excursion. Agree a settlement figure.",
    nodes=(
        DiagramNode("chair", "committee-chair", "orchestrator"),
        DiagramNode("mgr", "claims-manager", "agent"),
        DiagramNode("fin", "pricing-specialist", "agent"),
        DiagramNode("legal", "legal-counsel", "agent"),
        DiagramNode("acct", "ops-account-lead", "agent"),
        DiagramNode("thread", "Shared thread", "store"),
    ),
    edges=(
        DiagramEdge("chair", "mgr", "round 0"),
        DiagramEdge("chair", "fin", "selects"),
        DiagramEdge("chair", "legal", "selects"),
        DiagramEdge("chair", "acct", "selects"),
        DiagramEdge("mgr", "thread", ""),
        DiagramEdge("fin", "thread", ""),
        DiagramEdge("legal", "thread", ""),
        DiagramEdge("acct", "thread", ""),
        DiagramEdge("thread", "chair", "next speaker?", "loop"),
    ),
    devui_name="GroupChat",
    build=build,
)
