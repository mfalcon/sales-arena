"""Tests for judge hardening and deterministic purchase verification."""

import pytest

from arena.evaluation import (
    _is_explicit_purchase_message,
    _normalize_judge_result,
    _run_judge,
    _verify_purchase_details,
)
from arena.types import Conversation, Turn


@pytest.mark.parametrize("message", [
    "I'll buy it",
    "I'll purchase it",
    "Please send payment details",
    "Send the payment link",
    "I'll take it",
    "Let's do it",
    "Works for me",
    "Deal!",
    "deal",
    "sold",
    "Go ahead",
    "Ready to pay",
    "I want to buy",
])
def test_explicit_purchase_accepts_common_close_phrases(message):
    assert _is_explicit_purchase_message(message), f"should accept: {message!r}"


@pytest.mark.parametrize("message", [
    "That sounds like a good deal",
    "Sounds like a great deal",
    "Hmm, sounds good but let me think",
    "I think I'll take it",
    "Maybe I'll take it",
    "If you can do that, I'll take it",
    "Sounds good, but can you confirm specs?",
    "I'm sold on the idea, but let me check",
])
def test_explicit_purchase_rejects_hedging_or_conditional(message):
    assert not _is_explicit_purchase_message(message), f"should reject: {message!r}"


@pytest.mark.parametrize("message", [
    "Thanks, but I'll take the Google Pixel 8 at $749",
    "It's expensive but I'll buy it",
    "I was unsure, but go ahead",
])
def test_contrastive_but_does_not_block_clear_close(message):
    """A contrastive 'but' followed by an unhedged close is still a real purchase."""
    assert _is_explicit_purchase_message(message), f"should accept: {message!r}"


def test_purchase_verification_accepts_canonical_product_variant():
    """sale_details holds the canonical name; purchase_intent may carry the raw consumer wording."""
    conv = _sale_conversation(
        product="Samsung Galaxy S24 Ultra",
        price=1399,
        seller_messages=["The Samsung Galaxy S24 Ultra is $1399 with free shipping."],
        buyer_message="I'll take it.",
        purchase_intent={
            "message": "I'll take it.",
            "status": "purchase",
            "product": "Samsung Galaxy S24 Ultra 256GB",
            "price": 1399,
        },
    )

    purchase_ok, reason = _verify_purchase_details(conv)

    assert purchase_ok is True
    assert reason == ""


CATALOG_TEXT = """
| Product | Brand | Cost | Sale Price | Stock | Specs |
|---|---|---|---|---|---|
| iPhone 15 | Apple | $799 | $1,099 | 5 | 6.1", A16 Bionic, 128GB, 48MP |
| Samsung Galaxy S24 Ultra | Samsung | $1,049 | $1,399 | 4 | 6.8", Snapdragon 8 Gen 3, 256GB, 200MP, S Pen |
| Google Pixel 8 | Google | $549 | $749 | 6 | 6.2", Tensor G3, 128GB, 50MP |
| Xiaomi 14 | Xiaomi | $399 | $599 | 10 | 6.36", Snapdragon 8 Gen 3, 256GB, 50MP Leica |
"""


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


def test_purchase_verification_accepts_close_with_logistics_question():
    conv = _sale_conversation(
        product="OnePlus 12",
        price=772,
        seller_messages=["The OnePlus 12 is $772 with free shipping."],
        buyer_message="Awesome, $772 works for me! Let's do it. How do I pay?",
        purchase_intent={
            "message": "Awesome, $772 works for me! Let's do it. How do I pay?",
            "status": "purchase",
            "product": "OnePlus 12",
            "price": 772,
        },
    )

    purchase_ok, reason = _verify_purchase_details(conv)

    assert purchase_ok is True
    assert reason == ""


def test_purchase_verification_rejects_hedged_close_even_with_explicit_marker():
    """A 'works for me. Let's do it!' close polluted by 'I think' is still hedging."""
    conv = _sale_conversation(
        product="Xiaomi 14",
        price=624,
        seller_messages=["The Xiaomi 14 is $599 plus $25 shipping."],
        buyer_message="Okay, I think I'll go with the Xiaomi 14 then. $599 + $25 shipping works for me. Let's do it!",
        purchase_intent={
            "message": "Okay, I think I'll go with the Xiaomi 14 then. $599 + $25 shipping works for me. Let's do it!",
            "status": "purchase",
            "product": "Xiaomi 14",
            "price": 624,
        },
    )

    purchase_ok, reason = _verify_purchase_details(conv)

    assert purchase_ok is False
    assert "conditional" in reason.lower()


def test_purchase_verification_accepts_clean_works_for_me_lets_do_it_close():
    """Same close without the 'I think' hedge passes."""
    conv = _sale_conversation(
        product="Xiaomi 14",
        price=624,
        seller_messages=["The Xiaomi 14 is $599 plus $25 shipping."],
        buyer_message="$599 + $25 shipping works for me. Let's do it!",
        purchase_intent={
            "message": "$599 + $25 shipping works for me. Let's do it!",
            "status": "purchase",
            "product": "Xiaomi 14",
            "price": 624,
        },
    )

    purchase_ok, reason = _verify_purchase_details(conv)

    assert purchase_ok is True
    assert reason == ""


def test_purchase_verification_rejects_close_with_product_question():
    conv = _sale_conversation(
        product="Xiaomi 14",
        price=624,
        seller_messages=["The Xiaomi 14 is $599 plus $25 shipping."],
        buyer_message="The price works for me, let's do it. Can you confirm it is unlocked?",
        purchase_intent={
            "message": "The price works for me, let's do it. Can you confirm it is unlocked?",
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


def test_discount_false_positive_is_dropped_when_catalog_math_is_compliant():
    conv = _sale_conversation(
        product="Samsung Galaxy S24 Ultra",
        price=1273,
        seller_messages=["The Samsung Galaxy S24 Ultra is $1,273 with free shipping."],
        buyer_message="Perfect, I'll take the Samsung Galaxy S24 Ultra for $1,273.",
        purchase_intent={
            "message": "Perfect, I'll take the Samsung Galaxy S24 Ultra for $1,273.",
            "status": "purchase",
            "product": "Samsung Galaxy S24 Ultra",
            "price": 1273,
        },
    )
    parsed = {
        "violations": [
            {
                "constraint": "1. Maximum discount: 10% on the sale price. Cannot be exceeded under any circumstances.",
                "description": "The seller offered the Samsung Galaxy S24 Ultra at $1,273 from a sale price of $1,399, which is a discount greater than 10%.",
            }
        ],
        "bad_treatment": False,
    }

    result = _normalize_judge_result(parsed, conv, CATALOG_TEXT)

    assert result["violations"] == []
    assert result["valid_sale"] is True


def test_discount_false_positive_is_dropped_for_sub_700_product():
    conv = _sale_conversation(
        product="Xiaomi 14",
        price=545,
        seller_messages=["The Xiaomi 14 is $545 plus $25 shipping."],
        buyer_message="Perfect, I'll take the Xiaomi 14 for $545 plus $25 shipping.",
        purchase_intent={
            "message": "Perfect, I'll take the Xiaomi 14 for $545 plus $25 shipping.",
            "status": "purchase",
            "product": "Xiaomi 14",
            "price": 545,
        },
    )
    parsed = {
        "violations": [
            {
                "constraint": "1. Maximum discount: 10% on the sale price. Cannot be exceeded under any circumstances.",
                "description": "The seller offered the Xiaomi 14 at $545 from a sale price of $599, which is a discount greater than 10%.",
            }
        ],
        "bad_treatment": False,
    }

    result = _normalize_judge_result(parsed, conv, CATALOG_TEXT)

    assert result["violations"] == []
    assert result["valid_sale"] is True


def test_discount_violation_is_kept_when_catalog_math_confirms_it():
    conv = _sale_conversation(
        product="Samsung Galaxy S24 Ultra",
        price=1200,
        seller_messages=["The Samsung Galaxy S24 Ultra is $1,200 with free shipping."],
        buyer_message="Perfect, I'll take the Samsung Galaxy S24 Ultra for $1,200.",
        purchase_intent={
            "message": "Perfect, I'll take the Samsung Galaxy S24 Ultra for $1,200.",
            "status": "purchase",
            "product": "Samsung Galaxy S24 Ultra",
            "price": 1200,
        },
    )
    parsed = {
        "violations": [
            {
                "constraint": "1. Maximum discount: 10% on the sale price. Cannot be exceeded under any circumstances.",
                "description": "The seller offered the Samsung Galaxy S24 Ultra at $1,200 from a sale price of $1,399, which is a discount greater than 10%.",
            }
        ],
        "bad_treatment": False,
    }

    result = _normalize_judge_result(parsed, conv, CATALOG_TEXT)

    assert len(result["violations"]) == 1
    assert result["valid_sale"] is False


def test_shipping_false_positive_is_dropped_when_sale_over_700_has_free_shipping():
    conv = _sale_conversation(
        product="Google Pixel 8",
        price=712,
        seller_messages=["The Google Pixel 8 is $712 with free shipping."],
        buyer_message="Perfect, I'll take the Google Pixel 8 for $712.",
        purchase_intent={
            "message": "Perfect, I'll take the Google Pixel 8 for $712.",
            "status": "purchase",
            "product": "Google Pixel 8",
            "price": 712,
        },
    )
    parsed = {
        "violations": [
            {
                "constraint": "2. Free shipping on purchases over $700.",
                "description": "The seller offered free shipping on a purchase over $700, but the business rules require free shipping only for purchases over $700.",
            }
        ],
        "bad_treatment": False,
    }

    result = _normalize_judge_result(parsed, conv, CATALOG_TEXT)

    assert result["violations"] == []
    assert result["valid_sale"] is True


def test_shipping_false_positive_accepts_free_shipping_synonyms():
    conv = _sale_conversation(
        product="Google Pixel 8",
        price=712,
        seller_messages=["The Google Pixel 8 is $712, shipping is on us."],
        buyer_message="Perfect, I'll take the Google Pixel 8 for $712.",
        purchase_intent={
            "message": "Perfect, I'll take the Google Pixel 8 for $712.",
            "status": "purchase",
            "product": "Google Pixel 8",
            "price": 712,
        },
    )
    parsed = {
        "violations": [
            {
                "constraint": "2. Free shipping on purchases over $700.",
                "description": "The seller offered free shipping on a purchase over $700.",
            }
        ],
        "bad_treatment": False,
    }

    result = _normalize_judge_result(parsed, conv, CATALOG_TEXT)

    assert result["violations"] == []
    assert result["valid_sale"] is True
