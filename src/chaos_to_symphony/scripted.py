"""A deterministic, offline chat client.

Why this exists
---------------
A conference demo that needs an LLM endpoint is a demo that fails on the
conference wifi. ``ScriptedChatClient`` implements the same
:class:`~agent_framework._clients.BaseChatClient` contract a real provider
implements, so every pattern in this repo runs identically with no key, no
network and no cost - and switching to Azure OpenAI is one env var.

It is not a mock in the test sense. It honours the three things the
orchestration patterns actually depend on:

1. **Text replies** shaped per persona, quoting real rows from the in-memory
   store, so the transcript reads like work rather than lorem ipsum.
2. **Tool calls**, including the ``handoff_to_*`` tools the handoff pattern
   generates at build time - without these, pattern 4 cannot route.
3. **Structured output**: when ``response_format`` names a Pydantic model the
   reply is a valid instance of that model, which is what the switch-case
   router and the reflection loop's reviewer parse.

Determinism comes from hashing the conversation rather than from a counter, so
two agents running concurrently cannot interfere with each other's script.

What it knows that a real model does not
----------------------------------------
It reads the store directly. A live model knows only what its prompt and its
tools tell it, which is why every agent that speaks first or steers the graph
is given a tool to fetch the facts - see ``CLAUDE.md``. Nothing here should be
read as evidence that a prompt works against Foundry; only a live run shows that.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import AsyncIterable, Awaitable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from agent_framework import ChatResponse, ChatResponseUpdate, Content, Message, ResponseStream
from agent_framework._clients import BaseChatClient
from agent_framework._tools import FunctionInvocationLayer

from .memory import STORE, TODAY, Booking, Customer, IncidentKind, Severity
from .tools import (
    availability,
    disrupted_flight,
    eu261,
    exposure,
    open_slots,
    screening,
    sync_summary,
    window_bookings,
)

# --------------------------------------------------------------------------
# Shared conversation context
# --------------------------------------------------------------------------
#
# Agents built with ``require_per_service_call_history_persistence=True`` - which
# HandoffBuilder requires - are sent only the newest turn, because a real
# provider keeps the thread server-side. This module-level list is that
# server-side thread: whatever any scripted client is shown gets remembered, so
# a specialist receiving a handed-off case can still see which booking it is
# about. Reset between demo runs by :func:`reset_context`.
_SHARED_CONTEXT: list[str] = []


def reset_context() -> None:
    """Forget the shared thread. Called at the start of each demo run."""
    _SHARED_CONTEXT.clear()


def _remember(messages: Sequence[Message]) -> None:
    for message in messages:
        text = _text_of(message)
        if text and text not in _SHARED_CONTEXT:
            _SHARED_CONTEXT.append(text)

# --------------------------------------------------------------------------
# Persona voices
# --------------------------------------------------------------------------

#: Opening line per persona. Keyed by a substring of the agent name so that
#: "intake", "Intake Agent" and "triage-intake" all resolve to the same voice.
_VOICES: dict[str, str] = {
    "intake": "Logged the incident and pulled the booking.",
    "triage": "Graded the incident against the severity matrix.",
    "planner": "Rebuilt the itinerary around the change.",
    "flight": "Checked the disruption against EU261.",
    "hotel": "Checked the stay with the hotel and the channel manager.",
    "billing": "Reconciled the payment and the refund position.",
    "risk": "Ran the payment through fraud and chargeback screening.",
    "pricing": "Priced the exposure against the customer's tier.",
    "cost": "Modelled what this incident costs us.",
    "cost-agent": "Totalled the exposure across the affected bookings.",
    "legal": "Reviewed our liability as package organiser.",
    "ops": "Worked out where the travellers sleep tonight.",
    "account": "Weighed the customer relationship.",
    "care-manager": "Chairing the customer-care committee.",
    "manager": "Coordinated the investigation across the specialists.",
    "researcher": "Pulled the incidents that match the question.",
    "analyst": "Compared the channel-manager sync logs across the affected hotels.",
    "summar": "Reconciled this month's supplier invoices against what we booked.",
    "aggregat": "Aggregated the individual assessments.",
    "settle": "Proposed a settlement figure for sign-off.",
    "approver": "Assessed whether this needs human sign-off.",
    "writer": "Drafted the customer letter.",
    "letter-writer": "Drafted the customer letter.",
    "review": "Reviewed the draft against the letter policy.",
    "availability": "Confirmed live availability with the channel manager.",
}

_FALLBACK_VOICE = "Reviewed the case and recorded a position."

#: What a human-in-the-loop send-back says when it lands back in the agent's
#: conversation. It lives here, rather than in the runner that sends it, because
#: this client is the thing that has to *recognise* it: a real model re-prices
#: because it reads the sentence, and the offline stand-in has to do the same or
#: the approval gate looks broken - the same figure comes back forever and the
#: "Send back" button appears to do nothing. ``runner`` imports this constant so
#: the wording it sends and the wording matched here cannot drift apart.
SEND_BACK_INSTRUCTION = (
    "Sent back by the duty manager. Re-price this settlement at or below the approval threshold."
)


def _persona_key(name: str) -> str:
    """Map an agent name onto a persona voice key, most specific first.

    Longest match rather than first match: "care-manager" contains both
    "care-manager" and "manager", and "letter-writer" contains "writer". Taking
    whichever happened to be declared first in the dict would let dictionary
    order silently decide an agent's voice - the committee chair would answer
    as the Magentic manager. The longer key is the more specific one, so it wins.
    """
    lowered = (name or "").lower()
    matches = [key for key in _VOICES if key in lowered]
    return max(matches, key=len) if matches else "_fallback"


def _voice(name: str) -> str:
    return _VOICES.get(_persona_key(name), _FALLBACK_VOICE)


# --------------------------------------------------------------------------
# Deterministic content
# --------------------------------------------------------------------------


def _seed_of(messages: Sequence[Message], persona: str) -> int:
    """A stable integer seed from the conversation plus the persona.

    Hashing (rather than an instance counter) keeps parallel participants
    independent: the concurrent pattern runs three agents at once and each
    must produce its own stable answer regardless of scheduling order.
    """
    blob = persona + "||" + "||".join(_text_of(m) for m in messages[-6:])
    return int(hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12], 16)


def _text_of(message: Message) -> str:
    """Best-effort plain text for a message, whatever its content shape."""
    text = getattr(message, "text", None)
    if isinstance(text, str) and text:
        return text
    parts: list[str] = []
    for content in getattr(message, "contents", None) or []:
        if isinstance(content, str):
            parts.append(content)
        else:
            value = getattr(content, "text", None)
            if isinstance(value, str):
                parts.append(value)
    return " ".join(parts)


def _conversation_text(messages: Sequence[Message]) -> str:
    return "\n".join(_text_of(m) for m in messages)


def _last_user_text(messages: Sequence[Message]) -> str:
    """The newest prompt this agent was given - not the thread it sits on top of."""
    for message in reversed(messages):
        if str(getattr(message, "role", "")).endswith("user"):
            text = _text_of(message)
            if text:
                return text
    return _text_of(messages[-1]) if messages else ""


def _referenced_booking(messages: Sequence[Message]) -> Booking:
    """Find the booking the conversation is about, falling back to the worst open one."""
    found = STORE.booking_in(_conversation_text(messages) + "\n" + "\n".join(_SHARED_CONTEXT))
    if found is not None:
        return found
    ranked = sorted(
        STORE.open_incidents(),
        key=lambda b: (b.severity != Severity.CRITICAL, -b.package_price_eur),
    )
    return ranked[0] if ranked else next(iter(STORE.bookings.values()))


_RANK_RE = re.compile(r"^\s*1\.\s*(SI-\d{4}.*)$", re.MULTILINE)
_TOTAL_RE = re.compile(r"TOTAL EUR ([\d,]+) across (\d+) invoices")


def _top_ranked_row(messages: Sequence[Message]) -> str | None:
    """The first row of a ranked dispute list, if one was handed to this agent."""
    match = _RANK_RE.search(_conversation_text(messages))
    return match.group(1).strip() if match else None


def _prior_speakers(messages: Sequence[Message]) -> int:
    """How many agent turns are already in this thread."""
    return sum(1 for m in messages if str(getattr(m, "role", "")).endswith("assistant"))


#: The offer line this client writes, and reads back on the next round.
_OFFER_RE = re.compile(r"Settlement offer EUR ([\d,]+)")


def _settlement_offer(booking: Booking, messages: Sequence[Message]) -> tuple[int, int | None]:
    """The figure to put on the table now, and the one it replaces (None if first).

    Opens at the capped estimate - services not delivered plus the tier's
    allowance - and concedes 40% per send-back down to the approval threshold.

    It concedes from *the last offer in the transcript* rather than from a count
    of send-backs. Resuming a suspended workflow re-sends the gated agent's
    request, and the thread the agent then sees can carry a duplicated proposal
    or one send-back fewer than actually happened - so a count drifts, and a
    settlement figure that drifts back upwards in front of the approver is worse
    than no figure at all. The newest offer is always in the transcript to
    concede from, however the bookkeeping shook out.
    """
    estimate = exposure(booking)
    opening = estimate["capped_estimate_eur"]
    floor = estimate["approval_threshold_eur"]

    text = _conversation_text(messages)
    offers = _OFFER_RE.findall(text)
    if not offers or SEND_BACK_INSTRUCTION not in text:
        return opening, None
    previous = int(offers[-1].replace(",", ""))
    return max(floor, int(previous * 0.6)), previous


# --------------------------------------------------------------------------
# Facts, phrased
# --------------------------------------------------------------------------


def _customer(booking: Booking) -> Customer | None:
    return STORE.customers.get(booking.customer_id)


def _who(customer: Customer | None) -> str:
    """How a customer reads mid-sentence: "the Kalniņš family", "J. Miller"."""
    if customer is None:
        return "an unknown customer"
    return f"the {customer.name}" if customer.name.endswith("family") else customer.name


def _cap(text: str) -> str:
    """Upper-case the first letter only. str.capitalize() lower-cases the rest - "Mārtiņš" too."""
    return text[:1].upper() + text[1:]


def _greeting(customer: Customer | None) -> str:
    if customer is None:
        return "Dear traveller,"
    if customer.kind == "group":
        return f"Dear parents and staff of {customer.name},"
    if customer.kind == "corporate":
        return f"Dear colleagues at {customer.name},"
    return f"Dear {customer.name},"


def _day(value: datetime | Any) -> str:
    """"26 September" - how a letter writes a date."""
    return f"{value.day} {value:%B}"


_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}


def _count(n: int) -> str:
    return _WORDS.get(n, str(n))


def _base_line(booking: Booking) -> str:
    n = booking.travellers
    return (
        f"{booking.id} - {booking.trip} ({booking.route}) for {_who(_customer(booking))}, "
        f"{n} traveller{'s' if n != 1 else ''}."
    )


def _replan(booking: Booking) -> dict[str, Any] | None:
    """What a later outbound flight does to the rest of the trip.

    Hotel nights before the new arrival are released and refunded, and any
    activity that now falls before landing moves to the earliest departure with
    room for the whole party. None when the booking has no rebooked outbound.
    """
    flight = disrupted_flight(booking)
    if flight is None or flight.rebooked_departs is None or not booking.flights or flight is not booking.flights[0]:
        return None
    arrival = flight.rebooked_departs + (flight.arrives - flight.departs)

    released: list[tuple[str, int, int]] = []
    for stay in booking.hotels:
        lost = (arrival.date() - stay.check_in).days
        if lost > 0:
            released.append((stay.hotel, lost, lost * stay.rooms * stay.rate_eur))

    moved: list[tuple[str, datetime | None]] = []
    for activity in booking.activities:
        if activity.on < arrival:
            options = [s for s in open_slots(activity.name, arrival) if s["places"] >= activity.pax]
            moved.append((activity.name, datetime.fromisoformat(options[0]["on"]) if options else None))
    return {"flight": flight, "arrival": arrival, "released": released, "moved": moved}


def _replan_sentences(plan: dict[str, Any], *, letter: bool = False) -> list[str]:
    """The re-plan, as the planner reports it or as a letter tells it."""
    out: list[str] = []
    for hotel, nights, refund in plan["released"]:
        night_word = "night" if nights == 1 else f"{nights} nights"
        if letter:
            out.append(
                f"We have released your first {night_word} at {hotel} and are refunding "
                f"{'it' if nights == 1 else 'them'} to you, EUR {refund:,}, by {_day(TODAY + timedelta(days=10))}."
            )
        else:
            out.append(f"First {night_word} at {hotel} released without charge (EUR {refund:,} refunded).")
    for name, slot in plan["moved"]:
        if slot is None:
            out.append(f"No later {name} departure has room for the group; refund it.")
        elif letter:
            out.append(f"Your {name} moves to {_day(slot)} at {slot:%H:%M}.")
        else:
            out.append(f"{name} moved to {slot:%d %b at %H:%M} - the earlier slots are before landing or too small.")
    return out


def _eu261_sentence(booking: Booking, *, letter: bool = False) -> str | None:
    """EU261 in one sentence - always naming who pays, because that is what people get wrong."""
    result = eu261(booking)
    if not result.get("applies"):
        return None
    if result["eligible"]:
        if letter:
            return (
                f"Under EU261 {result['payable_by']} owes each of you EUR {result['per_passenger_eur']}, "
                f"EUR {result['total_eur']:,} in all, and we will file the claim for you by "
                f"{_day(TODAY + timedelta(days=7))}."
            )
        care = f" Care owed now: {', '.join(result['care_owed'])}." if result["care_owed"] else ""
        return (
            f"{result['flight']} ({result['distance_km']:,} km): {result['reason']} {result['payable_by']} owes "
            f"EUR {result['per_passenger_eur']} per passenger, EUR {result['total_eur']:,} in all - we file the "
            f"claim; we do not pay it.{care}"
        )
    if letter:
        care = " and ".join(result["care_owed"])
        return result["reason"] + (f" The airline still owes you {care}." if care else "")
    return (
        f"{result['flight']} ({result['distance_km']:,} km): {result['reason']} "
        f"Care still owed: {', '.join(result['care_owed']) or 'none'}."
    )


# --------------------------------------------------------------------------
# Letters
# --------------------------------------------------------------------------

_SIGN_OFF = "With apologies for the disruption, Baltic Travel Agency, Riga."


def _activity_body(booking: Booking) -> list[str]:
    """A cancelled excursion, said to the traveller - never the internal note pasted in.

    The first sentence follows the greeting's comma, so it starts lower-case:
    the letter is one line in the log, and "Dear Andris, The operator" reads
    like the mistake it would be on paper.
    """
    cancelled = next((a for a in booking.activities if a.cancelled_because or booking.incident), None)
    if cancelled is None:
        return ["thank you for travelling with us."]
    reason = f" {cancelled.cancelled_because}" if cancelled.cancelled_because else ""
    out = [f"the operator has cancelled your {cancelled.name} on {_day(cancelled.on)}{reason}."]
    if booking.refund_due:
        out.append(f"We are refunding EUR {booking.affected_value_eur:,} to your card by {_day(booking.refund_due)}.")
    else:
        out.append(f"We will confirm your refund by {_day(TODAY + timedelta(days=7))}.")
    return out


def _letter(booking: Booking, messages: Sequence[Message]) -> str:
    """A complete, compliant letter: dated promises, the right payer, no admission."""
    customer = _customer(booking)
    parts = [f"Booking {booking.id}.", _greeting(customer)]
    flight = disrupted_flight(booking)
    plan = _replan(booking)

    if flight is not None and flight.status == "cancelled":
        parts.append(
            f"{flight.carrier} has cancelled {flight.number} on {_day(flight.departs)} because of {flight.cause}."
        )
        if plan is not None:
            parts.append(
                f"You are now on the same flight on {_day(flight.rebooked_departs)}, landing at "
                f"{plan['arrival']:%H:%M}."
            )
            parts.extend(_replan_sentences(plan, letter=True))
        parts.append(
            "You keep the choice of the rebooked flight or a full refund of your flights; tell us by "
            f"{_day(TODAY + timedelta(days=1))}."
        )
        sentence = _eu261_sentence(booking, letter=True)
        if sentence:
            parts.append(sentence)
    elif booking.incident in (IncidentKind.OVERBOOKED, IncidentKind.NOT_AS_BOOKED):
        offers = _OFFER_RE.findall(_conversation_text(messages))
        figure = int(offers[-1].replace(",", "")) if offers else exposure(booking)["capped_estimate_eur"]
        stay = booking.hotels[0] if booking.hotels else None
        where = f"at {stay.hotel} " if stay else ""
        parts.append(f"we are sorry that your stay {where}was not the one you booked.")
        parts.append(f"We are refunding EUR {figure:,} to your card by {_day(TODAY + timedelta(days=10))}.")
    else:
        parts.extend(_activity_body(booking))

    parts.append(_SIGN_OFF)
    return " ".join(parts)


_REVISION_RE = re.compile(r"This is revision (\d+)")


def _reflection_draft(booking: Booking, messages: Sequence[Message]) -> str:
    """The reflection loop's drafts, each wrong in the way a first draft usually is.

    Revision 1 of a cancelled-flight letter promises the EU261 money, because
    that is what the traveller expects to hear - and on an air traffic control
    strike it is not owed. Revision 2 takes the promise out and forgets the
    rights that are owed. Revision 3 gets both. A letter whose remedy waits on a
    supplier can never put a date on it, however often it is rewritten, which
    is the case the loop's ceiling exists for. Anything else is right first time.
    """
    match = _REVISION_RE.search(_last_user_text(messages))
    revision = int(match.group(1)) if match else 1
    customer = _customer(booking)
    parts = [f"Booking {booking.id}.", _greeting(customer)]
    result = eu261(booking)
    flight = disrupted_flight(booking)

    if booking.awaiting_supplier:
        stay = booking.hotels[0] if booking.hotels else None
        hotel = stay.hotel if stay else "the hotel"
        refund = stay.nights * stay.rooms * stay.rate_eur if stay else booking.affected_value_eur
        parts.append(f"{hotel} could not honour your booking and moved you to another hotel.")
        if revision == 1:
            parts.append(f"We will refund the EUR {refund:,} you paid once the hotel confirms.")
        else:
            parts.append(f"We have chased {hotel} and will refund your EUR {refund:,} as soon as the hotel confirms.")
    elif flight is not None and flight.status == "cancelled" and result.get("applies"):
        parts.append(
            f"{flight.carrier} has cancelled {flight.number} on {_day(flight.departs)} because of {flight.cause}."
        )
        rights = "You can choose the next available flight or a full refund of your flights"
        rights += (
            ", and the airline must provide meals and refreshments while you wait."
            if any("meals" in c for c in result["care_owed"])
            else "."
        )
        if revision == 1 and not result["eligible"]:
            parts.append(rights)
            parts.append(
                f"You are entitled to EUR {result['band_eur']} compensation under EU261, which we will pay by "
                f"{_day(TODAY + timedelta(days=7))}."
            )
        elif revision == 2 and not result["eligible"]:
            parts.append(f"{result['reason']}")
        else:
            parts.append(rights)
            sentence = _eu261_sentence(booking, letter=True)
            if sentence:
                parts.append(sentence)
        stay = booking.hotels[0] if booking.hotels else None
        if stay:
            parts.append(
                f"We have asked {stay.hotel} to hold your room from the day you arrive and will confirm it by "
                f"{_day(TODAY + timedelta(days=1))}."
            )
    else:
        parts.extend(_activity_body(booking))

    parts.append(_SIGN_OFF)
    return " ".join(parts)


_LIABILITY = ("our fault", "we accept liability", "we admit")


def _review(draft: str, booking: Booking) -> tuple[str, str]:
    """Apply the letter policy to one draft. Returns (decision, reason).

    The same four rules the reviewer's prompt gives a live model, checked in
    the same order, so an offline run ends where the policy says it should
    rather than wherever a hash happened to fall.
    """
    result = eu261(booking)
    lowered = draft.lower()
    if result.get("applies") and not result["eligible"] and "entitled to eur" in lowered:
        return "revise", f"It promises EU261 compensation that is not owed. {result['reason']}"
    meals_owed = any("meals" in c for c in result.get("care_owed", ()))
    if result.get("applies") and result["disruption"] == "cancelled" and (
        "full refund" not in lowered or (meals_owed and "meals" not in lowered)
    ):
        return "revise", (
            "It leaves out the traveller's rights on a cancelled flight: the choice of rerouting or a full "
            "refund" + (", and meals while they wait." if meals_owed else ".")
        )
    if "hotel confirms" in lowered:
        return "revise", (
            "It promises a refund with no date on it. Every commitment in a letter needs one - and this one "
            "depends on a hotel that has not answered."
        )
    if any(phrase in lowered for phrase in _LIABILITY):
        return "revise", "It admits liability. State what happened; do not say whose fault it was."
    return "approve", "Accurate, dated, names the right payer, and admits nothing beyond the facts."


# --------------------------------------------------------------------------
# Composition, per persona
# --------------------------------------------------------------------------


def _largest_dispute(row: str) -> str:
    """The top invoice in the dispute list, said the way finance needs to hear it: what is wrong."""
    invoice = STORE.invoices.get(row.split()[0])
    if invoice is None:
        return f"Largest: {row}"
    unit = invoice.item.rsplit(", ", 1)[-1]
    reasons: list[str] = []
    if invoice.qty_invoiced > invoice.qty_delivered:
        extra = invoice.qty_invoiced - invoice.qty_delivered
        reasons.append(f"billed {_count(extra)} {unit} it never delivered")
    if invoice.rate_invoiced_eur > invoice.rate_contracted_eur:
        reasons.append(
            f"charged EUR {invoice.rate_invoiced_eur} against a contracted EUR {invoice.rate_contracted_eur}"
        )
    if invoice.commission_invoiced_pct < invoice.commission_contracted_pct:
        reasons.append(
            f"deducted {invoice.commission_invoiced_pct}% commission against the agreed "
            f"{invoice.commission_contracted_pct}%"
        )
    booking = STORE.bookings.get(invoice.booking_id)
    walked = booking is not None and booking.incident is IncidentKind.OVERBOOKED and invoice.qty_delivered == 0
    why = " and ".join(reasons) + (" - for the family it walked to another hotel" if walked else "")
    return f"The largest is {invoice.id}: {invoice.supplier} {why}."


def _magentic_manager(messages: Sequence[Message]) -> str:
    """The Magentic manager answers whichever of its four prompts it was just given."""
    prompt = _last_user_text(messages)
    incidents = window_bookings()
    if "pre-survey" in prompt:
        return (
            "GIVEN OR VERIFIED FACTS: three hotels have walked or downgraded our guests since mid-September. "
            "FACTS TO LOOK UP: which bookings they are, which channel manager connects each hotel, and what the "
            "sync logs show. EDUCATED GUESSES: one sync failure is likelier than three unrelated hotel errors."
        )
    if "We have completed the task" not in prompt:
        return (
            "Plan: 1. researcher-agent lists the open hotel incidents and when each was booked. "
            "2. analyst-agent reads the channel-manager sync logs for those hotels and names a root cause. "
            "3. cost-agent totals the exposure across the affected bookings."
        )
    logs = [sync_summary(b.hotels[0].hotel) for b in incidents if b.hotels]
    rejected = [log for log in logs if log.get("rejected")]
    first = min((log["first_rejected"] for log in rejected), default="")
    last = max((log["last_rejected"] for log in rejected), default="")
    total = sum(exposure(b)["capped_estimate_eur"] for b in incidents)
    listed = ", ".join(f"{b.id} at {b.hotels[0].hotel}" for b in incidents if b.hotels)
    return (
        f"{_voice('manager')} Root cause found. Every property on our SiteMinder connection shows availability "
        f"pushes rejected by our own endpoint between {first[11:]} and {last[11:]} on 15 September; properties "
        f"on D-EDGE show none. The {_count(len(incidents))} hotel incidents - {listed} - were all booked inside that "
        "window, from availability the hotels had already withdrawn. The hotels are not at fault. Exposure: "
        f"EUR {total:,} across the {_count(len(incidents))} bookings. Fix the credential rotation that broke our "
        "endpoint, and alert on the first rejected push rather than the third complaint."
    )


def _compose(persona: str, messages: Sequence[Message]) -> str:
    """Build a persona-flavoured reply that quotes real store data."""
    booking = _referenced_booking(messages)
    customer = _customer(booking)
    key = _persona_key(persona)
    tier = customer.tier if customer else "unknown"
    estimate = exposure(booking)

    if key == "manager":
        return _magentic_manager(messages)
    if key == "writer":
        return _letter(booking, messages)
    if key == "letter-writer":
        return _reflection_draft(booking, messages)

    head = _voice(persona)
    facts: list[str] = [_base_line(booking)]

    if key == "intake":
        if booking.notes:
            facts.append(booking.notes)
        facts.append(f"{_cap(_who(customer))} is on the {tier} tier.")
    elif key == "planner":
        plan = _replan(booking)
        if plan is None:
            facts.append("No outbound change on this booking; the itinerary stands.")
        else:
            flight = plan["flight"]
            facts.append(
                f"New arrival: {flight.number} on {flight.rebooked_departs:%d %b}, landing {plan['arrival']:%H:%M}."
            )
            facts.extend(_replan_sentences(plan))
            sentence = _eu261_sentence(booking)
            if sentence:
                facts.append(sentence)
    elif key == "flight":
        sentence = _eu261_sentence(booking)
        facts.append(sentence or "No flight on this booking was disrupted - this is not a flights case.")
        result = eu261(booking)
        if result.get("applies") and result["disruption"] == "cancelled":
            facts.append("Rights: the choice of rerouting or a full refund.")
    elif key == "hotel":
        stay = booking.hotels[0] if booking.hotels else None
        if booking.notes:
            facts.append(booking.notes)
        if stay and booking in window_bookings():
            log = sync_summary(stay.hotel)
            facts.append(
                f"Booked {booking.booked_at:%d %b at %H:%M}, while our {log['channel_manager']} endpoint was "
                f"rejecting availability pushes ({log.get('first_rejected', '')[11:]}-"
                f"{log.get('last_rejected', '')[11:]}) - the hotel had already sold the room elsewhere."
            )
        if stay:
            spare = [h for h in availability(stay.city) if h["hotel"] != stay.hotel]
            if spare:
                facts.append(
                    "Space nearby: "
                    + "; ".join(f"{h['hotel']} ({h['free_rooms']} rooms, EUR {h['rate_eur']})" for h in spare)
                    + "."
                )
    elif key == "billing":
        verdict = screening(booking)
        if not verdict["cleared"]:
            facts.append(f"Payment does not clear screening - {verdict['reason']} This is not a refund for billing.")
        else:
            by = f" by {_day(booking.refund_due)}" if booking.refund_due else ""
            facts.append(
                f"Payment cleared screening. Refund EUR {booking.affected_value_eur:,} for the services not "
                f"delivered to the original card{by}, plus the EUR {estimate['disruption_allowance_eur']:,} "
                f"{tier}-tier allowance: EUR {estimate['capped_estimate_eur']:,} in all."
            )
    elif key == "risk":
        verdict = screening(booking)
        if verdict["action"] == "freeze_and_escalate_to_risk":
            facts.append(
                f"Payment FAILS screening: {verdict['reason']} Booking frozen. No refund to any card until "
                "risk has reviewed it - a refund to a different card is itself a fraud indicator."
            )
        elif verdict["action"] == "do_not_refund_directly":
            facts.append(f"Chargeback already open: {verdict['reason']} The bank's process returns the money.")
        else:
            facts.append("Payment cleared screening; nothing here for risk.")
    elif key == "pricing":
        plan = _replan(booking)
        if plan is not None:
            facts.extend(_replan_sentences(plan))
        facts.append(
            f"Services not delivered come to EUR {estimate['services_not_delivered_eur']:,}; the "
            f"{tier}-tier allowance adds EUR {estimate['disruption_allowance_eur']:,}, so EUR "
            f"{estimate['raw_estimate_eur']:,} against a EUR {estimate['goodwill_ceiling_eur']:,} ceiling. "
            "Nothing above that."
        )
    elif key == "cost":
        capped = " (capped)" if estimate["raw_estimate_eur"] > estimate["capped_estimate_eur"] else ""
        facts.append(
            f"Cost to us: EUR {estimate['services_not_delivered_eur']:,} in services not delivered plus a EUR "
            f"{estimate['disruption_allowance_eur']:,} allowance ({tier} tier) - EUR "
            f"{estimate['capped_estimate_eur']:,}{capped}."
        )
        if booking.incident is IncidentKind.OVERBOOKED:
            facts.append("Relocation recoverable from the hotel under the allotment contract.")
    elif key == "cost-agent":
        rows = [(b.id, exposure(b)["capped_estimate_eur"]) for b in window_bookings()]
        facts = [
            "Exposure: " + ", ".join(f"{bid} EUR {amount:,}" for bid, amount in rows)
            + f" - EUR {sum(a for _, a in rows):,} in all, before anything is recovered from the hotels."
        ]
    elif key == "legal":
        if booking.incident in (IncidentKind.OVERBOOKED, IncidentKind.NOT_AS_BOOKED):
            facts.append(
                "As package organiser we are liable for every component under the Package Travel Directive, so "
                f"a price reduction of at least EUR {booking.affected_value_eur:,} is owed."
            )
            if booking in window_bookings():
                cost = (
                    "Relocation is our cost - it is not recoverable under the allotment contract."
                    if booking.incident is IncidentKind.OVERBOOKED
                    else "The reduction is ours to pay - there is nothing to recover from the hotel."
                )
                facts.append(
                    "And the hotel is not at fault: it closed the room, and our own SiteMinder endpoint rejected "
                    f"the update. {cost}"
                )
            facts.append("Admit nothing in writing beyond the price reduction.")
        elif disrupted_flight(booking) is not None:
            facts.append(
                "We owe a price reduction for what the family did not get; anything the airline pays under EU261 "
                "is set against it, not added to it."
            )
        else:
            facts.append(f"Liability limited to the services not delivered: EUR {booking.affected_value_eur:,}.")
    elif key == "ops":
        stay = booking.hotels[0] if booking.hotels else None
        if stay and booking.rooms_short:
            spare = [h for h in availability(stay.city) if h["hotel"] != stay.hotel]
            free = sum(h["free_rooms"] for h in spare)
            facts.append(
                f"We are {booking.rooms_short} rooms short at {stay.hotel}. "
                + "; ".join(f"{h['hotel']} has {h['free_rooms']} at EUR {h['rate_eur']}" for h in spare)
                + f" - {free} rooms between them, so split the group and run a shuttle to {stay.hotel} for the "
                "programme."
            )
        else:
            facts.append("Nobody needs rehousing; the itinerary holds.")
    elif key == "account":
        spend = f"EUR {customer.annual_spend_eur:,}" if customer else "an unknown amount"
        facts.append(f"{_cap(_who(customer)) if customer else 'They'} spend {spend} a year with us, {tier} tier.")
        if "honeymoon" in booking.trip.lower():
            facts.append("And this was their honeymoon: they will tell this story for years, to everyone.")
        facts.append("A figure that looks mean will cost more than it saves.")
    elif key == "care-manager":
        # A chair opens by framing the decision and closes by naming a number.
        # Both are the same persona, and the transcript is what tells them
        # apart - which is also what lets the committee's termination condition
        # fire on the summing-up rather than on the first specialist to mention
        # money in passing.
        if _prior_speakers(messages) < 2:
            # Not a figure anywhere in the opening, deliberately: the committee
            # stops when the chair names a number, so the chair naming one
            # while framing the question would end the meeting before it began.
            facts.append(
                f"Decision before the committee: what to offer {_who(customer)} ({tier} tier) for a stay that "
                "was not what they booked. Specialists to state their positions first."
            )
        else:
            facts.append(
                f"The specialists have spoken. Settlement agreed at EUR {estimate['capped_estimate_eur']:,}, "
                f"within the EUR {estimate['goodwill_ceiling_eur']:,} ceiling for the {tier} tier."
            )
    elif key == "researcher":
        rows = [
            b for b in STORE.open_incidents()
            if b.incident in (IncidentKind.OVERBOOKED, IncidentKind.NOT_AS_BOOKED) and b.hotels
        ]
        facts = [
            f"{_cap(_count(len(rows)))} hotel incidents on the book: "
            + "; ".join(
                f"{b.id} {b.hotels[0].hotel} ({b.incident.value}, booked {b.booked_at:%d %b %H:%M})" for b in rows
            )
            + ". All three hotels connect to us through "
            + ", ".join(sorted({STORE.properties[b.hotels[0].hotel].channel_manager for b in rows}))
            + "."
        ]
    elif key == "analyst":
        rows = window_bookings()
        logs = [sync_summary(b.hotels[0].hotel) for b in rows if b.hotels]
        clean = [p.name for p in STORE.properties.values() if p.channel_manager != "SiteMinder"]
        first = min((log.get("first_rejected", "") for log in logs), default="")[11:]
        last = max((log.get("last_rejected", "") for log in logs), default="")[11:]
        facts = [
            f"Every SiteMinder property shows availability pushes rejected by our own endpoint "
            f"({logs[0]['error'] if logs and logs[0].get('error') else 'no error recorded'}) between {first} and "
            f"{last} on 15 Sep; the {_count(len(clean))} D-EDGE properties show none. All three incidents were booked "
            f"inside that window ({', '.join(f'{b.booked_at:%H:%M}' for b in rows)}). Root cause: our SiteMinder "
            "connection, not the hotels - they closed the rooms, and we never heard."
        ]
    elif key in {"summar", "aggregat"}:
        # When the upstream node handed us a ranked list, lead with its top row
        # rather than inventing a headline. A summariser that ignores its input
        # is the single most common way these demos look fake.
        top = _top_ranked_row(messages)
        total = _TOTAL_RE.search(_conversation_text(messages))
        if top:
            lead = (
                f"Dispute {_count(int(total.group(2)))} invoices worth EUR {total.group(1)} before month-end."
                if total
                else ""
            )
            return f"{head} {lead} {_largest_dispute(top)}".replace("  ", " ")
    elif key == "settle":
        figure, previous = _settlement_offer(booking, messages)
        facts.append(
            f"Settlement offer EUR {figure:,} against a EUR {estimate['goodwill_ceiling_eur']:,} goodwill ceiling "
            f"({tier} tier); approval threshold EUR {estimate['approval_threshold_eur']:,}."
        )
        if previous is not None and figure < previous:
            facts.append(f"Re-priced after send-back: down from EUR {previous:,}.")
        elif previous is not None:
            facts.append(
                f"Held at the approval threshold EUR {estimate['approval_threshold_eur']:,}; no further "
                "concession available."
            )
    elif key == "approver":
        facts.append(f"Anything above EUR {estimate['approval_threshold_eur']:,} on this tier needs a named approver.")

    # The Magentic specialists answer about the whole book, not one booking,
    # so a severity line would describe whichever booking the fallback chose.
    if key not in {"researcher", "analyst", "cost-agent"}:
        facts.append(f"Severity on file: {booking.severity.value}.")
    return head + " " + " ".join(facts)


# --------------------------------------------------------------------------
# Structured output
# --------------------------------------------------------------------------


def _synthesise_model(model: Any, messages: Sequence[Message], persona: str) -> str:
    """Produce a JSON string that validates against a Pydantic response_format.

    Field values are chosen from the conversation where the field name gives a
    usable hint, and from the schema's own constraints otherwise, so the router
    in pattern 6 gets a decision it can actually branch on. The reflection
    loop's reviewer is the exception: its verdict is the letter policy applied
    to the draft in front of it, not a guess.
    """
    rng = random.Random(_seed_of(messages, persona))
    booking = _referenced_booking(messages)
    fields = getattr(model, "model_fields", None)
    if not fields:
        return json.dumps({"result": _compose(persona, messages)}, ensure_ascii=False)

    if _persona_key(persona) == "review" and "decision" in fields:
        decision, reason = _review(_last_user_text(messages), booking)
        return json.dumps({"decision": decision, "reason": reason}, ensure_ascii=False)

    payload: dict[str, Any] = {}
    for name, info in fields.items():
        annotation = info.annotation
        choices = getattr(annotation, "__args__", None)
        literals = [c for c in (choices or []) if isinstance(c, str)]
        lowered = name.lower()

        if literals:
            # A Literal[...] field: pick the option the booking actually implies.
            payload[name] = _pick_literal(literals, booking, rng)
        elif annotation is bool:
            payload[name] = booking.severity in (Severity.HIGH, Severity.CRITICAL)
        elif annotation is int:
            payload[name] = _int_for(lowered, booking, rng)
        elif annotation is float:
            payload[name] = round(rng.uniform(0.55, 0.97), 2)
        elif "id" in lowered:
            payload[name] = booking.id
        else:
            payload[name] = _compose(persona, messages)
    return json.dumps(payload, ensure_ascii=False)


def _pick_literal(literals: list[str], booking: Booking, rng: random.Random) -> str:
    """Choose the Literal option the booking's own data points at."""
    severity = booking.severity.value
    incident = booking.incident.value if booking.incident else "none"
    for candidate in literals:
        low = candidate.lower()
        if low == severity or low == incident:
            return candidate
    # Severity-ordered fallback so criticals never land on the benign branch.
    if booking.severity in (Severity.CRITICAL, Severity.HIGH):
        for candidate in literals:
            if any(w in candidate.lower() for w in ("escalate", "high", "critical", "urgent", "review", "hold")):
                return candidate
    return literals[rng.randrange(len(literals))]


def _int_for(field_name: str, booking: Booking, rng: random.Random) -> int:
    """A plausible integer for a named field."""
    if "score" in field_name or "confidence" in field_name:
        return rng.randint(55, 98)
    if "eur" in field_name or "amount" in field_name or "cost" in field_name or "value" in field_name:
        return exposure(booking)["capped_estimate_eur"]
    if "day" in field_name:
        return rng.randint(1, 7)
    return rng.randint(1, 10)


# --------------------------------------------------------------------------
# Magentic progress ledger
# --------------------------------------------------------------------------
#
# The Magentic manager does not chat: on every round it must return a strict
# JSON progress ledger, and a reply it cannot parse costs a retry, then a reset.
# Offline we answer that contract directly - cycling each specialist in once,
# then declaring the request satisfied so the loop ends on merit rather than on
# the max_round_count backstop.

_LEDGER_MARKER = "is_request_satisfied"
_NAMES_RE = re.compile(r"select from:\s*([^)\n]+)\)")


def _ledger_candidates(blob: str) -> list[str]:
    """The participant names the manager is allowed to pick from."""
    match = _NAMES_RE.search(blob)
    if not match:
        return []
    return [n.strip() for n in match.group(1).split(",") if n.strip()]


def _progress_ledger(blob: str, round_index: int) -> str:
    """One valid MagenticProgressLedger as JSON."""
    names = _ledger_candidates(blob)
    satisfied = not names or round_index >= len(names)
    speaker = names[round_index % len(names)] if names else ""

    def item(reason: str, answer: object) -> dict[str, object]:
        return {"reason": reason, "answer": answer}

    ledger = {
        "is_request_satisfied": item(
            "Every specialist has reported and the findings cover the question."
            if satisfied
            else f"Still outstanding: {len(names) - round_index} specialist(s) have not reported.",
            satisfied,
        ),
        "is_in_loop": item("Each round has gone to a different specialist.", False),
        "is_progress_being_made": item("The last response added new facts to the ledger.", True),
        "next_speaker": item(
            "This specialist owns the next unanswered part of the plan." if speaker else "Work is complete.",
            speaker,
        ),
        "instruction_or_question": item(
            "Give them the narrowest question that advances the plan.",
            f"{speaker}, report what you find on the three hotel incidents and the channel-manager sync logs, "
            "and keep it to the facts you own."
            if speaker
            else "No further instruction required.",
        ),
    }
    return json.dumps(ledger, ensure_ascii=False)


# --------------------------------------------------------------------------
# Tool calling
# --------------------------------------------------------------------------


def _tool_names(options: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    for tool in options.get("tools") or []:
        name = getattr(tool, "name", None)
        if name is None and isinstance(tool, Mapping):
            name = tool.get("name") or (tool.get("function") or {}).get("name")
        if isinstance(name, str):
            names.append(name)
    return names


#: Which persona owns which incident kind. Used both to route into a
#: specialist and - just as importantly - to stop that specialist handing the
#: case straight back, which is the hot-potato failure mode this pattern warns about.
_OWNERSHIP: dict[str, tuple[str, ...]] = {
    "flight_cancelled": ("flight",),
    "flight_delayed": ("flight",),
    "overbooked": ("hotel",),
    "not_as_booked": ("hotel",),
    "activity_cancelled": ("billing",),
}


def _payment_blocked(booking: Booking) -> bool:
    return booking.payment in ("flagged", "chargeback")


def _owns_case(persona: str, booking: Booking) -> bool:
    """True when this persona is a legitimate owner of the booking's incident."""
    lowered = (persona or "").lower()
    if _payment_blocked(booking):
        return "risk" in lowered
    incident = booking.incident.value if booking.incident else ""
    return any(hint in lowered for hint in _OWNERSHIP.get(incident, ()))


def _choose_handoff(handoff_tools: list[str], messages: Sequence[Message]) -> str | None:
    """Route to the specialist whose name best matches the case on the table.

    This is the scripted stand-in for the model's routing decision. It reads the
    payment verdict and the incident kind, which is exactly what a real model
    gets from the same two tools.
    """
    booking = _referenced_booking(messages)

    # A payment that fails screening outranks the incident kind.
    if _payment_blocked(booking):
        for name in handoff_tools:
            if "risk" in name.lower():
                return name

    incident = booking.incident.value if booking.incident else ""
    for hint in _OWNERSHIP.get(incident, ()):
        for name in handoff_tools:
            if hint in name.lower():
                return name
    return handoff_tools[0] if handoff_tools else None


#: The one tool each persona reaches for first. Offline an agent makes a single
#: tool call before answering, so it may as well be the one its job turns on -
#: that is the call the live log and the Traces tab then show.
_TOOL_PREFERENCE: dict[str, tuple[str, ...]] = {
    "planner": ("next_activity_slots", "check_availability"),
    "flight": ("check_eu261",),
    "hotel": ("get_sync_log", "check_availability"),
    "billing": ("screen_payment",),
    "risk": ("screen_payment",),
    "pricing": ("estimate_compensation",),
    "cost": ("estimate_compensation",),
    "cost-agent": ("estimate_compensation",),
    "settle": ("estimate_compensation",),
    "ops": ("check_availability",),
    "account": ("get_customer",),
    "researcher": ("list_open_incidents",),
    "analyst": ("get_sync_log",),
    "letter-writer": ("check_eu261",),
    "review": ("check_eu261",),
}


def _tool_arguments(name: str, booking: Booking) -> dict[str, Any] | None:
    """Arguments for a tool call about this booking, or None if it has nothing to ask."""
    if name in ("lookup_booking", "screen_payment", "check_eu261", "estimate_compensation"):
        return {"booking_id": booking.id}
    if name == "get_customer":
        return {"customer_id": booking.customer_id}
    if name == "list_open_incidents":
        return {}
    stay = booking.hotels[0] if booking.hotels else None
    if name == "get_sync_log":
        return {"hotel": stay.hotel} if stay else None
    if name == "check_availability" and stay:
        return {
            "city": stay.city,
            "check_in": stay.check_in.isoformat(),
            "nights": stay.nights,
            "rooms": booking.rooms_short or stay.rooms,
        }
    if name == "next_activity_slots" and booking.activities:
        plan = _replan(booking)
        after = plan["arrival"] if plan else booking.activities[0].on
        return {"activity": booking.activities[0].name, "from_date": after.isoformat(timespec="minutes")}
    return None


class ScriptedChatClient(FunctionInvocationLayer[Any], BaseChatClient[Any]):
    """Offline chat client that behaves like a provider for demo purposes.

    Args:
        persona: The agent's name. Drives the voice and the routing hints.
        tool_budget: How many tool-calling turns this client will take before it
            settles on a text answer. Keeping it small is what stops the handoff
            and magentic patterns from looping forever offline. Handoff triage
            takes two: one to screen the payment, one to route on the answer.
    """

    OTEL_PROVIDER_NAME = "scripted"

    def __init__(self, persona: str = "agent", *, tool_budget: int = 1) -> None:
        FunctionInvocationLayer.__init__(self)
        BaseChatClient.__init__(self)
        self.persona = persona
        self._tool_budget = tool_budget
        self._calls_made = 0
        self._ledger_round = 0
        self._screened = False

    def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> Awaitable[ChatResponse] | ResponseStream[ChatResponseUpdate, ChatResponse]:
        if stream:
            return self._build_response_stream(self._stream(messages, options))

        async def _once() -> ChatResponse:
            return self._respond(messages, options)

        return _once()

    # -- internals ---------------------------------------------------------

    def _respond(self, messages: Sequence[Message], options: Mapping[str, Any]) -> ChatResponse:
        call = self._maybe_tool_call(messages, options)
        if call is not None:
            return ChatResponse(messages=Message("assistant", [call]))
        return ChatResponse(messages=Message("assistant", [Content.from_text(self._text(messages, options))]))

    async def _stream(
        self, messages: Sequence[Message], options: Mapping[str, Any]
    ) -> AsyncIterable[ChatResponseUpdate]:
        call = self._maybe_tool_call(messages, options)
        if call is not None:
            yield ChatResponseUpdate(role="assistant", contents=[call])
            return
        # Chunk the text so DevUI shows a real token stream rather than one blob.
        text = self._text(messages, options)
        for chunk in _chunks(text):
            yield ChatResponseUpdate(role="assistant", contents=[Content.from_text(chunk)])

    def _text(self, messages: Sequence[Message], options: Mapping[str, Any]) -> str:
        _remember(messages)
        blob = _conversation_text(messages)
        if _LEDGER_MARKER in blob and _persona_key(self.persona) == "manager":
            ledger = _progress_ledger(blob, self._ledger_round)
            self._ledger_round += 1
            return ledger
        response_format = options.get("response_format")
        if response_format is not None and hasattr(response_format, "model_fields"):
            return _synthesise_model(response_format, messages, self.persona)
        return _compose(self.persona, messages)

    def _call(self, name: str, arguments: dict[str, Any], messages: Sequence[Message]) -> Content:
        self._calls_made += 1
        return Content.from_function_call(
            call_id=f"call_{self._calls_made}_{abs(_seed_of(messages, self.persona)) % 10_000}",
            name=name,
            arguments=json.dumps(arguments),
        )

    def _maybe_tool_call(self, messages: Sequence[Message], options: Mapping[str, Any]) -> Content | None:
        """Decide whether this turn is a tool call, and which tool."""
        _remember(messages)
        if self._calls_made >= self._tool_budget:
            return None
        names = _tool_names(options)
        if not names:
            return None
        booking = _referenced_booking(messages)

        handoffs = [n for n in names if "handoff" in n.lower() or n.lower().startswith("transfer")]
        if handoffs:
            # Only hand off while the conversation is still short; otherwise the
            # pattern ping-pongs between specialists and never terminates.
            if _handoff_depth(messages) >= 1:
                return None
            # A specialist that owns this incident resolves it. Handing back a
            # case you own is how handoff workflows ping-pong until the budget dies.
            if _owns_case(self.persona, booking):
                return None
            # Screen before routing, as triage's prompt tells a live model to:
            # the payment verdict is the one fact that outranks the incident
            # kind, and it is not in the booking record. Needs the second call.
            if "screen_payment" in names and not self._screened and self._calls_made < self._tool_budget - 1:
                self._screened = True
                return self._call("screen_payment", {"booking_id": booking.id}, messages)
            chosen = _choose_handoff(handoffs, messages)
            if chosen is None:
                return None
            return self._call(chosen, {}, messages)

        # A domain tool: the one this persona's job turns on, else the lookup.
        # record_decision is never called offline - it is the only write path,
        # and a scripted agent writing decisions nobody made would poison the
        # audit trail the Audit tab exists to show.
        preferred = _TOOL_PREFERENCE.get(_persona_key(self.persona), ()) + ("lookup_booking",)
        for name in preferred:
            if name in names:
                arguments = _tool_arguments(name, booking)
                if arguments is not None:
                    return self._call(name, arguments, messages)
        return None


def _handoff_depth(messages: Sequence[Message]) -> int:
    """How many handoffs the conversation has already been through."""
    depth = 0
    for message in messages:
        for content in getattr(message, "contents", None) or []:
            name = getattr(content, "name", "") or ""
            if "handoff" in str(name).lower():
                depth += 1
    return depth


def _chunks(text: str, size: int = 48) -> list[str]:
    """Split text on word boundaries into stream-sized pieces."""
    words = text.split(" ")
    out: list[str] = []
    buf = ""
    for word in words:
        candidate = f"{buf} {word}".strip()
        if len(candidate) >= size:
            out.append(candidate + " ")
            buf = ""
        else:
            buf = candidate
    if buf:
        out.append(buf)
    return out or [text]
