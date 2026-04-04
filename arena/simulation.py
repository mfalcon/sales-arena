"""Round-robin simulation engine for Sales Arena."""

import random
import re
from typing import Optional

from arena.llm import LLMClient, extract_json
from arena.prompts import (
    CONSUMER_PROFILES,
    build_consumer_messages,
    build_consumer_system_prompt,
    build_seller_context,
)
from arena.stock import StockTracker
from arena.types import Conversation, Turn


def run_simulation(
    llm: LLMClient,
    seller_prompt: str,
    catalog_text: str,
    constraints_text: str,
    stock: StockTracker,
    num_consumers: int = 20,
    max_turns: int = 10,
    consumer_profiles: Optional[dict] = None,
    product_list: Optional[list[str]] = None,
    price_map: Optional[dict[str, float]] = None,
    on_turn: Optional[callable] = None,
    consumer_llm: Optional[LLMClient] = None,
) -> list[Conversation]:
    """Run a full simulation with pseudo-parallel conversations.

    Args:
        llm: LLM client for the seller.
        seller_prompt: The user's seller prompt being tested.
        catalog_text: Full catalog text (free format).
        constraints_text: Business rules text (free format).
        stock: Stock tracker with initial quantities.
        num_consumers: Number of simulated consumers.
        max_turns: Maximum turns per conversation.
        consumer_profiles: Profile definitions (defaults to CONSUMER_PROFILES).
        product_list: List of product names for consumer interest generation.
        price_map: Product -> price mapping for budget calculation.
        on_turn: Optional callback(turn_round, conversation_id, role, content) for logging.
        consumer_llm: Optional separate LLM client for consumers (defaults to same as seller).

    Returns:
        List of completed Conversations.
    """
    c_llm = consumer_llm or llm
    profiles = consumer_profiles or CONSUMER_PROFILES
    profile_names = list(profiles.keys())

    # Create consumers with random profiles
    conversations: list[Conversation] = []
    consumer_system_prompts: dict[str, str] = {}

    for i in range(num_consumers):
        conv_id = f"c{i+1:02d}"
        profile_name = random.choice(profile_names)

        # Pick a product of interest
        interest = _pick_interest(product_list)

        # Calculate budget based on profile and product price
        budget = _calculate_budget(profile_name, interest, profiles, price_map)

        # Build consumer system prompt
        sys_prompt = build_consumer_system_prompt(profile_name, budget, interest)
        consumer_system_prompts[conv_id] = sys_prompt

        # Generate opening message from consumer
        opening = _generate_opening(c_llm, sys_prompt)

        conv = Conversation(
            id=conv_id,
            consumer_profile=profile_name,
            turns=[Turn(role="consumer", content=opening, turn_number=1)],
        )
        conversations.append(conv)

        if on_turn:
            on_turn(1, conv_id, "consumer", opening)

    # Round-robin turns
    for turn_round in range(1, max_turns + 1):
        active = [c for c in conversations if c.status == "active"]
        if not active:
            break

        # Seller responds to all active conversations
        for conv in active:
            other_convs = [c for c in conversations if c.id != conv.id]

            seller_messages = build_seller_context(
                seller_prompt=seller_prompt,
                catalog_text=catalog_text,
                constraints_text=constraints_text,
                current_conversation=conv.turns,
                other_conversations=other_convs,
                stock_text=stock.get_stock_text(),
            )

            seller_response = llm.send(seller_messages)
            conv.turns.append(
                Turn(role="seller", content=seller_response, turn_number=turn_round)
            )

            if on_turn:
                on_turn(turn_round, conv.id, "seller", seller_response)

        # Consumer responds to all active conversations
        active = [c for c in conversations if c.status == "active"]
        if not active:
            break

        # Don't generate consumer response on the last turn round
        # (seller already responded, that's enough)
        if turn_round >= max_turns:
            for conv in active:
                conv.outcome = "timeout"
                conv.status = "finished"
            break

        for conv in active:
            consumer_messages = build_consumer_messages(
                system_prompt=consumer_system_prompts[conv.id],
                turns=conv.turns,
            )

            consumer_response = c_llm.send(consumer_messages)
            conv.turns.append(
                Turn(
                    role="consumer",
                    content=consumer_response,
                    turn_number=turn_round + 1,
                )
            )

            if on_turn:
                on_turn(turn_round + 1, conv.id, "consumer", consumer_response)

            # Check for purchase
            purchase = detect_purchase(consumer_response)
            if purchase:
                product = purchase["producto"]
                price = purchase.get("precio", 0)
                if stock.sell(product):
                    conv.outcome = "sale"
                    conv.sale_details = {
                        "product": product,
                        "price": price,
                    }
                    conv.status = "finished"
                else:
                    # Out of stock — inject a note, conversation continues
                    # The seller will see updated stock on next turn
                    pass
            elif detect_no_buy(consumer_response):
                conv.outcome = "no_sale"
                conv.status = "finished"

    # Mark any remaining active conversations as timeout
    for conv in conversations:
        if conv.status == "active":
            conv.outcome = "timeout"
            conv.status = "finished"

    return conversations


def detect_purchase(text: str) -> Optional[dict]:
    """Detect if a consumer message contains a purchase signal.

    Looks for the COMPRA marker or purchase-related JSON.
    Returns {"producto": ..., "precio": ...} or None.
    """
    # Look for COMPRA: {...} marker
    compra_match = re.search(
        r"COMPRA:\s*(\{.*?\})", text, re.IGNORECASE | re.DOTALL
    )
    if compra_match:
        parsed = extract_json(compra_match.group(1))
        if parsed and "producto" in parsed:
            return parsed

    # Look for JSON with "COMPRA" key anywhere in text
    parsed = extract_json(text)
    if parsed:
        if "COMPRA" in parsed and isinstance(parsed["COMPRA"], dict):
            return parsed["COMPRA"]
        if "producto" in parsed and "precio" in parsed:
            return parsed

    return None


def detect_no_buy(text: str) -> bool:
    """Detect if a consumer is leaving without buying."""
    farewell_patterns = [
        r"voy a pensar",
        r"lo pienso",
        r"después vuelvo",
        r"después te aviso",
        r"no gracias",
        r"no, gracias",
        r"no me interesa",
        r"muy caro",
        r"no puedo",
        r"chau",
        r"hasta luego",
        r"nos vemos",
        r"gracias por la info",
        r"voy a seguir buscando",
        r"voy a buscar",
        r"no me convence",
        r"paso",
        r"mejor no",
    ]
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in farewell_patterns)


def _pick_interest(product_list: Optional[list[str]]) -> str:
    """Pick a product or category the consumer is interested in."""
    if product_list:
        return random.choice(product_list)
    return "productos disponibles"


def _calculate_budget(
    profile_name: str,
    interest: str,
    profiles: dict,
    price_map: Optional[dict[str, float]],
) -> float:
    """Calculate a consumer's budget based on profile and product price."""
    profile = profiles[profile_name]
    budget_min, budget_max = profile["budget_range"]

    # If we have a price for the product of interest, base budget on that
    base_price = 1000  # default
    if price_map:
        # Try to find the product price
        for product, price in price_map.items():
            if interest.lower() in product.lower() or product.lower() in interest.lower():
                base_price = price
                break
        else:
            # Use average price
            base_price = sum(price_map.values()) / len(price_map)

    budget = base_price * random.uniform(budget_min, budget_max)
    return round(budget, 2)


def _generate_opening(llm: LLMClient, consumer_system_prompt: str) -> str:
    """Generate the consumer's opening message."""
    messages = [
        {"role": "system", "content": consumer_system_prompt},
        {
            "role": "user",
            "content": (
                "Escribí tu primer mensaje al vendedor. "
                "Recordá que vos sos el cliente y estás iniciando el chat."
            ),
        },
    ]
    return llm.send(messages)
