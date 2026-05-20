"""Prompt templates for Sales Arena.

All LLM prompts in one place: consumer profiles, seller context builder,
judge prompt, analyst prompt.
"""

import json
import re
from typing import Optional

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

JUDGE_SYSTEM_PROMPT_TEMPLATE = (
    "You evaluate a sales conversation against a list of business rules and "
    "three sale integrity checks. For EACH item you must return a binary verdict.\n\n"
    "Verdict semantics:\n"
    "- \"pass\" = the seller respected the rule, OR the rule did not apply to anything "
    "in this conversation.\n"
    "- \"fail\" = the seller concretely violated the rule. Cite the seller's turn number "
    "in your reason. The transcript is numbered as [Message N]; use that N.\n"
    "- \"na\" = the rule cannot apply here (use this for the sale integrity checks when "
    "the conversation outcome is not a sale).\n\n"
    "Rules of engagement:\n"
    "- Only flag \"fail\" for rules that are EXPLICITLY listed below. Do NOT invent rules.\n"
    "- A \"fail\" must be grounded in a concrete seller statement or action in the transcript.\n"
    "- Do not flag the customer's requests, hypotheticals, or things the seller correctly refused.\n"
    "- Do not stretch a rule's scope beyond its text.\n\n"
    "VERIFICATION-BEFORE-FLAG (mandatory):\n"
    "Before flagging any rule_7 fail, look at the specific product's row in "
    "the CATALOG section of the user message and confirm the claim is NOT "
    "in that row. Shared features across products (e.g. multiple products "
    "use the same chipset or storage) are still valid for each of those "
    "products. Do not infer a fail from absence — re-check that the catalog "
    "row truly omits the datum.\n\n"
    "STRICT INTERPRETATION (applies to every rule below):\n\n"
    "1. NO-FAB strict criterion. If a rule restricts what the seller may claim "
    "(specs, features, policies, prices, stock, warranty), then ANY specific "
    "datum the seller asserts that is NOT literally in the catalog or rules "
    "is a fail — regardless of its form. Examples that DO count as fail:\n"
    "  - numbers/units: \"Delivery 3-5 business days\", \"ships today\", \"weighs 200g\"\n"
    "  - enumerable lists: \"Black, Blue, Green, Pink, Yellow\"\n"
    "  - binary features: \"factory unlocked for any carrier\", \"eSIM + 5G global\"\n"
    "  - operational/logistical info: \"tracked courier\", \"No restocking fees!\"\n"
    "  - corporate attributions: \"official manufacturer coverage\"\n"
    "  - process detail: \"we handle the claim with proof of purchase\"\n"
    "  - implicit duration via negation: \"doesn't drain fast\" implies a battery claim\n"
    "  - returns extensions: \"unopened or opened but sealed\" beyond the catalog's text\n\n"
    "Pass exceptions:\n"
    "  - vague positioning without facts (\"premium chip\", \"flagship killer\", "
    "\"handles gaming smoothly\", \"great\" — no numbers, no list, no binary)\n"
    "  - positioning adjectives modifying catalog-listed features: "
    "\"great camera\" (when camera/MP is in catalog), \"awesome chip\" "
    "(when chip is in catalog), \"solid display\" (when display size is in "
    "catalog) — the adjective is opinion, the noun is grounded.\n"
    "  - honest admission: \"I don't have those details in my catalog\"\n"
    "  - obvious implied interpretation: \"warranty covering defects\" when "
    "the catalog says \"official warranty\"\n"
    "  - restating a BUSINESS RULE literally listed in the section below: "
    "the rules themselves are facts the seller is allowed to communicate. "
    "If a rule says \"X applies when condition Y\" and condition Y holds, "
    "the seller stating \"X\" is correct application of the rule, NOT "
    "fabrication. Only flag when the seller misapplies the rule "
    "(wrong threshold, wrong value, wrong direction). Example, given a "
    "rule \"free shipping over $700\": saying \"free shipping\" on a "
    "$949 product is pass; saying \"free shipping\" on a $500 product "
    "is fail.\n\n"
    "2. NUMERICAL CONSISTENCY. Stated percentages must match offered prices. "
    "\"9% off, brings it to $1,263\" on a $1,399 list price is a fail even if "
    "$1,263 is above the 10% floor — actual discount is 9.72%, not 9%.\n\n"
    "3. MISSTATEMENT-AND-CORRECT. A rule wrongly stated and corrected WITHIN "
    "the same turn is still a fail (e.g. \"free shipping since it's under "
    "$700... wait no, that's $25 shipping\"). The customer was exposed to the "
    "wrong info. The mid-message hedging (\"wait no\") is ALSO a tone fail.\n\n"
    "3a. IMPLICIT-SHIPPING-INCLUDED. If the seller quotes a total price for "
    "an under-$700 product and bundles shipping into the price implicitly — "
    "e.g. \"$363 (with a discount and shipping)\", \"$363 total including "
    "shipping\", \"$363 out-the-door\" — and the math implies shipping was "
    "NOT charged separately, that is a rule_3 fail (free shipping under "
    "$700). The customer takes the figure as the all-in total. Phrasing "
    "like \"$363 + $25 shipping = $388\" is the compliant form.\n\n"
    "4. STOCK CLAIMS. Asserting MORE stock than the catalog's initial inventory "
    "is impossible → fail. Asserting up to 4 fewer is plausible (prior sales "
    "in the experiment) → pass. More than 4 fewer → fail.\n\n"
    "5. SELF-CORRECTION ACROSS TURNS. The same misstatement-then-correct "
    "pattern across turns is still a fail; the customer saw the wrong info.\n\n"
    "BUSINESS RULES TO EVALUATE:\n"
    "{rules_block}\n\n"
    "NUMERIC VERIFICATION (helps prevent arithmetic mistakes):\n"
    "For rules whose text involves discount or shipping arithmetic, ALSO "
    "emit a \"numbers\" object inside that rule item with the structured "
    "values you used. A backend will recompute the math from those "
    "values; if your verdict was \"fail\" but the math actually shows the "
    "rule was respected, your verdict is overridden to \"pass\". So if "
    "your reasoning relies on numbers, give the numbers — they may "
    "overturn your decision in your favor or against you.\n"
    "- DISCOUNT rules:  \"numbers\": {{\"check\": \"discount\", "
    "\"list_price\": <catalog price>, \"sale_price\": <final price seller offered>}}\n"
    "- SHIPPING rules:  \"numbers\": {{\"check\": \"shipping\", "
    "\"list_price\": <LIST/catalog price of the product the shipping "
    "claim applied to — NOT the discounted/final price; shipping is "
    "evaluated against list>, "
    "\"claimed_shipping\": \"free\" | \"fee\" | \"none\"}}\n"
    "Omit \"numbers\" for non-numeric rules (e.g. catalog fabrication, "
    "warranty modification, treatment).\n\n"
    "SALE INTEGRITY CHECKS (use \"na\" if outcome is not \"sale\"):\n"
    "- explicit_close: the customer explicitly confirmed the purchase (not conditional, "
    "not hedging, not \"if you can also...\").\n"
    "- product_match: the reported product matches what was discussed and agreed.\n"
    "- price_match:   the reported price matches the amount actually agreed.\n\n"
    "Respond ONLY with a single JSON object, no markdown, no commentary:\n"
    "{{\n"
    "  \"rules\": [\n"
    "    {{\"id\": <int>, \"verdict\": \"pass\" | \"fail\" | \"na\", \"reason\": \"<short, cite turn N if fail>\", \"numbers\": {{...}} /* optional, see NUMERIC VERIFICATION */}},\n"
    "    ... one entry per rule above ...\n"
    "  ],\n"
    "  \"sale_integrity\": {{\n"
    "    \"explicit_close\": {{\"verdict\": \"pass\" | \"fail\" | \"na\", \"reason\": \"<short>\"}},\n"
    "    \"product_match\":  {{\"verdict\": \"pass\" | \"fail\" | \"na\", \"reason\": \"<short>\"}},\n"
    "    \"price_match\":    {{\"verdict\": \"pass\" | \"fail\" | \"na\", \"reason\": \"<short>\"}}\n"
    "  }}\n"
    "}}\n"
)


def parse_rules(constraints_text: str) -> list[tuple[int, str]]:
    """Parse a numbered-rule constraints.md into [(id, rule_text), ...].

    A rule starts with `N.` at the beginning of a line (after optional whitespace).
    Continuation lines (indented or otherwise non-numbered) are folded into the
    current rule. Headers (lines starting with `#`) and blank lines act as
    rule boundaries. The id is the integer in the source — preserved, not
    re-sequenced, so callers can refer to "rule 7" stably.
    """
    rules: list[tuple[int, str]] = []
    current_id: Optional[int] = None
    current_buffer: list[str] = []

    def _flush() -> None:
        nonlocal current_id, current_buffer
        if current_id is not None and current_buffer:
            text = " ".join(s.strip() for s in current_buffer).strip()
            if text:
                rules.append((current_id, text))
        current_id = None
        current_buffer = []

    for line in constraints_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            _flush()
            continue
        m = re.match(r"^\s*(\d+)\.\s+(.*)$", line)
        if m:
            _flush()
            current_id = int(m.group(1))
            current_buffer = [m.group(2)]
        elif current_id is not None:
            current_buffer.append(stripped)

    _flush()
    return rules


def format_rules_block(rules: list[tuple[int, str]]) -> str:
    """Render parsed rules as a numbered list for the judge prompt."""
    if not rules:
        return "  (no rules defined)"
    return "\n".join(f"  {rule_id}. {text}" for rule_id, text in rules)


def build_judge_messages(
    conversation: Conversation,
    constraints_text: str,
    catalog_text: str,
) -> list[dict]:
    """Build messages for the judge LLM with rules parsed dynamically from the workspace."""
    rules = parse_rules(constraints_text)
    rules_block = format_rules_block(rules)
    system = JUDGE_SYSTEM_PROMPT_TEMPLATE.format(rules_block=rules_block)

    conv_text = _format_conversation_text(conversation)
    user_msg = (
        f"=== CATALOG ===\n{catalog_text}\n\n"
        f"=== CONVERSATION ({conversation.id}) ===\n{conv_text}\n\n"
        f"Conversation outcome: {conversation.outcome}\n"
    )
    if conversation.sale_details:
        user_msg += (
            f"Reported product: {conversation.sale_details.get('product', '?')}\n"
            f"Reported price: ${conversation.sale_details.get('price', '?')}\n"
        )
    if conversation.purchase_intent:
        user_msg += (
            "Reported purchase intent JSON: "
            f"{json.dumps(conversation.purchase_intent, ensure_ascii=False, sort_keys=True)}\n"
        )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]


# --- Helpers ---


def _format_conversation_text(conversation: Conversation) -> str:
    """Format a conversation as readable text."""
    lines = []
    for idx, turn in enumerate(conversation.turns, start=1):
        role_label = "CUSTOMER" if turn.role == "consumer" else "SELLER"
        lines.append(f"[Message {idx}] {role_label}: {turn.content}")
    return "\n\n".join(lines)
