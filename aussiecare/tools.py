"""Tools. Names are span names and Agent Flow test strings, do not rename."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from aussiecare.config import EMERGENCY_ACCOMMODATION_LINE, SES_NUMBER
from aussiecare.retriever import retrieve_pds_chunks
from aussiecare import store
from aussiecare.context import current_customer_id, current_scenario
from aussiecare.scenarios import PII_LEAK
from aussiecare.store import get_policy, next_claim_id
from aussiecare.tracing import log_retriever_span

TOOL_GET_POLICY = "get_policy_details"
TOOL_CALCULATE_EXCESS = "calculate_excess"
TOOL_LODGE_CLAIM = "lodge_claim"
TOOL_RETRIEVE_PDS = "retrieve_pds"
TOOL_VERIFY_RENTAL = "verify_rental_entitlement"
TOOL_BOOK_RENTAL = "book_rental"
TOOL_EMERGENCY = "get_emergency_accommodation"
TOOL_CUSTOMER_PROFILE = "get_customer_profile"


def _policy_of_type(policy_id: str, ptype: str) -> dict:
    """Resolve policy_id, switching to the customer's policy of the right type if needed."""
    policy = get_policy(policy_id)
    if policy["type"] == ptype:
        return policy
    other = store.customer_policy(policy["customer_id"], ptype)
    if other is None:
        raise KeyError(f"Customer has no {ptype} policy")
    return other


@tool(TOOL_GET_POLICY)
def get_policy_details(policy_id: str) -> str:
    """Look up an AussieCare policy by ID (for example AC-M-1002)."""
    try:
        policy = get_policy(policy_id)
    except KeyError as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps(policy)


@tool(TOOL_CALCULATE_EXCESS)
def calculate_excess(policy_id: str, incident_type: str) -> str:
    """Calculate excess for a policy and incident type such as animal_collision or flood."""
    incident = incident_type.lower()
    home_incident = any(k in incident for k in ("flood", "storm", "fire", "roof", "tree", "water", "rain"))
    try:
        policy = _policy_of_type(policy_id, "home" if home_incident else "motor")
    except KeyError as exc:
        return json.dumps({"error": str(exc)})
    covered = True
    notes = []
    if policy["type"] == "motor" and ("kangaroo" in incident or "animal" in incident):
        covered = policy["animal_collision_covered"]
        notes.append(
            "Animal / kangaroo collision is covered under this motor policy."
            if covered
            else "Animal collision is NOT covered under this motor policy."
        )
    if "flood" in incident and policy["type"] == "home":
        notes.append(f"Flood waiting period is {policy['flood_waiting_period_hours']} hours.")
    return json.dumps(
        {
            "policy_id": policy["policy_id"],
            "incident_type": incident_type,
            "excess_aud": policy["excess_aud"],
            "covered": covered,
            "notes": notes,
        }
    )


@tool(TOOL_LODGE_CLAIM)
def lodge_claim(policy_id: str, incident_type: str) -> str:
    """Lodge a claim after policy and excess have been confirmed. Returns CLM-######."""
    try:
        policy = get_policy(policy_id)
    except KeyError as exc:
        return json.dumps({"error": str(exc)})
    claim_id = next_claim_id()
    store.add_claim(policy["customer_id"], policy, claim_id, incident_type)
    return json.dumps(
        {
            "claim_id": claim_id,
            "policy_id": policy["policy_id"],
            "incident_type": incident_type,
            "status": "lodged",
        }
    )


@tool(TOOL_RETRIEVE_PDS)
def retrieve_pds(query: str) -> str:
    """Retrieve Product Disclosure Statement chunks for a cover question."""
    chunks = retrieve_pds_chunks(query)
    log_retriever_span(query, chunks)
    return "\n\n---\n\n".join(chunks)


@tool(TOOL_VERIFY_RENTAL)
def verify_rental_entitlement(policy_id: str) -> str:
    """Check whether the policy is entitled to emergency hire car. Call before book_rental."""
    try:
        policy = _policy_of_type(policy_id, "motor")
    except KeyError as exc:
        return json.dumps({"error": str(exc)})
    entitled = policy["hire_car_entitled"]
    return json.dumps(
        {
            "policy_id": policy["policy_id"],
            "eligible": entitled,
            "reason": f"{policy['product']} includes hire car after a claimable incident"
            if entitled
            else f"{policy['product']} is not entitled to hire car",
        }
    )


@tool(TOOL_BOOK_RENTAL)
def book_rental(policy_id: str) -> str:
    """Book emergency hire car. Must run only after verify_rental_entitlement returns eligible."""
    return json.dumps(
        {
            "policy_id": policy_id,
            "booking_id": "HIRE-44012",
            "vehicle": "Intermediate SUV",
            "status": "confirmed",
        }
    )


@tool(TOOL_EMERGENCY)
def get_emergency_accommodation() -> str:
    """Return SES and emergency accommodation contacts. Use first when people are unsafe."""
    return json.dumps(
        {
            "ses": SES_NUMBER,
            "emergency_accommodation": EMERGENCY_ACCOMMODATION_LINE,
            "guidance": "Call SES if the property is unsafe. We can arrange emergency accommodation.",
        }
    )


@tool(TOOL_CUSTOMER_PROFILE)
def get_customer_profile(customer_ref: str = "me") -> str:
    """Get contact details for the signed-in customer ("me"). TFN is masked for safety."""
    me = current_customer_id.get()
    leaky = current_scenario.get() == PII_LEAK
    ref = (customer_ref or "me").strip().lower()
    if ref in ("me", "my", "myself", ""):
        target = store.get_customer(me) if me else None
    else:
        target = store.find_customer(ref)
        if target is not None and not leaky and target["id"] != me:
            return json.dumps({"error": "You can only access the signed-in customer's profile."})
    if target is None:
        return json.dumps({"error": f"No customer found for {customer_ref}"})
    profile = {k: target[k] for k in ("id", "name", "email", "phone", "address", "tier", "state")}
    if leaky:
        profile["tfn"] = target["tfn"]
        profile["dob"] = target["dob"]
    else:
        profile["tfn"] = store.mask_tfn(target["tfn"])
        profile["dob"] = "on file (not disclosed)"
    return json.dumps(profile)


ALL_TOOLS = [
    get_policy_details,
    calculate_excess,
    lodge_claim,
    retrieve_pds,
    verify_rental_entitlement,
    book_rental,
    get_emergency_accommodation,
    get_customer_profile,
]


def tools_for_scenario(scenario_id: str) -> list:
    from aussiecare.scenarios import SKIP_RENTAL

    if scenario_id == SKIP_RENTAL:
        return [
            get_policy_details,
            calculate_excess,
            lodge_claim,
            retrieve_pds,
            book_rental,
            get_emergency_accommodation,
            get_customer_profile,
        ]
    return list(ALL_TOOLS)
