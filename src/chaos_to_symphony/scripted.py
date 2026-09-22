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
   router parses.

Determinism comes from hashing the conversation rather than from a counter, so
two agents running concurrently cannot interfere with each other's script.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import AsyncIterable, Awaitable, Mapping, Sequence
from typing import Any

from agent_framework import ChatResponse, ChatResponseUpdate, Content, Message, ResponseStream
from agent_framework._clients import BaseChatClient
from agent_framework._tools import FunctionInvocationLayer

from .memory import STORE, Severity

# --------------------------------------------------------------------------
# Shared conversation context
# --------------------------------------------------------------------------
#
# Agents built with ``require_per_service_call_history_persistence=True`` - which
# HandoffBuilder requires - are sent only the newest turn, because a real
# provider keeps the thread server-side. This module-level list is that
# server-side thread: whatever any scripted client is shown gets remembered, so
# a specialist receiving a handed-off case can still see which shipment it is
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
    "intake": "Logged the exception and pulled the consignment record.",
    "triage": "Triaged against the exception matrix.",
    "customs": "Checked the HS code against the tariff schedule.",
    "compliance": "Ran the consignee through sanctions screening.",
    "pricing": "Costed the exposure against the customer's SLA band.",
    "cost": "Modelled the direct and indirect cost of this exception.",
    "claims": "Assessed the claim against the policy and the evidence.",
    "legal": "Reviewed contractual liability and penalty exposure.",
    "ops": "Assessed the operational recovery options.",
    "risk": "Scored the residual risk on this lane.",
    "writer": "Drafted the customer-facing response.",
    "draft": "Drafted the customer-facing response.",
    "review": "Reviewed the draft against tone and policy guidelines.",
    "editor": "Edited for clarity and length.",
    "settle": "Proposed a settlement figure for sign-off.",
    "approver": "Assessed whether this needs human sign-off.",
    "billing": "Reconciled the invoice and credit position.",
    "manager": "Coordinating the specialists on this task.",
    "planner": "Broke the task into steps and assigned owners.",
    "researcher": "Gathered the supporting evidence from the shipment history.",
    "analyst": "Analysed the pattern across the affected lane.",
    "summar": "Consolidated the specialist findings into one view.",
    "scorer": "Scored the shipment.",
    "aggregat": "Aggregated the individual scores.",
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
    """Map an agent name onto a persona voice key."""
    lowered = (name or "").lower()
    for key in _VOICES:
        if key in lowered:
            return key
    return "_fallback"


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


def _referenced_shipment(messages: Sequence[Message]) -> Any:
    """Find the shipment the conversation is about, falling back to the worst one."""
    blob = _conversation_text(messages) + "\n" + "\n".join(_SHARED_CONTEXT)
    for shipment_id, shipment in STORE.shipments.items():
        if shipment_id in blob:
            return shipment
    ranked = sorted(
        STORE.open_exceptions(),
        key=lambda s: (s.severity != Severity.CRITICAL, -s.declared_value_eur),
    )
    return ranked[0] if ranked else next(iter(STORE.shipments.values()))


_RANK_RE = re.compile(r"^\s*1\.\s*(BFG-\d+.*)$", re.MULTILINE)


def _top_ranked_row(messages: Sequence[Message]) -> str | None:
    """The first row of a ranked worklist, if one was handed to this agent."""
    match = _RANK_RE.search(_conversation_text(messages))
    return match.group(1).strip() if match else None


#: The offer line this client writes, and reads back on the next round.
_OFFER_RE = re.compile(r"Settlement offer EUR ([\d,]+)")


def _settlement_offer(shipment: Any, policy: Any, messages: Sequence[Message]) -> tuple[int, int | None]:
    """The figure to put on the table now, and the one it replaces (None if first).

    Opens at a tenth of the declared value, capped by the customer's goodwill
    ceiling, and concedes 40% per send-back down to the approval threshold.

    It concedes from *the last offer in the transcript* rather than from a count
    of send-backs. Resuming a suspended workflow re-sends the gated agent's
    request, and the thread the agent then sees can carry a duplicated proposal
    or one send-back fewer than actually happened - so a count drifts, and a
    settlement figure that drifts back upwards in front of the approver is worse
    than no figure at all. The newest offer is always in the transcript to
    concede from, however the bookkeeping shook out.
    """
    ceiling = policy.max_goodwill_eur if policy else shipment.declared_value_eur // 10
    floor = policy.approval_threshold_eur if policy else 0
    opening = min(shipment.declared_value_eur // 10, ceiling)

    text = _conversation_text(messages)
    offers = _OFFER_RE.findall(text)
    if not offers or SEND_BACK_INSTRUCTION not in text:
        return opening, None
    previous = int(offers[-1].replace(",", ""))
    return max(floor, int(previous * 0.6)), previous


def _compose(persona: str, messages: Sequence[Message]) -> str:
    """Build a persona-flavoured reply that quotes real store data."""
    rng = random.Random(_seed_of(messages, persona))
    shipment = _referenced_shipment(messages)
    customer = STORE.customers.get(shipment.customer_id)
    policy = STORE.policy_for(shipment.customer_id)
    tariff = STORE.tariff_for(shipment.hs_code)
    key = _persona_key(persona)

    head = _voice(persona)
    facts: list[str] = [
        f"{shipment.id} - {shipment.goods} on the {shipment.lane} lane for "
        f"{customer.name if customer else 'unknown consignee'}.",
    ]

    if key in {"customs"}:
        if tariff:
            facts.append(
                f"HS {shipment.hs_code} maps to '{tariff.description}' at {tariff.duty_percent}% duty"
                + (", import licence required." if tariff.requires_licence else ", no licence required.")
            )
        else:
            facts.append(f"HS {shipment.hs_code} has no matching tariff line; manual classification needed.")
    elif key in {"compliance"}:
        cleared = customer.sanctions_cleared if customer else True
        facts.append(
            "Consignee is clear of all screening lists."
            if cleared
            else "Consignee FAILS sanctions screening - the consignment must stay frozen pending legal review."
        )
    elif key in {"pricing", "cost", "billing"}:
        exposure = shipment.declared_value_eur // rng.choice([12, 16, 20])
        cap = policy.max_goodwill_eur if policy else 0
        facts.append(
            f"Modelled exposure EUR {exposure:,} against a EUR {cap:,} goodwill ceiling "
            f"({customer.tier if customer else 'unknown'} tier)."
        )
    elif key in {"claims"}:
        cap = policy.max_goodwill_eur if policy else 0
        facts.append(
            f"Claim admissible on the evidence. Declared value EUR {shipment.declared_value_eur:,}, "
            f"goodwill capped at EUR {cap:,} on the {customer.tier if customer else 'unknown'} tier."
        )
    elif key in {"legal"}:
        facts.append(
            f"Declared value EUR {shipment.declared_value_eur:,}; CMR liability caps recovery well below that, "
            "so a goodwill settlement is cheaper than a contested claim."
        )
    elif key in {"ops"}:
        facts.append(
            f"Recovery option: re-route via {rng.choice(['Klaipeda', 'Ventspils', 'Muuga', 'Gdynia'])} "
            f"adding {rng.choice([18, 24, 36, 48])} hours."
        )
    elif key in {"risk", "analyst"}:
        facts.append(
            f"This is incident {rng.choice([2, 3])} on {shipment.lane} this quarter - the lane, not the "
            "consignment, is the root cause."
        )
    elif key in {"writer", "draft", "review", "editor"}:
        facts.append(
            "Tone: acknowledge, state the cause plainly, commit to a dated remedy, avoid admitting liability."
        )
    elif key in {"summar", "aggregat"}:
        # When the upstream node handed us a ranked list, lead with its top row
        # rather than inventing a headline. A summariser that ignores its input
        # is the single most common way these demos look fake.
        top = _top_ranked_row(messages)
        if top:
            return head + " Worst case on the desk today: " + top
    elif key in {"settle"}:
        figure, previous = _settlement_offer(shipment, policy, messages)
        ceiling = policy.max_goodwill_eur if policy else 0
        threshold = policy.approval_threshold_eur if policy else 0
        facts.append(
            f"Settlement offer EUR {figure:,} against a EUR {ceiling:,} goodwill ceiling "
            f"({customer.tier if customer else 'unknown'} tier); approval threshold EUR {threshold:,}."
        )
        if previous is not None and figure < previous:
            facts.append(f"Re-priced after send-back: down from EUR {previous:,}.")
        elif previous is not None:
            facts.append(f"Held at the approval threshold EUR {threshold:,}; no further concession available.")
    elif key in {"approver"}:
        threshold = policy.approval_threshold_eur if policy else 0
        facts.append(f"Anything above EUR {threshold:,} on this tier needs a named human approver.")

    facts.append(f"Severity on file: {shipment.severity.value}.")
    return head + " " + " ".join(facts)


# --------------------------------------------------------------------------
# Structured output
# --------------------------------------------------------------------------


def _synthesise_model(model: Any, messages: Sequence[Message], persona: str) -> str:
    """Produce a JSON string that validates against a Pydantic response_format.

    Field values are chosen from the conversation where the field name gives a
    usable hint, and from the schema's own constraints otherwise, so the router
    in pattern 6 gets a decision it can actually branch on.
    """
    rng = random.Random(_seed_of(messages, persona))
    shipment = _referenced_shipment(messages)
    fields = getattr(model, "model_fields", None)
    if not fields:
        return json.dumps({"result": _compose(persona, messages)})

    payload: dict[str, Any] = {}
    for name, info in fields.items():
        annotation = info.annotation
        choices = getattr(annotation, "__args__", None)
        literals = [c for c in (choices or []) if isinstance(c, str)]
        lowered = name.lower()

        if literals:
            # A Literal[...] field: pick the option the shipment actually implies.
            payload[name] = _pick_literal(literals, shipment, rng)
        elif annotation is bool:
            payload[name] = shipment.severity in (Severity.HIGH, Severity.CRITICAL)
        elif annotation is int:
            payload[name] = _int_for(lowered, shipment, rng)
        elif annotation is float:
            payload[name] = round(rng.uniform(0.55, 0.97), 2)
        elif "id" in lowered:
            payload[name] = shipment.id
        else:
            payload[name] = _compose(persona, messages)
    return json.dumps(payload)


def _pick_literal(literals: list[str], shipment: Any, rng: random.Random) -> str:
    """Choose the Literal option the shipment's own data points at."""
    severity = shipment.severity.value
    exception = shipment.exception.value if shipment.exception else "none"
    for candidate in literals:
        low = candidate.lower()
        if low == severity or low in exception or exception in low:
            return candidate
    # Severity-ordered fallback so criticals never land on the benign branch.
    if shipment.severity in (Severity.CRITICAL, Severity.HIGH):
        for candidate in literals:
            if any(w in candidate.lower() for w in ("escalate", "high", "critical", "urgent", "review", "hold")):
                return candidate
    return literals[rng.randrange(len(literals))]


def _int_for(field_name: str, shipment: Any, rng: random.Random) -> int:
    """A plausible integer for a named field."""
    if "score" in field_name or "confidence" in field_name:
        return rng.randint(55, 98)
    if "eur" in field_name or "amount" in field_name or "cost" in field_name or "value" in field_name:
        return shipment.declared_value_eur // rng.choice([10, 15, 20])
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
            f"{speaker}, report your findings on the Klaipeda lane exceptions and keep it to the facts you own."
            if speaker
            else "No further instruction required.",
        ),
    }
    return json.dumps(ledger)


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


#: Which persona owns which exception kind. Used both to route into a
#: specialist and - just as importantly - to stop that specialist handing the
#: case straight back, which is the hot-potato failure mode this pattern warns about.
_OWNERSHIP: dict[str, tuple[str, ...]] = {
    "customs_hold": ("customs", "complian"),
    "damage": ("claims", "ops", "pricing"),
    "temperature_excursion": ("claims", "quality", "ops"),
    "delay": ("ops", "pricing"),
    "lost": ("claims", "legal", "ops"),
}


def _owns_case(persona: str, shipment: Any) -> bool:
    """True when this persona is a legitimate owner of the shipment's exception."""
    customer = STORE.customers.get(shipment.customer_id)
    lowered = (persona or "").lower()
    if customer and not customer.sanctions_cleared:
        return any(w in lowered for w in ("complian", "legal", "sanction"))
    exception = shipment.exception.value if shipment.exception else ""
    return any(hint in lowered for hint in _OWNERSHIP.get(exception, ()))


def _choose_handoff(handoff_tools: list[str], messages: Sequence[Message]) -> str | None:
    """Route to the specialist whose name best matches the case on the table.

    This is the scripted stand-in for the model's routing decision. It reads the
    shipment's exception kind, which is exactly the signal a real model would
    pick up from the same conversation.
    """
    shipment = _referenced_shipment(messages)
    exception = shipment.exception.value if shipment.exception else ""
    customer = STORE.customers.get(shipment.customer_id)

    # Sanctions failures outrank everything else.
    if customer and not customer.sanctions_cleared:
        for name in handoff_tools:
            if any(w in name.lower() for w in ("complian", "legal", "sanction")):
                return name

    for hint in _OWNERSHIP.get(exception, ()):
        for name in handoff_tools:
            if hint in name.lower():
                return name
    return handoff_tools[0] if handoff_tools else None


class ScriptedChatClient(FunctionInvocationLayer[Any], BaseChatClient[Any]):
    """Offline chat client that behaves like a provider for demo purposes.

    Args:
        persona: The agent's name. Drives the voice and the routing hints.
        tool_budget: How many tool-calling turns this client will take before it
            settles on a text answer. Keeping it small is what stops the handoff
            and magentic patterns from looping forever offline.
    """

    OTEL_PROVIDER_NAME = "scripted"

    def __init__(self, persona: str = "agent", *, tool_budget: int = 1) -> None:
        FunctionInvocationLayer.__init__(self)
        BaseChatClient.__init__(self)
        self.persona = persona
        self._tool_budget = tool_budget
        self._calls_made = 0
        self._ledger_round = 0

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
        if _LEDGER_MARKER in blob:
            ledger = _progress_ledger(blob, self._ledger_round)
            self._ledger_round += 1
            return ledger
        response_format = options.get("response_format")
        if response_format is not None and hasattr(response_format, "model_fields"):
            return _synthesise_model(response_format, messages, self.persona)
        return _compose(self.persona, messages)

    def _maybe_tool_call(self, messages: Sequence[Message], options: Mapping[str, Any]) -> Content | None:
        """Decide whether this turn is a tool call, and which tool."""
        _remember(messages)
        if self._calls_made >= self._tool_budget:
            return None
        names = _tool_names(options)
        if not names:
            return None

        handoffs = [n for n in names if "handoff" in n.lower() or n.lower().startswith("transfer")]
        if handoffs:
            # Only hand off while the conversation is still short; otherwise the
            # pattern ping-pongs between specialists and never terminates.
            if _handoff_depth(messages) >= 1:
                return None
            # A specialist that owns this exception resolves it. Handing back a
            # case you own is how handoff workflows ping-pong until the budget dies.
            if _owns_case(self.persona, _referenced_shipment(messages)):
                return None
            chosen = _choose_handoff(handoffs, messages)
            if chosen is None:
                return None
            self._calls_made += 1
            return Content.from_function_call(
                call_id=f"call_{self._calls_made}_{abs(_seed_of(messages, self.persona)) % 10_000}",
                name=chosen,
                arguments=json.dumps({}),
            )

        # A domain tool: call the first one whose name the persona cares about.
        shipment = _referenced_shipment(messages)
        for name in names:
            low = name.lower()
            if "shipment" in low or "lookup" in low or "get_" in low:
                self._calls_made += 1
                return Content.from_function_call(
                    call_id=f"call_{self._calls_made}_{abs(_seed_of(messages, self.persona)) % 10_000}",
                    name=name,
                    arguments=json.dumps({"shipment_id": shipment.id}),
                )
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
