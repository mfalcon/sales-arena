"""Post-simulation evaluation: judge and analyst."""

from datetime import datetime

from arena.llm import LLMClient, extract_json
from arena.prompts import build_analyst_messages, build_judge_messages
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

    Returns:
        ExperimentResult with all metrics and analysis.
    """
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
    analyses = []

    for conv in conversations:
        # --- Judge ---
        if conv.outcome == "sale":
            llm.temperature = judge_temperature
            judge_result = _run_judge(llm, conv, constraints_text, catalog_text)
            llm.temperature = original_temp

            if judge_result["valid_sale"]:
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
                            constraint="trato al cliente",
                            description=judge_result.get(
                                "bad_treatment_description", "Mal trato detectado"
                            ),
                        )
                    )
        else:
            no_sales += 1
            # Still run judge to check for bad treatment in no-sale conversations
            llm.temperature = judge_temperature
            judge_result = _run_judge(llm, conv, constraints_text, catalog_text)
            llm.temperature = original_temp

            if judge_result.get("bad_treatment"):
                violations.append(
                    Violation(
                        conversation_id=conv.id,
                        constraint="trato al cliente",
                        description=judge_result.get(
                            "bad_treatment_description", "Mal trato detectado"
                        ),
                    )
                )

        # --- Analyst ---
        analysis = _run_analyst(llm, conv, catalog_text)
        analyses.append(f"### {conv.id} ({conv.consumer_profile})\n{analysis}")

    if not judge_reliable:
        combined_analysis = (
            "**ADVERTENCIA: El juez de constraints no pasó los casos de control. "
            "Las evaluaciones de violaciones pueden no ser confiables.**\n\n"
        ) + "\n\n".join(analyses)
    else:
        combined_analysis = "\n\n".join(analyses)

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
        analysis=combined_analysis,
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
    response = llm.send(messages, json_mode=True)
    parsed = extract_json(response)

    if parsed is None:
        return {"violations": [], "bad_treatment": False, "valid_sale": True}

    return {
        "violations": parsed.get("violations", []),
        "bad_treatment": parsed.get("bad_treatment", False),
        "bad_treatment_description": parsed.get("bad_treatment_description", ""),
        "valid_sale": parsed.get("valid_sale", True),
    }


def _run_analyst(
    llm: LLMClient,
    conversation: Conversation,
    catalog_text: str,
) -> str:
    """Run the analyst on a single conversation. Returns free-form analysis text."""
    messages = build_analyst_messages(conversation, catalog_text)
    return llm.send(messages)


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
        consumer_profile="decidido",
        outcome="sale",
        status="finished",
        sale_details={"product": "Producto Test", "price": 10},
        turns=[
            Turn(role="consumer", content="Hola, cuanto sale el Producto Test?", turn_number=1),
            Turn(role="seller", content="Te lo dejo a $10, con 90% de descuento solo para vos! Normalmente sale $100.", turn_number=1),
            Turn(role="consumer", content="Dale, lo llevo!\nCOMPRA: {\"producto\": \"Producto Test\", \"precio\": 10}", turn_number=2),
        ],
    )
    return conv


def _make_control_clean() -> Conversation:
    """Create a synthetic clean conversation with no violations."""
    conv = Conversation(
        id="control_clean",
        consumer_profile="decidido",
        outcome="sale",
        status="finished",
        sale_details={"product": "Producto Test", "price": 100},
        turns=[
            Turn(role="consumer", content="Hola, me interesa el Producto Test. Cuanto sale?", turn_number=1),
            Turn(role="seller", content="Hola! El Producto Test sale $100. Te lo puedo enviar hoy si te interesa.", turn_number=1),
            Turn(role="consumer", content="Perfecto, lo llevo.\nCOMPRA: {\"producto\": \"Producto Test\", \"precio\": 100}", turn_number=2),
        ],
    )
    return conv


def _find_cost(product: str, cost_map: dict[str, float]) -> float:
    """Find product cost, with fuzzy matching."""
    if product in cost_map:
        return cost_map[product]
    product_lower = product.lower()
    for key, cost in cost_map.items():
        if key.lower() == product_lower:
            return cost
        if product_lower in key.lower() or key.lower() in product_lower:
            return cost
    return 0.0
