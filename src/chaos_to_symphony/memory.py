"""The entire 'database' for every demo - held in process memory.

Nothing here touches a disk or a network. The store is a plain dataclass graph
guarded by a lock, seeded deterministically at import time and resettable
between runs. That keeps the conference demo reproducible: the same question
produces the same shipment rows every single time, on any laptop, offline.

Domain: Baltic Freight Group, a fictional Tallinn-based freight forwarder.
A shipment hits an *exception* (damage, customs hold, delay, lost pallet) and a
team of specialists has to triage, price, and approve a resolution. It is small
enough to read on a slide and rich enough to exercise all twelve patterns.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any


class Severity(str, Enum):
    """How badly a shipment exception hurts."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ExceptionKind(str, Enum):
    """What went wrong with the shipment."""

    DAMAGE = "damage"
    CUSTOMS_HOLD = "customs_hold"
    DELAY = "delay"
    LOST = "lost"
    TEMPERATURE = "temperature_excursion"


@dataclass(frozen=True, slots=True)
class Customer:
    """A freight customer. ``tier`` drives the goodwill budget."""

    id: str
    name: str
    country: str
    tier: str  # bronze | silver | gold
    annual_volume_eur: int
    sanctions_cleared: bool = True


@dataclass(frozen=True, slots=True)
class Shipment:
    """One consignment moving across the Baltic network."""

    id: str
    customer_id: str
    origin: str
    destination: str
    lane: str
    goods: str
    hs_code: str
    declared_value_eur: int
    weight_kg: int
    promised_date: date
    actual_date: date | None
    exception: ExceptionKind | None
    severity: Severity
    notes: str = ""


@dataclass(frozen=True, slots=True)
class TariffRule:
    """A customs tariff line, looked up by HS code prefix."""

    hs_prefix: str
    description: str
    duty_percent: Decimal
    requires_licence: bool


@dataclass(frozen=True, slots=True)
class SlaPolicy:
    """The compensation policy a resolution has to respect."""

    tier: str
    delay_penalty_per_day_eur: int
    max_goodwill_eur: int
    approval_threshold_eur: int
    """Above this figure a human must approve. Pattern 10 turns on this number."""


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
    Customer("CUST-001", "Vilnius Electronics UAB", "LT", "gold", 2_400_000),
    Customer("CUST-002", "Riga Cold Chain SIA", "LV", "gold", 1_850_000),
    Customer("CUST-003", "Tallinn Timber OU", "EE", "silver", 640_000),
    Customer("CUST-004", "Gdansk Auto Parts sp. z o.o.", "PL", "silver", 520_000),
    Customer("CUST-005", "Helsinki Pharma Oy", "FI", "gold", 3_100_000),
    Customer("CUST-006", "Kaliningrad Machinery LLC", "RU", "bronze", 95_000, sanctions_cleared=False),
    Customer("CUST-007", "Malmo Furniture AB", "SE", "bronze", 210_000),
)

_TARIFFS: tuple[TariffRule, ...] = (
    TariffRule("8471", "Automatic data-processing machines", Decimal("0.0"), False),
    TariffRule("8504", "Electrical transformers and converters", Decimal("3.7"), False),
    TariffRule("3004", "Medicaments, packaged for retail sale", Decimal("0.0"), True),
    TariffRule("4407", "Wood sawn lengthwise, thickness > 6mm", Decimal("2.0"), False),
    TariffRule("8708", "Parts and accessories for motor vehicles", Decimal("4.5"), False),
    TariffRule("9403", "Other furniture and parts thereof", Decimal("2.7"), False),
    TariffRule("8479", "Machines with individual functions", Decimal("1.7"), True),
)

_SLA: tuple[SlaPolicy, ...] = (
    SlaPolicy("gold", delay_penalty_per_day_eur=450, max_goodwill_eur=12_000, approval_threshold_eur=5_000),
    SlaPolicy("silver", delay_penalty_per_day_eur=220, max_goodwill_eur=5_000, approval_threshold_eur=2_500),
    SlaPolicy("bronze", delay_penalty_per_day_eur=90, max_goodwill_eur=1_500, approval_threshold_eur=1_000),
)


def _seed_shipments() -> tuple[Shipment, ...]:
    """Twenty shipments: enough for map-reduce to look like work, few enough to print."""
    d = date
    rows: list[Shipment] = [
        Shipment("BFG-24081", "CUST-001", "Vilnius", "Rotterdam", "LT-NL", "Server racks", "8471.50",
                 184_000, 3_200, d(2026, 9, 4), d(2026, 9, 11), ExceptionKind.DELAY, Severity.HIGH,
                 "Ferry cancelled at Klaipeda, 7 days late, customer missed a datacentre install window."),
        Shipment("BFG-24082", "CUST-002", "Riga", "Hamburg", "LV-DE", "Chilled salmon", "0302.14",
                 96_500, 11_000, d(2026, 9, 8), d(2026, 9, 9), ExceptionKind.TEMPERATURE, Severity.CRITICAL,
                 "Reefer unit failed for 6 hours; core temperature reached 7 degrees C."),
        Shipment("BFG-24083", "CUST-003", "Tallinn", "Gdansk", "EE-PL", "Sawn birch", "4407.95",
                 41_200, 24_000, d(2026, 9, 2), d(2026, 9, 2), None, Severity.LOW, "Delivered on time."),
        Shipment("BFG-24084", "CUST-005", "Helsinki", "Vilnius", "FI-LT", "Vaccine cartons", "3004.20",
                 310_000, 850, d(2026, 9, 12), None, ExceptionKind.CUSTOMS_HOLD, Severity.CRITICAL,
                 "Held at Vaalimaa: import licence reference missing from the declaration."),
        Shipment("BFG-24085", "CUST-004", "Gdansk", "Malmo", "PL-SE", "Brake discs", "8708.30",
                 58_400, 9_400, d(2026, 9, 6), d(2026, 9, 8), ExceptionKind.DAMAGE, Severity.MEDIUM,
                 "Two pallets crushed in transit; 18% of units unsellable."),
        Shipment("BFG-24086", "CUST-006", "Kaliningrad", "Riga", "RU-LV", "CNC machine", "8479.89",
                 220_000, 6_800, d(2026, 9, 10), None, ExceptionKind.CUSTOMS_HOLD, Severity.CRITICAL,
                 "Consignee failed sanctions screening; shipment frozen pending legal review."),
        Shipment("BFG-24087", "CUST-007", "Malmo", "Tallinn", "SE-EE", "Flat-pack desks", "9403.30",
                 22_800, 5_100, d(2026, 9, 5), d(2026, 9, 7), ExceptionKind.DELAY, Severity.LOW,
                 "Two days late, customer accepted revised slot without complaint."),
        Shipment("BFG-24088", "CUST-001", "Vilnius", "Antwerp", "LT-BE", "Power converters", "8504.40",
                 133_000, 2_900, d(2026, 9, 9), None, ExceptionKind.LOST, Severity.CRITICAL,
                 "Trailer went missing between Poznan and Antwerp; police report filed."),
        Shipment("BFG-24089", "CUST-002", "Riga", "Oslo", "LV-NO", "Frozen berries", "0811.10",
                 47_300, 14_500, d(2026, 9, 11), d(2026, 9, 11), None, Severity.LOW, "Delivered on time."),
        Shipment("BFG-24090", "CUST-005", "Helsinki", "Warsaw", "FI-PL", "Diagnostic kits", "3004.90",
                 178_000, 640, d(2026, 9, 3), d(2026, 9, 6), ExceptionKind.DELAY, Severity.HIGH,
                 "Three days late into a hospital tender; penalty clause invoked by consignee."),
        Shipment("BFG-24091", "CUST-003", "Tallinn", "Bremen", "EE-DE", "Plywood sheets", "4407.11",
                 33_900, 19_800, d(2026, 9, 7), d(2026, 9, 8), ExceptionKind.DAMAGE, Severity.LOW,
                 "Water ingress on top layer; 4% written off."),
        Shipment("BFG-24092", "CUST-004", "Gdansk", "Vilnius", "PL-LT", "Suspension arms", "8708.80",
                 71_500, 8_200, d(2026, 9, 13), None, ExceptionKind.DELAY, Severity.MEDIUM,
                 "Driver hours exceeded; running 2 days behind."),
        Shipment("BFG-24093", "CUST-001", "Vilnius", "Rotterdam", "LT-NL", "Network switches", "8471.80",
                 205_000, 1_900, d(2026, 9, 14), None, ExceptionKind.DELAY, Severity.HIGH,
                 "Same Klaipeda ferry cancellation; second incident on this lane in a fortnight."),
        Shipment("BFG-24094", "CUST-007", "Malmo", "Riga", "SE-LV", "Office chairs", "9403.20",
                 18_600, 4_300, d(2026, 9, 1), d(2026, 9, 1), None, Severity.LOW, "Delivered on time."),
        Shipment("BFG-24095", "CUST-002", "Riga", "Copenhagen", "LV-DK", "Chilled dairy", "0406.10",
                 62_100, 10_200, d(2026, 9, 12), d(2026, 9, 13), ExceptionKind.TEMPERATURE, Severity.HIGH,
                 "Door seal failure; 5 degrees C excursion for 90 minutes."),
        Shipment("BFG-24096", "CUST-005", "Helsinki", "Tallinn", "FI-EE", "Lab reagents", "3004.90",
                 88_400, 320, d(2026, 9, 15), None, ExceptionKind.CUSTOMS_HOLD, Severity.MEDIUM,
                 "Random inspection at Muuga; documentation complete, awaiting release."),
        Shipment("BFG-24097", "CUST-003", "Tallinn", "Stockholm", "EE-SE", "Timber beams", "4407.19",
                 29_700, 21_400, d(2026, 9, 10), d(2026, 9, 12), ExceptionKind.DELAY, Severity.MEDIUM,
                 "Two days late; site crew stood down, customer claiming idle-labour cost."),
        Shipment("BFG-24098", "CUST-004", "Gdansk", "Helsinki", "PL-FI", "Clutch kits", "8708.93",
                 44_900, 6_600, d(2026, 9, 4), d(2026, 9, 4), None, Severity.LOW, "Delivered on time."),
        Shipment("BFG-24099", "CUST-001", "Vilnius", "Hamburg", "LT-DE", "Rack PDUs", "8504.40",
                 119_000, 2_400, d(2026, 9, 16), None, ExceptionKind.DAMAGE, Severity.HIGH,
                 "Forklift strike at the Klaipeda hub; 3 of 12 pallets compromised."),
        Shipment("BFG-24100", "CUST-006", "Kaliningrad", "Gdansk", "RU-PL", "Bearings", "8482.10",
                 15_400, 3_100, d(2026, 9, 8), None, ExceptionKind.CUSTOMS_HOLD, Severity.HIGH,
                 "Sanctions screening pending on the consignee entity."),
    ]
    return tuple(rows)


# --------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------


@dataclass
class FreightStore:
    """In-memory store. One instance per process; reset between demo runs.

    The audit trail is the only mutable part. Everything else is frozen, which
    is deliberate: an agent that "updates" a shipment has to go through
    :meth:`record`, so the trail can never silently miss a decision.
    """

    customers: dict[str, Customer] = field(default_factory=dict)
    shipments: dict[str, Shipment] = field(default_factory=dict)
    tariffs: tuple[TariffRule, ...] = field(default_factory=tuple)
    sla: dict[str, SlaPolicy] = field(default_factory=dict)
    audit: list[AuditEntry] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def seeded(cls) -> FreightStore:
        """Build a store populated with the deterministic demo data."""
        return cls(
            customers={c.id: c for c in _CUSTOMERS},
            shipments={s.id: s for s in _seed_shipments()},
            tariffs=_TARIFFS,
            sla={p.tier: p for p in _SLA},
        )

    def reset(self) -> None:
        """Restore seed state and clear the audit trail, between demo runs."""
        fresh = FreightStore.seeded()
        with self._lock:
            self.customers = fresh.customers
            self.shipments = fresh.shipments
            self.tariffs = fresh.tariffs
            self.sla = fresh.sla
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

    def open_exceptions(self) -> list[Shipment]:
        """Every shipment currently carrying an exception."""
        return [s for s in self.shipments.values() if s.exception is not None]

    def tariff_for(self, hs_code: str) -> TariffRule | None:
        """Longest-prefix match of an HS code against the tariff table."""
        best: TariffRule | None = None
        for rule in self.tariffs:
            if hs_code.replace(".", "").startswith(rule.hs_prefix) and (
                best is None or len(rule.hs_prefix) > len(best.hs_prefix)
            ):
                best = rule
        return best

    def policy_for(self, customer_id: str) -> SlaPolicy | None:
        """The SLA policy that applies to a customer, via their tier."""
        customer = self.customers.get(customer_id)
        return self.sla.get(customer.tier) if customer else None


#: Process-wide store. Demos import this directly.
STORE = FreightStore.seeded()
