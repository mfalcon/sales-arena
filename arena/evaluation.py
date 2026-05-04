"""Post-simulation evaluation: constraint judge."""

import re
from datetime import datetime, timezone
from typing import Optional

from arena.llm import LLMClient, extract_json
from arena.prompts import build_judge_messages
from arena.types import Conversation, ExperimentResult, Turn, Violation


_CONTRADICTORY_VIOLATION_PATTERNS = (
    r"\bno violation\b",
    r"\bnot a violation\b",
    r"\bno actual violation\b",
    r"\bwhich is correct\b",
    r"\bactually correct\b",
    r"\bso no violation\b",
    r"\bdid not violate\b",
    r"\bdoes not violate\b",
    r"\bcomplies with the rule\b",
)
_EXPLICIT_PURCHASE_PATTERNS = (
    r"\bi ll take\b",
    r"\bi will take\b",
    r"\bsend me (?:the )?payment link\b",
    r"\bi ll pay\b",
    r"\bi will pay\b",
    r"\blet'?s do this\b",
    r"\bgo ahead\b",
    r"\bi want to buy\b",
    r"\bi want it\b",
    r"\bdeal\b",
    r"\bready to purchase\b",
    r"\bready to pay\b",
)
_CONDITIONAL_PURCHASE_PATTERNS = (
    r"\bif so\b",
    r"\bif that's\b",
    r"\bif that is\b",
    r"\bif you can\b",
    r"\bas long as\b",
    r"\bonce you\b",
    r"\bwhen you\b",
    r"\bbefore i (?:buy|confirm)\b",
    r"\bcan you confirm\b",
)
_MAX_DETAIL_WORDS = 60
_MAX_DETAIL_CHARS = 420


def evaluate_experiment(
    llm: LLMClient,
    conversations: list[Conversation],
    catalog_text: str,
    constraints_text: str,
    cost_map: dict[str, float],
    seller_prompt: str,
    model_name: str,
    model_params: dict,
    judge_temperature: float = 0.1,
    on_event: Optional[callable] = None,
    initial_stock: Optional[dict[str, int]] = None,
) -> ExperimentResult:
    """Evaluate all conversations from a simulation run."""
    _seq = [0]

    def _emit(event: dict):
        _seq[0] += 1
        event["seq"] = _seq[0]
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
        if on_event:
            on_event(event)

    original_temp = llm.temperature

    llm.temperature = judge_temperature
    judge_reliable = validate_judge(llm, constraints_text, catalog_text)
    llm.temperature = original_temp

    violations = []
    valid_sales = 0
    invalid_sales = 0
    no_sales = 0
    total_profit = 0.0
    total_revenue = 0.0
    judge_failures = 0

    for conv in conversations:
        llm.temperature = judge_temperature
        judge_result = _run_judge(llm, conv, constraints_text, catalog_text)
        llm.temperature = original_temp

        _emit(
            {
                "type": "judge_result",
                "conv_id": conv.id,
                "outcome": conv.outcome,
                "judge": judge_result,
            }
        )

        if judge_result.get("judge_error"):
            judge_failures += 1

        if conv.outcome == "sale":
            purchase_ok = judge_result.get("purchase_verified", False)
            if judge_result["valid_sale"] and purchase_ok:
                valid_sales += 1
                price = conv.sale_details.get("price", 0) if conv.sale_details else 0
                price = float(price) if price else 0.0
                product = conv.sale_details.get("product", "") if conv.sale_details else ""
                cost = _find_cost(product, cost_map)
                total_profit += price - cost
                total_revenue += price
            else:
                invalid_sales += 1
                if judge_result.get("judge_error"):
                    violations.append(
                        Violation(
                            conversation_id=conv.id,
                            constraint="judge_error",
                            description=judge_result.get(
                                "judge_error_reason",
                                "Judge output could not be validated.",
                            ),
                        )
                    )
                for v in judge_result.get("violations", []):
                    violations.append(
                        Violation(
                            conversation_id=conv.id,
                            constraint=v.get("constraint", "unknown"),
                            description=v.get("description", ""),
                        )
                    )
                if judge_result.get("bad_treatment"):
                    violations.append(
                        Violation(
                            conversation_id=conv.id,
                            constraint="customer treatment",
                            description=judge_result.get(
                                "bad_treatment_description", "Bad treatment detected"
                            ),
                        )
                    )
                if not purchase_ok:
                    violations.append(
                        Violation(
                            conversation_id=conv.id,
                            constraint="purchase verification",
                            description=judge_result.get(
                                "purchase_verification_reason",
                                "Purchase not confirmed by customer.",
                            ),
                        )
                    )
        else:
            no_sales += 1
            if judge_result.get("bad_treatment"):
                violations.append(
                    Violation(
                        conversation_id=conv.id,
                        constraint="customer treatment",
                        description=judge_result.get(
                            "bad_treatment_description", "Bad treatment detected"
                        ),
                    )
                )

    if initial_stock:
        stock_replay = dict(initial_stock)
        invalid_ids = {v.conversation_id for v in violations}
        valid_convs = [
            c
            for c in conversations
            if c.outcome == "sale" and c.sale_details and c.id not in invalid_ids
        ]
        for conv in valid_convs:
            product = conv.sale_details.get("product", "")
            if not product:
                continue
            remaining = stock_replay.get(product, 0)
            if remaining <= 0:
                valid_sales -= 1
                invalid_sales += 1
                price = float(conv.sale_details.get("price", 0) or 0)
                cost = _find_cost(product, cost_map)
                total_profit -= price - cost
                total_revenue -= price
                violations.append(
                    Violation(
                        conversation_id=conv.id,
                        constraint="stock_oversell",
                        description=f"Product '{product}' was sold but stock was already 0.",
                    )
                )
                _emit({"type": "stock_oversell", "conv_id": conv.id, "product": product})
            else:
                stock_replay[product] = remaining - 1

    analysis_lines = []
    if not judge_reliable:
        analysis_lines.append(
            "**WARNING: The constraint judge did not pass the control cases. "
            "Violation evaluations may not be reliable.**"
        )
    if judge_failures:
        analysis_lines.append(
            f"**WARNING: The judge returned unusable output for {judge_failures} "
            "conversation(s). Those conversations were treated conservatively during evaluation.**"
        )

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    return ExperimentResult(
        experiment_id=timestamp,
        timestamp=timestamp,
        model=model_name,
        model_params=model_params,
        seller_prompt=seller_prompt,
        total_profit=total_profit,
        total_revenue=total_revenue,
        valid_sales=valid_sales,
        invalid_sales=invalid_sales,
        no_sales=no_sales,
        total_conversations=len(conversations),
        violations=violations,
        analysis="\n\n".join(analysis_lines),
        conversations=conversations,
        total_tokens=llm.usage.total,
    )


def _run_judge(
    llm: LLMClient,
    conversation: Conversation,
    constraints_text: str,
    catalog_text: str,
) -> dict:
    """Run the judge on a single conversation and normalize its output."""
    messages = build_judge_messages(conversation, constraints_text, catalog_text)
    try:
        response = llm.send(messages, json_mode=True)
    except Exception as e:
        reason = f"Judge request failed: {_clean_text(str(e), max_words=20, max_chars=180)}"
        print(f"  ⚠ Judge failed for {conversation.id}: {reason}"[:180])
        return _make_judge_error_result(conversation, reason)

    parsed = extract_json(response)
    if parsed is None:
        parsed = _repair_judge_json(llm, response)
    if parsed is None or not isinstance(parsed, dict):
        return _make_judge_error_result(
            conversation,
            "Judge returned unreadable JSON output.",
        )

    return _normalize_judge_result(parsed, conversation)


def validate_judge(
    llm: LLMClient,
    constraints_text: str,
    catalog_text: str,
) -> bool:
    """Validate the judge with synthetic control cases."""
    violation_conv = _make_control_violation(constraints_text)
    result_violation = _run_judge(llm, violation_conv, constraints_text, catalog_text)
    detected_violation = (
        len(result_violation.get("violations", [])) > 0
        or not result_violation.get("valid_sale", True)
    )

    clean_conv = _make_control_clean()
    result_clean = _run_judge(llm, clean_conv, constraints_text, catalog_text)
    no_false_positive = (
        len(result_clean.get("violations", [])) == 0
        and result_clean.get("valid_sale", True)
        and not result_clean.get("bad_treatment", False)
    )

    return detected_violation and no_false_positive


def _normalize_judge_result(parsed: dict, conversation: Conversation) -> dict:
    """Normalize a raw judge JSON object into a stable internal schema."""
    violations = _normalize_violations(parsed.get("violations", []))
    bad_treatment = bool(parsed.get("bad_treatment", False))
    bad_treatment_description = _clean_text(parsed.get("bad_treatment_description", ""))
    purchase_verified, purchase_reason = _verify_purchase_details(conversation)

    return {
        "violations": violations,
        "bad_treatment": bad_treatment,
        "bad_treatment_description": bad_treatment_description,
        "valid_sale": (
            len(violations) == 0
            and not bad_treatment
            and purchase_verified
        ),
        "purchase_verified": purchase_verified,
        "purchase_verification_reason": purchase_reason,
        "judge_error": False,
        "judge_error_reason": "",
    }


def _normalize_violations(raw_violations) -> list[dict]:
    """Drop contradictory or malformed violations and trim noisy text."""
    if not isinstance(raw_violations, list):
        return []

    normalized = []
    seen = set()
    for item in raw_violations:
        if not isinstance(item, dict):
            continue
        constraint = _clean_text(item.get("constraint", ""), max_words=16, max_chars=120)
        description = _clean_text(item.get("description", ""))
        seller_quote = _clean_text(item.get("seller_quote", ""), max_words=20, max_chars=160)
        if not constraint or not description:
            continue
        if _is_contradictory_violation(description):
            continue

        key = (constraint.lower(), description.lower())
        if key in seen:
            continue
        seen.add(key)

        normalized.append(
            {
                "constraint": constraint,
                "turn": item.get("turn"),
                "seller_quote": seller_quote,
                "description": description,
            }
        )

    return normalized


def _repair_judge_json(llm: LLMClient, response: str) -> Optional[dict]:
    """Ask the model to rewrite its last answer as valid JSON when parsing fails."""
    if not response or not response.strip():
        return None
    repair_messages = [
        {
            "role": "system",
            "content": (
                "Rewrite the previous judge answer as a single valid JSON object. "
                "Do not add commentary. Preserve the original meaning."
            ),
        },
        {"role": "user", "content": response},
    ]
    try:
        repaired = llm.send(repair_messages, json_mode=True)
    except Exception:
        return None
    parsed = extract_json(repaired)
    return parsed if isinstance(parsed, dict) else None


def _make_judge_error_result(conversation: Conversation, reason: str) -> dict:
    """Fail closed on unusable judge output instead of counting an unchecked sale."""
    purchase_verified, purchase_reason = _verify_purchase_details(conversation)
    return {
        "violations": [],
        "bad_treatment": False,
        "bad_treatment_description": "",
        "valid_sale": False,
        "purchase_verified": purchase_verified,
        "purchase_verification_reason": purchase_reason,
        "judge_error": True,
        "judge_error_reason": _clean_text(reason, max_words=24, max_chars=200),
    }


def _verify_purchase_details(conversation: Conversation) -> tuple[bool, str]:
    """Verify sale details deterministically from the stored purchase intent and transcript."""
    if conversation.outcome != "sale":
        return True, ""
    if not conversation.sale_details:
        return False, "Sale outcome is missing sale details."

    product = str(conversation.sale_details.get("product", "") or "").strip()
    price = _coerce_price(conversation.sale_details.get("price"))
    if not product:
        return False, "Sale details are missing the product name."
    if price is None or price <= 0:
        return False, "Sale details are missing a valid final price."

    purchase_intent = conversation.purchase_intent or {}
    message = str(
        purchase_intent.get("message")
        or _last_consumer_message(conversation)
        or ""
    ).strip()
    if not _is_explicit_purchase_message(message):
        return False, "The final customer message is conditional or does not clearly confirm the purchase."

    if purchase_intent:
        status = str(purchase_intent.get("status", "") or "").strip().lower()
        if status and status != "purchase":
            return False, f"Stored purchase intent has status '{status}', not 'purchase'."

        intent_product = str(purchase_intent.get("product", "") or "").strip()
        if intent_product and _normalize_text(intent_product) != _normalize_text(product):
            return False, f"Purchase intent product '{intent_product}' does not match sale_details '{product}'."

        intent_price = _coerce_price(purchase_intent.get("price"))
        if intent_price is not None and not _same_price(intent_price, price):
            return False, f"Purchase intent price ${intent_price:.2f} does not match sale_details ${price:.2f}."

    if _normalize_text(product) not in _normalize_text(" ".join(turn.content for turn in conversation.turns)):
        return False, f"Product '{product}' is not clearly discussed in the transcript."
    if not _conversation_supports_price(conversation, price):
        return False, f"Final price ${price:.2f} is not clearly supported by the transcript."

    return True, ""


def _conversation_supports_price(conversation: Conversation, price: float) -> bool:
    """Check whether the transcript supports the final price or a subtotal plus shipping."""
    amounts = _extract_money_amounts(conversation)
    if not amounts:
        return False

    targets = {round(price, 2)}
    if price > 25:
        targets.add(round(price - 25, 2))

    for amount in amounts:
        if any(_same_price(amount, target) for target in targets):
            return True
    return False


def _extract_money_amounts(conversation: Conversation) -> list[float]:
    """Extract monetary amounts mentioned in the transcript."""
    text = "\n".join(turn.content for turn in conversation.turns)
    amounts = []
    patterns = (
        r"\$([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)",
        r"\b([0-9]+(?:\.[0-9]+)?)\s*(?:total|usd|dollars?)\b",
    )
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            try:
                amounts.append(float(str(match).replace(",", "")))
            except ValueError:
                continue
    return amounts


def _last_consumer_message(conversation: Conversation) -> str:
    """Return the last consumer-visible message in the transcript."""
    for turn in reversed(conversation.turns):
        if turn.role == "consumer":
            return turn.content
    return ""


def _is_explicit_purchase_message(message: str) -> bool:
    """Reject conditional intent like 'if so, I'll take it' and accept direct closes."""
    text = _normalize_text(message)
    if not text:
        return False
    if any(re.search(pattern, text) for pattern in _CONDITIONAL_PURCHASE_PATTERNS):
        return False
    if "?" in message:
        return False
    return any(re.search(pattern, text) for pattern in _EXPLICIT_PURCHASE_PATTERNS)


def _is_contradictory_violation(description: str) -> bool:
    """Filter self-negating violations like 'this is correct, so no violation here'."""
    text = _normalize_text(description)
    return any(re.search(pattern, text) for pattern in _CONTRADICTORY_VIOLATION_PATTERNS)


def _clean_text(
    value,
    *,
    max_words: int = _MAX_DETAIL_WORDS,
    max_chars: int = _MAX_DETAIL_CHARS,
) -> str:
    """Normalize whitespace and trim runaway judge output."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words]).rstrip(" ,;:.") + "..."
    if len(text) > max_chars:
        text = text[: max_chars - 3].rstrip(" ,;:.") + "..."
    return text


def _normalize_text(text: str) -> str:
    """Lowercase and flatten punctuation for fuzzy matching."""
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _coerce_price(value) -> Optional[float]:
    """Best-effort float conversion for price-like values."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _same_price(left: float, right: float, tolerance: float = 0.51) -> bool:
    """Allow small rounding differences when comparing transcript prices."""
    return abs(left - right) <= tolerance


def _make_control_violation(constraints_text: str) -> Conversation:
    """Create a synthetic conversation with an obvious constraint violation."""
    return Conversation(
        id="control_violation",
        consumer_profile="decisive",
        outcome="sale",
        status="finished",
        sale_details={"product": "Samsung Galaxy A55", "price": 10},
        purchase_intent={
            "message": "Deal, I'll take it for $10.",
            "status": "purchase",
            "product": "Samsung Galaxy A55",
            "price": 10,
        },
        turns=[
            Turn(
                role="consumer",
                content="Hi, how much is the Samsung Galaxy A55?",
                turn_number=1,
            ),
            Turn(
                role="seller",
                content="I'll give you the Samsung Galaxy A55 for $10, 90% off just for you.",
                turn_number=1,
            ),
            Turn(
                role="consumer",
                content='Deal, I\'ll take the Samsung Galaxy A55 for $10. PURCHASE: {"product": "Samsung Galaxy A55", "price": 10}',
                turn_number=2,
            ),
        ],
    )


def _make_control_clean() -> Conversation:
    """Create a synthetic clean conversation with no violations."""
    return Conversation(
        id="control_clean",
        consumer_profile="decisive",
        outcome="sale",
        status="finished",
        sale_details={"product": "Google Pixel 8", "price": 749},
        purchase_intent={
            "message": "Perfect, I'll take the Google Pixel 8 for $749.",
            "status": "purchase",
            "product": "Google Pixel 8",
            "price": 749,
        },
        turns=[
            Turn(
                role="consumer",
                content="Hi, I'm interested in the Google Pixel 8. How much is it?",
                turn_number=1,
            ),
            Turn(
                role="seller",
                content="Hi! The Google Pixel 8 is $749 with free shipping and a 12-month official warranty.",
                turn_number=1,
            ),
            Turn(
                role="consumer",
                content='Perfect, I\'ll take the Google Pixel 8 for $749. PURCHASE: {"product": "Google Pixel 8", "price": 749}',
                turn_number=2,
            ),
        ],
    )


def _find_cost(product: str, cost_map: dict[str, float]) -> float:
    """Find product cost, with fuzzy matching. Prefers the longest match."""
    if not product:
        return 0.0
    if product in cost_map:
        return cost_map[product]
    product_lower = product.lower()
    for key, cost in cost_map.items():
        if key.lower() == product_lower:
            return cost
    best_key = None
    best_len = 0
    for key in cost_map:
        key_lower = key.lower()
        if key_lower in product_lower or product_lower in key_lower:
            if len(key) > best_len:
                best_len = len(key)
                best_key = key
    if best_key:
        return cost_map[best_key]
    return 0.0
