"""Mock customer, policy and claims store (in memory, reset on restart)."""

from __future__ import annotations

import copy
import json
import re
from datetime import date
from typing import Any

from aussiecare.config import CUSTOMERS_PATH
from aussiecare.context import current_customer_id

_CLAIM_SEQ = 0
_EXTRA_CLAIMS: dict[str, list[dict[str, Any]]] = {}
POLICY_ID_RE = re.compile(r"(?:AC-[A-Z]-\d+|POL-\d+)", re.IGNORECASE)


def _raw() -> dict[str, Any]:
    return json.loads(CUSTOMERS_PATH.read_text(encoding="utf-8"))


def mask_tfn(tfn: str) -> str:
    return "*** *** " + tfn.replace(" ", "")[-3:]


def list_customers() -> list[dict[str, Any]]:
    out = []
    for c in _raw().values():
        out.append(
            {
                "id": c["id"],
                "name": c["name"],
                "tier": c["tier"],
                "state": c["state"],
                "summary": ", ".join(p["product"] for p in c["policies"]),
            }
        )
    return out


def get_customer(customer_id: str) -> dict[str, Any]:
    data = _raw()
    if customer_id not in data:
        raise KeyError(f"Unknown customer {customer_id}")
    customer = copy.deepcopy(data[customer_id])
    customer["claims"] = customer["claims"] + copy.deepcopy(_EXTRA_CLAIMS.get(customer_id, []))
    return customer


def public_customer(customer_id: str) -> dict[str, Any]:
    """Customer view for the portal: no TFN or date of birth."""
    c = get_customer(customer_id)
    c.pop("tfn", None)
    c.pop("dob", None)
    return c


def find_customer(ref: str) -> dict[str, Any] | None:
    ref = (ref or "").strip().lower()
    if not ref:
        return None
    for c in _raw().values():
        if ref in (c["id"], c["name"].lower(), c["email"].lower()) or ref in c["name"].lower():
            return get_customer(c["id"])
    return None


def get_policy(policy_id: str, *, owner_only: bool = True) -> dict[str, Any]:
    key = (policy_id or "").upper()
    owner = current_customer_id.get()
    for c in _raw().values():
        for p in c["policies"]:
            if p["policy_id"].upper() == key:
                if owner_only and owner and c["id"] != owner:
                    raise KeyError(f"Policy {policy_id} does not belong to the signed-in customer")
                return {**p, "customer_id": c["id"]}
    raise KeyError(f"Unknown policy {policy_id}")


def customer_policy(customer_id: str, ptype: str) -> dict[str, Any] | None:
    for p in get_customer(customer_id)["policies"]:
        if p["type"] == ptype:
            return {**p, "customer_id": customer_id}
    return None


def extract_policy_id(text: str) -> str | None:
    match = POLICY_ID_RE.search(text or "")
    return match.group(0).upper() if match else None


def next_claim_id() -> str:
    global _CLAIM_SEQ
    _CLAIM_SEQ += 1
    return f"CLM-{_CLAIM_SEQ:06d}"


def add_claim(customer_id: str, policy: dict[str, Any], claim_id: str, incident_type: str) -> None:
    _EXTRA_CLAIMS.setdefault(customer_id, []).append(
        {
            "claim_id": claim_id,
            "type": policy["type"],
            "policy_id": policy["policy_id"],
            "loss_date": date.today().isoformat(),
            "claimed_aud": None,
            "settled_aud": None,
            "status": "lodged",
            "note": f"Lodged via AussieCare Assistant: {incident_type}.",
        }
    )


def reset() -> None:
    global _CLAIM_SEQ
    _CLAIM_SEQ = 0
    _EXTRA_CLAIMS.clear()
