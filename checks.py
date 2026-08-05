"""Cổng kiểm tra trước khi nộp — chạy độc lập với pipeline.

    python checks.py

Kiểm tra lại output/ từ đầu bằng dữ liệu CSV gốc, KHÔNG dùng lại kết quả trung
gian của agent nào. Mục đích là bắt được cả lỗi mà chính Verifier Agent bỏ sót.

Exit code 0 = sẵn sàng nộp, 1 = còn lỗi.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from data_access import evidence as ev
from data_access import policy_table
from data_access.data_store import DataStore
from schema.output_schema import (
    MAX_ACTIONS,
    MAX_ENTITY_IDS,
    MAX_EVIDENCE,
    MAX_RESPONSIBLE_PARTIES,
    MAX_ROOT_CAUSES,
    CaseOutput,
)

ROOT = Path(__file__).resolve().parent
EXPECTED_CASES = [f"EC_{i:03d}" for i in range(1, 51)]
MONEY_EPS = 0.011

VALID_ACTIONS = {
    "issue_full_refund",
    "refund_freight",
    "explain_valid_split_payment",
    "reject_late_refund",
}


def check_file_set(output_dir: Path) -> list[str]:
    """Zip nộp bài phải chứa đúng 50 JSON, không file lạ (README mục 8)."""
    errors = []
    if not output_dir.is_dir():
        return [f"không có thư mục {output_dir}"]

    present = sorted(p.name for p in output_dir.iterdir() if p.is_file())
    expected = {f"{c}.json" for c in EXPECTED_CASES}

    for missing in sorted(expected - set(present)):
        errors.append(f"thiếu file {missing}")
    for extra in sorted(set(present) - expected):
        errors.append(f"file lạ trong output/: {extra}")
    return errors


def check_case(case_id: str, data: dict, store: DataStore, claimed_order_id: str) -> list[str]:
    errors: list[str] = []

    try:
        CaseOutput.model_validate(data)
    except Exception as exc:  # pydantic ValidationError
        return [f"schema không hợp lệ: {str(exc)[:200]}"]

    if data["case_id"] != case_id:
        errors.append(f"case_id trong file là {data['case_id']}, không khớp tên file")

    assessment = data["assessment"]
    entities = data["affected_entities"]
    financial = data["financial_resolution"]

    # --- primary_issue phải nằm trong bảng policy
    rule = next(
        (r for r in policy_table.POLICY_RULES if r.primary_issue == assessment["primary_issue"]),
        None,
    )
    if rule is None:
        errors.append(f"primary_issue lạ: {assessment['primary_issue']}")
        return errors

    # --- tính lại tiền từ CSV
    item_total, freight_total, payment_total = store.totals(claimed_order_id)
    for field, want in (
        ("item_total_brl", item_total),
        ("freight_total_brl", freight_total),
        ("payment_total_brl", payment_total),
    ):
        if abs(float(financial[field]) - want) > MONEY_EPS:
            errors.append(f"{field}={financial[field]} nhưng CSV cho {want}")

    want_refund = policy_table.refund_for(
        rule, {"payment_total_brl": payment_total, "freight_total_brl": freight_total}
    )
    if abs(float(financial["recommended_refund_brl"]) - want_refund) > MONEY_EPS:
        errors.append(
            f"refund={financial['recommended_refund_brl']} != {rule.refund_basis}={want_refund}"
        )
    if financial["currency"] != "BRL":
        errors.append(f"currency={financial['currency']}")

    # --- evidence phải dựng được từ CSV
    for evidence_id in data["evidence_ids"]:
        if not ev.is_well_formed(evidence_id):
            errors.append(f"evidence sai định dạng: {evidence_id}")
        elif not store.evidence_exists(evidence_id):
            errors.append(f"evidence không có trong CSV: {evidence_id}")
    if len(set(data["evidence_ids"])) != len(data["evidence_ids"]):
        errors.append("evidence_ids có phần tử trùng")

    # --- entity phải có thật
    real_items = {i.entity_id for i in store.get_items(claimed_order_id)}
    real_payments = {p.entity_id for p in store.get_payments(claimed_order_id)}
    for oid in entities["order_ids"]:
        if store.get_order(oid) is None:
            errors.append(f"order_id không tồn tại: {oid}")
    for iid in entities["item_ids"]:
        if iid not in real_items:
            errors.append(f"item_id không tồn tại: {iid}")
    for pid in entities["payment_ids"]:
        if pid not in real_payments:
            errors.append(f"payment_id không tồn tại: {pid}")
    for sid in entities["seller_ids"]:
        if store.get_seller(sid) is None:
            errors.append(f"seller_id không tồn tại: {sid}")

    # --- quy tắc riêng: đơn không có item row (README mục 6)
    if not real_items:
        if entities["item_ids"] or entities["seller_ids"]:
            errors.append("đơn không có item row nhưng item_ids/seller_ids không rỗng")
        if financial["item_total_brl"] != 0.0 or financial["freight_total_brl"] != 0.0:
            errors.append("đơn không có item row nhưng item_total/freight_total != 0.0")

    # --- giới hạn độ dài
    for key, limit in (
        ("order_ids", MAX_ENTITY_IDS),
        ("item_ids", MAX_ENTITY_IDS),
        ("seller_ids", MAX_ENTITY_IDS),
        ("payment_ids", MAX_ENTITY_IDS),
    ):
        if len(entities[key]) > limit:
            errors.append(f"{key} có {len(entities[key])} phần tử (>{limit})")
    if len(data["evidence_ids"]) > MAX_EVIDENCE:
        errors.append(f"evidence_ids vượt {MAX_EVIDENCE}")

    rca = data["root_cause_analysis"]
    if len(rca["ranked_causes"]) > MAX_ROOT_CAUSES:
        errors.append(f"ranked_causes vượt {MAX_ROOT_CAUSES}")
    if len(rca["responsible_parties"]) > MAX_RESPONSIBLE_PARTIES:
        errors.append(f"responsible_parties vượt {MAX_RESPONSIBLE_PARTIES}")
    if len(data["resolution_actions"]) > MAX_ACTIONS:
        errors.append(f"resolution_actions vượt {MAX_ACTIONS}")

    # --- nội dung root cause / action / party
    if not rca["ranked_causes"]:
        errors.append("ranked_causes rỗng")
    for cause in rca["ranked_causes"]:
        if cause["cause_code"] not in policy_table.ROOT_CAUSE_CODES:
            errors.append(f"cause_code lạ: {cause['cause_code']}")
    if rca["ranked_causes"] and rca["ranked_causes"][0]["cause_code"] != rule.root_cause_code:
        errors.append(
            f"root cause rank 1 = {rca['ranked_causes'][0]['cause_code']}, "
            f"đáng lẽ {rule.root_cause_code}"
        )
    for action in data["resolution_actions"]:
        if action not in VALID_ACTIONS:
            errors.append(f"action lạ: {action}")
    if data["resolution_actions"] != [rule.action]:
        errors.append(f"actions={data['resolution_actions']}, đáng lẽ ['{rule.action}']")

    for party in rca["responsible_parties"]:
        ptype, pid = party["party_type"], party["party_id"]
        if ptype == "seller" and store.get_seller(pid) is None:
            errors.append(f"responsible seller không tồn tại: {pid}")
        if ptype == "platform" and pid != policy_table.PLATFORM_PARTY_ID:
            errors.append(f"platform party_id lạ: {pid}")
        if ptype == "logistics_provider" and pid != policy_table.LOGISTICS_PARTY_ID:
            errors.append(f"logistics party_id lạ: {pid}")

    # --- case_status phải nhất quán với refund
    status = assessment["case_status"]
    refund = float(financial["recommended_refund_brl"])
    if status != rule.case_status:
        errors.append(f"case_status={status}, rule {rule.primary_issue} yêu cầu {rule.case_status}")
    if status == "action_required" and refund <= 0:
        errors.append("action_required nhưng refund=0")
    if status == "no_action" and refund > 0:
        errors.append(f"no_action nhưng refund={refund}")

    return errors


def main() -> int:
    output_dir = ROOT / "output"
    input_dir = ROOT / "input"

    print("Đang load CSV ...")
    store = DataStore(ROOT / "data")

    all_errors: dict[str, list[str]] = {}
    file_errors = check_file_set(output_dir)
    if file_errors:
        all_errors["<bộ file>"] = file_errors

    issues: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    total_refund = 0.0

    for case_id in EXPECTED_CASES:
        out_path = output_dir / f"{case_id}.json"
        in_path = input_dir / f"{case_id}.json"
        if not out_path.exists() or not in_path.exists():
            continue

        case_input = json.loads(in_path.read_text(encoding="utf-8"))
        claimed_order_id = case_input["customer_request"]["claimed_order_id"]
        data = json.loads(out_path.read_text(encoding="utf-8"))

        errors = check_case(case_id, data, store, claimed_order_id)
        if errors:
            all_errors[case_id] = errors

        issues[data["assessment"]["primary_issue"]] += 1
        statuses[data["assessment"]["case_status"]] += 1
        total_refund += float(data["financial_resolution"]["recommended_refund_brl"])

    print("\n--- phân bố kết luận ---")
    for issue, count in issues.most_common():
        print(f"  {issue:<26} {count}")
    print(f"  {'case_status':<26} {dict(statuses)}")
    print(f"  {'tổng refund đề xuất':<26} {round(total_refund, 2)} BRL")

    print("\n--- kết quả kiểm tra ---")
    if not all_errors:
        print(f"  PASS — {len(EXPECTED_CASES)} case hợp lệ, sẵn sàng nén output/ để nộp.")
        return 0

    count = sum(len(v) for v in all_errors.values())
    print(f"  FAIL — {count} lỗi trên {len(all_errors)} case:")
    for case_id, errors in all_errors.items():
        for error in errors:
            print(f"    {case_id}: {error}")
    return 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
