"""Round-robin simulation engine for Sales Arena."""

import json
import random
import re
from datetime import datetime, timezone
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
    on_event: Optional[callable] = None,
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
        on_event: Optional callback(event_dict) for structured logging. Events:
            {"type": "turn", "round": N, "conv_id": "cXX", "role": "seller|consumer", "content": "...", "seq": N}
            {"type": "stock_update", "product": "...", "before": N, "after": N, "conv_id": "cXX", "seq": N}
            {"type": "status_change", "conv_id": "cXX", "outcome": "sale|no_sale|timeout", "details": {...}, "seq": N}
            {"type": "consumer_intent", "conv_id": "cXX", "status": "browsing|purchase|no_purchase", "raw_json": {...}, "seq": N}
        consumer_llm: Optional separate LLM client for consumers (defaults to same as seller).

    Returns:
        List of completed Conversations.
    """
    c_llm = consumer_llm or llm
    profiles = consumer_profiles or CONSUMER_PROFILES
    profile_names = list(profiles.keys())
    _seq = [0]  # mutable counter for event sequencing

    def _emit(event: dict):
        _seq[0] += 1
        event["seq"] = _seq[0]
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
        if on_event:
            on_event(event)

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
        sys_prompt = build_consumer_system_prompt(profile_name, budget, interest, product_list)
        consumer_system_prompts[conv_id] = sys_prompt

        # Generate opening message from consumer
        opening = _generate_opening(c_llm, sys_prompt)

        conv = Conversation(
            id=conv_id,
            consumer_profile=profile_name,
            turns=[Turn(role="consumer", content=opening, turn_number=1)],
        )
        conversations.append(conv)

        _emit({"type": "turn", "round": 1, "conv_id": conv_id, "role": "consumer",
               "content": opening, "stock": stock.snapshot()})
        if on_turn:
            on_turn(1, conv_id, "consumer", opening)

    # Round-robin turns — one full exchange (seller→consumer) per conversation,
    # in random order each round, so stock is always current.
    for turn_round in range(1, max_turns + 1):
        active = [c for c in conversations if c.status == "active"]
        if not active:
            break

        # Shuffle order each round
        random.shuffle(active)

        for conv in active:
            # --- Seller responds ---
            other_convs = [c for c in conversations if c.id != conv.id]

            seller_messages = build_seller_context(
                seller_prompt=seller_prompt,
                catalog_text=catalog_text,
                constraints_text=constraints_text,
                current_conversation=conv.turns,
                other_conversations=other_convs,
                stock_text=stock.get_stock_text(),
            )

            try:
                raw_seller = llm.send(seller_messages, json_mode=True)
            except Exception:
                raw_seller = ""
            if not raw_seller or not raw_seller.strip():
                last_msg = conv.turns[-1].content if conv.turns else ""
                simple_msgs = [
                    {"role": "system", "content": seller_prompt},
                    {"role": "user", "content": last_msg},
                ]
                try:
                    raw_seller = llm.send(simple_msgs, json_mode=True)
                except Exception:
                    raw_seller = ""

            seller_response = _parse_seller_response(raw_seller)
            conv.turns.append(
                Turn(role="seller", content=seller_response, turn_number=turn_round)
            )
            _emit({"type": "turn", "round": turn_round, "conv_id": conv.id, "role": "seller",
                   "content": seller_response, "stock": stock.snapshot()})
            if on_turn:
                on_turn(turn_round, conv.id, "seller", seller_response)

            # --- Consumer responds (skip on last round) ---
            if turn_round >= max_turns:
                conv.outcome = "timeout"
                conv.status = "finished"
                continue

            consumer_messages = build_consumer_messages(
                system_prompt=consumer_system_prompts[conv.id],
                turns=conv.turns,
            )

            try:
                raw_response = c_llm.send(consumer_messages, json_mode=True)
            except Exception:
                raw_response = ""

            parsed = _parse_consumer_response(raw_response)
            message = parsed.get("message", "").strip()
            status = parsed.get("status", "browsing")

            if not message:
                message = "No thanks, I'll keep looking around."
                status = "no_purchase"

            conv.turns.append(
                Turn(
                    role="consumer",
                    content=message,
                    turn_number=turn_round + 1,
                )
            )

            _emit({"type": "turn", "round": turn_round + 1, "conv_id": conv.id, "role": "consumer",
                   "content": message, "stock": stock.snapshot()})
            _emit({"type": "consumer_intent", "conv_id": conv.id, "status": status,
                   "raw_json": parsed})
            if on_turn:
                on_turn(turn_round + 1, conv.id, "consumer", message)

            # --- Process outcome immediately (stock updates before next conversation) ---
            if status == "purchase":
                product = parsed.get("product", "")
                price = parsed.get("price", 0) or 0
                stock_before = stock.get_stock(product)
                sold = product and stock.sell(product)
                if sold:
                    stock_after = stock.get_stock(product)
                    conv.outcome = "sale"
                    conv.sale_details = {
                        "product": product,
                        "price": float(price) if price else 0,
                    }
                    conv.status = "finished"
                    _emit({"type": "stock_update", "product": product,
                           "before": stock_before, "after": stock_after, "conv_id": conv.id})
                    _emit({"type": "status_change", "conv_id": conv.id, "outcome": "sale",
                           "details": conv.sale_details})
            elif status == "no_purchase":
                conv.outcome = "no_sale"
                conv.status = "finished"
                _emit({"type": "status_change", "conv_id": conv.id, "outcome": "no_sale",
                       "details": {}})

    # Mark any remaining active conversations as timeout
    for conv in conversations:
        if conv.status == "active":
            conv.outcome = "timeout"
            conv.status = "finished"

    return conversations


def _parse_seller_response(raw: str) -> str:
    """Parse structured JSON response from seller LLM. Returns the message string."""
    if not raw or not raw.strip():
        return "Hi! Tell me what you're looking for and I'll help you out."

    try:
        parsed = json.loads(raw.strip())
        if isinstance(parsed, dict) and "message" in parsed:
            return parsed["message"].strip()
    except json.JSONDecodeError:
        pass

    parsed = extract_json(raw)
    if parsed and isinstance(parsed, dict) and "message" in parsed:
        return parsed["message"].strip()

    # Last resort: strip reasoning tags and return raw text
    from arena.llm import _strip_reasoning_tags
    return _strip_reasoning_tags(raw).strip() or "Hi! Tell me what you're looking for and I'll help you out."


def _parse_consumer_response(raw: str) -> dict:
    """Parse structured JSON response from consumer LLM.

    Expected format: {"message": "...", "status": "browsing|purchase|no_purchase", ...}
    Falls back gracefully if the LLM doesn't return valid JSON.
    """
    if not raw or not raw.strip():
        return {"message": "", "status": "no_purchase"}

    # Try direct json.loads first (ideal path)
    try:
        parsed = json.loads(raw.strip())
        if isinstance(parsed, dict) and "message" in parsed:
            return parsed
    except json.JSONDecodeError:
        pass

    # Fallback: use extract_json for models that wrap in markdown etc.
    parsed = extract_json(raw)
    if parsed and isinstance(parsed, dict) and "message" in parsed:
        return parsed

    # Last resort: treat raw text as a plain message
    return {"message": raw.strip(), "status": "browsing"}


def _pick_interest(product_list: Optional[list[str]]) -> str:
    """Pick a product or category the consumer is interested in."""
    if product_list:
        return random.choice(product_list)
    return "available products"


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


def _find_product_by_price(price: float, price_map: dict[str, float]) -> Optional[str]:
    """Find the product whose list price best matches the given sale price.

    Returns the product if the price is within 15% below list price (valid discount range).
    """
    best = None
    best_diff = float("inf")
    for product, list_price in price_map.items():
        # Price should be between 85% and 100% of list price
        if list_price * 0.85 <= price <= list_price * 1.05:
            diff = abs(price - list_price)
            if diff < best_diff:
                best_diff = diff
                best = product
    return best


def _extract_product_from_conversation(
    conv: Conversation, stock: StockTracker
) -> str:
    """Try to figure out which product is being discussed from conversation context."""
    # Look through all messages for product names that are in stock
    all_text = " ".join(t.content for t in conv.turns).lower()
    snapshot = stock.snapshot()
    for product in snapshot:
        if product.lower() in all_text:
            return product
    return "unknown"


def _generate_opening(llm: LLMClient, consumer_system_prompt: str) -> str:
    """Generate the consumer's opening message."""
    messages = [
        {"role": "system", "content": consumer_system_prompt},
        {
            "role": "user",
            "content": (
                "Write your first message to the seller. "
                "Remember that you are the customer and you are starting the chat."
            ),
        },
    ]
    raw = llm.send(messages, json_mode=True)
    if not raw or not raw.strip():
        return "Hi, what products do you have available?"
    parsed = _parse_consumer_response(raw)
    return parsed.get("message", "Hi, what products do you have available?")
