"""Nơi DUY NHẤT dựng evidence ID (README mục 5).

Không agent nào được tự concat string ID — evidence sai định dạng hoặc không
tồn tại trong CSV bị tính là false positive khi chấm.

    order:<order_id>
    item:<order_id>:<order_item_id>
    payment:<order_id>:<payment_sequential>
    seller:<seller_id>
    policy:<root_cause_code>
"""

from __future__ import annotations

import re

from schema.output_schema import MAX_EVIDENCE

# Olist id là chuỗi hex 32 ký tự; policy code là UPPER_SNAKE.
_HEX_ID = r"[0-9a-f]{32}"
_EVIDENCE_PATTERNS = {
    "order": re.compile(rf"^order:{_HEX_ID}$"),
    "item": re.compile(rf"^item:{_HEX_ID}:\d+$"),
    "payment": re.compile(rf"^payment:{_HEX_ID}:\d+$"),
    "seller": re.compile(rf"^seller:{_HEX_ID}$"),
    "policy": re.compile(r"^policy:[A-Z_]+$"),
}


def order_evidence(order_id: str) -> str:
    return f"order:{order_id}"


def item_evidence(order_id: str, order_item_id: int | str) -> str:
    return f"item:{order_id}:{order_item_id}"


def payment_evidence(order_id: str, payment_sequential: int | str) -> str:
    return f"payment:{order_id}:{payment_sequential}"


def seller_evidence(seller_id: str) -> str:
    return f"seller:{seller_id}"


def policy_evidence(root_cause_code: str) -> str:
    return f"policy:{root_cause_code}"


def item_entity_id(order_id: str, order_item_id: int | str) -> str:
    """affected_entities.item_ids dùng dạng KHÔNG prefix: "<order_id>:<n>"."""
    return f"{order_id}:{order_item_id}"


def payment_entity_id(order_id: str, payment_sequential: int | str) -> str:
    """affected_entities.payment_ids dùng dạng KHÔNG prefix: "<order_id>:<seq>"."""
    return f"{order_id}:{payment_sequential}"


def is_well_formed(evidence_id: str) -> bool:
    """Chỉ kiểm tra định dạng. Việc ID có tồn tại trong CSV do DataStore chốt."""
    prefix = evidence_id.split(":", 1)[0] if ":" in evidence_id else ""
    pattern = _EVIDENCE_PATTERNS.get(prefix)
    return bool(pattern and pattern.match(evidence_id))


def assemble(
    order_ev: str | None,
    item_evs: list[str],
    payment_evs: list[str],
    seller_evs: list[str],
    policy_ev: str | None,
    limit: int = MAX_EVIDENCE,
) -> list[str]:
    """Gom evidence trong giới hạn ≤10, luôn giữ order (đầu) và policy (cuối).

    Hai ID này neo case vào đơn hàng và vào rule đã áp — mất một trong hai thì
    bằng chứng không còn nói lên điều gì, nên chúng được ưu tiên tuyệt đối.
    """
    anchors = [e for e in (order_ev, policy_ev) if e]
    room = max(limit - len(anchors), 0)

    pool: list[str] = []
    for candidate in [*item_evs, *payment_evs, *seller_evs]:
        if candidate not in pool and candidate not in anchors:
            pool.append(candidate)

    chosen = pool[:room]
    result = ([order_ev] if order_ev else []) + chosen + ([policy_ev] if policy_ev else [])
    return result[:limit]
