"""Validator độc lập cho output/ — KHÔNG import src/, KHÔNG gọi LLM.

Đọc thẳng CSV trong data/ bằng pandas, tự suy ra đáp án đúng cho từng case theo
README mục 4-6, rồi so với file JSON trong output/. Đây là lưới an toàn chống
việc agent trả lời trôi.

    py -3 tools/validate_output.py            # kiểm output/
    py -3 tools/validate_output.py --expected # in bảng đáp án suy từ CSV

Exit code 0 nếu sạch, 1 nếu có sai lệch.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
INPUT_DIR = REPO_ROOT / "input"
OUTPUT_DIR = REPO_ROOT / "output"

TOLERANCE_BRL = 0.10
MONEY_EPS = 0.005  # so tiền: lệch dưới nửa xu coi là khớp

# Console Windows mặc định cp1252, không in được tiếng Việt khi pipe.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# `.gitkeep` giữ output/ trong git; hợp lệ ở repo nhưng phải loại khi nén nộp.
ALLOWED_NON_CASE_FILES = {".gitkeep"}

MAX_ENTITY_IDS = 5
MAX_EVIDENCE = 10
MAX_ROOT_CAUSES = 3
MAX_RESPONSIBLE_PARTIES = 3
MAX_ACTIONS = 5

# README mục 4, đúng thứ tự ưu tiên.
ISSUE_PRIORITY = (
    "canceled_order_paid",
    "unavailable_order_paid",
    "late_delivery_seller",
    "late_delivery_logistics",
    "valid_split_payment",
    "unsupported_late_claim",
)
ISSUE_ACTION = {
    "canceled_order_paid": "issue_full_refund",
    "unavailable_order_paid": "issue_full_refund",
    "late_delivery_seller": "refund_freight",
    "late_delivery_logistics": "refund_freight",
    "valid_split_payment": "explain_valid_split_payment",
    "unsupported_late_claim": "reject_late_refund",
}
ISSUE_ROOT_CAUSE = {
    "canceled_order_paid": "ORDER_CANCELED_AFTER_PAYMENT",
    "unavailable_order_paid": "ORDER_UNAVAILABLE_AFTER_PAYMENT",
    "late_delivery_seller": "SELLER_HANDOFF_AFTER_LIMIT",
    "late_delivery_logistics": "CARRIER_DELIVERED_AFTER_ESTIMATE",
    "valid_split_payment": "MULTIPLE_PAYMENTS_RECONCILED",
    "unsupported_late_claim": "DELIVERY_WITHIN_ESTIMATE",
}
ROOT_CAUSE_CODES = set(ISSUE_ROOT_CAUSE.values())
ACTIONS = set(ISSUE_ACTION.values())
PARTY_TYPES = {"platform", "seller", "logistics_provider"}


# --- nạp dữ liệu ---------------------------------------------------------------


@dataclass
class Dataset:
    orders: pd.DataFrame
    items_by_order: dict[str, pd.DataFrame]
    payments_by_order: dict[str, pd.DataFrame]
    seller_ids: set[str]
    order_ids: set[str]


def load_dataset() -> Dataset:
    orders = pd.read_csv(
        DATA_DIR / "olist_orders_dataset.csv",
        parse_dates=[
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ],
    ).set_index("order_id", drop=False)
    items = pd.read_csv(
        DATA_DIR / "olist_order_items_dataset.csv", parse_dates=["shipping_limit_date"]
    ).sort_values(["order_id", "order_item_id"])
    payments = pd.read_csv(DATA_DIR / "olist_order_payments_dataset.csv").sort_values(
        ["order_id", "payment_sequential"]
    )
    sellers = pd.read_csv(DATA_DIR / "olist_sellers_dataset.csv")
    return Dataset(
        orders=orders,
        items_by_order=dict(list(items.groupby("order_id", sort=False))),
        payments_by_order=dict(list(payments.groupby("order_id", sort=False))),
        seller_ids=set(sellers["seller_id"]),
        order_ids=set(orders.index),
    )


# --- suy đáp án từ CSV ---------------------------------------------------------


@dataclass
class Expected:
    case_id: str
    order_id: str
    order_found: bool
    order_status: str
    primary_issue: str
    case_status: str
    root_cause: str
    action: str
    responsible_parties: list[dict[str, str]]
    item_total_brl: float
    freight_total_brl: float
    payment_total_brl: float
    recommended_refund_brl: float
    valid_item_ids: list[str]
    valid_seller_ids: list[str]
    valid_payment_ids: list[str]
    late_seller_ids: list[str] = field(default_factory=list)
    late_vs_estimate: bool = False
    n_payment_rows: int = 0
    reconciled: bool = False


def derive(case_id: str, order_id: str, ds: Dataset) -> Expected:
    """Suy toàn bộ đáp án đúng của một case chỉ từ CSV."""
    found = order_id in ds.order_ids
    if not found:
        return Expected(
            case_id=case_id,
            order_id=order_id,
            order_found=False,
            order_status="",
            primary_issue="unsupported_late_claim",
            case_status="no_action",
            root_cause=ISSUE_ROOT_CAUSE["unsupported_late_claim"],
            action=ISSUE_ACTION["unsupported_late_claim"],
            responsible_parties=[],
            item_total_brl=0.0,
            freight_total_brl=0.0,
            payment_total_brl=0.0,
            recommended_refund_brl=0.0,
            valid_item_ids=[],
            valid_seller_ids=[],
            valid_payment_ids=[],
        )

    row = ds.orders.loc[order_id]
    items = ds.items_by_order.get(order_id)
    pays = ds.payments_by_order.get(order_id)

    item_total = round(float(items["price"].sum()), 2) if items is not None else 0.0
    freight_total = round(float(items["freight_value"].sum()), 2) if items is not None else 0.0
    payment_total = round(float(pays["payment_value"].sum()), 2) if pays is not None else 0.0
    n_pay = len(pays) if pays is not None else 0

    delivered_at = row["order_delivered_customer_date"]
    estimated_at = row["order_estimated_delivery_date"]
    carrier_at = row["order_delivered_carrier_date"]

    late = bool(
        pd.notna(delivered_at) and pd.notna(estimated_at) and delivered_at > estimated_at
    )

    # Seller bàn giao muộn: carrier nhận hàng sau shipping_limit_date của item seller đó.
    late_sellers: list[str] = []
    if items is not None and pd.notna(carrier_at):
        for seller_id, group in items.groupby("seller_id", sort=False):
            if bool((carrier_at > group["shipping_limit_date"]).any()):
                late_sellers.append(str(seller_id))

    reconciled = abs(payment_total - (item_total + freight_total)) <= TOLERANCE_BRL
    status = str(row["order_status"])

    conditions = {
        "canceled_order_paid": status == "canceled" and payment_total > 0,
        "unavailable_order_paid": status == "unavailable" and payment_total > 0,
        "late_delivery_seller": late and bool(late_sellers),
        "late_delivery_logistics": late and not late_sellers,
        "valid_split_payment": n_pay >= 2 and reconciled,
        "unsupported_late_claim": True,  # fallback cuối bảng ưu tiên
    }
    issue = next(i for i in ISSUE_PRIORITY if conditions[i])

    if issue in ("canceled_order_paid", "unavailable_order_paid"):
        refund = payment_total
        parties = [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]
    elif issue == "late_delivery_seller":
        refund = freight_total
        parties = [{"party_type": "seller", "party_id": sid} for sid in late_sellers]
    elif issue == "late_delivery_logistics":
        refund = freight_total
        parties = [{"party_type": "logistics_provider", "party_id": "LOGISTICS_PROVIDER"}]
    else:
        refund = 0.0
        parties = []

    return Expected(
        case_id=case_id,
        order_id=order_id,
        order_found=True,
        order_status=status,
        primary_issue=issue,
        case_status="action_required" if refund > 0 else "no_action",
        root_cause=ISSUE_ROOT_CAUSE[issue],
        action=ISSUE_ACTION[issue],
        responsible_parties=parties,
        item_total_brl=item_total,
        freight_total_brl=freight_total,
        payment_total_brl=payment_total,
        recommended_refund_brl=round(refund, 2),
        valid_item_ids=(
            [f"{order_id}:{int(r)}" for r in items["order_item_id"]] if items is not None else []
        ),
        valid_seller_ids=(
            list(dict.fromkeys(str(s) for s in items["seller_id"])) if items is not None else []
        ),
        valid_payment_ids=(
            [f"{order_id}:{int(s)}" for s in pays["payment_sequential"]] if pays is not None else []
        ),
        late_seller_ids=late_sellers,
        late_vs_estimate=late,
        n_payment_rows=n_pay,
        reconciled=reconciled,
    )


# --- kiểm tra ------------------------------------------------------------------


@dataclass
class Issue:
    case_id: str
    field: str
    got: str
    want: str


def _fmt(value: Any, limit: int = 68) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _money_eq(a: float, b: float) -> bool:
    return abs(a - b) < MONEY_EPS


class Checker:
    def __init__(self, ds: Dataset) -> None:
        self.ds = ds
        self.issues: list[Issue] = []

    def add(self, case_id: str, field_name: str, got: Any, want: Any) -> None:
        self.issues.append(Issue(case_id, field_name, _fmt(got), _fmt(want)))

    # --- A. cấu trúc bàn giao -------------------------------------------------
    def check_handover(self, case_ids: list[str]) -> dict[str, dict[str, Any]]:
        docs: dict[str, dict[str, Any]] = {}
        if not OUTPUT_DIR.is_dir():
            self.add("-", "output/", "không tồn tại", "thư mục output/")
            return docs

        present = {p.name for p in OUTPUT_DIR.iterdir() if p.is_file()}
        wanted = {f"{cid}.json" for cid in case_ids}
        for extra in sorted(present - wanted - ALLOWED_NON_CASE_FILES):
            self.add("-", f"output/{extra}", "file lạ trong output/", "chỉ 50 file EC_*.json")
        for missing in sorted(wanted - present):
            self.add(missing[:-5], "output file", "thiếu", missing)

        for cid in case_ids:
            path = OUTPUT_DIR / f"{cid}.json"
            if not path.exists():
                continue
            try:
                docs[cid] = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as err:
                self.add(cid, "json", f"parse lỗi: {err}", "JSON hợp lệ")
        return docs

    def check_schema(self, cid: str, doc: dict[str, Any]) -> bool:
        """Kiểm field bắt buộc + kiểu. Trả False nếu hỏng tới mức không kiểm tiếp được."""
        ok = True

        if doc.get("case_id") != cid:
            self.add(cid, "case_id", doc.get("case_id"), cid)

        spec: list[tuple[str, Any, type | tuple[type, ...]]] = [
            ("assessment", dict, dict),
            ("affected_entities", dict, dict),
            ("root_cause_analysis", dict, dict),
            ("evidence_ids", list, list),
            ("financial_resolution", dict, dict),
            ("resolution_actions", list, list),
        ]
        for key, _, kind in spec:
            if key not in doc:
                self.add(cid, key, "thiếu", f"{kind.__name__} theo schema mục 6")
                ok = False
            elif not isinstance(doc[key], kind):
                self.add(cid, key, type(doc[key]).__name__, kind.__name__)
                ok = False
        if not ok:
            return False

        assess = doc["assessment"]
        for key, kind in (("primary_issue", str), ("case_status", str), ("confidence", (int, float))):
            if key not in assess:
                self.add(cid, f"assessment.{key}", "thiếu", key)
                ok = False
            elif not isinstance(assess[key], kind) or isinstance(assess[key], bool):
                self.add(cid, f"assessment.{key}", type(assess[key]).__name__, getattr(kind, "__name__", "float"))
                ok = False
        conf = assess.get("confidence")
        if isinstance(conf, (int, float)) and not isinstance(conf, bool) and not 0.0 <= conf <= 1.0:
            self.add(cid, "assessment.confidence", conf, "trong [0, 1]")
        if assess.get("case_status") not in ("action_required", "no_action"):
            self.add(cid, "assessment.case_status", assess.get("case_status"), "action_required|no_action")

        ent = doc["affected_entities"]
        for key in ("order_ids", "item_ids", "seller_ids", "payment_ids"):
            values = ent.get(key)
            if not isinstance(values, list):
                self.add(cid, f"affected_entities.{key}", type(values).__name__, "list[str]")
                ok = False
                continue
            if any(not isinstance(v, str) for v in values):
                self.add(cid, f"affected_entities.{key}", values, "list[str]")
            if len(values) > MAX_ENTITY_IDS:
                self.add(cid, f"affected_entities.{key} (số lượng)", len(values), f"<= {MAX_ENTITY_IDS}")

        rca = doc["root_cause_analysis"]
        causes = rca.get("ranked_causes")
        if not isinstance(causes, list):
            self.add(cid, "root_cause_analysis.ranked_causes", type(causes).__name__, "list")
            ok = False
        else:
            if len(causes) > MAX_ROOT_CAUSES:
                self.add(cid, "ranked_causes (số lượng)", len(causes), f"<= {MAX_ROOT_CAUSES}")
            for idx, cause in enumerate(causes):
                if not isinstance(cause, dict):
                    self.add(cid, f"ranked_causes[{idx}]", type(cause).__name__, "dict")
                    continue
                if cause.get("cause_code") not in ROOT_CAUSE_CODES:
                    self.add(cid, f"ranked_causes[{idx}].cause_code", cause.get("cause_code"), "mã trong README mục 4")
                if not isinstance(cause.get("rank"), int) or isinstance(cause.get("rank"), bool):
                    self.add(cid, f"ranked_causes[{idx}].rank", cause.get("rank"), "int >= 1")
                elif cause["rank"] < 1:
                    self.add(cid, f"ranked_causes[{idx}].rank", cause["rank"], "int >= 1")

        parties = rca.get("responsible_parties")
        if not isinstance(parties, list):
            self.add(cid, "root_cause_analysis.responsible_parties", type(parties).__name__, "list")
            ok = False
        else:
            if len(parties) > MAX_RESPONSIBLE_PARTIES:
                self.add(cid, "responsible_parties (số lượng)", len(parties), f"<= {MAX_RESPONSIBLE_PARTIES}")
            for idx, party in enumerate(parties):
                if not isinstance(party, dict):
                    self.add(cid, f"responsible_parties[{idx}]", type(party).__name__, "dict")
                    continue
                if party.get("party_type") not in PARTY_TYPES:
                    self.add(cid, f"responsible_parties[{idx}].party_type", party.get("party_type"), "|".join(sorted(PARTY_TYPES)))
                if not isinstance(party.get("party_id"), str) or not party.get("party_id"):
                    self.add(cid, f"responsible_parties[{idx}].party_id", party.get("party_id"), "str không rỗng")

        if len(doc["evidence_ids"]) > MAX_EVIDENCE:
            self.add(cid, "evidence_ids (số lượng)", len(doc["evidence_ids"]), f"<= {MAX_EVIDENCE}")
        if any(not isinstance(e, str) for e in doc["evidence_ids"]):
            self.add(cid, "evidence_ids", "phần tử không phải str", "list[str]")
            ok = False

        fin = doc["financial_resolution"]
        if fin.get("currency") != "BRL":
            self.add(cid, "financial_resolution.currency", fin.get("currency"), "BRL")
        for key in ("item_total_brl", "freight_total_brl", "payment_total_brl", "recommended_refund_brl"):
            value = fin.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                self.add(cid, f"financial_resolution.{key}", value, "float")
                ok = False

        acts = doc["resolution_actions"]
        if len(acts) > MAX_ACTIONS:
            self.add(cid, "resolution_actions (số lượng)", len(acts), f"<= {MAX_ACTIONS}")
        for act in acts:
            if act not in ACTIONS:
                self.add(cid, "resolution_actions", act, "|".join(sorted(ACTIONS)))
        return ok

    # --- B. nghiệp vụ ---------------------------------------------------------
    def check_business(self, cid: str, doc: dict[str, Any], exp: Expected) -> None:
        assess = doc["assessment"]
        got_issue = assess.get("primary_issue")
        if got_issue != exp.primary_issue:
            self.add(cid, "assessment.primary_issue", got_issue, exp.primary_issue)

        # Kiểm chéo cả 4 vế của bảng, không chỉ primary_issue.
        causes = [c for c in doc["root_cause_analysis"].get("ranked_causes", []) if isinstance(c, dict)]
        top = min(causes, key=lambda c: c.get("rank", 99), default=None)
        if top is None:
            self.add(cid, "ranked_causes", "rỗng", f"rank 1 = {exp.root_cause}")
        elif top.get("cause_code") != exp.root_cause:
            self.add(cid, "ranked_causes rank1.cause_code", top.get("cause_code"), exp.root_cause)

        got_parties = [
            {"party_type": p.get("party_type"), "party_id": p.get("party_id")}
            for p in doc["root_cause_analysis"].get("responsible_parties", [])
            if isinstance(p, dict)
        ]
        want_parties = exp.responsible_parties
        if sorted(map(json.dumps, got_parties), key=str) != sorted(map(json.dumps, want_parties), key=str):
            self.add(cid, "responsible_parties", got_parties, want_parties)

        acts = doc["resolution_actions"]
        if acts != [exp.action]:
            self.add(cid, "resolution_actions", acts, [exp.action])

        refund = doc["financial_resolution"].get("recommended_refund_brl")
        want_status = "action_required" if isinstance(refund, (int, float)) and refund > 0 else "no_action"
        if assess.get("case_status") != want_status:
            self.add(cid, "assessment.case_status", assess.get("case_status"), f"{want_status} (refund={refund})")
        if assess.get("case_status") != exp.case_status:
            self.add(cid, "assessment.case_status (theo CSV)", assess.get("case_status"), exp.case_status)

    # --- C. tiền --------------------------------------------------------------
    def check_money(self, cid: str, doc: dict[str, Any], exp: Expected) -> None:
        fin = doc["financial_resolution"]
        for key, want in (
            ("item_total_brl", exp.item_total_brl),
            ("freight_total_brl", exp.freight_total_brl),
            ("payment_total_brl", exp.payment_total_brl),
            ("recommended_refund_brl", exp.recommended_refund_brl),
        ):
            got = fin.get(key)
            if not isinstance(got, (int, float)) or isinstance(got, bool):
                continue
            if not _money_eq(float(got), want):
                self.add(cid, f"financial_resolution.{key}", got, want)
            if round(float(got), 2) != float(got):
                self.add(cid, f"financial_resolution.{key} (làm tròn)", got, round(float(got), 2))

        # Order không có item row: item/seller rỗng, tiền hàng và freight = 0.0
        if not exp.valid_item_ids:
            ent = doc["affected_entities"]
            if ent.get("item_ids"):
                self.add(cid, "affected_entities.item_ids", ent.get("item_ids"), "[] (order không có item row)")
            if ent.get("seller_ids"):
                self.add(cid, "affected_entities.seller_ids", ent.get("seller_ids"), "[] (order không có item row)")

    # --- entity ---------------------------------------------------------------
    def check_entities(self, cid: str, doc: dict[str, Any], exp: Expected) -> None:
        ent = doc["affected_entities"]

        if ent.get("order_ids") != [exp.order_id]:
            self.add(cid, "affected_entities.order_ids", ent.get("order_ids"), [exp.order_id])

        for key, valid in (
            ("item_ids", exp.valid_item_ids),
            ("seller_ids", exp.valid_seller_ids),
            ("payment_ids", exp.valid_payment_ids),
        ):
            got = [v for v in ent.get(key, []) if isinstance(v, str)]
            if len(set(got)) != len(got):
                self.add(cid, f"affected_entities.{key} (trùng)", got, "không trùng lặp")
            for value in got:
                if value not in valid:
                    self.add(cid, f"affected_entities.{key}", value, f"phải nằm trong {_fmt(valid, 44)}")
            if valid and not got:
                self.add(cid, f"affected_entities.{key}", "[]", f"ít nhất 1 trong {_fmt(valid, 44)}")

    # --- D. evidence ----------------------------------------------------------
    def check_evidence(self, cid: str, doc: dict[str, Any], exp: Expected) -> None:
        evidence = [e for e in doc["evidence_ids"] if isinstance(e, str)]

        if len(set(evidence)) != len(evidence):
            dupes = sorted({e for e in evidence if evidence.count(e) > 1})
            self.add(cid, "evidence_ids (trùng)", dupes, "mỗi ID xuất hiện 1 lần")

        # Tập ID dựng được từ CSV cho order này.
        known_order = {f"order:{exp.order_id}"} if exp.order_found else set()
        known_item = {f"item:{i}" for i in exp.valid_item_ids}
        known_payment = {f"payment:{p}" for p in exp.valid_payment_ids}
        known_seller = {f"seller:{s}" for s in exp.valid_seller_ids}

        cited_causes = {
            c.get("cause_code")
            for c in doc["root_cause_analysis"].get("ranked_causes", [])
            if isinstance(c, dict)
        }

        for ev in evidence:
            kind, sep, rest = ev.partition(":")
            if not sep:
                self.add(cid, "evidence_ids (định dạng)", ev, "order:|item:|payment:|seller:|policy:")
                continue

            if kind == "order":
                if ev not in known_order:
                    want = f"order:{exp.order_id}" if exp.order_found else "order_id có thật trong CSV"
                    self.add(cid, "evidence order: không có trong CSV", ev, want)
            elif kind == "item":
                if ev not in known_item:
                    self.add(cid, "evidence item: không có trong CSV", ev, _fmt(sorted(known_item), 60) or "order không có item row")
            elif kind == "payment":
                if ev not in known_payment:
                    self.add(cid, "evidence payment: không có trong CSV", ev, _fmt(sorted(known_payment), 60))
            elif kind == "seller":
                if ev not in known_seller:
                    if rest in self.ds.seller_ids:
                        self.add(cid, "evidence seller: không thuộc order này", ev, _fmt(sorted(known_seller), 56) or "order không có seller")
                    else:
                        self.add(cid, "evidence seller: không có trong CSV", ev, "seller_id có thật")
            elif kind == "policy":
                if rest not in ROOT_CAUSE_CODES:
                    self.add(cid, "evidence policy: mã lạ", ev, "mã trong README mục 4")
                elif rest not in cited_causes:
                    self.add(cid, "evidence policy: không khớp root cause", ev, f"policy trong {_fmt(sorted(cited_causes), 48)}")
            else:
                self.add(cid, "evidence_ids (định dạng)", ev, "order:|item:|payment:|seller:|policy:")

        want_policy = f"policy:{exp.root_cause}"
        if want_policy not in evidence:
            self.add(cid, "evidence_ids", "thiếu policy của root cause chính", want_policy)


def load_case_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for path in sorted(INPUT_DIR.glob("EC_*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        index[case["case_id"]] = case["customer_request"]["claimed_order_id"]
    return index


def print_expected(cases: dict[str, str], ds: Dataset) -> None:
    header = f"{'case_id':<8} {'primary_issue':<24} {'refund':>10} {'item':>9} {'freight':>8} {'payment':>10}  parties"
    print(header)
    print("-" * len(header))
    for cid, oid in cases.items():
        e = derive(cid, oid, ds)
        parties = ",".join(f"{p['party_type']}/{p['party_id'][:12]}" for p in e.responsible_parties) or "-"
        print(
            f"{cid:<8} {e.primary_issue:<24} {e.recommended_refund_brl:>10.2f} "
            f"{e.item_total_brl:>9.2f} {e.freight_total_brl:>8.2f} {e.payment_total_brl:>10.2f}  {parties}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validator độc lập cho output/ (không dùng LLM)")
    parser.add_argument("--expected", action="store_true", help="chỉ in bảng đáp án suy từ CSV")
    parser.add_argument("--max-rows", type=int, default=0, help="giới hạn số dòng lỗi in ra (0 = in hết)")
    args = parser.parse_args(argv)

    cases = load_case_index()
    if not cases:
        print("Không tìm thấy input/EC_*.json", file=sys.stderr)
        return 1
    ds = load_dataset()

    if args.expected:
        print_expected(cases, ds)
        return 0

    checker = Checker(ds)
    docs = checker.check_handover(list(cases))

    for cid, order_id in cases.items():
        doc = docs.get(cid)
        if doc is None:
            continue
        if not isinstance(doc, dict):
            checker.add(cid, "root", type(doc).__name__, "JSON object")
            continue
        if not checker.check_schema(cid, doc):
            continue
        expected = derive(cid, order_id, ds)
        checker.check_business(cid, doc, expected)
        checker.check_money(cid, doc, expected)
        checker.check_entities(cid, doc, expected)
        checker.check_evidence(cid, doc, expected)

    issues = checker.issues
    if not issues:
        print(f"OK — {len(docs)}/{len(cases)} case hợp lệ: schema, nghiệp vụ, tiền và evidence đều khớp CSV.")
        return 0

    shown = issues[: args.max_rows] if args.max_rows else issues
    widths = (
        max(8, *(len(i.case_id) for i in shown)),
        max(24, *(len(i.field) for i in shown)),
        max(20, *(len(i.got) for i in shown)),
    )
    header = f"{'case_id':<{widths[0]}}  {'field sai':<{widths[1]}}  {'giá trị output':<{widths[2]}}  giá trị đúng theo CSV"
    print(header)
    print("-" * len(header))
    for issue in shown:
        print(f"{issue.case_id:<{widths[0]}}  {issue.field:<{widths[1]}}  {issue.got:<{widths[2]}}  {issue.want}")
    if len(shown) < len(issues):
        print(f"... còn {len(issues) - len(shown)} lỗi nữa")

    cases_with_issues = len({i.case_id for i in issues} - {"-"})
    print(f"\nFAIL — {len(issues)} sai lệch trên {cases_with_issues} case.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
