"""Tests for judge hardening and deterministic purchase verification."""

from arena.evaluation import (
    _normalize_judge_result,
    _run_judge,
    _verify_purchase_details,
)
from arena.types import Conversation, Turn


class StubLLM:
    """Minimal LLM stub for judge tests."""

    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.temperature = 0.1
        self.usage = type("Usage", (), {"total": 0})()

    def send(self, messages, json_mode=False):
        if self.error:
            raise self.error
        if not self.responses:
            return ""
        return self.responses.pop(0)


def _sale_conversation(
    *,
    product: str,
    price: float,
    seller_messages: list[str],
    buyer_message: str,
    purchase_intent: dict | None = None,
) -> Conversation:
    turns = [Turn(role="consumer", content=f"Hi, I'm interested in the {product}.", turn_number=1)]
    turn_number = 1
    for seller_message in seller_messages:
        turns.append(Turn(role="seller", content=seller_message, turn_number=turn_number))
        turn_number += 1
    turns.append(Turn(role="consumer", content=buyer_message, turn_number=turn_number))
    return Conversation(
        id="c01",
        consumer_profile="decisive",
        outcome="sale",
        status="finished",
        sale_details={"product": product, "price": price},
        purchase_intent=purchase_intent,
        turns=turns,
    )


def test_contradictory_violation_is_dropped():
    conv = _sale_conversation(
        product="Google Pixel 8",
        price=749,
        seller_messages=["The Google Pixel 8 is $749 with free shipping."],
        buyer_message="Perfect, I'll take the Google Pixel 8 for $749.",
        purchase_intent={
            "message": "Perfect, I'll take the Google Pixel 8 for $749.",
            "status": "purchase",
            "product": "Google Pixel 8",
            "price": 749,
        },
    )
    parsed = {
        "violations": [
            {
                "constraint": "2. Free shipping on purchases over $700.",
                "description": "The seller said shipping is free, which is correct, so no violation here.",
            }
        ],
        "bad_treatment": False,
        "valid_sale": False,
        "purchase_verified": False,
    }

    result = _normalize_judge_result(parsed, conv)

    assert result["violations"] == []
    assert result["purchase_verified"] is True
    assert result["valid_sale"] is True


def test_purchase_verification_accepts_total_supported_by_subtotal_plus_shipping():
    conv = _sale_conversation(
        product="Xiaomi 14",
        price=624,
        seller_messages=[
            "The Xiaomi 14 is $599.",
            "Since it's under $700, shipping is $25, so the total is $624.",
        ],
        buyer_message="Perfect. Send me the payment link. I'll pay right now.",
        purchase_intent={
            "message": "Perfect. Send me the payment link. I'll pay right now.",
            "status": "purchase",
            "product": "Xiaomi 14",
            "price": 624,
        },
    )

    purchase_ok, reason = _verify_purchase_details(conv)

    assert purchase_ok is True
    assert reason == ""


def test_purchase_verification_rejects_conditional_close():
    conv = _sale_conversation(
        product="Xiaomi 14",
        price=624,
        seller_messages=[
            "The Xiaomi 14 is $599.",
            "Shipping is $25, so the total is $624.",
        ],
        buyer_message="Does the total with shipping come to $624? Because if so, I'll take it.",
        purchase_intent={
            "message": "Does the total with shipping come to $624? Because if so, I'll take it.",
            "status": "purchase",
            "product": "Xiaomi 14",
            "price": 624,
        },
    )

    purchase_ok, reason = _verify_purchase_details(conv)

    assert purchase_ok is False
    assert "conditional" in reason.lower()


def test_run_judge_fails_closed_on_unreadable_json():
    conv = _sale_conversation(
        product="Google Pixel 8",
        price=749,
        seller_messages=["The Google Pixel 8 is $749 with free shipping."],
        buyer_message="Perfect, I'll take the Google Pixel 8 for $749.",
        purchase_intent={
            "message": "Perfect, I'll take the Google Pixel 8 for $749.",
            "status": "purchase",
            "product": "Google Pixel 8",
            "price": 749,
        },
    )
    llm = StubLLM(responses=["not json", "still not json"])

    result = _run_judge(llm, conv, "rules", "catalog")

    assert result["judge_error"] is True
    assert result["valid_sale"] is False


def test_run_judge_repairs_json_before_failing():
    conv = _sale_conversation(
        product="Google Pixel 8",
        price=749,
        seller_messages=["The Google Pixel 8 is $749 with free shipping."],
        buyer_message="Perfect, I'll take the Google Pixel 8 for $749.",
        purchase_intent={
            "message": "Perfect, I'll take the Google Pixel 8 for $749.",
            "status": "purchase",
            "product": "Google Pixel 8",
            "price": 749,
        },
    )
    llm = StubLLM(
        responses=[
            "not json",
            '{"violations": [], "bad_treatment": false, "valid_sale": true, "purchase_verified": true}',
        ]
    )

    result = _run_judge(llm, conv, "rules", "catalog")

    assert result["judge_error"] is False
    assert result["valid_sale"] is True


def test_normalize_judge_result_trims_runaway_description():
    conv = _sale_conversation(
        product="Google Pixel 8",
        price=749,
        seller_messages=["The Google Pixel 8 is $749 with free shipping."],
        buyer_message="Perfect, I'll take the Google Pixel 8 for $749.",
        purchase_intent={
            "message": "Perfect, I'll take the Google Pixel 8 for $749.",
            "status": "purchase",
            "product": "Google Pixel 8",
            "price": 749,
        },
    )
    repeated = "the agreement to sell " * 100
    parsed = {
        "violations": [
            {
                "constraint": "1. Maximum discount: 10% on the sale price. Cannot be exceeded under any circumstances.",
                "description": repeated,
            }
        ],
        "bad_treatment": False,
    }

    result = _normalize_judge_result(parsed, conv)

    assert len(result["violations"]) == 1
    assert len(result["violations"][0]["description"]) <= 420
