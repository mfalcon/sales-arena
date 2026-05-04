"""Prompt templates for Sales Arena.

All LLM prompts in one place: consumer profiles, seller context builder,
judge prompt, analyst prompt.
"""

from arena.types import Conversation, Turn


# --- Consumer Profiles ---

CONSUMER_PROFILES = {
    "decisive": {
        "description": "Knows what they want, asks for price and buys quickly.",
        "budget_range": (0.8, 1.2),  # multiplier on product price
        "system_prompt": (
            "You are a customer in a WhatsApp chat with a store. "
            "You know exactly what you want to buy. You get straight to the point: "
            "you ask about the product, the price, and if it works for you, you buy quickly. "
            "You don't waste time with unnecessary chat. "
            "You're friendly but concise."
        ),
    },
    "bargain_hunter": {
        "description": "Wants a discount, compares, pushes for a better price.",
        "budget_range": (0.5, 0.9),
        "system_prompt": (
            "You are a customer in a WhatsApp chat with a store. "
            "Your goal is to get the best possible price. You always ask for a discount, "
            "mention that you've seen cheaper prices elsewhere, and ask about promotions. "
            "If they don't give you a good price, you leave. If you get a reasonable discount, you buy. "
            "You're persistent but not aggressive."
        ),
    },
    "indecisive": {
        "description": "Not sure what they want, needs guidance, asks many questions.",
        "budget_range": (0.7, 1.3),
        "system_prompt": (
            "You are a customer in a WhatsApp chat with a store. "
            "You're not sure what you want to buy. You ask many questions, "
            "request recommendations, and compare options. You need to be guided. "
            "If the seller helps you well, you end up buying. If not, you leave without buying. "
            "You're friendly and a bit scattered."
        ),
    },
    "demanding": {
        "description": "Asks about specs, warranty, after-sales. Needs to be convinced.",
        "budget_range": (0.9, 1.5),
        "system_prompt": (
            "You are a customer in a WhatsApp chat with a store. "
            "Before buying, you need to know everything: technical specifications, warranty, "
            "return policy, shipping times. You ask detailed questions. "
            "If the seller demonstrates knowledge and confidence, you buy. "
            "If they give vague or incorrect answers, you don't buy. "
            "You're polite but rigorous."
        ),
    },
    "rushed": {
        "description": "Wants to resolve quickly, leaves if it takes too long.",
        "budget_range": (0.9, 1.1),
        "system_prompt": (
            "You are a customer in a WhatsApp chat with a store. "
            "You have little time and want to resolve quickly. If the seller responds "
            "clearly and directly, you buy. If they beat around the bush, take too long, or aren't clear, "
            "you leave because you don't have time. You're cordial but impatient. "
            "You value efficiency."
        ),
    },
    "browser": {
        "description": "Just browsing, no real intention to buy.",
        "budget_range": (0.3, 0.6),
        "system_prompt": (
            "You are a customer in a WhatsApp chat with a store. "
            "You have no real intention to buy, you're just looking around. "
            "You ask casual questions, request prices, but always find "
            "an excuse not to buy ('I'll think about it', 'I'll come back later', etc.). "
            "You only buy if the offer is truly irresistible. "
            "You're friendly and chatty."
        ),
    },
}


CONSUMER_CONTEXT_TEMPLATE = (
    "{profile_prompt}\n\n"
    "IMPORTANT INFORMATION:\n"
    "- Your maximum budget is ${budget}.\n"
    "- You are interested in: {interest}.\n\n"
    "You MUST respond with ONLY a JSON object. No text outside the JSON. Format:\n\n"
    '{{"message": "your WhatsApp message here", "status": "browsing"}}\n'
    '{{"message": "your WhatsApp message here", "status": "purchase", "product": "exact product name", "price": 123.45}}\n'
    '{{"message": "your WhatsApp message here", "status": "no_purchase"}}\n\n'
    "Rules:\n"
    '- "message": your natural WhatsApp chat message. Short, casual, like a real person.\n'
    '- "status": MUST be one of "browsing", "purchase", or "no_purchase".\n'
    '- "browsing": you are asking questions, negotiating, thinking, making conditional offers.\n'
    '- "purchase": you are CONFIRMING the buy. You accepted a specific product at a specific price. '
    "Not conditional ('if you can do X'). The seller already agreed to this price.\n"
    '- "no_purchase": you are leaving, saying goodbye, or declining.\n'
    '- "product": MUST be one of these exact names: {product_list}\n'
    '- "price": the final price you BOTH agreed on.\n\n'
    "If you already said goodbye or bought, do not continue the conversation."
)


def build_consumer_system_prompt(
    profile_name: str, budget: float, interest: str, product_list: list[str] = None,
) -> str:
    """Build the system prompt for a consumer."""
    profile = CONSUMER_PROFILES[profile_name]
    products_str = ", ".join(product_list) if product_list else "the products in the catalog"
    return CONSUMER_CONTEXT_TEMPLATE.format(
        profile_prompt=profile["system_prompt"],
        budget=f"{budget:,.0f}",
        interest=interest,
        product_list=products_str,
    )


def build_consumer_messages(
    system_prompt: str, turns: list[Turn]
) -> list[dict]:
    """Build the messages list for a consumer LLM call.

    The consumer's own messages become "assistant" and seller messages become "user".
    The first consumer message is folded into the system prompt to avoid starting
    with an assistant message (some model templates require user-first).
    Ensures the last message is always "user" (required by some model templates).
    """
    messages = [{"role": "system", "content": system_prompt}]

    for i, turn in enumerate(turns):
        if turn.role == "consumer":
            if i == 0:
                # Fold first consumer message into system context
                messages[0]["content"] += (
                    f"\n\nYour first message to the seller was:\n\"{turn.content}\""
                )
            else:
                messages.append({"role": "assistant", "content": turn.content})
        else:
            messages.append({"role": "user", "content": turn.content})

    # Ensure last message is "user" — some model templates require it
    if not messages or messages[-1]["role"] != "user":
        messages.append({
            "role": "user",
            "content": "Respond as the customer in this conversation.",
        })

    return messages


# --- Seller Context ---

SELLER_CONTEXT_TEMPLATE = (
    "{seller_prompt}\n\n"
    "CATALOG:\n{catalog}\n\n"
    "RULES:\n{constraints}\n\n"
    "STOCK:\n{stock}\n\n"
    "OTHER CHATS:\n{other_conversations}\n\n"
    "You MUST respond with ONLY a JSON object. No text outside the JSON.\n"
    '{{"message": "your WhatsApp message to the customer here"}}\n'
    "The message should be natural, short, and casual like WhatsApp. No markdown."
)


def build_seller_context(
    seller_prompt: str,
    catalog_text: str,
    constraints_text: str,
    current_conversation: list[Turn],
    other_conversations: list[Conversation],
    stock_text: str,
) -> list[dict]:
    """Build the full messages list for a seller LLM call.

    The seller gets:
    - System: user's prompt + catalog + constraints + stock + other chats summary
    - History: the current conversation turns
    - The last message is always from the consumer (the seller needs to respond)
    """
    # Build other conversations summary
    if other_conversations:
        summaries = [conv.summary() for conv in other_conversations]
        other_text = "\n".join(summaries)
    else:
        other_text = "None."

    system = SELLER_CONTEXT_TEMPLATE.format(
        seller_prompt=seller_prompt,
        catalog=catalog_text,
        constraints=constraints_text,
        stock=stock_text,
        other_conversations=other_text,
    )

    messages = [{"role": "system", "content": system}]

    # Add conversation history
    for turn in current_conversation:
        role = "user" if turn.role == "consumer" else "assistant"
        messages.append({"role": role, "content": turn.content})

    return messages


# --- Judge ---

JUDGE_SYSTEM_PROMPT = (
    "You are a strict evaluator of sales conversations. Your job is to review "
    "whether the seller violated any business rules or treated the customer poorly.\n\n"
    "You will be given:\n"
    "1. The business rules (constraints) that the seller MUST follow.\n"
    "2. The product catalog with its data.\n"
    "3. The complete conversation between seller and customer.\n\n"
    "Evaluate EACH business rule and determine if it was respected or violated.\n"
    "Also evaluate if the seller was respectful and professional.\n\n"
    "IMPORTANT: Only flag violations for rules that are EXPLICITLY listed in the business rules. "
    "Do NOT invent or infer rules that are not written. If a behavior is not covered by any rule, it is NOT a violation.\n\n"
    "If the conversation outcome is 'sale', you MUST also verify:\n"
    "- Did the customer EXPLICITLY confirm the purchase? (not conditional, not ambiguous)\n"
    "- Does the reported product match what was discussed?\n"
    "- Does the reported price match what was agreed in the conversation?\n"
    "If any of these fail, set purchase_verified to false and explain why.\n\n"
    "Respond ONLY with JSON in this format:\n"
    "```json\n"
    "{\n"
    '  "violations": [\n'
    '    {"constraint": "violated rule", "turn": N, "description": "what happened"}\n'
    "  ],\n"
    '  "bad_treatment": false,\n'
    '  "bad_treatment_description": "",\n'
    '  "valid_sale": true,\n'
    '  "purchase_verified": true,\n'
    '  "purchase_verification_reason": ""\n'
    "}\n"
    "```\n"
    "If there are no violations, return violations as an empty list.\n"
    "valid_sale is false if there is any violation, bad treatment, or purchase_verified is false."
)


def build_judge_messages(
    conversation: Conversation,
    constraints_text: str,
    catalog_text: str,
) -> list[dict]:
    """Build messages for the judge LLM."""
    conv_text = _format_conversation_text(conversation)

    user_msg = (
        f"=== BUSINESS RULES ===\n{constraints_text}\n\n"
        f"=== CATALOG ===\n{catalog_text}\n\n"
        f"=== CONVERSATION ({conversation.id}) ===\n{conv_text}\n\n"
        f"Conversation outcome: {conversation.outcome}\n"
    )
    if conversation.sale_details:
        user_msg += (
            f"Product sold: {conversation.sale_details.get('product', '?')}\n"
            f"Sale price: ${conversation.sale_details.get('price', '?')}\n"
        )

    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]


# --- Helpers ---


def _format_conversation_text(conversation: Conversation) -> str:
    """Format a conversation as readable text."""
    lines = []
    for turn in conversation.turns:
        role_label = "CUSTOMER" if turn.role == "consumer" else "SELLER"
        lines.append(f"[Turn {turn.turn_number}] {role_label}: {turn.content}")
    return "\n\n".join(lines)
