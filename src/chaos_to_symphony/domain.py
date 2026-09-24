"""The business the demos are set in, in the audience's language.

Twelve orchestration patterns need one story, or the session becomes twelve
unrelated toys. This module is that story - and it is assembled from the live
store rather than written out by hand, so the counts, tiers and thresholds on
screen are the ones the agents actually work against. Change a seed row in
``memory.py`` and this page changes with it.

``scripts/smoke.py`` checks that the roles named here are agents that really
exist and that every shipment referenced is really in the store, because a
briefing that has drifted from the demo is worse on stage than no briefing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .memory import STORE, ExceptionKind
from .tools import ALL_TOOLS


@dataclass(frozen=True, slots=True)
class Term:
    """One line in a glossary block."""

    term: str
    meaning: str


def _exception_kinds() -> list[Term]:
    """The five ways a shipment goes wrong, with a count of each in the store."""
    plain = {
        ExceptionKind.DAMAGE: "Goods arrived broken. Someone pays for the write-off.",
        ExceptionKind.CUSTOMS_HOLD: "Stopped at a border. Paperwork, tariff or licence.",
        ExceptionKind.DELAY: "Late. The cost is the customer's missed window, not the freight.",
        ExceptionKind.LOST: "The consignment cannot be found. The worst case, and the most expensive.",
        ExceptionKind.TEMPERATURE: "A reefer failed. Food and pharma: the cargo may be a total loss.",
    }
    rows: list[Term] = []
    for kind, meaning in plain.items():
        count = sum(1 for s in STORE.shipments.values() if s.exception is kind)
        label = kind.value.replace("_", " ")
        rows.append(Term(f"{label} ({count})", meaning))
    return rows


def _tiers() -> list[Term]:
    """Customer tiers, and the two numbers each one sets.

    These are the numbers the patterns actually branch on - the approval
    threshold is what suspends the workflow in pattern 10 - so they are read
    from the SLA policies rather than repeated in prose.
    """
    rows: list[Term] = []
    for policy in sorted(STORE.sla.values(), key=lambda p: -p.max_goodwill_eur):
        names = sorted(c.name for c in STORE.customers.values() if c.tier == policy.tier)
        rows.append(
            Term(
                policy.tier,
                f"Goodwill ceiling EUR {policy.max_goodwill_eur:,} · a person must approve above "
                f"EUR {policy.approval_threshold_eur:,} · EUR {policy.delay_penalty_per_day_eur:,} "
                f"per day late. {', '.join(names)}.",
            )
        )
    return rows


#: The specialists the patterns staff their workflows with. Names match the
#: agent ids in ``patterns/``, which ``smoke.py`` verifies.
ROLES: tuple[Term, ...] = (
    Term("triage-agent", "First contact. Reads the case and decides who owns it."),
    Term("claims-specialist", "Damage, loss and temperature claims. Prices the write-off."),
    Term("customs-specialist", "Border holds: tariff classification, duty, import licences."),
    Term("compliance-specialist", "Sanctions screening. Can freeze a consignment outright."),
    Term("ops-specialist", "Delays, re-routing and recovery. Owns the promise to the customer."),
    Term("pricing-specialist", "What the settlement should cost, against the SLA band."),
    Term("legal-counsel", "Liability and wording. Stops the company admitting fault in writing."),
    Term("ops-account-lead", "The commercial relationship. Argues for keeping the customer."),
    Term("claims-manager", "Chairs the committee and signs the final figure."),
    Term("writer-agent", "Turns the decision into the letter the customer receives."),
)


#: The concrete facts an attendee needs to follow any of the twelve runs.
RULES: tuple[Term, ...] = (
    Term(
        "One customer is blocked",
        "Kaliningrad Machinery LLC fails sanctions screening. Two shipments are theirs, and any "
        "workflow that screens them gets 'freeze and escalate to legal' instead of a resolution. "
        "Patterns 4 and 9 both turn on this.",
    ),
    Term(
        "Compensation is capped, then gated",
        "An estimate is capped at the tier's goodwill ceiling, and anything over that tier's "
        "approval threshold sets needs_human_approval. Nobody set a flag to make the demo pause - "
        "the arithmetic does it.",
    ),
    Term(
        "One lane keeps failing",
        "Three separate exceptions touch the Klaipeda ferry route inside a fortnight. No single "
        "case shows it; you have to look across the book. That is pattern 5's whole question.",
    ),
    Term(
        "There is exactly one write path",
        "Six tools read. Only record_decision writes, and everything it writes shows up in the "
        "Audit trail tab. That is what makes the transcript defensible.",
    ),
)


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
    lanes = {s.lane for s in STORE.shipments.values()}
    return {
        "name": "Baltic Freight Group",
        "tagline": "A freight forwarder in Tallinn. Fictional, deliberately small, entirely in memory.",
        "story": [
            "Baltic Freight Group moves other companies' goods around the Baltic and into "
            "western Europe. Most consignments arrive on time and nobody thinks about them.",
            "This session is about the ones that do not. A shipment hits an exception - it is "
            "damaged, held at a border, late, lost, or a refrigeration unit failed - and a small "
            "team of specialists has to work out what happened, what it costs, who owns the "
            "decision, and what the customer gets told.",
            "That is the work the twelve patterns orchestrate. Every demo answers a question a "
            "real claims desk asks, against the same book of shipments. The pattern changes; the "
            "business does not.",
        ],
        "facts": [
            {"label": "Shipments on the book", "value": str(len(STORE.shipments))},
            {"label": "Carrying an open exception", "value": str(len(STORE.open_exceptions()))},
            {"label": "Customers", "value": str(len(STORE.customers))},
            {"label": "Lanes", "value": str(len(lanes))},
            {"label": "Tariff lines", "value": str(len(STORE.tariffs))},
            {"label": "Tools the agents can call", "value": str(len(ALL_TOOLS))},
        ],
        "groups": [
            {
                "title": "What can go wrong",
                "blurb": "Five exception kinds. Which one a case carries decides who should own it.",
                "terms": [{"term": t.term, "meaning": t.meaning} for t in _exception_kinds()],
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
                "title": "Four facts that decide every demo",
                "blurb": "If you follow nothing else, follow these.",
                "terms": [{"term": t.term, "meaning": t.meaning} for t in RULES],
            },
        ],
        "footnote": (
            "Nothing here touches a disk or a network. The store is seeded deterministically at "
            "import time, so the same question gives the same rows on any laptop, offline, every "
            "time. Reset data puts it back."
        ),
    }
