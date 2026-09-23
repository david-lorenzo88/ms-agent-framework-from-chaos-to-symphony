"""Tools the agents call. Every one reads or writes the in-memory store only.

These are ordinary typed Python functions. Agent Framework turns them into tool
schemas from the signature and docstring, which is why the docstrings here are
written for the model to read, not just for a developer.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from .memory import STORE


def lookup_shipment(
    shipment_id: Annotated[str, Field(description="Shipment reference, e.g. BFG-24084")],
) -> dict[str, Any]:
    """Look up one shipment by its reference and return its full record."""
    shipment = STORE.shipments.get(shipment_id.strip().upper())
    if shipment is None:
        return {"error": f"No shipment {shipment_id}"}
    STORE.record("tool:lookup_shipment", "read", shipment.id)
    return {
        "id": shipment.id,
        "customer_id": shipment.customer_id,
        "lane": shipment.lane,
        "goods": shipment.goods,
        "hs_code": shipment.hs_code,
        "declared_value_eur": shipment.declared_value_eur,
        "weight_kg": shipment.weight_kg,
        "promised_date": shipment.promised_date.isoformat(),
        "actual_date": shipment.actual_date.isoformat() if shipment.actual_date else None,
        "exception": shipment.exception.value if shipment.exception else None,
        "severity": shipment.severity.value,
        "notes": shipment.notes,
    }


def get_customer(
    customer_id: Annotated[str, Field(description="Customer reference, e.g. CUST-001")],
) -> dict[str, Any]:
    """Return a customer's profile, tier and sanctions-screening status."""
    customer = STORE.customers.get(customer_id.strip().upper())
    if customer is None:
        return {"error": f"No customer {customer_id}"}
    STORE.record("tool:get_customer", "read", customer.id)
    return {
        "id": customer.id,
        "name": customer.name,
        "country": customer.country,
        "tier": customer.tier,
        "annual_volume_eur": customer.annual_volume_eur,
        "sanctions_cleared": customer.sanctions_cleared,
    }


def classify_tariff(
    hs_code: Annotated[str, Field(description="Harmonised System code, e.g. 3004.20")],
) -> dict[str, Any]:
    """Classify an HS code against the customs tariff schedule."""
    rule = STORE.tariff_for(hs_code)
    STORE.record("tool:classify_tariff", "read", hs_code)
    if rule is None:
        return {"hs_code": hs_code, "matched": False, "note": "No tariff line; manual classification required."}
    return {
        "hs_code": hs_code,
        "matched": True,
        "description": rule.description,
        "duty_percent": float(rule.duty_percent),
        "requires_licence": rule.requires_licence,
    }


def check_sanctions(
    customer_id: Annotated[str, Field(description="Customer reference to screen")],
) -> dict[str, Any]:
    """Screen a consignee against sanctions lists. A failure blocks the shipment."""
    customer = STORE.customers.get(customer_id.strip().upper())
    if customer is None:
        return {"error": f"No customer {customer_id}"}
    STORE.record("tool:check_sanctions", "screen", f"{customer.id} cleared={customer.sanctions_cleared}")
    return {
        "customer_id": customer.id,
        "cleared": customer.sanctions_cleared,
        "action": "proceed" if customer.sanctions_cleared else "freeze_and_escalate_to_legal",
    }


def estimate_compensation(
    shipment_id: Annotated[str, Field(description="Shipment reference")],
    days_late: Annotated[int, Field(description="How many days late the delivery was", ge=0)] = 0,
) -> dict[str, Any]:
    """Estimate compensation for a shipment exception, capped by the customer's SLA band."""
    shipment = STORE.shipments.get(shipment_id.strip().upper())
    if shipment is None:
        return {"error": f"No shipment {shipment_id}"}
    policy = STORE.policy_for(shipment.customer_id)
    if policy is None:
        return {"error": "No SLA policy for this customer"}

    raw = days_late * policy.delay_penalty_per_day_eur
    if shipment.exception and shipment.exception.value in {"damage", "lost", "temperature_excursion"}:
        raw += shipment.declared_value_eur // 10
    capped = min(raw, policy.max_goodwill_eur)
    STORE.record("tool:estimate_compensation", "compute", f"{shipment.id} -> EUR {capped}")
    return {
        "shipment_id": shipment.id,
        "raw_estimate_eur": raw,
        "capped_estimate_eur": capped,
        "goodwill_ceiling_eur": policy.max_goodwill_eur,
        "approval_threshold_eur": policy.approval_threshold_eur,
        "needs_human_approval": capped > policy.approval_threshold_eur,
    }


def list_open_exceptions() -> list[dict[str, Any]]:
    """List every shipment currently carrying an unresolved exception."""
    rows = STORE.open_exceptions()
    STORE.record("tool:list_open_exceptions", "read", f"{len(rows)} rows")
    return [
        {
            "id": s.id,
            "lane": s.lane,
            "exception": s.exception.value if s.exception else None,
            "severity": s.severity.value,
            "declared_value_eur": s.declared_value_eur,
        }
        for s in rows
    ]


def record_decision(
    shipment_id: Annotated[str, Field(description="Shipment the decision applies to")],
    decision: Annotated[str, Field(description="The decision taken, in one sentence")],
    amount_eur: Annotated[int, Field(description="Compensation agreed, 0 if none", ge=0)] = 0,
) -> dict[str, Any]:
    """Write a resolution decision to the audit trail. This is the only write path."""
    entry = STORE.record("tool:record_decision", "decide", f"{shipment_id}: {decision} (EUR {amount_eur})")
    return {"recorded_at": entry.at.isoformat(), "shipment_id": shipment_id, "amount_eur": amount_eur}


#: The tools handed to most agents.
CASE_TOOLS = [lookup_shipment, get_customer, estimate_compensation, record_decision]
CUSTOMS_TOOLS = [lookup_shipment, classify_tariff, check_sanctions]
ANALYSIS_TOOLS = [list_open_exceptions, lookup_shipment, get_customer]

#: Read-only, for an agent that grades a case rather than acting on it. A
#: classifier that could also write to the audit trail would be a classifier
#: with side effects, which is not what the routing pattern is demonstrating.
TRIAGE_TOOLS = [lookup_shipment]
