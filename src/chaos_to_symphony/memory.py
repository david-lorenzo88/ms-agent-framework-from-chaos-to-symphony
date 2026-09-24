"""The entire 'database' for every demo - held in process memory.

Nothing here touches a disk or a network. The store is a plain dataclass graph
guarded by a lock, seeded deterministically at import time and resettable
between runs. That keeps the conference demo reproducible: the same question
produces the same booking rows every single time, on any laptop, offline.

Domain: Baltic Travel Agency, a Riga travel agency. It sells flights, hotels
and activities, as packages and as single components. A trip hits an
*incident* - an airline cancels, a hotel oversells, an excursion is called off
- and a team of specialists has to work out what happened, what it costs, who
owns it, and what the traveller gets told. Small enough to read on a slide and
rich enough to exercise all twelve patterns.

Places, airports, airlines, hotels and platforms carry real names, so the demo
sounds like the industry it is set in. The travellers, the bookings and every
incident are invented: none of it describes something that actually happened.
"""

from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any

#: How a booking reference and a supplier invoice reference look. Everything
#: that finds a reference in free text - the offline client, the gate in
#: pattern 9, the smoke check on the case cards - matches against these, so a
#: renumbering happens in one place.
BOOKING_REF = re.compile(r"\bBTA-\d{5}\b")
INVOICE_REF = re.compile(r"\bSI-\d{4}\b")


class Severity(str, Enum):
    """How badly an incident hurts."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentKind(str, Enum):
    """What went wrong with the trip."""

    FLIGHT_CANCELLED = "flight_cancelled"
    FLIGHT_DELAYED = "flight_delayed"
    OVERBOOKED = "overbooked"
    NOT_AS_BOOKED = "not_as_booked"
    ACTIVITY_CANCELLED = "activity_cancelled"


@dataclass(frozen=True, slots=True)
class Customer:
    """A traveller or an account. ``tier`` drives the goodwill budget."""

    id: str
    name: str
    country: str
    tier: str  # bronze | silver | gold
    kind: str  # leisure | corporate | group
    annual_spend_eur: int


@dataclass(frozen=True, slots=True)
class Airport:
    """An airport, with the coordinates EU261 measures distance from."""

    code: str
    city: str
    lat: float
    lon: float
    in_eu: bool = True


@dataclass(frozen=True, slots=True)
class Flight:
    """One flight segment on a booking, and what happened to it."""

    carrier: str
    number: str
    origin: str
    destination: str
    departs: datetime
    arrives: datetime
    status: str = "on time"  # on time | cancelled | delayed
    delay_minutes: int = 0
    """Arrival delay. EU261 measures delay at the final destination."""
    cause: str = ""
    extraordinary: bool = False
    """Weather, air traffic control, security: the airline owes no compensation."""
    notice_days: int = 0
    """How far ahead the traveller was told of a cancellation."""
    rebooked_departs: datetime | None = None


@dataclass(frozen=True, slots=True)
class HotelStay:
    """A hotel component on a booking."""

    hotel: str
    city: str
    check_in: date
    nights: int
    rooms: int
    room_type: str
    rate_eur: int
    """Per room-night, as sold."""


@dataclass(frozen=True, slots=True)
class Activity:
    """An excursion, tour or ticket on a booking."""

    name: str
    city: str
    on: datetime
    pax: int
    price_eur: int
    """Per person."""
    supplier: str = "GetYourGuide"
    cancelled_because: str = ""
    """Why the operator called it off, as a letter would say it - "because of high water"."""


@dataclass(frozen=True, slots=True)
class Booking:
    """One trip on the book, with the incident it is carrying, if any."""

    id: str
    customer_id: str
    trip: str
    route: str
    travellers: int
    departs: date
    returns: date
    package_price_eur: int
    booked_at: datetime
    flights: tuple[Flight, ...] = ()
    hotels: tuple[HotelStay, ...] = ()
    activities: tuple[Activity, ...] = ()
    payment: str = "cleared"  # cleared | flagged | chargeback
    payment_note: str = ""
    incident: IncidentKind | None = None
    severity: Severity = Severity.LOW
    notes: str = ""
    affected_value_eur: int = 0
    """Services paid for and not delivered - what a price reduction starts from."""
    days_affected: int = 0
    refund_due: date | None = None
    """The date a refund has been committed to, when there is one."""
    awaiting_supplier: bool = False
    """The remedy depends on a supplier who has not answered yet."""
    rooms_short: int = 0
    """Rooms an oversold hotel cannot honour - what rehousing has to find."""


@dataclass(frozen=True, slots=True)
class TierPolicy:
    """The compensation policy a resolution has to respect."""

    tier: str
    allowance_per_day_eur: int
    max_goodwill_eur: int
    approval_threshold_eur: int
    """Above this figure a person must approve. Pattern 10 turns on this number."""


@dataclass(frozen=True, slots=True)
class Property:
    """A hotel as the channel manager sees it: availability and a sell rate."""

    name: str
    city: str
    channel_manager: str
    free_rooms: int
    rate_eur: int


@dataclass(frozen=True, slots=True)
class SyncEvent:
    """One availability push from a channel manager to this agency."""

    hotel: str
    at: datetime
    status: str  # accepted | rejected
    detail: str


@dataclass(frozen=True, slots=True)
class ActivitySlot:
    """A future departure of an activity, with places left."""

    activity: str
    on: datetime
    places: int


@dataclass(frozen=True, slots=True)
class SupplierInvoice:
    """One line a supplier billed us for this month."""

    id: str
    supplier: str
    booking_id: str
    item: str
    qty_invoiced: int
    qty_delivered: int
    rate_invoiced_eur: int
    rate_contracted_eur: int
    commission_invoiced_pct: int
    commission_contracted_pct: int


@dataclass(slots=True)
class AuditEntry:
    """One immutable line in the run's audit trail."""

    at: datetime
    actor: str
    action: str
    detail: str
    pattern: str | None = None


# --------------------------------------------------------------------------
# Seed data - deterministic, so a demo replays identically
# --------------------------------------------------------------------------

_CUSTOMERS: tuple[Customer, ...] = (
    Customer("CUST-001", "Kalniņš family", "LV", "gold", "leisure", 14_800),
    Customer("CUST-002", "Elīna and Mārtiņš Ozols", "LV", "gold", "leisure", 11_200),
    Customer("CUST-003", "Nordic Code Labs SIA", "LV", "gold", "corporate", 148_000),
    Customer("CUST-004", "Mežaparks Secondary School", "LV", "silver", "group", 41_000),
    Customer("CUST-005", "Saimaa Analytics Oy", "FI", "silver", "corporate", 36_500),
    Customer("CUST-006", "J. Miller", "GB", "bronze", "leisure", 508),
    Customer("CUST-007", "Tamm family", "EE", "silver", "leisure", 6_200),
    Customer("CUST-008", "Andris Bērziņš", "LV", "bronze", "leisure", 1_150),
    Customer("CUST-009", "Liepiņš family", "LV", "silver", "leisure", 7_900),
    Customer("CUST-010", "Laura Vītola", "LV", "silver", "leisure", 3_400),
    Customer("CUST-011", "Marta Nowak", "PL", "bronze", "leisure", 478),
    Customer("CUST-012", "Jonas Petrauskas", "LT", "silver", "leisure", 2_900),
)

_TIERS: tuple[TierPolicy, ...] = (
    TierPolicy("gold", allowance_per_day_eur=120, max_goodwill_eur=2_500, approval_threshold_eur=1_000),
    TierPolicy("silver", allowance_per_day_eur=60, max_goodwill_eur=1_200, approval_threshold_eur=500),
    TierPolicy("bronze", allowance_per_day_eur=30, max_goodwill_eur=500, approval_threshold_eur=250),
)

_AIRPORTS: tuple[Airport, ...] = (
    Airport("RIX", "Riga", 56.9236, 23.9711),
    Airport("HEL", "Helsinki", 60.3172, 24.9633),
    Airport("BCN", "Barcelona", 41.2974, 2.0833),
    Airport("CDG", "Paris", 49.0097, 2.5479),
    Airport("FCO", "Rome", 41.8003, 12.2389),
    Airport("DBV", "Dubrovnik", 42.5614, 18.2682),
    Airport("LIS", "Lisbon", 38.7813, -9.1359),
    Airport("TFS", "Tenerife", 28.0445, -16.5725),
    Airport("FRA", "Frankfurt", 50.0379, 8.5622),
)

#: Every hotel the agency sells, and which channel manager connects it to us.
_PROPERTIES: tuple[Property, ...] = (
    Property("Baltic Beach Hotel & SPA", "Jūrmala", "SiteMinder", 0, 165),
    Property("Hotel Jūrmala Spa", "Jūrmala", "SiteMinder", 5, 195),
    Property("Semarah Hotel Lielupe", "Jūrmala", "SiteMinder", 4, 210),
    Property("Pullman Riga Old Town", "Riga", "SiteMinder", 0, 150),
    Property("Hotel Neiburgs", "Riga", "SiteMinder", 3, 185),
    Property("Radisson Blu Latvija", "Riga", "SiteMinder", 6, 128),
    Property("Grand Hotel Kempinski Riga", "Riga", "SiteMinder", 4, 260),
    Property("Hotel Excelsior Dubrovnik", "Dubrovnik", "SiteMinder", 1, 690),
    Property("Catalonia Plaza Catalunya", "Barcelona", "SiteMinder", 2, 215),
    Property("Swissôtel Tallinn", "Tallinn", "SiteMinder", 5, 160),
    Property("Hotel Telegraaf", "Tallinn", "SiteMinder", 2, 240),
    Property("Hotel Nord Nuova Roma", "Rome", "SiteMinder", 20, 98),
    Property("Iberostar Selection Anthelia", "Tenerife", "SiteMinder", 2, 280),
    Property("Hard Rock Hotel Tenerife", "Tenerife", "SiteMinder", 3, 230),
    Property("Pullman Paris Tour Eiffel", "Paris", "SiteMinder", 4, 320),
    Property("Hotel Sigulda", "Sigulda", "D-EDGE", 6, 79),
    Property("Amberton Hotel Klaipėda", "Klaipėda", "D-EDGE", 7, 95),
    Property("Hotel Pacai", "Vilnius", "D-EDGE", 3, 200),
    Property("Tivoli Avenida Liberdade Lisboa", "Lisbon", "D-EDGE", 4, 250),
    Property("Hotel Artemide", "Rome", "D-EDGE", 3, 220),
)

#: The demo's present day. Letters date their commitments from it.
TODAY = date(2026, 9, 24)

#: The night our own SiteMinder endpoint rejected every availability push.
#: Nothing on any single booking says so. Pattern 5 exists to find it.
OUTAGE_STARTS = datetime(2026, 9, 15, 2, 4)
OUTAGE_ENDS = datetime(2026, 9, 15, 7, 34)
_OUTAGE_ERROR = "401 Unauthorized - credentials rejected by the Baltic Travel Agency endpoint"


def _sync_log() -> tuple[SyncEvent, ...]:
    """Availability pushes for 14-16 September, every 30 minutes, per property.

    SiteMinder properties show the six hours in which our endpoint turned every
    push away; D-EDGE properties, on a different connection, show none. The
    hotels were sending the right availability the whole time - we were not
    accepting it, and kept selling rooms that had already gone elsewhere.
    """
    events: list[SyncEvent] = []
    start = datetime(2026, 9, 14, 0, 4)
    for prop in _PROPERTIES:
        at = start
        while at < datetime(2026, 9, 17, 0, 0):
            failed = prop.channel_manager == "SiteMinder" and OUTAGE_STARTS <= at <= OUTAGE_ENDS
            events.append(
                SyncEvent(
                    prop.name,
                    at,
                    "rejected" if failed else "accepted",
                    _OUTAGE_ERROR if failed else "availability and rates applied",
                )
            )
            at += timedelta(minutes=30)
    return tuple(events)


_SLOTS: tuple[ActivitySlot, ...] = (
    ActivitySlot("Sagrada Família guided tour", datetime(2026, 9, 27, 10, 0), 6),
    ActivitySlot("Sagrada Família guided tour", datetime(2026, 9, 27, 15, 30), 2),
    ActivitySlot("Sagrada Família guided tour", datetime(2026, 9, 28, 10, 0), 12),
    ActivitySlot("Colosseum & Roman Forum guided tour", datetime(2026, 9, 26, 9, 30), 10),
    ActivitySlot("Colosseum & Roman Forum guided tour", datetime(2026, 9, 27, 9, 30), 40),
    ActivitySlot("Colosseum & Roman Forum guided tour", datetime(2026, 9, 28, 14, 0), 40),
    ActivitySlot("Louvre Museum guided tour", datetime(2026, 9, 25, 10, 0), 8),
    ActivitySlot("Riga Central Market food tour", datetime(2026, 9, 27, 11, 0), 10),
    ActivitySlot("Gauja National Park kayak trip", datetime(2026, 10, 3, 10, 0), 8),
    ActivitySlot("Curonian Spit & Nida day trip", datetime(2026, 9, 27, 9, 0), 14),
    ActivitySlot("Ķemeri National Park bog walk", datetime(2026, 9, 26, 10, 0), 30),
)


def _dt(month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute)


def _seed_bookings() -> tuple[Booking, ...]:
    """Twenty bookings: enough for the patterns to look like work, few enough to print."""
    d = date
    rows: list[Booking] = [
        Booking(
            "BTA-26101", "CUST-001", "Barcelona family holiday", "Riga → Barcelona", 4,
            d(2026, 9, 26), d(2026, 10, 3), 4_370, _dt(7, 2, 20, 41),
            flights=(
                Flight("airBaltic", "BT651", "RIX", "BCN", _dt(9, 26, 10, 5), _dt(9, 26, 12, 45),
                       status="cancelled", cause="operational reasons", notice_days=2,
                       rebooked_departs=_dt(9, 27, 10, 5)),
                Flight("airBaltic", "BT652", "BCN", "RIX", _dt(10, 3, 13, 40), _dt(10, 3, 18, 15)),
            ),
            hotels=(HotelStay("Catalonia Plaza Catalunya", "Barcelona", d(2026, 9, 26), 7, 2, "Family Room", 210),),
            activities=(Activity("Sagrada Família guided tour", "Barcelona", _dt(9, 26, 16, 0), 4, 47),),
            incident=IncidentKind.FLIGHT_CANCELLED, severity=Severity.HIGH,
            notes="airBaltic cancelled BT651 on 26 Sep for operational reasons and moved the family to the same "
                  "flight on 27 Sep. Night one at the hotel and the Saturday Sagrada Família tour are affected.",
            affected_value_eur=0, days_affected=1,
        ),
        Booking(
            "BTA-26102", "CUST-003", "Jūrmala team incentive", "Riga → Jūrmala", 24,
            d(2026, 9, 25), d(2026, 9, 28), 9_600, _dt(9, 15, 6, 40),
            hotels=(HotelStay("Baltic Beach Hotel & SPA", "Jūrmala", d(2026, 9, 25), 3, 12, "Standard Twin", 165),),
            activities=(Activity("Ķemeri National Park bog walk", "Jūrmala", _dt(9, 26, 10, 0), 24, 35),),
            incident=IncidentKind.OVERBOOKED, severity=Severity.CRITICAL,
            notes="The hotel can honour 5 of the group's 12 rooms for 25-28 Sep; 7 rooms (14 guests) must be "
                  "rehoused. The rooms were sold on 15 Sep at 06:40, after the hotel had already closed them.",
            affected_value_eur=960, days_affected=3, rooms_short=7,
        ),
        Booking(
            "BTA-26103", "CUST-002", "Dubrovnik honeymoon", "Riga → Dubrovnik", 2,
            d(2026, 9, 16), d(2026, 9, 23), 5_430, _dt(9, 15, 2, 51),
            flights=(
                Flight("airBaltic", "BT667", "RIX", "DBV", _dt(9, 16, 7, 30), _dt(9, 16, 9, 35)),
                Flight("airBaltic", "BT668", "DBV", "RIX", _dt(9, 23, 10, 25), _dt(9, 23, 14, 30)),
            ),
            hotels=(HotelStay("Hotel Excelsior Dubrovnik", "Dubrovnik", d(2026, 9, 16), 7, 1, "Sea View Suite", 640),),
            activities=(Activity("Elaphiti Islands boat trip", "Dubrovnik", _dt(9, 18, 9, 0), 2, 85),),
            incident=IncidentKind.NOT_AS_BOOKED, severity=Severity.HIGH,
            notes="The Sea View Suite was sold twice. The couple spent 5 of their 7 nights in a Superior Room "
                  "(EUR 360 a night against the suite's EUR 640) before the suite came free.",
            affected_value_eur=1_400, days_affected=5,
        ),
        Booking(
            "BTA-26104", "CUST-006", "Riga weekend", "London → Riga", 2,
            d(2026, 9, 19), d(2026, 9, 21), 508, _dt(9, 12, 23, 14),
            hotels=(HotelStay("Hotel Neiburgs", "Riga", d(2026, 9, 19), 2, 1, "Deluxe Double", 185),),
            activities=(Activity("Riga Central Market food tour", "Riga", _dt(9, 20, 11, 0), 2, 69),),
            payment="flagged",
            payment_note="Card ending 4417 reported stolen by the issuing bank on 21 Sep. The refund is "
                         "requested to a different card, ending 9032.",
            incident=IncidentKind.ACTIVITY_CANCELLED, severity=Severity.MEDIUM,
            notes="The operator cancelled the food tour on 20 Sep. The customer asks for a refund of the whole "
                  "booking, EUR 508, to a different card.",
            affected_value_eur=138, days_affected=1,
        ),
        Booking(
            "BTA-26105", "CUST-005", "Lisbon client workshop", "Helsinki → Lisbon", 3,
            d(2026, 9, 21), d(2026, 9, 24), 3_420, _dt(8, 28, 9, 12),
            flights=(
                Flight("Finnair", "AY1671", "HEL", "LIS", _dt(9, 21, 7, 35), _dt(9, 21, 10, 50),
                       status="delayed", delay_minutes=250, cause="a late inbound aircraft"),
                Flight("Finnair", "AY1672", "LIS", "HEL", _dt(9, 24, 11, 45), _dt(9, 24, 18, 50)),
            ),
            hotels=(HotelStay("Tivoli Avenida Liberdade Lisboa", "Lisbon", d(2026, 9, 21), 3, 3,
                              "Superior Room", 240),),
            incident=IncidentKind.FLIGHT_DELAYED, severity=Severity.MEDIUM,
            notes="AY1671 reached Lisbon 4 h 10 min late after a late inbound aircraft; the team missed the "
                  "afternoon client workshop.",
            affected_value_eur=0, days_affected=1,
        ),
        Booking(
            "BTA-26106", "CUST-007", "Riga weekend", "Tallinn → Riga", 4,
            d(2026, 9, 18), d(2026, 9, 20), 700, _dt(9, 15, 3, 17),
            hotels=(HotelStay("Pullman Riga Old Town", "Riga", d(2026, 9, 18), 2, 2, "Family Room", 150),),
            activities=(Activity("Riga Old Town walking tour", "Riga", _dt(9, 19, 10, 0), 4, 25),),
            incident=IncidentKind.OVERBOOKED, severity=Severity.HIGH,
            notes="Pullman Riga Old Town walked the family on arrival to Radisson Blu Latvija. The hotel has "
                  "not confirmed whether it will refund the two nights.",
            affected_value_eur=600, days_affected=2, awaiting_supplier=True, rooms_short=2,
        ),
        Booking(
            "BTA-26107", "CUST-008", "Sigulda weekend", "Riga → Sigulda", 2,
            d(2026, 9, 19), d(2026, 9, 21), 248, _dt(8, 30, 18, 2),
            hotels=(HotelStay("Hotel Sigulda", "Sigulda", d(2026, 9, 19), 2, 1, "Double", 79),),
            activities=(Activity("Gauja National Park kayak trip", "Sigulda", _dt(9, 20, 10, 0), 2, 45,
                                cancelled_because="because of high water on the Gauja"),),
            incident=IncidentKind.ACTIVITY_CANCELLED, severity=Severity.MEDIUM,
            notes="The operator cancelled the kayak trip for high water on the Gauja. A full refund of EUR 90 "
                  "is confirmed for 2 Oct.",
            affected_value_eur=90, days_affected=1, refund_due=d(2026, 10, 2),
        ),
        Booking(
            "BTA-26108", "CUST-005", "Frankfurt sales meeting", "Riga → Frankfurt", 1,
            d(2026, 9, 22), d(2026, 9, 22), 380, _dt(9, 10, 14, 30),
            flights=(
                Flight("Lufthansa", "LH893", "RIX", "FRA", _dt(9, 22, 6, 0), _dt(9, 22, 7, 25),
                       status="delayed", delay_minutes=40, cause="a slot restriction at Frankfurt"),
            ),
            incident=IncidentKind.FLIGHT_DELAYED, severity=Severity.LOW,
            notes="LH893 landed 40 minutes late; the traveller made the meeting. Nothing to do beyond a note "
                  "on file.",
        ),
        Booking(
            "BTA-26109", "CUST-004", "Rome school trip", "Riga → Rome", 36,
            d(2026, 9, 24), d(2026, 9, 29), 20_050, _dt(6, 11, 10, 20),
            flights=(
                Flight("airBaltic", "BT633", "RIX", "FCO", _dt(9, 24, 12, 10), _dt(9, 24, 14, 5),
                       status="cancelled", cause="an Italian air traffic control strike", extraordinary=True,
                       notice_days=1, rebooked_departs=_dt(9, 26, 12, 10)),
                Flight("airBaltic", "BT634", "FCO", "RIX", _dt(9, 29, 15, 5), _dt(9, 29, 19, 0)),
            ),
            hotels=(HotelStay("Hotel Nord Nuova Roma", "Rome", d(2026, 9, 24), 5, 18, "Triple Room", 98),),
            activities=(Activity("Colosseum & Roman Forum guided tour", "Rome", _dt(9, 25, 9, 30), 36, 52),),
            incident=IncidentKind.FLIGHT_CANCELLED, severity=Severity.CRITICAL,
            notes="BT633 cancelled: Italian air traffic control strike. The group of 36 (32 students, 4 teachers) "
                  "is rebooked on 26 Sep; the hotel and the Colosseum slot have to move with it.",
            affected_value_eur=0, days_affected=2,
        ),
        Booking(
            "BTA-26110", "CUST-009", "Tenerife autumn break", "Riga → Tenerife", 4,
            d(2026, 9, 16), d(2026, 9, 23), 5_760, _dt(7, 19, 21, 5),
            flights=(
                Flight("airBaltic", "BT715", "RIX", "TFS", _dt(9, 16, 6, 45), _dt(9, 16, 10, 55)),
                Flight("airBaltic", "BT716", "TFS", "RIX", _dt(9, 23, 11, 50), _dt(9, 23, 19, 50),
                       status="cancelled", cause="a technical fault", notice_days=0,
                       rebooked_departs=_dt(9, 25, 11, 50)),
            ),
            hotels=(HotelStay("Iberostar Selection Anthelia", "Tenerife", d(2026, 9, 16), 7, 2,
                              "Family Suite", 260),),
            activities=(Activity("Teide National Park cable car tour", "Tenerife", _dt(9, 18, 9, 0), 4, 79),),
            incident=IncidentKind.FLIGHT_CANCELLED, severity=Severity.CRITICAL,
            notes="Return BT716 cancelled for a technical fault; the next seats are on 25 Sep. A family of four "
                  "is in Tenerife with no hotel for two nights.",
            affected_value_eur=0, days_affected=2,
        ),
        Booking(
            "BTA-26111", "CUST-010", "Paris long weekend", "Riga → Paris", 1,
            d(2026, 9, 23), d(2026, 9, 26), 1_245, _dt(8, 21, 12, 48),
            flights=(
                Flight("airBaltic", "BT691", "RIX", "CDG", _dt(9, 23, 9, 15), _dt(9, 23, 11, 5),
                       status="cancelled", cause="a French air traffic control strike", extraordinary=True,
                       notice_days=0),
                Flight("airBaltic", "BT692", "CDG", "RIX", _dt(9, 26, 12, 0), _dt(9, 26, 15, 45)),
            ),
            hotels=(HotelStay("Pullman Paris Tour Eiffel", "Paris", d(2026, 9, 23), 3, 1, "Superior Room", 310),),
            activities=(Activity("Louvre Museum guided tour", "Paris", _dt(9, 24, 10, 0), 1, 75),),
            incident=IncidentKind.FLIGHT_CANCELLED, severity=Severity.MEDIUM,
            notes="BT691 cancelled on the day: French air traffic control strike. The traveller waited six hours "
                  "at Riga airport before the cancellation was announced.",
            affected_value_eur=310, days_affected=1,
        ),
        Booking(
            "BTA-26112", "CUST-011", "Tallinn Old Town weekend", "Warsaw → Tallinn", 2,
            d(2026, 9, 19), d(2026, 9, 21), 478, _dt(9, 2, 19, 55),
            hotels=(HotelStay("Swissôtel Tallinn", "Tallinn", d(2026, 9, 19), 2, 1, "Classic Room", 150),),
            activities=(Activity("Lahemaa National Park day trip", "Tallinn", _dt(9, 20, 9, 0), 2, 89,
                                cancelled_because="because too few people had booked for it to run"),),
            payment="chargeback",
            payment_note="The customer's bank opened a chargeback for the full EUR 478 on 20 Sep.",
            incident=IncidentKind.ACTIVITY_CANCELLED, severity=Severity.MEDIUM,
            notes="The operator cancelled the Lahemaa day trip because the minimum group size was not met.",
            affected_value_eur=178, days_affected=1,
        ),
        Booking(
            "BTA-26113", "CUST-012", "Klaipėda and the Curonian Spit", "Vilnius → Klaipėda", 2,
            d(2026, 9, 19), d(2026, 9, 21), 348, _dt(9, 4, 8, 37),
            hotels=(HotelStay("Amberton Hotel Klaipėda", "Klaipėda", d(2026, 9, 19), 2, 1, "Standard Double", 95),),
            activities=(Activity("Curonian Spit & Nida day trip", "Klaipėda", _dt(9, 20, 9, 0), 2, 79,
                                cancelled_because="because a storm warning closed the Smiltynė ferry"),),
            incident=IncidentKind.ACTIVITY_CANCELLED, severity=Severity.LOW,
            notes="The Curonian Spit day trip was cancelled for a storm warning; the Smiltynė ferry was "
                  "suspended. A refund of EUR 158 is due.",
            affected_value_eur=158, days_affected=1, refund_due=d(2026, 9, 30),
        ),
        Booking(
            "BTA-26114", "CUST-003", "Vilnius client visit", "Riga → Vilnius", 1,
            d(2026, 9, 10), d(2026, 9, 13), 570, _dt(8, 25, 10, 11),
            hotels=(HotelStay("Hotel Pacai", "Vilnius", d(2026, 9, 10), 3, 1, "Superior Room", 190),),
        ),
        Booking(
            "BTA-26115", "CUST-003", "Tallinn engineering offsite", "Riga → Tallinn", 4,
            d(2026, 9, 11), d(2026, 9, 15), 2_400, _dt(8, 18, 16, 3),
            hotels=(HotelStay("Swissôtel Tallinn", "Tallinn", d(2026, 9, 11), 4, 4, "Classic Room", 150),),
        ),
        Booking(
            "BTA-26116", "CUST-005", "Riga partner meetings", "Helsinki → Riga", 1,
            d(2026, 9, 5), d(2026, 9, 9), 960, _dt(8, 14, 11, 26),
            hotels=(HotelStay("Grand Hotel Kempinski Riga", "Riga", d(2026, 9, 5), 4, 1, "Deluxe Room", 240),),
        ),
        Booking(
            "BTA-26117", "CUST-012", "Riga city break", "Vilnius → Riga", 2,
            d(2026, 9, 8), d(2026, 9, 11), 768, _dt(8, 9, 20, 17),
            hotels=(HotelStay("Radisson Blu Latvija", "Riga", d(2026, 9, 8), 3, 2, "Standard Room", 128),),
        ),
        Booking(
            "BTA-26118", "CUST-009", "Tallinn weekend", "Riga → Tallinn", 2,
            d(2026, 9, 12), d(2026, 9, 14), 460, _dt(8, 31, 9, 44),
            hotels=(HotelStay("Hotel Telegraaf", "Tallinn", d(2026, 9, 12), 2, 1, "Deluxe Room", 230),),
        ),
        Booking(
            "BTA-26119", "CUST-010", "Rome in September", "Riga → Rome", 1,
            d(2026, 9, 7), d(2026, 9, 11), 840, _dt(7, 28, 22, 9),
            hotels=(HotelStay("Hotel Artemide", "Rome", d(2026, 9, 7), 4, 1, "Classic Room", 210),),
        ),
        Booking(
            "BTA-26120", "CUST-001", "Sigulda autumn walk", "Riga → Sigulda", 4,
            d(2026, 9, 5), d(2026, 9, 6), 158, _dt(8, 20, 8, 52),
            hotels=(HotelStay("Hotel Sigulda", "Sigulda", d(2026, 9, 5), 1, 2, "Family Room", 79),),
        ),
    ]
    return tuple(rows)


#: This month's supplier invoices. Five of the sixteen are wrong, each in a way
#: one of pattern 7's three checkers is built to catch - and one is wrong twice.
_INVOICES: tuple[SupplierInvoice, ...] = (
    SupplierInvoice("SI-2601", "Hotel Excelsior Dubrovnik", "BTA-26103", "Sea View Suite, room-nights",
                    7, 7, 640, 590, 10, 10),
    SupplierInvoice("SI-2602", "Pullman Riga Old Town", "BTA-26106", "Family Room, room-nights",
                    4, 0, 150, 150, 12, 12),
    SupplierInvoice("SI-2603", "Swissôtel Tallinn", "BTA-26115", "Classic Room, room-nights",
                    16, 16, 150, 150, 8, 12),
    SupplierInvoice("SI-2604", "Grand Hotel Kempinski Riga", "BTA-26116", "Deluxe Room, room-nights",
                    5, 4, 265, 240, 10, 10),
    SupplierInvoice("SI-2605", "GetYourGuide", "BTA-26103", "Elaphiti Islands boat trip, places",
                    3, 2, 85, 85, 20, 20),
    SupplierInvoice("SI-2606", "Hotel Neiburgs", "BTA-26104", "Deluxe Double, room-nights",
                    2, 2, 185, 185, 12, 12),
    SupplierInvoice("SI-2607", "Tivoli Avenida Liberdade Lisboa", "BTA-26105", "Superior Room, room-nights",
                    9, 9, 240, 240, 10, 10),
    SupplierInvoice("SI-2608", "Hotel Sigulda", "BTA-26107", "Double, room-nights",
                    2, 2, 79, 79, 10, 10),
    SupplierInvoice("SI-2609", "Iberostar Selection Anthelia", "BTA-26110", "Family Suite, room-nights",
                    14, 14, 260, 260, 12, 12),
    SupplierInvoice("SI-2610", "Swissôtel Tallinn", "BTA-26112", "Classic Room, room-nights",
                    2, 2, 150, 150, 12, 12),
    SupplierInvoice("SI-2611", "Amberton Hotel Klaipėda", "BTA-26113", "Standard Double, room-nights",
                    2, 2, 95, 95, 10, 10),
    SupplierInvoice("SI-2612", "Hotel Pacai", "BTA-26114", "Superior Room, room-nights",
                    3, 3, 190, 190, 10, 10),
    SupplierInvoice("SI-2613", "Radisson Blu Latvija", "BTA-26117", "Standard Room, room-nights",
                    6, 6, 128, 128, 12, 12),
    SupplierInvoice("SI-2614", "Hotel Telegraaf", "BTA-26118", "Deluxe Room, room-nights",
                    2, 2, 230, 230, 10, 10),
    SupplierInvoice("SI-2615", "Hotel Artemide", "BTA-26119", "Classic Room, room-nights",
                    4, 4, 210, 210, 10, 10),
    SupplierInvoice("SI-2616", "GetYourGuide", "BTA-26106", "Riga Old Town walking tour, places",
                    4, 4, 25, 25, 20, 20),
)


# --------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------


@dataclass
class TravelStore:
    """In-memory store. One instance per process; reset between demo runs.

    The audit trail is the only mutable part. Everything else is frozen, which
    is deliberate: an agent that "updates" a booking has to go through
    :meth:`record`, so the trail can never silently miss a decision.
    """

    customers: dict[str, Customer] = field(default_factory=dict)
    bookings: dict[str, Booking] = field(default_factory=dict)
    tiers: dict[str, TierPolicy] = field(default_factory=dict)
    airports: dict[str, Airport] = field(default_factory=dict)
    properties: dict[str, Property] = field(default_factory=dict)
    sync_log: tuple[SyncEvent, ...] = field(default_factory=tuple)
    slots: tuple[ActivitySlot, ...] = field(default_factory=tuple)
    invoices: dict[str, SupplierInvoice] = field(default_factory=dict)
    audit: list[AuditEntry] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def seeded(cls) -> TravelStore:
        """Build a store populated with the deterministic demo data."""
        return cls(
            customers={c.id: c for c in _CUSTOMERS},
            bookings={b.id: b for b in _seed_bookings()},
            tiers={p.tier: p for p in _TIERS},
            airports={a.code: a for a in _AIRPORTS},
            properties={p.name: p for p in _PROPERTIES},
            sync_log=_sync_log(),
            slots=_SLOTS,
            invoices={i.id: i for i in _INVOICES},
        )

    def reset(self) -> None:
        """Restore seed state and clear the audit trail, between demo runs."""
        fresh = TravelStore.seeded()
        with self._lock:
            self.customers = fresh.customers
            self.bookings = fresh.bookings
            self.tiers = fresh.tiers
            self.airports = fresh.airports
            self.properties = fresh.properties
            self.sync_log = fresh.sync_log
            self.slots = fresh.slots
            self.invoices = fresh.invoices
            self.audit = []

    def record(self, actor: str, action: str, detail: str, pattern: str | None = None) -> AuditEntry:
        """Append one line to the audit trail and return it."""
        entry = AuditEntry(datetime.now(timezone.utc), actor, action, detail, pattern)
        with self._lock:
            self.audit.append(entry)
        return entry

    def audit_dicts(self) -> list[dict[str, Any]]:
        """The audit trail as JSON-ready rows, for the showcase site."""
        with self._lock:
            return [
                {
                    "at": e.at.isoformat(),
                    "actor": e.actor,
                    "action": e.action,
                    "detail": e.detail,
                    "pattern": e.pattern,
                }
                for e in self.audit
            ]

    # -- queries used by the agent tools -----------------------------------

    def open_incidents(self) -> list[Booking]:
        """Every booking currently carrying an incident."""
        return [b for b in self.bookings.values() if b.incident is not None]

    def policy_for(self, customer_id: str) -> TierPolicy | None:
        """The compensation policy that applies to a customer, via their tier."""
        customer = self.customers.get(customer_id)
        return self.tiers.get(customer.tier) if customer else None

    def booking_in(self, text: str) -> Booking | None:
        """The first booking a piece of text refers to, in the order it is mentioned."""
        for match in BOOKING_REF.finditer(text or ""):
            booking = self.bookings.get(match.group(0))
            if booking is not None:
                return booking
        return None

    def distance_km(self, origin: str, destination: str) -> int:
        """Great-circle distance between two airports - the distance EU261 bands on."""
        a, b = self.airports[origin], self.airports[destination]
        phi1, phi2 = math.radians(a.lat), math.radians(b.lat)
        dphi, dlam = phi2 - phi1, math.radians(b.lon - a.lon)
        h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        return round(2 * 6371 * math.asin(math.sqrt(h)))


#: Process-wide store. Demos import this directly.
STORE = TravelStore.seeded()
