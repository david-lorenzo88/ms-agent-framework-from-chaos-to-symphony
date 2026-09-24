"""Pattern 3 - Group Chat.

A customer-care committee argues about what to pay a couple whose honeymoon
suite was sold twice, in one shared thread, with an orchestrator deciding who
speaks next and a hard round cap ending it.

The point on stage: this is the only core pattern where agents *react* to each
other, and that is exactly why it needs the tightest termination rules.
"""

from __future__ import annotations

import re

from agent_framework import Agent, Message
from agent_framework.orchestrations import GroupChatBuilder, GroupChatState

from ..base import CaseBrief, CaseFact, DiagramEdge, DiagramNode, PatternSpec
from ..clients import chat_client
from ..tools import CASE_TOOLS, estimate_compensation, get_customer, lookup_booking

MAX_ROUNDS = 6

#: The seat that is allowed to end the meeting.
CHAIR = "care-manager"


def committee_selector(state: GroupChatState) -> str:
    """Choose the next speaker.

    A plain Python function, not a model call: speaker selection is control
    flow, and control flow you can unit-test beats control flow you have to
    evaluate. Swapping in ``orchestrator_agent=`` makes the choice model-driven
    instead - one keyword, same builder.
    """
    names = list(state.participants.keys())
    chair, specialists = names[0], names[1:]
    if not specialists:
        return chair

    # The chair opens, every specialist speaks once, then the chair sums up.
    #
    # It hands back to the chair after the last specialist rather than at
    # MAX_ROUNDS - 1. Close on the final permitted round and you can no longer
    # tell which rule ended the meeting: the condition and the cap fire at the
    # same moment and look identical in the log. Closing early leaves a spare
    # round the run never needs, so a meeting that ends is a meeting the
    # termination condition ended - and a cap that fires is a real fault worth
    # seeing, not the normal path.
    if state.current_round == 0:
        return chair
    if state.current_round <= len(specialists):
        return specialists[state.current_round - 1]
    if state.current_round == len(names):
        return chair

    # And if that summing-up did not settle it, the floor goes back round the
    # specialists rather than to the chair a second time. Never the same
    # speaker twice running: the orchestrator broadcasts a turn to everyone
    # *except* the agent that produced it, and then asks the next speaker to
    # respond with an empty message list, trusting the broadcast to have
    # carried the conversation. Pick the same agent again and there was no
    # broadcast to it, so a real provider is asked to complete nothing and
    # answers "Messages are required for chat completions". Offline never sees
    # it, because the scripted chair always names a figure and the meeting
    # always ends at the summing-up.
    return specialists[(state.current_round - len(names) - 1) % len(specialists)]


#: A money figure: "EUR 8,000", "8000 EUR", "8.000 euros" or a bare symbol.
_AMOUNT = re.compile(r"(?:eur|euros?|\u20ac)\s*[\d][\d.,]*|[\d][\d.,]*\s*(?:eur|euros?|\u20ac)", re.IGNORECASE)


def settled(conversation: list[Message]) -> bool:
    """Stop once *the chair* has stated a settlement figure.

    Two halves, and the pattern breaks without either.

    It looks for a *number with a currency on it* rather than for the word
    "settle". The scripted client is predictable, but a real model will write
    "I propose a goodwill payment of EUR 8,000" or use a symbol, and a
    condition keyed on particular English words would never fire - leaving
    max_rounds as the only thing ending the conversation, which is the failure
    this pattern's own slide warns about.

    And it insists the figure came from the chair. Money is what this committee
    argues about, so the specialists quote it constantly: pricing states the
    price difference, legal the reduction the law makes us owe. A condition that accepts any
    figure from anyone ends the meeting on the first specialist to open their
    mouth, and a debate that terminates before anyone can disagree is not a
    debate - it is a one-turn pipeline wearing a group chat's clothes. Which
    speaker said it is part of the condition, not decoration.
    """
    if not conversation:
        return False
    last = conversation[-1]
    if (getattr(last, "author_name", "") or "") != CHAIR:
        return False

    # And nobody settles a debate that has not happened yet.
    #
    # The two checks above read the *text*, and a live model writes whatever it
    # likes into it. Asked to open the meeting, one wrote the whole committee
    # itself in a single turn - every specialist's position, figures and all -
    # and that opening carried a figure from the chair, so the meeting ended
    # at round 0 with three agents who never spoke. Whether anyone else has taken a turn is a fact about the
    # transcript rather than about the prose, and no amount of fluent writing
    # can fake it.
    others = {(getattr(m, "author_name", "") or "") for m in conversation} - {"", CHAIR}
    if not others:
        return False

    return bool(_AMOUNT.search(getattr(last, "text", "") or ""))


def build():
    """A four-seat customer-care committee under a chair, capped at six rounds."""
    chair = Agent(
        client=chat_client("care-manager"),
        name="care-manager",
        description="Chairs the committee and records the settlement.",
        instructions=(
            "You chair the customer-care committee. You speak twice and only twice, and the specialists are "
            "separate agents who each take their own turn.\n"
            "Your first turn: look the booking up, then state the decision the committee has to make, in one "
            "or two sentences. Name no figure - nobody has argued yet - and do not write the specialists' "
            "contributions for them.\n"
            "Your last turn: having read what they actually said, state a single settlement figure in "
            "EUR and the reason for it."
        ),
        tools=CASE_TOOLS,
    )
    pricing = Agent(
        client=chat_client("pricing-specialist"),
        name="pricing-specialist",
        description="Argues the financial position.",
        instructions=(
            "Argue the money. Start from what was paid for and not delivered, and push back on any figure "
            "above the goodwill ceiling for this tier."
        ),
        tools=[lookup_booking, estimate_compensation],
    )
    legal = Agent(
        client=chat_client("legal-counsel"),
        name="legal-counsel",
        description="Argues liability as package organiser.",
        instructions=(
            "Argue liability. Under the Package Travel Directive the organiser owes a price reduction for "
            "any service not delivered as booked; say what that floor is, and warn against admitting more "
            "in writing."
        ),
        tools=[lookup_booking],
    )
    account = Agent(
        client=chat_client("account-lead"),
        name="account-lead",
        description="Argues the customer relationship.",
        instructions="Argue the relationship. Weigh what the customer spends with us, and what they will tell people.",
        tools=[lookup_booking, get_customer],
    )

    return GroupChatBuilder(
        name="GroupChat",
        participants=[chair, pricing, legal, account],
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
    scenario="BTA-26103: a honeymoon suite in Dubrovnik, sold twice. Gold tier, EUR 2,500 goodwill ceiling.",
    case=CaseBrief(
        about=(
            "Elīna and Mārtiņš Ozols booked the Sea View Suite at Hotel Excelsior Dubrovnik for their honeymoon. The "
            "suite had been sold twice, and they spent five of their seven nights in a Superior Room before it came "
            "free. They are home now, and they have written. Four people have to agree what Baltic Travel Agency pays, "
            "and they want different things: pricing wants the figure low, legal knows the Package Travel Directive "
            "makes a price reduction owed, and the account lead knows a honeymoon story gets told for years."
        ),
        why=(
            "This is a decision that has to show its reasoning, which is exactly what a single accumulating thread "
            "gives you. Every participant reads every prior turn, so positions get challenged and revised in the open "
            "rather than averaged away in private. Watch the chair: it opens without a figure, lets each specialist "
            "argue, and only then puts a number on it. The run ends on a structural condition - the chair spoke last, "
            "at least one specialist spoke before it, and the closing text carries an actual currency amount - not on "
            "anybody sounding satisfied."
        ),
        facts=(
            CaseFact("Booking", "BTA-26103"),
            CaseFact("Couple", "Elīna and Mārtiņš Ozols, gold tier"),
            CaseFact("Booked", "Hotel Excelsior Dubrovnik, Sea View Suite, 7 nights"),
            CaseFact("What they got", "A Superior Room for 5 nights - EUR 360 a night against EUR 640"),
            CaseFact("Not delivered", "EUR 1,400"),
            CaseFact("The constraint", "Goodwill is capped at EUR 2,500"),
        ),
    ),
    default_prompt="Booking BTA-26103: the honeymoon suite was sold twice. Agree a settlement figure.",
    nodes=(
        DiagramNode("chair", "committee-chair", "orchestrator"),
        DiagramNode("mgr", "care-manager", "agent"),
        DiagramNode("fin", "pricing-specialist", "agent"),
        DiagramNode("legal", "legal-counsel", "agent"),
        DiagramNode("acct", "account-lead", "agent"),
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
