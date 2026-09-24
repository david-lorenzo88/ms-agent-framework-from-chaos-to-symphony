"""Tools the agents call. Every one reads or writes the in-memory store only.

These are ordinary typed Python functions. Agent Framework turns them into tool
schemas from the signature and docstring, which is why the docstrings here are
written for the model to read, not just for a developer.

Each tool is a thin wrapper over a pure function of the same data. The wrapper
writes a line to the audit trail, because a tool call is something an agent
*did*. The pure function writes nothing, so the offline client and the
Python-only nodes in patterns 7 and 9 can use the same rules - the same EU261
bands, the same compensation arithmetic, the same screening verdict - without
leaving phantom tool calls in the trail.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from pydantic import Field

from .memory import OUTAGE_ENDS, OUTAGE_STARTS, STORE, Booking, Flight

# --------------------------------------------------------------------------
# The rules, as pure functions
# --------------------------------------------------------------------------

#: Simplified EU261. Real passenger rights have more edges than this - reroutes
#: that land close to schedule halve the payment, for one - but every rule the
#: demo leans on is here and is the rule as written.
EU261_DELAY_MINUTES = 180
EU261_NOTICE_DAYS = 14
EU261_CARE_MINUTES = 120


def booking_record(booking: Booking) -> dict[str, Any]:
    """The booking as an agent sees it. Payment screening is deliberately not in it."""
    return {
        "id": booking.id,
        "customer_id": booking.customer_id,
        "trip": booking.trip,
        "route": booking.route,
        "travellers": booking.travellers,
        "departs": booking.departs.isoformat(),
        "returns": booking.returns.isoformat(),
        "package_price_eur": booking.package_price_eur,
        "booked_at": booking.booked_at.isoformat(timespec="minutes"),
        "flights": [
            {
                "flight": f"{f.carrier} {f.number}",
                "from": f.origin,
                "to": f.destination,
                "departs": f.departs.isoformat(timespec="minutes"),
                "arrives": f.arrives.isoformat(timespec="minutes"),
                "status": f.status,
                "delay_minutes": f.delay_minutes,
                "cause": f.cause or None,
                "rebooked_departs": f.rebooked_departs.isoformat(timespec="minutes") if f.rebooked_departs else None,
            }
            for f in booking.flights
        ],
        "hotels": [
            {
                "hotel": h.hotel,
                "city": h.city,
                "check_in": h.check_in.isoformat(),
                "nights": h.nights,
                "rooms": h.rooms,
                "room_type": h.room_type,
                "rate_eur": h.rate_eur,
            }
            for h in booking.hotels
        ],
        "activities": [
            {
                "activity": a.name,
                "city": a.city,
                "on": a.on.isoformat(timespec="minutes"),
                "pax": a.pax,
                "price_eur": a.price_eur,
                "supplier": a.supplier,
            }
            for a in booking.activities
        ],
        "incident": booking.incident.value if booking.incident else None,
        "severity": booking.severity.value,
        "notes": booking.notes,
        "refund_due": booking.refund_due.isoformat() if booking.refund_due else None,
        "awaiting_supplier": booking.awaiting_supplier,
    }


def disrupted_flight(booking: Booking) -> Flight | None:
    """The flight on this booking that went wrong, if one did."""
    return next((f for f in booking.flights if f.status != "on time"), None)


def eu261_band(flight: Flight) -> tuple[int, int]:
    """(distance in km, compensation per passenger in EUR) for a flight.

    Inside the EU the band tops out at EUR 400 however far the flight goes, so
    Tenerife - 4,500 km from Riga - is a EUR 400 flight, not a EUR 600 one. The
    EUR 600 band exists only for flights leaving the EU.
    """
    km = STORE.distance_km(flight.origin, flight.destination)
    origin, destination = STORE.airports[flight.origin], STORE.airports[flight.destination]
    if km <= 1500:
        return km, 250
    if (origin.in_eu and destination.in_eu) or km <= 3500:
        return km, 400
    return km, 600


def eu261(booking: Booking) -> dict[str, Any]:
    """What EU261 says about this booking's disrupted flight - and who pays it."""
    flight = disrupted_flight(booking)
    if flight is None:
        return {"booking_id": booking.id, "applies": False, "reason": "No flight on this booking was disrupted."}

    km, band = eu261_band(flight)
    if flight.status == "cancelled":
        qualifies = flight.notice_days < EU261_NOTICE_DAYS
        why_not = f"Notified {flight.notice_days} days ahead - at least {EU261_NOTICE_DAYS} removes compensation."
    else:
        qualifies = flight.delay_minutes >= EU261_DELAY_MINUTES
        why_not = f"Arrival delay {flight.delay_minutes} minutes - compensation starts at three hours."
    if flight.extraordinary:
        cause = flight.cause[:1].upper() + flight.cause[1:]
        eligible, reason = False, f"{cause} is an extraordinary circumstance - no compensation is owed."
    elif not qualifies:
        eligible, reason = False, why_not
    else:
        eligible = True
        reason = f"{flight.status.capitalize()} because of {flight.cause} - within the airline's control."

    # Care is owed whatever caused the disruption - that is what travellers
    # most often do not know - but only to someone actually stranded: at the
    # airport when it happened, or away from home on a return leg. A group
    # told a day ahead is at home, and is owed a choice of flights, not a hotel.
    away = flight is not booking.flights[0]
    stranded = flight.status == "delayed" or flight.notice_days == 0 or away
    overnight = bool(flight.rebooked_departs and flight.rebooked_departs.date() > flight.departs.date())
    care: list[str] = []
    if stranded and (flight.status == "cancelled" or flight.delay_minutes >= EU261_CARE_MINUTES):
        care.append("meals and refreshments in proportion to the wait")
    if stranded and overnight:
        care.append("hotel accommodation and transfers for each night until departure")
    rights = "choice of rerouting or a full refund" if flight.status == "cancelled" else "right to care"
    per_passenger = band if eligible else 0
    return {
        "booking_id": booking.id,
        "applies": True,
        "flight": f"{flight.carrier} {flight.number} {flight.origin}-{flight.destination}",
        "disruption": flight.status if flight.status == "cancelled" else f"delayed {flight.delay_minutes} min",
        "cause": flight.cause,
        "distance_km": km,
        "band_eur": band,
        "eligible": eligible,
        "reason": reason,
        "per_passenger_eur": per_passenger,
        "passengers": booking.travellers,
        "total_eur": per_passenger * booking.travellers,
        "payable_by": flight.carrier,
        "rights": rights,
        "care_owed": care,
        "note": "Compensation is owed by the operating airline, not by the travel agency. Care and rerouting are "
                "owed even when an extraordinary circumstance removes the compensation.",
    }


def screening(booking: Booking) -> dict[str, Any]:
    """The payment-risk verdict on a booking."""
    if booking.payment == "flagged":
        return {
            "booking_id": booking.id,
            "cleared": False,
            "action": "freeze_and_escalate_to_risk",
            "reason": booking.payment_note,
        }
    if booking.payment == "chargeback":
        return {
            "booking_id": booking.id,
            "cleared": False,
            "action": "do_not_refund_directly",
            "reason": booking.payment_note + " Refunding as well would pay the customer twice.",
        }
    return {"booking_id": booking.id, "cleared": True, "action": "none", "reason": "Payment cleared screening."}


def exposure(booking: Booking) -> dict[str, Any]:
    """The agency's own exposure: price reduction plus a tier allowance, capped, then gated.

    EU261 money is not in here. It is the airline's bill; as package organiser
    we owe a price reduction for services not delivered, and anything the
    airline pays the traveller is set against that, not added to it.
    """
    policy = STORE.policy_for(booking.customer_id)
    allowance_rate = policy.allowance_per_day_eur if policy else 0
    ceiling = policy.max_goodwill_eur if policy else 0
    threshold = policy.approval_threshold_eur if policy else 0
    allowance = booking.days_affected * allowance_rate
    raw = booking.affected_value_eur + allowance
    capped = min(raw, ceiling)
    customer = STORE.customers.get(booking.customer_id)
    return {
        "booking_id": booking.id,
        "tier": customer.tier if customer else None,
        "services_not_delivered_eur": booking.affected_value_eur,
        "disruption_allowance_eur": allowance,
        "raw_estimate_eur": raw,
        "capped_estimate_eur": capped,
        "goodwill_ceiling_eur": ceiling,
        "approval_threshold_eur": threshold,
        "needs_human_approval": capped > threshold,
    }


def sync_summary(hotel: str) -> dict[str, Any]:
    """A channel-manager log, summarised the way an engineer would read it."""
    prop = STORE.properties.get(hotel)
    if prop is None:
        return {"error": f"No hotel named {hotel!r} on either channel manager."}
    events = [e for e in STORE.sync_log if e.hotel == prop.name]
    rejected = [e for e in events if e.status == "rejected"]
    summary: dict[str, Any] = {
        "hotel": prop.name,
        "channel_manager": prop.channel_manager,
        "window": f"{events[0].at:%d %b %H:%M} to {events[-1].at:%d %b %H:%M}" if events else None,
        "pushes": len(events),
        "accepted": len(events) - len(rejected),
        "rejected": len(rejected),
    }
    if rejected:
        summary["first_rejected"] = rejected[0].at.isoformat(timespec="minutes")
        summary["last_rejected"] = rejected[-1].at.isoformat(timespec="minutes")
        summary["error"] = rejected[0].detail
    return summary


def availability(city: str, rooms: int = 1) -> list[dict[str, Any]]:
    """Hotels in a city with rooms free, as the channel managers report them."""
    wanted = city.strip().lower()
    return [
        {
            "hotel": p.name,
            "free_rooms": p.free_rooms,
            "rate_eur": p.rate_eur,
            "fits_request": p.free_rooms >= rooms,
        }
        for p in STORE.properties.values()
        if p.city.lower() == wanted and p.free_rooms > 0
    ]


def open_slots(activity: str, after: datetime) -> list[dict[str, Any]]:
    """Future departures of an activity after a moment, with places left."""
    wanted = activity.strip().lower()
    return [
        {"activity": s.activity, "on": s.on.isoformat(timespec="minutes"), "places": s.places}
        for s in STORE.slots
        if s.activity.lower() == wanted and s.on > after
    ]


def window_bookings() -> list[Booking]:
    """Bookings taken while our channel-manager endpoint was rejecting availability."""
    return [b for b in STORE.bookings.values() if OUTAGE_STARTS <= b.booked_at <= OUTAGE_ENDS]


def _booking(booking_id: str) -> Booking | None:
    return STORE.bookings.get((booking_id or "").strip().upper())


# --------------------------------------------------------------------------
# The tools
# --------------------------------------------------------------------------


def lookup_booking(
    booking_id: Annotated[str, Field(description="Booking reference, e.g. BTA-26101")],
) -> dict[str, Any]:
    """Look up one booking by its reference: flights, hotels, activities, the incident and its severity."""
    booking = _booking(booking_id)
    if booking is None:
        return {"error": f"No booking {booking_id}"}
    STORE.record("tool:lookup_booking", "read", booking.id)
    return booking_record(booking)


def get_customer(
    customer_id: Annotated[str, Field(description="Customer reference, e.g. CUST-001")],
) -> dict[str, Any]:
    """Return a customer's profile, tier and annual spend with the agency."""
    customer = STORE.customers.get((customer_id or "").strip().upper())
    if customer is None:
        return {"error": f"No customer {customer_id}"}
    STORE.record("tool:get_customer", "read", customer.id)
    return {
        "id": customer.id,
        "name": customer.name,
        "country": customer.country,
        "tier": customer.tier,
        "kind": customer.kind,
        "annual_spend_eur": customer.annual_spend_eur,
    }


def check_availability(
    city: Annotated[str, Field(description="City to search, e.g. Jūrmala")],
    check_in: Annotated[str, Field(description="Check-in date, YYYY-MM-DD")],
    nights: Annotated[int, Field(description="Number of nights", ge=1)] = 1,
    rooms: Annotated[int, Field(description="Rooms needed", ge=1)] = 1,
) -> dict[str, Any]:
    """Search live hotel availability in a city through the channel managers."""
    STORE.record("tool:check_availability", "read", f"{city} {check_in} x{nights} nights, {rooms} rooms")
    return {"city": city, "check_in": check_in, "nights": nights, "rooms": rooms, "hotels": availability(city, rooms)}


def next_activity_slots(
    activity: Annotated[str, Field(description="Activity name, e.g. Sagrada Família guided tour")],
    from_date: Annotated[str, Field(description="Earliest date and time, YYYY-MM-DDTHH:MM")],
) -> dict[str, Any]:
    """List the next departures of an activity after a given moment, with places left."""
    try:
        after = datetime.fromisoformat(from_date)
    except ValueError:
        after = datetime.combine(date.fromisoformat(from_date[:10]), datetime.min.time())
    STORE.record("tool:next_activity_slots", "read", f"{activity} after {from_date}")
    return {"activity": activity, "slots": open_slots(activity, after)}


def get_sync_log(
    hotel: Annotated[str, Field(description="Hotel name exactly as on the booking")],
) -> dict[str, Any]:
    """Read the channel-manager availability sync log for one hotel, 14-16 September."""
    STORE.record("tool:get_sync_log", "read", hotel)
    return sync_summary(hotel)


def check_eu261(
    booking_id: Annotated[str, Field(description="Booking reference, e.g. BTA-26101")],
) -> dict[str, Any]:
    """Check a disrupted flight against EU261: distance band, eligibility, who pays, and care owed."""
    booking = _booking(booking_id)
    if booking is None:
        return {"error": f"No booking {booking_id}"}
    STORE.record("tool:check_eu261", "read", booking.id)
    return eu261(booking)


def screen_payment(
    booking_id: Annotated[str, Field(description="Booking reference, e.g. BTA-26104")],
) -> dict[str, Any]:
    """Screen a booking's payment for fraud flags and open chargebacks. Run it before any refund."""
    booking = _booking(booking_id)
    if booking is None:
        return {"error": f"No booking {booking_id}"}
    STORE.record("tool:screen_payment", "read", booking.id)
    return screening(booking)


def estimate_compensation(
    booking_id: Annotated[str, Field(description="Booking reference, e.g. BTA-26103")],
) -> dict[str, Any]:
    """Estimate what the agency owes: price reduction plus tier allowance, capped by the tier's goodwill ceiling."""
    booking = _booking(booking_id)
    if booking is None:
        return {"error": f"No booking {booking_id}"}
    result = exposure(booking)
    STORE.record("tool:estimate_compensation", "compute", f"{booking.id} -> EUR {result['capped_estimate_eur']}")
    return result


def list_open_incidents() -> list[dict[str, Any]]:
    """List every booking currently carrying an unresolved incident."""
    rows = STORE.open_incidents()
    STORE.record("tool:list_open_incidents", "read", f"{len(rows)} rows")
    return [
        {
            "id": b.id,
            "trip": b.trip,
            "incident": b.incident.value if b.incident else None,
            "severity": b.severity.value,
            "hotel": b.hotels[0].hotel if b.hotels else None,
            "booked_at": b.booked_at.isoformat(timespec="minutes"),
            "package_price_eur": b.package_price_eur,
        }
        for b in rows
    ]


def record_decision(
    booking_id: Annotated[str, Field(description="Booking the decision applies to")],
    decision: Annotated[str, Field(description="The decision taken, in one sentence")],
    amount_eur: Annotated[int, Field(description="Compensation or refund agreed, 0 if none", ge=0)] = 0,
) -> dict[str, Any]:
    """Write a resolution decision to the audit trail. This is the only write path."""
    entry = STORE.record("tool:record_decision", "decide", f"{booking_id}: {decision} (EUR {amount_eur})")
    return {"recorded_at": entry.at.isoformat(), "booking_id": booking_id, "amount_eur": amount_eur}


# --------------------------------------------------------------------------
# Tool sets, per role
# --------------------------------------------------------------------------

#: The tools handed to most case-handling agents.
CASE_TOOLS = [lookup_booking, get_customer, estimate_compensation, record_decision]

#: For the agent that opens a case: what the booking is, and who it belongs to.
INTAKE_TOOLS = [lookup_booking, get_customer]

#: Read-only, for an agent that grades a case rather than acting on it. A
#: classifier that could also write to the audit trail would be a classifier
#: with side effects, which is not what the routing pattern is demonstrating.
TRIAGE_TOOLS = [lookup_booking]

#: Handoff triage routes on the payment as well as the incident. The screening
#: verdict is not in the booking record - it lives in the payments system - so
#: an agent that should route a stolen card to risk has to be able to ask.
HANDOFF_TRIAGE_TOOLS = [lookup_booking, screen_payment]

FLIGHT_TOOLS = [lookup_booking, check_eu261]
HOTEL_TOOLS = [lookup_booking, check_availability, get_sync_log]
PAYMENT_TOOLS = [lookup_booking, screen_payment, estimate_compensation, record_decision]
PLANNER_TOOLS = [lookup_booking, check_availability, next_activity_slots, check_eu261]
ANALYSIS_TOOLS = [list_open_incidents, lookup_booking, get_sync_log]

#: For agents that write to or about a traveller with no one before them in
#: the conversation - the reflection loop's writer speaks first, so it has to
#: fetch the facts it is writing about or it can only guess.
LETTER_TOOLS = [lookup_booking, check_eu261]

#: Every tool in the demo, for the domain briefing to list. Nine read, one
#: writes - which is the property the audit trail depends on, so keep it true.
ALL_TOOLS = [
    lookup_booking,
    get_customer,
    check_availability,
    next_activity_slots,
    get_sync_log,
    check_eu261,
    screen_payment,
    estimate_compensation,
    list_open_incidents,
    record_decision,
]

