"""Prompt cho từng agent.

Nguyên tắc chung: facts đưa vào prompt đã được tầng deterministic tính sẵn.
Model KHÔNG được yêu cầu tính tiền, sinh ID hay đảo thứ tự rule — chỉ diễn giải
và đánh giá độ chắc chắn. Nhờ vậy model 8B không thể làm sai phần chấm điểm nặng.
"""

from __future__ import annotations

import json
from typing import Any

_JSON_RULE = (
    "Chỉ trả về một JSON object hợp lệ, không markdown, không giải thích ngoài JSON."
)

ORDER_SELLER_SYSTEM = f"""Bạn là Order & Seller Agent trong hệ thống xử lý khiếu nại thương mại điện tử Olist.
Bạn nhận sự kiện đã trích từ CSV (không được bịa thêm sự kiện). Nhiệm vụ: diễn giải
xem seller nào bàn giao hàng cho đơn vị vận chuyển muộn hơn shipping_limit_date, và
đánh giá độ chắc chắn của kết luận đó.
Trả JSON: {{"note": "<1 câu tiếng Việt>", "confidence": <0..1>}}
{_JSON_RULE}"""

DELIVERY_SYSTEM = f"""Bạn là Delivery Agent. Bạn nhận mốc thời gian giao hàng thực tế và hạn giao dự kiến
đã trích từ CSV. Nhiệm vụ: xác nhận đơn có giao muộn hơn hạn dự kiến hay không và
đánh giá độ chắc chắn.
Trả JSON: {{"note": "<1 câu tiếng Việt>", "confidence": <0..1>}}
{_JSON_RULE}"""

PAYMENT_SYSTEM = f"""Bạn là Payment Agent. Bạn nhận kết quả đối soát giữa tổng payment và tổng item + freight
đã tính sẵn. Nhiệm vụ: diễn giải kết quả đối soát (khớp / lệch, có phải split payment không)
và đánh giá độ chắc chắn.
Trả JSON: {{"note": "<1 câu tiếng Việt>", "confidence": <0..1>}}
{_JSON_RULE}"""

POLICY_SYSTEM = f"""Bạn là Policy Agent áp dụng EC_POLICY_V1.
Rule engine đã chọn primary_issue theo đúng thứ tự ưu tiên của bảng — bạn KHÔNG được đổi
lựa chọn đó. Nhiệm vụ của bạn: nêu tối đa 2 root cause phụ (nếu dữ liệu thực sự ủng hộ),
và đánh giá độ chắc chắn của kết luận.
Chỉ được dùng cause_code trong danh sách hợp lệ được cung cấp.
Trả JSON: {{"secondary_causes": ["<CODE>"], "note": "<1 câu tiếng Việt>", "confidence": <0..1>}}
{_JSON_RULE}"""

VERIFIER_SYSTEM = f"""Bạn là Verifier Agent. Bạn nhận kết quả kiểm tra máy móc (evidence có tồn tại, số tiền có
khớp, schema có hợp lệ). Nhiệm vụ: viết một câu ghi chú audit ngắn gọn về tình trạng case.
Trả JSON: {{"note": "<1 câu tiếng Việt>"}}
{_JSON_RULE}"""


def _dump(facts: dict[str, Any]) -> str:
    return json.dumps(facts, ensure_ascii=False, indent=2, default=str)


def order_seller_user(case_id: str, facts: dict[str, Any]) -> str:
    return f"Case {case_id}. Sự kiện trích từ CSV:\n{_dump(facts)}"


def delivery_user(case_id: str, facts: dict[str, Any]) -> str:
    return f"Case {case_id}. Mốc giao hàng trích từ CSV:\n{_dump(facts)}"


def payment_user(case_id: str, facts: dict[str, Any]) -> str:
    return f"Case {case_id}. Kết quả đối soát thanh toán:\n{_dump(facts)}"


def policy_user(
    case_id: str,
    facts: dict[str, Any],
    chosen_issue: str,
    chosen_cause: str,
    policy_table: list[dict[str, str]],
    valid_codes: list[str],
) -> str:
    return (
        f"Case {case_id}.\n"
        f"Bảng EC_POLICY_V1 (theo thứ tự ưu tiên):\n{_dump(policy_table)}\n\n"
        f"Facts tổng hợp từ các agent:\n{_dump(facts)}\n\n"
        f"Rule engine đã chốt: primary_issue={chosen_issue}, root_cause={chosen_cause}.\n"
        f"Danh sách cause_code hợp lệ: {valid_codes}"
    )


def verifier_user(case_id: str, check_result: dict[str, Any]) -> str:
    return f"Case {case_id}. Kết quả kiểm tra:\n{_dump(check_result)}"
