"""The business the demos are set in, in the audience's language.

Twelve orchestration patterns need one story, or the session becomes twelve
unrelated toys. This module is that story - and it is assembled from the live
store rather than written out by hand, so the counts, tiers and thresholds on
screen are the ones the agents actually work against. Change a seed row in
``memory.py`` and this page changes with it.

``scripts/smoke.py`` checks that the roles named here are agents that really
exist and that every booking and invoice referenced is really in the store,
because a briefing that has drifted from the demo is worse on stage than no
briefing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .memory import STORE, IncidentKind
from .tools import ALL_TOOLS, window_bookings


@dataclass(frozen=True, slots=True)
class Term:
    """One line in a glossary block."""

    term: str
    meaning: str


def _incident_kinds() -> list[Term]:
    """The five ways a trip goes wrong, with a count of each in the store."""
    plain = {
        IncidentKind.FLIGHT_CANCELLED: "The airline cancelled. Everything booked around the flight has to move.",
        IncidentKind.FLIGHT_DELAYED: "Late, not gone. Three hours at arrival is where EU261 starts to pay.",
        IncidentKind.OVERBOOKED: "The hotel sold the room twice and walked our guest to another hotel.",
        IncidentKind.NOT_AS_BOOKED: "A room or a service that was not the one sold - a suite that became a double.",
        IncidentKind.ACTIVITY_CANCELLED: "An excursion called off - weather, an operator, too few people.",
    }
    rows: list[Term] = []
    for kind, meaning in plain.items():
        count = sum(1 for b in STORE.bookings.values() if b.incident is kind)
        rows.append(Term(f"{kind.value.replace('_', ' ')} ({count})", meaning))
    return rows


def _tiers() -> list[Term]:
    """Customer tiers, and the three numbers each one sets.

    These are the numbers the patterns actually branch on - the approval
    threshold is what suspends the workflow in pattern 10 - so they are read
    from the tier policies rather than repeated in prose.
    """
    rows: list[Term] = []
    for policy in sorted(STORE.tiers.values(), key=lambda p: -p.max_goodwill_eur):
        names = sorted(c.name for c in STORE.customers.values() if c.tier == policy.tier)
        rows.append(
            Term(
                policy.tier,
                f"Goodwill ceiling EUR {policy.max_goodwill_eur:,} · a person must approve above "
                f"EUR {policy.approval_threshold_eur:,} · EUR {policy.allowance_per_day_eur:,} allowance per "
                f"day disrupted. {', '.join(names)}.",
            )
        )
    return rows


#: The specialists the patterns staff their workflows with. Names match the
#: agent ids in ``patterns/``, which ``smoke.py`` verifies.
ROLES: tuple[Term, ...] = (
    Term("triage-agent", "First contact. Reads the booking, screens the payment, decides who owns it."),
    Term("flights-specialist", "Cancellations and delays. Knows what EU261 makes the airline pay."),
    Term("hotels-specialist", "Overbookings and downgrades. Finds beds when a hotel cannot house our guests."),
    Term("billing-specialist", "Refunds and invoices. Never refunds a payment that has not cleared screening."),
    Term("risk-specialist", "Stolen cards and open chargebacks. Can freeze a booking outright."),
    Term("trip-planner", "Rebuilds an itinerary around a changed flight: hotel nights, tours, transfers."),
    Term("pricing-specialist", "What a settlement should cost, against the customer's tier."),
    Term("legal-counsel", "Liability as package organiser. Stops the company admitting fault in writing."),
    Term("account-lead", "The relationship. Argues for keeping the customer."),
    Term("care-manager", "Chairs the customer-care committee and signs the final figure."),
    Term("writer-agent", "Turns the decision into the letter the traveller receives."),
)


def _rules() -> list[Term]:
    """The concrete facts an attendee needs to follow any of the twelve runs."""
    window = window_bookings()
    return [
        Term(
            "One card is stolen",
            "BTA-26104 was paid with a card its bank has since reported stolen, and the customer wants the refund "
            "on a different card. Any workflow that screens it gets 'freeze and escalate to risk'. Patterns 4 and "
            "9 turn on this - and BTA-26112, with a chargeback already open, is pattern 9's other way to be held.",
        ),
        Term(
            "Compensation is capped, then gated",
            "An estimate is services not delivered plus the tier's daily allowance, capped at the tier's goodwill "
            "ceiling. Anything over the approval threshold sets needs_human_approval. Nobody set a flag to make "
            "pattern 10 pause - the arithmetic does it.",
        ),
        Term(
            "One bad night on the channel manager",
            f"In the early hours of 15 September our own SiteMinder endpoint rejected every availability push for "
            f"six hours. {len(window)} hotel rooms sold in that window had already gone elsewhere. No single "
            "complaint shows it; patterns 2 and 3 show two of the symptoms, and pattern 5 has to find the cause.",
        ),
        Term(
            "EU261 is the airline's bill, not ours",
            "Cancelled or three hours late: EUR 250 up to 1,500 km, EUR 400 beyond - inside the EU the band stops "
            "at 400, so Tenerife is a EUR 400 flight. Nothing is owed for an extraordinary circumstance such as an "
            "air traffic control strike, but rerouting, refunds, meals and hotels still are. Patterns 1, 8 and 11.",
        ),
        Term(
            "There is exactly one write path",
            "Nine tools read. Only record_decision writes, and everything it writes shows up in the Audit trail "
            "tab. That is what makes the transcript defensible.",
        ),
    ]


def _tools() -> list[Term]:
    """The tools the agents can call, from the functions themselves."""
    writes = {"record_decision"}
    rows: list[Term] = []
    for fn in ALL_TOOLS:
        doc = (fn.__doc__ or "").strip().splitlines()[0]
        mark = "writes" if fn.__name__ in writes else "reads"
        rows.append(Term(f"{fn.__name__} · {mark}", doc))
    return rows


def brief() -> dict[str, Any]:
    """The whole briefing, JSON-ready, with the counts taken live from the store."""
    cities = {h.city for b in STORE.bookings.values() for h in b.hotels}
    return {
        "name": "Baltic Travel Agency",
        "tagline": "A travel agency in Riga. Real places and suppliers; invented travellers and incidents.",
        "story": [
            "Baltic Travel Agency sells trips: flights, hotels and excursions, as packages or on their own, to "
            "travellers leaving the Baltics and to visitors arriving. Most trips go as booked and nobody thinks "
            "about them.",
            "This session is about the ones that do not. An airline cancels, a hotel sells the same room twice, a "
            "storm closes the ferry - and a small team of specialists has to work out what happened, what it "
            "costs, who owns the decision, and what the traveller gets told.",
            "That is the work the twelve patterns orchestrate. Every demo answers a question a real travel desk "
            "asks, against the same book of trips. The pattern changes; the business does not.",
        ],
        "facts": [
            {"label": "Bookings on the book", "value": str(len(STORE.bookings))},
            {"label": "Carrying an open incident", "value": str(len(STORE.open_incidents()))},
            {"label": "Customers", "value": str(len(STORE.customers))},
            {"label": "Destinations", "value": str(len(cities))},
            {"label": "Supplier invoices this month", "value": str(len(STORE.invoices))},
            {"label": "Tools the agents can call", "value": str(len(ALL_TOOLS))},
        ],
        "groups": [
            {
                "title": "What can go wrong",
                "blurb": "Five incident kinds. Which one a booking carries decides who should own it - unless the "
                         "payment says otherwise.",
                "terms": [{"term": t.term, "meaning": t.meaning} for t in _incident_kinds()],
            },
            {
                "title": "Who the customer is decides what it costs",
                "blurb": "Tier is the single most important field in the store.",
                "terms": [{"term": t.term, "meaning": t.meaning} for t in _tiers()],
            },
            {
                "title": "The cast",
                "blurb": "Not all of them appear in every pattern. Open the Agents tab to see a "
                         "pattern's own line-up, with the system prompt each one was given.",
                "terms": [{"term": t.term, "meaning": t.meaning} for t in ROLES],
            },
            {
                "title": "What the agents can actually do",
                "blurb": "No agent has free rein. These functions are the whole surface, and they "
                         "reach nothing but the in-memory store - no disk, no network.",
                "terms": [{"term": t.term, "meaning": t.meaning} for t in _tools()],
            },
            {
                "title": "Five facts that decide every demo",
                "blurb": "If you follow nothing else, follow these.",
                "terms": [{"term": t.term, "meaning": t.meaning} for t in _rules()],
            },
        ],
        "footnote": (
            "Airports, hotels, airlines, SiteMinder and GetYourGuide are real names, so the demo sounds like the "
            "industry it is set in. The travellers, their bookings and every incident are invented - none of it "
            "describes something that actually happened. EU261 and the Package Travel Directive are simplified. "
            "Nothing here touches a disk or a network: the store is seeded at import time, so the same question "
            "gives the same rows on any laptop, offline, every time. Reset data puts it back."
        ),
    }
