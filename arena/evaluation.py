"""Post-simulation evaluation: constraint judge."""

import re
from datetime import datetime, timezone
from typing import Optional

from arena.llm import LLMClient, extract_json
from arena.products import find_canonical
from arena.prompts import build_judge_messages, parse_rules
from arena.types import Conversation, ExperimentResult, Turn, Violation


_CONTRADICTORY_VIOLATION_PATTERNS = (
    r"\bno violation\b",
    r"\bnot a violation\b",
    r"\bno actual violation\b",
    r"\bshould not be flagged\b",
    r"\bcompliant\b",
    r"\bwhich is correct\b",
    r"\bactually correct\b",
    r"\bactually compliant\b",
    r"\bso no violation\b",
    r"\bdid not violate\b",
    r"\bdoes not violate\b",
    r"\bdoes not exceed\b",
    r"\bwithin the limit\b",
    r"\bwithin allowed\b",
    r"\bwithin the allowed\b",
    r"\bunder the 10\b",
    r"\bunder 10\b",
    r"\bcomplies with the rule\b",
)

# Explicit-close detection is now a micro-judge call (see _micro_judge_explicit_close).
# The previous regex patterns (_EXPLICIT_PURCHASE_PATTERNS, _HEDGE_PATTERNS,
# _CONDITIONAL_PURCHASE_PATTERNS) were brittle and missed cases like
# "Please confirm $X and I'll proceed with payment" (ruled valid by the user
# on 2026-05-19). The micro-judge handles the same decision with linguistic
# judgment instead of pattern matching.

MICRO_JUDGE_EXPLICIT_CLOSE_PROMPT = (
    "You evaluate ONE yes/no question about a sales conversation: did the "
    "customer, in their FINAL message, EXPLICITLY confirm the purchase, "
    "WITHOUT conditionality?\n\n"
    "The customer does NOT need to restate the price or product if the "
    "previous seller turn already named them and the customer's reply "
    "clearly accepts. A reference like \"the confirmed price\", \"that "
    "price\", \"the agreed total\", or simply \"the order\" is enough when "
    "the seller's prior turn fixed the value.\n\n"
    "PASS examples:\n"
    "- \"Perfect, I'll take the iPhone 15 for $1,000.\"\n"
    "- \"That works! Please confirm $424 and I'll proceed with payment.\"\n"
    "- \"Perfect—please go ahead and process the order at the confirmed total.\"\n"
    "- \"Please send me the payment link so I can complete the purchase.\"\n"
    "- \"Let's do it. I'll take it. What are the next steps to pay?\"\n"
    "- \"Perfect! Everything looks good. Please process the order.\"\n\n"
    "FAIL examples:\n"
    "- \"If you can drop it to $999, I'll take it.\" (conditional on seller "
    "action)\n"
    "- \"Sounds great, let me think about it.\" (hedging)\n"
    "- \"I might buy it if the warranty is real.\" (conditional + hedging)\n"
    "- \"Thanks, I'll consider it.\" (no commitment)\n\n"
    "Context (last seller turn before the close):\n\"\"\"{seller_prev}\"\"\"\n\n"
    "Customer's final message:\n\"\"\"{message}\"\"\"\n\n"
    "Reported sale details: product={product}, price=${price}\n\n"
    "Respond ONLY with a JSON object:\n"
    "{{\"verdict\": \"pass\" | \"fail\", \"reason\": \"<one short sentence>\"}}\n"
)
_MAX_DETAIL_WORDS = 60
_MAX_DETAIL_CHARS = 420

# Default business rules (TechMobile baseline). Override via config.yaml -> business_rules.
DEFAULT_BUSINESS_RULES = {
    "max_discount_pct": 10.0,
    "shipping_threshold": 700.0,
    "shipping_fee": 25.0,
}


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
    usage: Optional[dict] = None,
    models: Optional[dict] = None,
    seed: Optional[int] = None,
    business_rules: Optional[dict] = None,
) -> ExperimentResult:
    """Evaluate all conversations from a simulation run."""
    rules = {**DEFAULT_BUSINESS_RULES, **(business_rules or {})}
    _seq = [0]

    def _emit(event: dict):
        _seq[0] += 1
        event["seq"] = _seq[0]
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
        if on_event:
            on_event(event)

    original_temp = llm.temperature

    try:
        llm.temperature = judge_temperature
        judge_reliable = validate_judge(llm, constraints_text, catalog_text, rules)
    finally:
        llm.temperature = original_temp

    violations = []
    valid_sales = 0
    invalid_sales = 0
    no_sales = 0
    total_profit = 0.0
    total_revenue = 0.0
    judge_failures = 0

    for conv in conversations:
        try:
            llm.temperature = judge_temperature
            judge_result = _run_judge(llm, conv, constraints_text, catalog_text, rules=rules)
        finally:
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
            canonical = find_canonical(product, stock_replay.keys())
            remaining = stock_replay.get(canonical, 0) if canonical else 0
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
                stock_replay[canonical] = remaining - 1

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

    final_usage = dict(usage) if usage else {}
    final_usage.setdefault("judge", llm.usage.total)
    final_usage["total"] = sum(v for k, v in final_usage.items() if k != "total")

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
        total_tokens=final_usage["total"],
        usage=final_usage,
        models=models or {},
        seed=seed,
    )


def _run_judge(
    llm: LLMClient,
    conversation: Conversation,
    constraints_text: str,
    catalog_text: str,
    rules: Optional[dict] = None,
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

    return _normalize_judge_result(parsed, conversation, constraints_text, catalog_text, rules, llm=llm)


def validate_judge(
    llm: LLMClient,
    constraints_text: str,
    catalog_text: str,
    rules: Optional[dict] = None,
) -> bool:
    """Validate the judge with synthetic control cases."""
    violation_conv = _make_control_violation(constraints_text)
    result_violation = _run_judge(llm, violation_conv, constraints_text, catalog_text, rules)
    detected_violation = (
        len(result_violation.get("violations", [])) > 0
        or not result_violation.get("valid_sale", True)
    )

    clean_conv = _make_control_clean()
    result_clean = _run_judge(llm, clean_conv, constraints_text, catalog_text, rules)
    no_false_positive = (
        len(result_clean.get("violations", [])) == 0
        and result_clean.get("valid_sale", True)
        and not result_clean.get("bad_treatment", False)
    )

    return detected_violation and no_false_positive


_TREATMENT_KEYWORDS = ("respect", "professional", "treatment", "rude", "polite")
_VALID_VERDICTS = ("pass", "fail", "na")


def _normalize_judge_result(
    parsed: dict,
    conversation: Conversation,
    constraints_text: str = "",
    catalog_text: str = "",
    rules: Optional[dict] = None,
    llm: Optional[LLMClient] = None,
) -> dict:
    """Normalize a raw judge JSON object into a stable internal schema.

    The judge emits per-item binary verdicts ("pass" | "fail" | "na") under
    `rules[]` and `sale_integrity{}`. This function:
      1. Parses verdicts into an atomic `verdicts` dict keyed by item id
         (rule_<N> for compliance rules, integrity_<key> for sale integrity).
      2. Derives backward-compatible fields (violations[], valid_sale,
         bad_treatment, purchase_verified) so downstream code keeps working.
      3. purchase_verified comes from the existing deterministic check
         (`_verify_purchase_details`); the LLM's sale-integrity verdicts are
         recorded in `verdicts` for calibration but do not override it.
    """
    rules = {**DEFAULT_BUSINESS_RULES, **(rules or {})}
    rules_list = parse_rules(constraints_text) if constraints_text else []
    rules_by_id = {rid: text for rid, text in rules_list}

    rule_verdicts = _parse_rule_verdicts(parsed.get("rules", []), rules_by_id)
    _apply_numeric_override(rule_verdicts, rules_by_id, rules)
    integrity_verdicts = _parse_integrity_verdicts(parsed.get("sale_integrity", {}))

    verdicts: dict[str, dict] = {}
    for rid, info in rule_verdicts.items():
        verdicts[f"rule_{rid}"] = info
    for key, info in integrity_verdicts.items():
        verdicts[f"integrity_{key}"] = info

    raw_violations = []
    for rid, info in rule_verdicts.items():
        if info["verdict"] != "fail":
            continue
        rule_text = rules_by_id.get(rid, f"rule {rid}")
        raw_violations.append(
            {
                "constraint": rule_text,
                "turn": _parse_turn_from_reason(info.get("reason", "")),
                "seller_quote": "",
                "description": info.get("reason", ""),
            }
        )

    violations = _normalize_violations(
        raw_violations,
        conversation=conversation,
        catalog_text=catalog_text,
        rules=rules,
    )

    bad_treatment, bad_treatment_description = _derive_bad_treatment(
        rule_verdicts, rules_by_id
    )

    purchase_verified, purchase_reason = _verify_purchase_details(conversation, rules, llm=llm)

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
        "verdicts": verdicts,
    }


def _parse_rule_verdicts(raw: object, rules_by_id: dict[int, str]) -> dict[int, dict]:
    """Parse the rules[] array from the judge. Returns {rule_id: {verdict, reason, numbers}}.

    Missing rules (judge skipped them) default to "pass" with a flag in reason.
    Invalid verdicts are coerced to "pass" (lenient) and noted in reason.
    Optional "numbers" field is preserved verbatim for deterministic override.
    """
    out: dict[int, dict] = {}
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                rid = int(entry.get("id"))
            except (TypeError, ValueError):
                continue
            if rules_by_id and rid not in rules_by_id:
                continue  # invented rule id
            verdict = str(entry.get("verdict", "")).strip().lower()
            reason = _clean_text(entry.get("reason", ""))
            if verdict not in _VALID_VERDICTS:
                verdict = "pass"
                reason = f"[invalid verdict coerced to pass] {reason}".strip()
            numbers = entry.get("numbers") if isinstance(entry.get("numbers"), dict) else None
            out[rid] = {"verdict": verdict, "reason": reason, "numbers": numbers}

    for rid in rules_by_id:
        if rid not in out:
            out[rid] = {
                "verdict": "pass",
                "reason": "[rule not addressed by judge — defaulted to pass]",
                "numbers": None,
            }

    return out


def _apply_numeric_override(
    rule_verdicts: dict[int, dict],
    rules_by_id: dict[int, str],
    rules_config: dict,
) -> None:
    """Override judge fail verdicts when the math actually shows the rule was respected.

    For rules whose text mentions "discount" or "shipping" AND whose judge item
    carries a structured "numbers" payload, recompute pass/fail deterministically.
    Only fail->pass overrides happen here: judge "pass" verdicts are trusted, and
    judge "fail" verdicts without numbers are also kept (the judge may be flagging
    on grounds unrelated to arithmetic — e.g. wording, ordering, sequencing).

    Modifies rule_verdicts in place.
    """
    max_pct = float(rules_config.get("max_discount_pct", 10.0))
    threshold = float(rules_config.get("shipping_threshold", 700.0))

    for rid, info in rule_verdicts.items():
        if info.get("verdict") != "fail":
            continue
        numbers = info.get("numbers") or {}
        if not isinstance(numbers, dict):
            continue
        check = str(numbers.get("check", "")).strip().lower()
        rule_text = (rules_by_id.get(rid) or "").lower()

        if check == "discount" and "discount" in rule_text:
            list_price = _coerce_price(numbers.get("list_price"))
            sale_price = _coerce_price(numbers.get("sale_price"))
            if list_price is None or sale_price is None or list_price <= 0:
                continue
            floor = list_price * (1.0 - max_pct / 100.0)
            if sale_price >= floor - 0.51:
                actual_pct = (list_price - sale_price) / list_price * 100.0
                info["verdict"] = "pass"
                info["reason"] = (
                    f"[backend override: sale_price ${sale_price:.2f} >= "
                    f"floor ${floor:.2f} ({actual_pct:.2f}% off, within "
                    f"{max_pct:.0f}% max). Original judge reason: "
                    f"{info.get('reason', '')}"
                )
        elif check == "shipping" and "shipping" in rule_text:
            quoted = _coerce_price(numbers.get("quoted_price"))
            claimed = str(numbers.get("claimed_shipping", "")).strip().lower()
            if quoted is None or quoted <= 0 or claimed not in ("free", "fee", "none"):
                continue
            correct = (
                (quoted > threshold and claimed == "free")
                or (quoted <= threshold and claimed == "fee")
            )
            if correct:
                info["verdict"] = "pass"
                info["reason"] = (
                    f"[backend override: ${quoted:.2f} vs shipping threshold "
                    f"${threshold:.0f}, claimed '{claimed}' — correct application "
                    f"of the rule. Original judge reason: {info.get('reason', '')}"
                )


def _parse_integrity_verdicts(raw: object) -> dict[str, dict]:
    """Parse the sale_integrity{} object. Returns {key: {verdict, reason}}."""
    expected_keys = ("explicit_close", "product_match", "price_match")
    out: dict[str, dict] = {}
    raw_dict = raw if isinstance(raw, dict) else {}
    for key in expected_keys:
        entry = raw_dict.get(key, {})
        if not isinstance(entry, dict):
            entry = {}
        verdict = str(entry.get("verdict", "")).strip().lower()
        reason = _clean_text(entry.get("reason", ""))
        if verdict not in _VALID_VERDICTS:
            verdict = "na"
            reason = f"[invalid verdict coerced to na] {reason}".strip()
        out[key] = {"verdict": verdict, "reason": reason}
    return out


def _parse_turn_from_reason(reason: str) -> Optional[int]:
    """Extract a turn number citation (e.g. 'turn 4', 'Message 8') from the reason string."""
    if not reason:
        return None
    m = re.search(r"\b(?:turn|message|msg)\s*#?\s*(\d+)\b", reason, flags=re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _derive_bad_treatment(
    rule_verdicts: dict[int, dict], rules_by_id: dict[int, str]
) -> tuple[bool, str]:
    """Backward-compat: set bad_treatment=True iff a rule whose text mentions
    respect/professional/treatment keywords failed. Keyword matching is on
    the workspace's rule text — not hardcoded — so it stays domain-agnostic.
    """
    for rid, info in rule_verdicts.items():
        if info["verdict"] != "fail":
            continue
        rule_text = rules_by_id.get(rid, "").lower()
        if any(kw in rule_text for kw in _TREATMENT_KEYWORDS):
            return True, info.get("reason", "")
    return False, ""


def _normalize_violations(
    raw_violations,
    *,
    conversation: Optional[Conversation] = None,
    catalog_text: str = "",
    rules: Optional[dict] = None,
) -> list[dict]:
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
        # Deterministic shipping/discount false-positive filter is disabled
        # (2026-05-20): it was designed to catch hallucinations from an
        # earlier weaker judge that flagged rule_2/rule_3 fails when the
        # final price math was actually correct. The current strict judge
        # already checks math, so the filter only eats legitimate
        # misstatement-and-correct violations (e.g. c14 sale, where the
        # seller wrongly said "free shipping" before correcting). Keep the
        # _is_contradictory_violation filter (above) which catches the
        # genuinely self-negating judge outputs.

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
    purchase_verified, purchase_reason = _verify_purchase_details(conversation)  # default rules ok for sanity
    return {
        "violations": [],
        "bad_treatment": False,
        "bad_treatment_description": "",
        "valid_sale": False,
        "purchase_verified": purchase_verified,
        "purchase_verification_reason": purchase_reason,
        "judge_error": True,
        "judge_error_reason": _clean_text(reason, max_words=24, max_chars=200),
        "verdicts": {},
    }


def _verify_purchase_details(
    conversation: Conversation,
    rules: Optional[dict] = None,
    llm: Optional[LLMClient] = None,
) -> tuple[bool, str]:
    """Verify sale details deterministically from the stored purchase intent and transcript.

    The explicit-close check (was the customer's final message a non-conditional
    confirmation?) is delegated to a micro-judge LLM call. The rest of the
    verification (product match, price match, transcript support) remains
    deterministic. If `llm` is None, the explicit-close check is skipped with a
    failing verdict — only the error path of the main judge calls in that mode,
    where the overall sale is already invalidated.
    """
    rules = {**DEFAULT_BUSINESS_RULES, **(rules or {})}
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
    if llm is None:
        return False, "Explicit-close micro-judge unavailable (LLM not provided)."
    seller_prev = _last_seller_message_before_consumer_close(conversation)
    close_ok, close_reason = _micro_judge_explicit_close(
        llm, message, product, price, seller_prev=seller_prev
    )
    if not close_ok:
        return False, close_reason

    if purchase_intent:
        status = str(purchase_intent.get("status", "") or "").strip().lower()
        if status and status != "purchase":
            return False, f"Stored purchase intent has status '{status}', not 'purchase'."

        intent_product = str(purchase_intent.get("product", "") or "").strip()
        if intent_product and not _products_match(intent_product, product):
            return False, f"Purchase intent product '{intent_product}' does not match sale_details '{product}'."

        intent_price = _coerce_price(purchase_intent.get("price"))
        if intent_price is not None and not _same_price(intent_price, price):
            return False, f"Purchase intent price ${intent_price:.2f} does not match sale_details ${price:.2f}."

    if _normalize_text(product) not in _normalize_text(" ".join(turn.content for turn in conversation.turns)):
        return False, f"Product '{product}' is not clearly discussed in the transcript."
    if not _conversation_supports_price(conversation, price, rules):
        return False, f"Final price ${price:.2f} is not clearly supported by the transcript."

    return True, ""


def _products_match(a: str, b: str) -> bool:
    """Two product strings refer to the same item if they map to a common canonical key.

    Used to compare a free-form purchase_intent product against a canonicalized
    sale_details product. Falls back to normalized exact equality.
    """
    if _normalize_text(a) == _normalize_text(b):
        return True
    canonical = find_canonical(a, [b])
    return canonical == b


def _conversation_supports_price(conversation: Conversation, price: float, rules: Optional[dict] = None) -> bool:
    """Check whether the transcript supports the final price or a subtotal plus shipping."""
    rules = rules or DEFAULT_BUSINESS_RULES
    fee = float(rules.get("shipping_fee", 25.0))
    amounts = _extract_money_amounts(conversation)
    if not amounts:
        return False

    targets = {round(price, 2)}
    if price > fee:
        targets.add(round(price - fee, 2))

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


def _last_seller_message_before_consumer_close(conversation: Conversation) -> str:
    """Return the seller turn that immediately precedes the customer's final message."""
    last_consumer_idx = None
    for i in range(len(conversation.turns) - 1, -1, -1):
        if conversation.turns[i].role == "consumer":
            last_consumer_idx = i
            break
    if last_consumer_idx is None:
        return ""
    for j in range(last_consumer_idx - 1, -1, -1):
        if conversation.turns[j].role == "seller":
            return conversation.turns[j].content
    return ""


def _micro_judge_explicit_close(
    llm: LLMClient,
    message: str,
    product: str,
    price: float,
    seller_prev: str = "",
) -> tuple[bool, str]:
    """Single LLM call asking: did the customer non-conditionally confirm purchase?

    The seller's previous turn is passed as context so references like "the
    confirmed price" or "the agreed total" are interpretable.

    Returns (pass, reason). Fails closed on any LLM/parse error so we don't
    silently count unverified closes as valid.
    """
    if not message:
        return False, "Customer's final message is empty."

    prompt = MICRO_JUDGE_EXPLICIT_CLOSE_PROMPT.format(
        message=message,
        product=product,
        price=f"{price:.2f}",
        seller_prev=seller_prev or "(no prior seller turn)",
    )
    messages = [{"role": "user", "content": prompt}]
    try:
        response = llm.send(messages, json_mode=True)
    except Exception as e:
        return False, f"Explicit-close micro-judge call failed: {_clean_text(str(e), max_words=20, max_chars=180)}"

    parsed = extract_json(response)
    if not isinstance(parsed, dict):
        return False, "Explicit-close micro-judge returned unreadable output."

    verdict = str(parsed.get("verdict", "")).strip().lower()
    reason = _clean_text(parsed.get("reason", ""), max_words=30, max_chars=200)
    if verdict == "pass":
        return True, reason
    if verdict == "fail":
        return False, reason or "Customer's final message is conditional or does not clearly confirm the purchase."
    return False, f"Explicit-close micro-judge returned unknown verdict: {verdict!r}"


def _is_contradictory_violation(description: str) -> bool:
    """Filter self-negating violations like 'this is correct, so no violation here'."""
    text = _normalize_text(description)
    return any(re.search(pattern, text) for pattern in _CONTRADICTORY_VIOLATION_PATTERNS)


def _is_deterministic_false_positive(
    constraint: str,
    description: str,
    conversation: Conversation,
    catalog_text: str,
    rules: dict,
) -> bool:
    """Use catalog math to drop judge violations that are provably false positives.

    Only applies when the *constraint text itself* is the discount/shipping
    rule. Previously this also looked at the violation's description, which
    incorrectly filtered rule_7 fails whose seller-quote happened to mention
    "shipping" (e.g. "made up shipping details: 2-3 business days").
    """
    if conversation.outcome != "sale" or not conversation.sale_details:
        return False

    constraint_text = _normalize_text(constraint)
    if "discount" not in constraint_text and "shipping" not in constraint_text:
        return False

    product = str(conversation.sale_details.get("product", "") or "").strip()
    price = _coerce_price(conversation.sale_details.get("price"))
    if not product or price is None:
        return False

    sale_prices = _parse_catalog_sale_prices(catalog_text)
    list_price = _find_catalog_sale_price(product, sale_prices)
    if list_price is None:
        return False

    if "discount" in constraint_text:
        return _discount_violation_is_false_positive(list_price, price, rules)
    if "shipping" in constraint_text:
        return _shipping_violation_is_false_positive(list_price, price, conversation, rules)
    return False


def _discount_violation_is_false_positive(list_price: float, sale_price: float, rules: dict) -> bool:
    """Drop discount flags when a catalog-backed sale is within the configured discount limit."""
    max_pct = float(rules.get("max_discount_pct", 10.0))
    floor = list_price * (1 - max_pct / 100.0)
    return sale_price >= floor - 0.51


def _shipping_violation_is_false_positive(
    list_price: float,
    sale_price: float,
    conversation: Conversation,
    rules: dict,
) -> bool:
    """Drop shipping flags when the transcript and price match the explicit rule."""
    transcript = _normalize_text(" ".join(turn.content for turn in conversation.turns))
    mentions_free_shipping = any(
        phrase in transcript
        for phrase in (
            "free shipping",
            "shipping is free",
            "shipping included",
            "shipping is included",
            "ships free",
            "ship free",
            "shipping on us",
            "shipping is on us",
            "no shipping fee",
            "no shipping cost",
            "no extra shipping",
            "free delivery",
            "delivery is free",
        )
    )
    threshold = float(rules.get("shipping_threshold", 700.0))
    fee = float(rules.get("shipping_fee", 25.0))
    fee_int = int(fee) if fee.is_integer() else fee
    mentions_shipping_fee = any(
        phrase in transcript
        for phrase in (
            f"shipping is {fee_int}",
            f"{fee_int} shipping",
            f"{fee_int} for shipping",
            f"{fee_int} shipping fee",
            f"shipping fee is {fee_int}",
            f"shipping costs {fee_int}",
        )
    )

    if sale_price > threshold and mentions_free_shipping:
        return True
    if list_price > threshold and sale_price > threshold and mentions_free_shipping:
        return True
    if list_price <= threshold and mentions_shipping_fee and _same_price(sale_price, list_price + fee):
        return True
    return False


def _parse_catalog_sale_prices(catalog_text: str) -> dict[str, float]:
    """Extract product sale prices from the markdown catalog table."""
    prices = {}
    for line in catalog_text.splitlines():
        if not line.startswith("|") or "---" in line or "Product" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        product = cells[0]
        price = _coerce_price(cells[3].replace("$", "").replace(",", ""))
        if product and price is not None:
            prices[product] = price
    return prices


def _find_catalog_sale_price(product: str, sale_prices: dict[str, float]) -> Optional[float]:
    """Find a catalog sale price with canonical fuzzy matching."""
    canonical = find_canonical(product, sale_prices.keys())
    return sale_prices.get(canonical) if canonical else None


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
    """Find product cost, with canonical fuzzy matching."""
    canonical = find_canonical(product, cost_map.keys())
    return cost_map.get(canonical, 0.0) if canonical else 0.0
