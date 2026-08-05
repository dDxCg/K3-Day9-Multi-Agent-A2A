"""CSV-derived, cap-safe construction of affected entities and evidence IDs.

Both are rebuilt from the Olist CSVs rather than copied from the agents'
reports, for the same reason the financial totals already are: an agent can
under-report a 21-item order, and a filter-only verifier can drop a bad ID
but never restore a missing one.

Ordering is priority order. `order:` and `policy:<cause>` are emitted first
so that the 10-evidence cap can only ever discard the least important IDs —
previously `policy:` was appended last and was the first thing truncated on
any order with more than ten evidence rows.
"""

from __future__ import annotations

from typing import Any

from src.data_store import get_order, get_order_items, get_payments

MAX_ENTITY_IDS = 5
MAX_EVIDENCE = 10
MAX_CAUSES = 3
MAX_PARTIES = 3
MAX_ACTIONS = 5

# Issues where the seller is not the party at fault, so `seller:` evidence is
# noise under CAUSAL mode.
_NON_SELLER_ISSUES = {
    "late_delivery_logistics",
    "canceled_order_paid",
    "unavailable_order_paid",
    "valid_split_payment",
    "unsupported_late_claim",
}

# Issues resolved entirely from order status + payments, where item-level
# evidence adds nothing under CAUSAL mode.
_PAYMENT_ONLY_ISSUES = {"canceled_order_paid", "unavailable_order_paid"}


def _responsible_seller_ids(decision: dict[str, Any]) -> list[str]:
    return [
        party["party_id"]
        for party in decision.get("responsible_parties", [])
        if party.get("party_type") == "seller"
    ]


def collect_facts(order_id: str, decision: dict[str, Any]) -> dict[str, Any]:
    """Read the order once and rank its items/sellers/payments so that the
    entities implicated by the root cause come first."""
    order_exists = get_order(order_id) is not None
    items = get_order_items(order_id)
    payments = get_payments(order_id)

    causal_sellers = _responsible_seller_ids(decision)

    causal_items: list[str] = []
    other_items: list[str] = []
    for item in items:
        item_id = f"{order_id}:{item['order_item_id']}"
        if item["seller_id"] in causal_sellers:
            causal_items.append(item_id)
        else:
            other_items.append(item_id)

    seen: list[str] = []
    for item in items:
        if item["seller_id"] not in seen:
            seen.append(item["seller_id"])
    ranked_sellers = [s for s in causal_sellers if s in seen] + [
        s for s in seen if s not in causal_sellers
    ]

    return {
        "order_exists": order_exists,
        "item_ids": causal_items + other_items,
        "causal_item_ids": causal_items,
        "seller_ids": ranked_sellers,
        "causal_seller_ids": [s for s in causal_sellers if s in seen],
        "payment_ids": [
            f"{order_id}:{p['payment_sequential']}"
            for p in sorted(payments, key=lambda p: int(p["payment_sequential"]))
        ],
    }


def build_entities(order_id: str, facts: dict[str, Any]) -> dict[str, list[str]]:
    """Affected entities, causally ranked then capped at 5 each. An order id
    absent from orders.csv is never emitted — it would be a false positive."""
    if not facts["order_exists"]:
        return {"order_ids": [], "item_ids": [], "seller_ids": [], "payment_ids": []}

    return {
        "order_ids": [order_id],
        "item_ids": facts["item_ids"][:MAX_ENTITY_IDS],
        "seller_ids": facts["seller_ids"][:MAX_ENTITY_IDS],
        "payment_ids": facts["payment_ids"][:MAX_ENTITY_IDS],
    }


def build_evidence(
    order_id: str, decision: dict[str, Any], facts: dict[str, Any], mode: str
) -> list[str]:
    """Evidence IDs in priority order, capped at 10.

    FULL keeps every existing ID; CAUSAL drops the ones the root cause does
    not implicate (see `_NON_SELLER_ISSUES` / `_PAYMENT_ONLY_ISSUES`).
    """
    issue = decision.get("primary_issue", "")
    causal = mode == "CAUSAL"

    evidence: list[str] = []
    if facts["order_exists"]:
        evidence.append(f"order:{order_id}")
    evidence.append(f"policy:{decision['cause_code']}")

    if causal and issue in _PAYMENT_ONLY_ISSUES:
        item_ids: list[str] = []
        seller_ids: list[str] = []
    elif causal and issue in _NON_SELLER_ISSUES:
        item_ids = facts["item_ids"]
        seller_ids = []
    elif causal:
        # Seller at fault: only the offending seller and that seller's items.
        item_ids = facts["causal_item_ids"] or facts["item_ids"]
        seller_ids = facts["causal_seller_ids"]
    else:
        item_ids = facts["item_ids"]
        seller_ids = facts["seller_ids"]

    ordered = (
        [f"item:{i}" for i in item_ids[:MAX_ENTITY_IDS]]
        + [f"seller:{s}" for s in seller_ids[:MAX_ENTITY_IDS]]
        + [f"payment:{p}" for p in facts["payment_ids"]]
        + [f"item:{i}" for i in item_ids[MAX_ENTITY_IDS:]]
        + [f"seller:{s}" for s in seller_ids[MAX_ENTITY_IDS:]]
    )

    for evidence_id in ordered:
        if evidence_id not in evidence:
            evidence.append(evidence_id)

    return evidence[:MAX_EVIDENCE]
