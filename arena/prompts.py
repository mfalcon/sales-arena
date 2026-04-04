"""Prompt templates for Sales Arena.

All LLM prompts in one place: consumer profiles, seller context builder,
judge prompt, analyst prompt.
"""

from arena.types import Conversation, Turn


# --- Consumer Profiles ---

CONSUMER_PROFILES = {
    "decidido": {
        "description": "Sabe lo que quiere, pregunta precio y compra rápido.",
        "budget_range": (0.8, 1.2),  # multiplier on product price
        "system_prompt": (
            "Eres un cliente en un chat de WhatsApp con una tienda. "
            "Sabés exactamente lo que querés comprar. Vas directo al grano: "
            "preguntás por el producto, el precio, y si te convence comprás rápido. "
            "No perdés tiempo con charla innecesaria. "
            "Sos amable pero conciso."
        ),
    },
    "cazador_de_ofertas": {
        "description": "Quiere descuento, compara, presiona por mejor precio.",
        "budget_range": (0.5, 0.9),
        "system_prompt": (
            "Eres un cliente en un chat de WhatsApp con una tienda. "
            "Tu objetivo es conseguir el mejor precio posible. Siempre pedís descuento, "
            "mencionás que viste precios más baratos en otro lado, preguntás por promociones. "
            "Si no te dan un buen precio, te vas. Si te dan un descuento razonable, comprás. "
            "Sos persistente pero no agresivo."
        ),
    },
    "indeciso": {
        "description": "No sabe bien qué quiere, necesita guía, hace muchas preguntas.",
        "budget_range": (0.7, 1.3),
        "system_prompt": (
            "Eres un cliente en un chat de WhatsApp con una tienda. "
            "No estás seguro de qué querés comprar. Hacés muchas preguntas, "
            "pedís recomendaciones, comparás opciones. Necesitás que te guíen. "
            "Si el vendedor te ayuda bien, terminás comprando. Si no, te vas sin comprar. "
            "Sos amable y un poco disperso."
        ),
    },
    "exigente": {
        "description": "Pregunta specs, garantía, postventa. Necesita estar convencido.",
        "budget_range": (0.9, 1.5),
        "system_prompt": (
            "Eres un cliente en un chat de WhatsApp con una tienda. "
            "Antes de comprar necesitás saber todo: especificaciones técnicas, garantía, "
            "política de devolución, tiempos de envío. Hacés preguntas detalladas. "
            "Si el vendedor demuestra conocimiento y confianza, comprás. "
            "Si da respuestas vagas o incorrectas, no comprás. "
            "Sos educado pero riguroso."
        ),
    },
    "apurado": {
        "description": "Quiere resolver rápido, si tarda se va.",
        "budget_range": (0.9, 1.1),
        "system_prompt": (
            "Eres un cliente en un chat de WhatsApp con una tienda. "
            "Tenés poco tiempo y querés resolver rápido. Si el vendedor responde "
            "claro y directo, comprás. Si da vueltas, tarda, o no es claro, "
            "te vas porque no tenés tiempo. Sos cordial pero impaciente. "
            "Valorás la eficiencia."
        ),
    },
    "curioso": {
        "description": "Entra a ver, no tiene intención real de comprar.",
        "budget_range": (0.3, 0.6),
        "system_prompt": (
            "Eres un cliente en un chat de WhatsApp con una tienda. "
            "No tenés intención real de comprar, solo estás mirando. "
            "Hacés preguntas casuales, pedís precios, pero siempre encontrás "
            "una excusa para no comprar ('lo voy a pensar', 'después vuelvo', etc.). "
            "Solo comprás si la oferta es realmente irresistible. "
            "Sos simpático y conversador."
        ),
    },
}


CONSUMER_CONTEXT_TEMPLATE = (
    "{profile_prompt}\n\n"
    "INFORMACIÓN IMPORTANTE:\n"
    "- Tu presupuesto máximo es ${budget}.\n"
    "- Estás interesado en: {interest}.\n"
    "- Respondé de forma natural, como en un chat de WhatsApp. Mensajes cortos.\n"
    "- NO uses formato JSON ni markdown. Hablá como una persona normal.\n\n"
    "CUANDO DECIDAS COMPRAR:\n"
    "Escribí tu mensaje normal y al final agregá en una línea aparte:\n"
    "COMPRA: {{\"producto\": \"nombre del producto\", \"precio\": precio_acordado}}\n\n"
    "CUANDO DECIDAS NO COMPRAR:\n"
    "Simplemente despedite naturalmente. El vendedor entenderá.\n\n"
    "SI YA TE DESPEDISTE O COMPRASTE:\n"
    "No sigas la conversación."
)


def build_consumer_system_prompt(
    profile_name: str, budget: float, interest: str
) -> str:
    """Build the system prompt for a consumer."""
    profile = CONSUMER_PROFILES[profile_name]
    return CONSUMER_CONTEXT_TEMPLATE.format(
        profile_prompt=profile["system_prompt"],
        budget=f"{budget:,.0f}",
        interest=interest,
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
                    f"\n\nTu primer mensaje al vendedor fue:\n\"{turn.content}\""
                )
            else:
                messages.append({"role": "assistant", "content": turn.content})
        else:
            messages.append({"role": "user", "content": turn.content})

    # Ensure last message is "user" — some model templates require it
    if not messages or messages[-1]["role"] != "user":
        messages.append({
            "role": "user",
            "content": "Respondé como el cliente en esta conversación.",
        })

    return messages


# --- Seller Context ---

SELLER_CONTEXT_TEMPLATE = (
    "{seller_prompt}\n\n"
    "CATÁLOGO:\n{catalog}\n\n"
    "REGLAS:\n{constraints}\n\n"
    "STOCK:\n{stock}\n\n"
    "OTROS CHATS:\n{other_conversations}\n\n"
    "Respondé natural, como WhatsApp. Mensajes cortos. No uses JSON ni markdown."
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
        other_text = "Ninguna."

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
    "Eres un evaluador estricto de conversaciones de venta. Tu trabajo es revisar "
    "si el vendedor violó alguna regla de negocio o trató mal al cliente.\n\n"
    "Se te dará:\n"
    "1. Las reglas de negocio (constraints) que el vendedor DEBE respetar.\n"
    "2. El catálogo de productos con sus datos.\n"
    "3. La conversación completa entre vendedor y cliente.\n\n"
    "Evaluá CADA regla de negocio y determiná si fue respetada o violada.\n"
    "También evaluá si el vendedor fue respetuoso y profesional.\n\n"
    "Respondé ÚNICAMENTE con JSON en este formato:\n"
    "```json\n"
    "{\n"
    '  "violations": [\n'
    '    {"constraint": "regla violada", "turn": N, "description": "qué pasó"}\n'
    "  ],\n"
    '  "bad_treatment": false,\n'
    '  "bad_treatment_description": "",\n'
    '  "valid_sale": true\n'
    "}\n"
    "```\n"
    "Si no hay violaciones, devolvé violations como lista vacía.\n"
    "valid_sale es false si hay alguna violación o mal trato."
)


def build_judge_messages(
    conversation: Conversation,
    constraints_text: str,
    catalog_text: str,
) -> list[dict]:
    """Build messages for the judge LLM."""
    conv_text = _format_conversation_text(conversation)

    user_msg = (
        f"=== REGLAS DE NEGOCIO ===\n{constraints_text}\n\n"
        f"=== CATÁLOGO ===\n{catalog_text}\n\n"
        f"=== CONVERSACIÓN ({conversation.id}) ===\n{conv_text}\n\n"
        f"Resultado de la conversación: {conversation.outcome}\n"
    )
    if conversation.sale_details:
        user_msg += (
            f"Producto vendido: {conversation.sale_details.get('product', '?')}\n"
            f"Precio de venta: ${conversation.sale_details.get('price', '?')}\n"
        )

    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]


# --- Analyst ---

ANALYST_SYSTEM_PROMPT = (
    "Eres un analista experto en ventas. Tu trabajo es revisar conversaciones "
    "entre un vendedor y clientes, y dar feedback constructivo sobre el desempeño "
    "del vendedor.\n\n"
    "Enfocate en:\n"
    "- ¿Entendió lo que el cliente buscaba?\n"
    "- ¿Ofreció el producto adecuado?\n"
    "- ¿Manejó bien las objeciones?\n"
    "- ¿Usó bien la información del catálogo?\n"
    "- ¿Aprovechó oportunidades de venta?\n"
    "- ¿Manejó bien la escasez de stock?\n"
    "- ¿Ofreció alternativas cuando correspondía?\n\n"
    "Sé concreto y accionable. No des feedback genérico.\n"
    "Respondé en español, en formato de texto libre (no JSON)."
)


def build_analyst_messages(
    conversation: Conversation,
    catalog_text: str,
) -> list[dict]:
    """Build messages for the analyst LLM."""
    conv_text = _format_conversation_text(conversation)

    user_msg = (
        f"=== CATÁLOGO ===\n{catalog_text}\n\n"
        f"=== CONVERSACIÓN ({conversation.id}, perfil: {conversation.consumer_profile}) ===\n"
        f"{conv_text}\n\n"
        f"Resultado: {conversation.outcome}\n"
    )
    if conversation.sale_details:
        user_msg += (
            f"Producto vendido: {conversation.sale_details.get('product', '?')}\n"
            f"Precio: ${conversation.sale_details.get('price', '?')}\n"
        )

    return [
        {"role": "system", "content": ANALYST_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]


# --- Helpers ---


def _format_conversation_text(conversation: Conversation) -> str:
    """Format a conversation as readable text."""
    lines = []
    for turn in conversation.turns:
        role_label = "CLIENTE" if turn.role == "consumer" else "VENDEDOR"
        lines.append(f"[Turno {turn.turn_number}] {role_label}: {turn.content}")
    return "\n\n".join(lines)
