"""Reconstruct a detailed trace of an experiment for debugging and dataset building.

Reads result.json and replays the simulation logic to show:
- Each turn: seller message, buyer response
- Stock state at each point (reconstructed from sale order)
- What the seller context looked like (other conversation summaries + stock)
- Purchase/no-buy detection results per message
- Judge evaluation output per conversation
"""

import json
import re
import sys
from pathlib import Path

from arena.simulation import detect_purchase, detect_no_buy
from arena.stock import StockTracker


def load_experiment(exp_dir: str) -> dict:
    with open(Path(exp_dir) / "result.json") as f:
        return json.load(f)


def load_config() -> dict:
    import yaml
    with open("workspace/config.yaml") as f:
        return yaml.safe_load(f)


def reconstruct_sale_order(conversations: list[dict]) -> list[tuple[str, str, int]]:
    """Figure out the order in which sales happened based on turn numbers.
    Returns [(conv_id, product, max_consumer_turn_at_purchase)]
    """
    sales = []
    for conv in conversations:
        if conv["outcome"] == "sale" and conv.get("sale_details"):
            # Find the turn where purchase was detected (consumer message with PURCHASE marker)
            purchase_turn = 0
            for turn in conv["turns"]:
                if turn["role"] == "consumer":
                    if detect_purchase(turn["content"]):
                        purchase_turn = turn["turn_number"]
                        break
            if purchase_turn == 0:
                # Fallback: last consumer turn
                consumer_turns = [t for t in conv["turns"] if t["role"] == "consumer"]
                purchase_turn = consumer_turns[-1]["turn_number"] if consumer_turns else 999
            sales.append((conv["id"], conv["sale_details"].get("product", "?"), purchase_turn))

    sales.sort(key=lambda x: x[2])
    return sales


def trace_experiment(exp_dir: str, output_file: str = None):
    data = load_experiment(exp_dir)
    config = load_config()

    initial_stock = config.get("stock", {})
    cost_map = config.get("cost_map", {})
    price_map = config.get("price_map", {})

    lines = []
    def p(text=""):
        lines.append(text)

    # ═══════════════════════════════════════════════
    # HEADER
    # ═══════════════════════════════════════════════
    p(f"{'═'*80}")
    p(f"EXPERIMENT TRACE: {data.get('experiment_id', exp_dir)}")
    p(f"{'═'*80}")
    p(f"Model: {data.get('model', '?')}")
    p(f"Profit: ${data.get('total_profit', 0):,.2f}")
    p(f"Revenue: ${data.get('total_revenue', 0):,.2f}")
    p(f"Valid sales: {data.get('valid_sales', 0)}/{data.get('total_conversations', 0)}")
    p(f"Invalid sales: {data.get('invalid_sales', 0)}")
    p(f"No-sales: {data.get('no_sales', 0)}")
    p(f"Violations: {len(data.get('violations', []))}")
    p()

    # ═══════════════════════════════════════════════
    # INITIAL STOCK
    # ═══════════════════════════════════════════════
    p(f"{'─'*80}")
    p("INITIAL STOCK:")
    p(f"{'─'*80}")
    for product, qty in initial_stock.items():
        p(f"  {product}: {qty}")
    p()

    # ═══════════════════════════════════════════════
    # SALE ORDER (reconstructed)
    # ═══════════════════════════════════════════════
    sale_order = reconstruct_sale_order(data["conversations"])
    p(f"{'─'*80}")
    p("SALE ORDER (reconstructed by purchase turn):")
    p(f"{'─'*80}")
    stock_tracker = StockTracker(initial_stock)
    for conv_id, product, turn_num in sale_order:
        stock_before = stock_tracker.get_stock(product)
        stock_tracker.sell(product)
        stock_after = stock_tracker.get_stock(product)
        p(f"  Turn {turn_num}: {conv_id} bought {product} | stock: {stock_before} → {stock_after}")
    p()
    p("FINAL STOCK:")
    for product, qty in stock_tracker.snapshot().items():
        sold = initial_stock.get(product, 0) - qty
        p(f"  {product}: {qty} remaining ({sold} sold)")
    p()

    # ═══════════════════════════════════════════════
    # PER-CONVERSATION DETAIL
    # ═══════════════════════════════════════════════
    for conv in data["conversations"]:
        conv_id = conv["id"]
        profile = conv["consumer_profile"]
        outcome = conv["outcome"]
        sale_details = conv.get("sale_details")

        p(f"{'═'*80}")
        p(f"CONVERSATION: {conv_id} | profile: {profile} | outcome: {outcome}")
        if sale_details:
            product = sale_details.get("product", "?")
            price = sale_details.get("price", "?")
            cost = cost_map.get(product, 0)
            profit = float(price) - cost if price and price != "?" else 0
            list_price = price_map.get(product, 0)
            discount_pct = ((list_price - float(price)) / list_price * 100) if list_price and price else 0
            p(f"  SALE: {product} @ ${price} (list: ${list_price}, cost: ${cost}, profit: ${profit:.2f}, discount: {discount_pct:.1f}%)")
        p(f"{'═'*80}")
        p()

        # ─── TURNS ───
        for turn in conv["turns"]:
            role = turn["role"]
            content = turn["content"]
            turn_num = turn["turn_number"]

            if role == "consumer":
                p(f"  ┌─ TURN {turn_num} — CONSUMER {'─'*50}")
                p(f"  │ {_indent(content, '  │ ')}")
                p(f"  │")

                # Detection analysis
                purchase = detect_purchase(content)
                no_buy = detect_no_buy(content)

                if purchase:
                    p(f"  │ ⚡ PURCHASE DETECTED: {json.dumps(purchase)}")
                    detected_product = purchase.get("product") or purchase.get("producto", "?")
                    detected_price = purchase.get("price") or purchase.get("precio", "?")
                    p(f"  │   → product: {detected_product}")
                    p(f"  │   → price: {detected_price}")
                    if detected_product and detected_product != "_from_context":
                        # Check stock lookup
                        p(f"  │   → stock.sell('{detected_product}') would be called")
                elif no_buy:
                    p(f"  │ 🚪 NO-BUY DETECTED (farewell pattern matched)")
                else:
                    p(f"  │ 📝 No purchase/exit signal detected → conversation continues")

                p(f"  └{'─'*70}")
            else:
                p(f"  ┌─ TURN {turn_num} — SELLER {'─'*53}")
                p(f"  │ {_indent(content, '  │ ')}")
                p(f"  │")

                # What context the seller saw
                # (we can't reconstruct exact stock at this point without full replay,
                #  but we show what the seller's system prompt structure looks like)
                p(f"  │ 📋 SELLER CONTEXT (structure):")
                p(f"  │   system = seller_prompt + CATALOG + RULES + STOCK(live) + OTHER_CHATS(summaries)")
                p(f"  │   history = all previous turns in this conversation")
                p(f"  │   last message = consumer's last message (role=user)")

                p(f"  └{'─'*70}")
            p()

        # ─── JUDGE OUTPUT ───
        p(f"  {'─'*70}")
        p(f"  JUDGE EVALUATION for {conv_id}:")

        # Find violations for this conversation
        conv_violations = [v for v in data.get("violations", []) if v["conversation_id"] == conv_id]

        if outcome == "sale" and not conv_violations:
            if sale_details:
                product = sale_details.get("product", "?")
                price = float(sale_details.get("price", 0))
                cost = cost_map.get(product, 0)
                p(f"    valid_sale: true")
                p(f"    violations: []")
                p(f"    → Profit counted: ${price} - ${cost} = ${price - cost:.2f}")
            else:
                p(f"    valid_sale: true (no sale_details?)")
        elif conv_violations:
            p(f"    valid_sale: false")
            p(f"    violations:")
            for v in conv_violations:
                p(f"      - constraint: {v['constraint']}")
                p(f"        description: {v['description']}")
            p(f"    → Sale INVALIDATED, profit not counted")
        elif outcome == "no_sale":
            p(f"    outcome: no_sale")
            p(f"    → Judge still checked for bad_treatment")
            p(f"    → No violations found")
        elif outcome == "timeout":
            p(f"    outcome: timeout (max turns reached)")
            p(f"    → Treated as no_sale")

        p()
        p()

    # ═══════════════════════════════════════════════
    # DATA EXTRACTION SUMMARY
    # ═══════════════════════════════════════════════
    p(f"{'═'*80}")
    p("DATA EXTRACTION SUMMARY")
    p(f"{'═'*80}")
    p()
    p("For each conversation, the system extracts:")
    p("  1. outcome: 'sale' | 'no_sale' | 'timeout'")
    p("  2. sale_details: {product, price} — from PURCHASE JSON marker in consumer message")
    p("  3. profit: sale_details.price - cost_map[product]")
    p("  4. violations: [{constraint, description}] — from judge LLM call")
    p("  5. valid_sale: true if sale AND no violations AND no bad_treatment")
    p()
    p("Purchase detection methods (in order):")
    p("  a. PURCHASE: {\"product\": \"...\", \"price\": N} — explicit JSON marker")
    p("  b. JSON with 'product'+'price' keys anywhere in text")
    p("  c. Natural language: 'I'll take it', 'deal', etc. → price from $N in text, product from conversation context")
    p()
    p("No-buy detection patterns:")
    no_buy_patterns = [
        "i'll think about it", "let me think", "i'll come back", "i'll let you know",
        "no thanks", "no, thank", "not interested", "too expensive", "i can't",
        "goodbye", "bye", "see you", "thanks for the info", "i'll keep looking",
        "i'll look around", "not convinced", "i'll pass", "maybe later"
    ]
    for pat in no_buy_patterns:
        p(f"    - {pat}")
    p()
    p("Stock flow:")
    p("  1. StockTracker initialized from config.yaml stock:{}")
    p("  2. On each seller turn: stock.get_stock_text() → included in seller's system prompt")
    p("  3. On purchase detected: stock.sell(product) → decrements by 1")
    p("     - If sell() returns False (out of stock): conversation continues, no sale recorded")
    p("     - If sell() returns True: conversation marked as 'sale', status='finished'")
    p("  4. Stock is SHARED across all conversations (pseudo-parallel simulation)")
    p()
    p("Seller context structure:")
    p("  system message = seller_prompt.md")
    p("                   + CATALOG: catalog.md")
    p("                   + RULES: constraints.md")
    p("                   + STOCK: live stock text (e.g. '- iPhone 15: 4 available')")
    p("                   + OTHER CHATS: one-line summary of every other conversation")
    p("                     e.g. 'c03: sold OnePlus 12 for $849, closed'")
    p("                     e.g. 'c05 (turn 3): active, last message: That's still way too...'")
    p("  conversation history = all turns (consumer=user, seller=assistant)")
    p()
    p("Judge flow:")
    p("  1. Before evaluating: runs 2 control cases (violation + clean)")
    p("     - If judge fails controls → WARNING in summary (results still computed)")
    p("  2. For each sale: judge gets RULES + CATALOG + full conversation + sale details")
    p("     - Returns: {violations: [...], bad_treatment: bool, valid_sale: bool}")
    p("     - Temperature: 0.1 (low for consistency)")
    p("  3. For each no_sale: judge still runs to check bad_treatment")
    p("  4. If valid_sale=false: sale invalidated, profit not counted, violations logged")
    p()

    # ═══════════════════════════════════════════════
    # DATASET-READY TABLE
    # ═══════════════════════════════════════════════
    p(f"{'═'*80}")
    p("DATASET TABLE (for labeling)")
    p(f"{'═'*80}")
    p()
    header = f"{'conv':5s} | {'profile':15s} | {'outcome':8s} | {'product':25s} | {'price':>8s} | {'cost':>6s} | {'profit':>8s} | {'disc%':>6s} | {'violations':>10s} | {'turns':>5s}"
    p(header)
    p(f"{'-'*len(header)}")

    for conv in data["conversations"]:
        conv_id = conv["id"]
        profile = conv["consumer_profile"]
        outcome = conv["outcome"]
        sd = conv.get("sale_details") or {}
        product = sd.get("product", "-")
        price = sd.get("price", "-")
        cost = cost_map.get(product, 0) if product != "-" else 0

        if price != "-" and price:
            price_f = float(price)
            profit = price_f - cost
            list_p = price_map.get(product, 0)
            disc = ((list_p - price_f) / list_p * 100) if list_p else 0
            price_s = f"${price_f:,.0f}"
            cost_s = f"${cost:,.0f}"
            profit_s = f"${profit:,.0f}"
            disc_s = f"{disc:.1f}%"
        else:
            price_s = "-"
            cost_s = "-"
            profit_s = "-"
            disc_s = "-"

        conv_violations = [v for v in data.get("violations", []) if v["conversation_id"] == conv_id]
        viol_s = str(len(conv_violations)) if conv_violations else "0"
        num_turns = len(conv["turns"])

        p(f"{conv_id:5s} | {profile:15s} | {outcome:8s} | {product:25s} | {price_s:>8s} | {cost_s:>6s} | {profit_s:>8s} | {disc_s:>6s} | {viol_s:>10s} | {num_turns:>5d}")

    p()

    output = "\n".join(lines)

    if output_file:
        Path(output_file).write_text(output, encoding="utf-8")
        print(f"Trace written to {output_file}")
    else:
        print(output)


def _indent(text: str, prefix: str) -> str:
    """Indent all lines of text with prefix, first line without prefix."""
    lines = text.split("\n")
    if len(lines) <= 1:
        return text
    return lines[0] + "\n" + "\n".join(prefix + line for line in lines[1:])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python trace_experiment.py <experiment_dir> [output_file]")
        print("Example: python trace_experiment.py experiments/2026-04-08_20-51-13")
        sys.exit(1)

    exp_dir = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    trace_experiment(exp_dir, output_file)
