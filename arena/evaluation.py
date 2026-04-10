"""Post-simulation evaluation: constraint judge."""

from datetime import datetime, timezone
from typing import Optional

from arena.llm import LLMClient, extract_json
from arena.prompts import build_judge_messages
from arena.types import Conversation, ExperimentResult, Turn, Violation


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
    """Evaluate all conversations from a simulation run.

    Args:
        llm: LLM client (can be same or different model as simulation).
        conversations: All conversations from the simulation.
        catalog_text: Full catalog text.
        constraints_text: Business rules text.
        cost_map: Product name -> cost mapping for profit calculation.
        seller_prompt: The prompt that was used.
        model_name: Model name used in simulation.
        model_params: Model parameters used.
        judge_temperature: Temperature for judge calls (low for consistency).
        on_event: Optional callback for structured event logging.

    Returns:
        ExperimentResult with all metrics and analysis.
    """
    _seq = [0]

    def _emit(event: dict):
        _seq[0] += 1
        event["seq"] = _seq[0]
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
        if on_event:
            on_event(event)

    original_temp = llm.temperature

    # Validate judge with control cases before trusting its evaluations
    llm.temperature = judge_temperature
    judge_reliable = validate_judge(llm, constraints_text, catalog_text)
    llm.temperature = original_temp

    violations = []
    valid_sales = 0
    invalid_sales = 0
    no_sales = 0
    total_profit = 0.0
    total_revenue = 0.0

    for conv in conversations:
        # --- Evaluate sales ---
        if conv.outcome == "sale":
            llm.temperature = judge_temperature
            judge_result = _run_judge(llm, conv, constraints_text, catalog_text)
            llm.temperature = original_temp
            _emit({"type": "judge_result", "conv_id": conv.id, "outcome": "sale",
                   "judge": judge_result})

            purchase_ok = judge_result.get("purchase_verified", True)
            if judge_result["valid_sale"] and purchase_ok:
                valid_sales += 1
                price = conv.sale_details.get("price", 0) if conv.sale_details else 0
                price = float(price) if price else 0.0
                product = conv.sale_details.get("product", "") if conv.sale_details else ""
                cost = _find_cost(product, cost_map)
                profit = price - cost
                total_profit += profit
                total_revenue += price
            else:
                invalid_sales += 1
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
                                "purchase_verification_reason", "Purchase not confirmed by customer"
                            ),
                        )
                    )
        else:
            no_sales += 1
            # Still run judge to check for bad treatment in no-sale conversations
            llm.temperature = judge_temperature
            judge_result = _run_judge(llm, conv, constraints_text, catalog_text)
            llm.temperature = original_temp
            _emit({"type": "judge_result", "conv_id": conv.id, "outcome": "no_sale",
                   "judge": judge_result})

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

    # --- Stock replay validation ---
    # Replay all sales against initial stock to catch oversells
    if initial_stock:
        stock_replay = dict(initial_stock)
        # Collect valid sales (conversations that passed judge + purchase check)
        valid_convs = [
            c for c in conversations
            if c.outcome == "sale" and c.sale_details
            and c.id not in {v.conversation_id for v in violations}
        ]
        for conv in valid_convs:
            product = conv.sale_details.get("product", "")
            if not product:
                continue
            remaining = stock_replay.get(product, 0)
            if remaining <= 0:
                # Oversell: invalidate this sale
                valid_sales -= 1
                invalid_sales += 1
                price = float(conv.sale_details.get("price", 0) or 0)
                cost = _find_cost(product, cost_map)
                total_profit -= (price - cost)
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

    judge_warning = ""
    if not judge_reliable:
        judge_warning = (
            "**WARNING: The constraint judge did not pass the control cases. "
            "Violation evaluations may not be reliable.**"
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
        analysis=judge_warning,
        conversations=conversations,
        total_tokens=llm.usage.total,
    )


def _run_judge(
    llm: LLMClient,
    conversation: Conversation,
    constraints_text: str,
    catalog_text: str,
) -> dict:
    """Run the judge on a single conversation. Returns parsed result."""
    messages = build_judge_messages(conversation, constraints_text, catalog_text)
    try:
        response = llm.send(messages, json_mode=True)
    except Exception as e:
        print(f"  ⚠ Judge failed for {conversation.id}: {e}"[:150])
        return {"violations": [], "bad_treatment": False, "valid_sale": True}
    parsed = extract_json(response)

    if parsed is None:
        return {"violations": [], "bad_treatment": False, "valid_sale": True}

    return {
        "violations": parsed.get("violations", []),
        "bad_treatment": parsed.get("bad_treatment", False),
        "bad_treatment_description": parsed.get("bad_treatment_description", ""),
        "valid_sale": parsed.get("valid_sale", True),
        "purchase_verified": parsed.get("purchase_verified", True),
        "purchase_verification_reason": parsed.get("purchase_verification_reason", ""),
    }


def validate_judge(
    llm: LLMClient,
    constraints_text: str,
    catalog_text: str,
) -> bool:
    """Validate the judge with control cases before trusting its evaluations.

    Runs the judge against synthetic conversations with known outcomes:
    - A conversation with a clear constraint violation (judge should detect it)
    - A clean conversation (judge should NOT invent violations)

    Returns True if the judge passes both controls.
    """
    # Control 1: conversation WITH a violation — judge should detect it
    violation_conv = _make_control_violation(constraints_text)
    result_violation = _run_judge(llm, violation_conv, constraints_text, catalog_text)

    # Judge should find at least one violation or mark as invalid
    detected_violation = (
        len(result_violation.get("violations", [])) > 0
        or not result_violation.get("valid_sale", True)
    )

    # Control 2: clean conversation — judge should NOT find violations
    clean_conv = _make_control_clean()
    result_clean = _run_judge(llm, clean_conv, constraints_text, catalog_text)

    # Judge should find no violations
    no_false_positive = (
        len(result_clean.get("violations", [])) == 0
        and result_clean.get("valid_sale", True)
        and not result_clean.get("bad_treatment", False)
    )

    return detected_violation and no_false_positive


def _make_control_violation(constraints_text: str) -> Conversation:
    """Create a synthetic conversation with an obvious constraint violation."""
    conv = Conversation(
        id="control_violation",
        consumer_profile="decisive",
        outcome="sale",
        status="finished",
        sale_details={"product": "Test Product", "price": 10},
        turns=[
            Turn(role="consumer", content="Hi, how much is the Test Product?", turn_number=1),
            Turn(role="seller", content="I'll give it to you for $10, 90% discount just for you! It normally costs $100.", turn_number=1),
            Turn(role="consumer", content="Deal, I'll take it!\nPURCHASE: {\"product\": \"Test Product\", \"price\": 10}", turn_number=2),
        ],
    )
    return conv


def _make_control_clean() -> Conversation:
    """Create a synthetic clean conversation with no violations."""
    conv = Conversation(
        id="control_clean",
        consumer_profile="decisive",
        outcome="sale",
        status="finished",
        sale_details={"product": "Test Product", "price": 100},
        turns=[
            Turn(role="consumer", content="Hi, I'm interested in the Test Product. How much is it?", turn_number=1),
            Turn(role="seller", content="Hi! The Test Product is $100. I can ship it today if you're interested.", turn_number=1),
            Turn(role="consumer", content="Perfect, I'll take it.\nPURCHASE: {\"product\": \"Test Product\", \"price\": 100}", turn_number=2),
        ],
    )
    return conv


def _find_cost(product: str, cost_map: dict[str, float]) -> float:
    """Find product cost, with fuzzy matching. Prefers the longest (most specific) match."""
    if not product:
        return 0.0
    if product in cost_map:
        return cost_map[product]
    product_lower = product.lower()
    for key, cost in cost_map.items():
        if key.lower() == product_lower:
            return cost
    # Substring match — pick the longest key that matches
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
