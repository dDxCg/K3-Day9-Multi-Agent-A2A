from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import AppConfig
from .domain import (
    CaseRequest,
    DeliveryHandoff,
    OrderSellerHandoff,
    PaymentHandoff,
    PolicyDecision,
)


@dataclass(frozen=True, slots=True)
class ReviewResult:
    status: str
    verdict: str
    reason: str
    attempts: int

    def trace_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "verdict": self.verdict,
            "reason": self.reason,
            "attempts": self.attempts,
        }


class GeminiPolicyReviewer:
    """Independent LLM review; never changes deterministic output."""

    name = "GeminiPolicyReviewerAgent"

    def __init__(self, config: AppConfig) -> None:
        self.model = config.model
        self.api_key = config.google_api_key
        self.temperature = config.temperature
        self.top_p = config.top_p
        self.max_output_tokens = min(config.max_output_tokens, 128)
        self.timeout = config.request_timeout_seconds
        self.max_retries = max(1, config.max_retries)
        self.request_delay_seconds = max(0.0, config.request_delay_seconds)
        self._last_request_at = 0.0

    def review(
        self,
        case: CaseRequest,
        order_facts: OrderSellerHandoff,
        payment_facts: PaymentHandoff,
        delivery_facts: DeliveryHandoff,
        decision: PolicyDecision,
    ) -> ReviewResult:
        facts = {
            "case_id": case.case_id,
            "claim": case.message,
            "order": order_facts.trace_payload(),
            "payment": payment_facts.trace_payload(),
            "delivery": delivery_facts.trace_payload(),
            "deterministic_decision": decision.trace_payload(),
        }
        prompt = (
            "You are a strict verifier for EC_POLICY_V1. Use this exact ordered truth table: "
            "(1) canceled and paid => canceled_order_paid; "
            "(2) unavailable and paid => unavailable_order_paid; "
            "(3) delivered_late=true and seller_handoff_late=true => late_delivery_seller; "
            "(4) delivered_late=true and seller_handoff_late=false => late_delivery_logistics; "
            "(5) split_payment=true and reconciled=true => valid_split_payment; "
            "(6) delivered_late=false and reconciled=true => unsupported_late_claim. "
            "Rule 6 means data contradicts the customer's late claim, so that decision is correct. "
            "Rule 4 means logistics is responsible after an on-time seller handoff; never describe "
            "the customer as delivering the order. Review only the primary issue, cause, party, "
            "refund and action. Do not invent data or infer beyond supplied booleans. Return JSON with "
            'keys verdict ("agree" or "disagree") and reason (max 30 words).\nFACTS:\n'
            + json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "topP": self.top_p,
                "maxOutputTokens": self.max_output_tokens,
                "responseMimeType": "application/json",
            },
        }
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
            method="POST",
        )

        last_error = "unknown error"
        for attempt in range(1, self.max_retries + 1):
            elapsed = time.monotonic() - self._last_request_at
            remaining_delay = self.request_delay_seconds - elapsed
            if remaining_delay > 0:
                time.sleep(remaining_delay)
            self._last_request_at = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                text = body["candidates"][0]["content"]["parts"][0]["text"]
                parsed = self._parse_json_object(text)
                verdict = str(parsed.get("verdict", "")).lower()
                reason = str(parsed.get("reason", "")).strip()[:300]
                if verdict not in {"agree", "disagree"}:
                    raise ValueError(f"Unexpected verdict: {verdict!r}")
                return ReviewResult("success", verdict, reason, attempt)
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}"
                if exc.code not in {429, 500, 502, 503, 504}:
                    break
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                if retry_after and retry_after.isdigit():
                    time.sleep(min(int(retry_after), 60))
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
                last_error = type(exc).__name__
            if attempt < self.max_retries:
                time.sleep(min(2 ** (attempt - 1), 4))

        return ReviewResult("error", "unavailable", last_error, self.max_retries)

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any]:
        """Accept strict JSON plus harmless markdown fences around it."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
        start = cleaned.find("{")
        if start < 0:
            raise json.JSONDecodeError("No JSON object", cleaned, 0)
        parsed, _ = json.JSONDecoder().raw_decode(cleaned[start:])
        if not isinstance(parsed, dict):
            raise ValueError("Reviewer response must be a JSON object")
        return parsed
