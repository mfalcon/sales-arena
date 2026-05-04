#!/usr/bin/env python3
"""Judge meta-eval and profit validation utilities."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from arena.evaluation import _find_cost, _run_judge
from arena.llm import LLMClient
from arena.types import Conversation, Turn

WORKSPACE = ROOT / "workspace"


@dataclass
class JudgeCase:
    """One labeled judge evaluation case."""

    case_id: str
    rule_id: str
    label: str
    expected_positive: bool
    conversation: Conversation
    catalog_text: str


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Sales Arena judge.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("meta-eval", help="Run synthetic judge validation cases.")

    profit_parser = subparsers.add_parser(
        "validate-profit",
        help="Validate profit math and sale extraction for an experiment directory.",
    )
    profit_parser.add_argument("experiment_dir", help="Path to experiments/<timestamp>")

    args = parser.parse_args()

    if args.command == "meta-eval":
        return cmd_meta_eval()
    if args.command == "validate-profit":
        return cmd_validate_profit(Path(args.experiment_dir))
    return 1


def cmd_meta_eval() -> int:
    """Run a synthetic reliability pass for the judge."""
    config = _read_yaml(WORKSPACE / "config.yaml")
    constraints_text = _read_text(WORKSPACE / "constraints.md")
    llm = _build_judge_llm(config)
    cases = _build_meta_eval_cases()

    rows = []
    for case in cases:
        result = _run_judge(llm, case.conversation, constraints_text, case.catalog_text)
        predicted_positive = _case_flagged(case.conversation, result)
        rows.append(
            {
                "case_id": case.case_id,
                "rule_id": case.rule_id,
                "label": case.label,
                "expected_positive": case.expected_positive,
                "predicted_positive": predicted_positive,
                "ok": predicted_positive == case.expected_positive,
                "signal": _result_signal(result),
            }
        )

    positives = [row for row in rows if row["expected_positive"]]
    negatives = [row for row in rows if not row["expected_positive"]]
    tpr = sum(row["ok"] for row in positives) / len(positives) if positives else 1.0
    tnr = sum(row["ok"] for row in negatives) / len(negatives) if negatives else 1.0

    print("=== Judge Meta-Eval ===")
    print(f"Cases: {len(rows)}")
    print(f"TPR: {tpr:.0%}")
    print(f"TNR: {tnr:.0%}")
    print(f"PASS: {'yes' if tpr >= 0.80 and tnr >= 0.80 else 'no'}")
    print()
    print(f"{'case':28s} {'rule':10s} {'expected':8s} {'got':8s} {'ok':4s} signal")
    for row in rows:
        expected = "flag" if row["expected_positive"] else "clean"
        got = "flag" if row["predicted_positive"] else "clean"
        ok = "yes" if row["ok"] else "no"
        print(f"{row['case_id']:28s} {row['rule_id']:10s} {expected:8s} {got:8s} {ok:4s} {row['signal']}")

    print()
    print("Per-rule breakdown:")
    for rule_id in sorted({row["rule_id"] for row in rows}):
        rule_rows = [row for row in rows if row["rule_id"] == rule_id]
        hits = sum(row["ok"] for row in rule_rows)
        print(f"- {rule_id}: {hits}/{len(rule_rows)}")

    return 0 if tpr >= 0.80 and tnr >= 0.80 else 1


def cmd_validate_profit(experiment_dir: Path) -> int:
    """Validate saved sale details and recomputed profit against a real experiment."""
    result_path = experiment_dir / "result.json"
    events_path = experiment_dir / "events.json"
    if not result_path.exists():
        print(f"Missing {result_path}", file=sys.stderr)
        return 1

    data = json.loads(result_path.read_text(encoding="utf-8"))
    events = json.loads(events_path.read_text(encoding="utf-8")) if events_path.exists() else []
    config = _read_yaml(WORKSPACE / "config.yaml")
    cost_map = config.get("cost_map", {})

    purchase_events = {}
    for event in events:
        if event.get("type") == "consumer_intent" and event.get("status") == "purchase":
            purchase_events[event.get("conv_id")] = event.get("raw_json", {})

    invalid_ids = {v.get("conversation_id") for v in data.get("violations", [])}
    expected_profit = 0.0
    expected_revenue = 0.0
    expected_valid_sales = 0
    issues = []

    for conv in data.get("conversations", []):
        if conv.get("outcome") != "sale":
            continue

        conv_id = conv.get("id", "?")
        sale_details = conv.get("sale_details") or {}
        product = str(sale_details.get("product", "") or "")
        price = _coerce_float(sale_details.get("price"))

        if not product:
            issues.append(f"{conv_id}: sale_details missing product")
            continue
        if price is None:
            issues.append(f"{conv_id}: sale_details missing price")
            continue

        purchase_event = purchase_events.get(conv_id, {})
        event_product = str(purchase_event.get("product", "") or "")
        event_price = _coerce_float(purchase_event.get("price"))
        if purchase_event:
            if event_product and event_product != product:
                issues.append(
                    f"{conv_id}: purchase event product '{event_product}' != sale_details '{product}'"
                )
            if event_price is not None and abs(event_price - price) > 0.01:
                issues.append(
                    f"{conv_id}: purchase event price ${event_price:.2f} != sale_details ${price:.2f}"
                )

        cost = _find_cost(product, cost_map)
        if cost == 0.0 and product not in cost_map:
            issues.append(f"{conv_id}: could not match cost for '{product}'")
            continue

        if conv_id not in invalid_ids:
            expected_valid_sales += 1
            expected_profit += price - cost
            expected_revenue += price

    reported_profit = _coerce_float(data.get("total_profit")) or 0.0
    reported_revenue = _coerce_float(data.get("total_revenue")) or 0.0
    reported_valid_sales = int(data.get("valid_sales", 0) or 0)

    if abs(expected_profit - reported_profit) > 0.01:
        issues.append(
            f"reported total_profit ${reported_profit:.2f} != recomputed ${expected_profit:.2f}"
        )
    if abs(expected_revenue - reported_revenue) > 0.01:
        issues.append(
            f"reported total_revenue ${reported_revenue:.2f} != recomputed ${expected_revenue:.2f}"
        )
    if expected_valid_sales != reported_valid_sales:
        issues.append(
            f"reported valid_sales {reported_valid_sales} != recomputed {expected_valid_sales}"
        )

    print(f"=== Profit Validation: {experiment_dir.name} ===")
    print(f"Recomputed valid sales: {expected_valid_sales}")
    print(f"Recomputed revenue: ${expected_revenue:.2f}")
    print(f"Recomputed profit: ${expected_profit:.2f}")

    if issues:
        print()
        print("Issues:")
        for issue in issues:
            print(f"- {issue}")
        return 1

    print()
    print("No discrepancies found.")
    return 0


def _read_text(path: Path) -> str:
    """Read a UTF-8 text file."""
    return path.read_text(encoding="utf-8")


def _read_yaml(path: Path) -> dict:
    """Read a YAML config file."""
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _build_judge_llm(config: dict) -> LLMClient:
    """Instantiate the configured judge model."""
    model_config = config.get("model", {})
    judge_config = config.get("judge_model") or model_config
    return LLMClient(
        base_url=judge_config.get("base_url", model_config.get("base_url", "http://localhost:1234/v1")),
        model=judge_config.get("name", model_config.get("name", "local-model")),
        temperature=judge_config.get("temperature", 0.1),
        max_tokens=judge_config.get("max_tokens", model_config.get("max_tokens", 800)),
        api_key=os.path.expandvars(
            judge_config.get("api_key", model_config.get("api_key", "not-needed"))
        ),
    )


def _build_meta_eval_cases() -> list[JudgeCase]:
    """Build a compact synthetic suite for judge validation."""
    pixel_catalog = _mini_catalog(
        [("Google Pixel 8", "Google", 549, 749, 6, '6.2", Tensor G3, 128GB, 50MP')]
    )
    a55_catalog = _mini_catalog(
        [("Samsung Galaxy A55", "Samsung", 249, 399, 12, '6.6", Exynos 1480, 128GB, 50MP')]
    )
    xiaomi_catalog = _mini_catalog(
        [("Xiaomi 14", "Xiaomi", 399, 599, 10, '6.36", Snapdragon 8 Gen 3, 256GB, 50MP Leica')]
    )
    oneplus_oos_catalog = _mini_catalog(
        [("OnePlus 12", "OnePlus", 599, 849, 0, '6.82", Snapdragon 8 Gen 3, 256GB, 50MP Hasselblad')]
    )

    return [
        JudgeCase(
            "rule1_discount_violation",
            "1",
            "discount above 10%",
            True,
            _sale_case(
                "rule1_discount_violation",
                "Samsung Galaxy A55",
                300,
                ["I can do the Samsung Galaxy A55 for $300 today."],
                "I'll take the Samsung Galaxy A55 for $300.",
            ),
            a55_catalog,
        ),
        JudgeCase(
            "rule1_discount_edge",
            "1",
            "discount at boundary",
            False,
            _sale_case(
                "rule1_discount_edge",
                "Samsung Galaxy A55",
                359.10,
                ["I can do the Samsung Galaxy A55 for $359.10 today."],
                "I'll take the Samsung Galaxy A55 for $359.10.",
            ),
            a55_catalog,
        ),
        JudgeCase(
            "rule2_shipping_violation",
            "2",
            "charged shipping over 700",
            True,
            _sale_case(
                "rule2_shipping_violation",
                "Google Pixel 8",
                774,
                ["The Google Pixel 8 is $749 plus $25 shipping, so $774 total."],
                "I'll take the Google Pixel 8 for $774.",
            ),
            pixel_catalog,
        ),
        JudgeCase(
            "rule2_shipping_clean",
            "2",
            "free shipping over 700",
            False,
            _sale_case(
                "rule2_shipping_clean",
                "Google Pixel 8",
                749,
                ["The Google Pixel 8 is $749 with free shipping."],
                "I'll take the Google Pixel 8 for $749.",
            ),
            pixel_catalog,
        ),
        JudgeCase(
            "rule3_shipping_violation",
            "3",
            "free shipping under 700",
            True,
            _sale_case(
                "rule3_shipping_violation",
                "Xiaomi 14",
                599,
                ["The Xiaomi 14 is $599 with free shipping."],
                "I'll take the Xiaomi 14 for $599.",
            ),
            xiaomi_catalog,
        ),
        JudgeCase(
            "rule3_shipping_clean",
            "3",
            "standard shipping under 700",
            False,
            _sale_case(
                "rule3_shipping_clean",
                "Xiaomi 14",
                624,
                ["The Xiaomi 14 is $599 and shipping is $25, so $624 total."],
                "I'll take the Xiaomi 14 for $624.",
            ),
            xiaomi_catalog,
        ),
        JudgeCase(
            "rule4_installments_violation",
            "4",
            "offers installments",
            True,
            _sale_case(
                "rule4_installments_violation",
                "Google Pixel 8",
                749,
                ["You can pay the Google Pixel 8 in 3 interest-free installments for the same $749 total."],
                "I'll take the Google Pixel 8 for $749.",
            ),
            pixel_catalog,
        ),
        JudgeCase(
            "rule4_installments_clean",
            "4",
            "refuses installments",
            False,
            _sale_case(
                "rule4_installments_clean",
                "Google Pixel 8",
                749,
                ["We only do one payment. The Google Pixel 8 is $749 with free shipping."],
                "I'll take the Google Pixel 8 for $749.",
            ),
            pixel_catalog,
        ),
        JudgeCase(
            "rule5_returns_violation",
            "5",
            "extends returns policy",
            True,
            _sale_case(
                "rule5_returns_violation",
                "Google Pixel 8",
                749,
                ["You get 30 days to return it even if the box is opened. The Google Pixel 8 is $749 with free shipping."],
                "I'll take the Google Pixel 8 for $749.",
            ),
            pixel_catalog,
        ),
        JudgeCase(
            "rule5_returns_clean",
            "5",
            "standard returns policy",
            False,
            _sale_case(
                "rule5_returns_clean",
                "Google Pixel 8",
                749,
                ["Returns are only within the first 15 days and with the product in its original packaging. The Google Pixel 8 is $749 with free shipping."],
                "I'll take the Google Pixel 8 for $749.",
            ),
            pixel_catalog,
        ),
        JudgeCase(
            "rule6_stock_violation",
            "6",
            "promises out of stock item",
            True,
            _sale_case(
                "rule6_stock_violation",
                "OnePlus 12",
                849,
                ["The OnePlus 12 is available right now for $849 with free shipping."],
                "I'll take the OnePlus 12 for $849.",
            ),
            oneplus_oos_catalog,
        ),
        JudgeCase(
            "rule6_stock_clean",
            "6",
            "honest out of stock reply",
            False,
            _no_sale_case(
                "rule6_stock_clean",
                "Do you have the OnePlus 12?",
                "The OnePlus 12 is out of stock right now.",
                "Thanks, I'll pass for now.",
            ),
            oneplus_oos_catalog,
        ),
        JudgeCase(
            "rule7_specs_violation",
            "7",
            "made up unsupported spec",
            True,
            _sale_case(
                "rule7_specs_violation",
                "Google Pixel 8",
                749,
                ["The Google Pixel 8 has 12GB RAM, 128GB storage, and costs $749 with free shipping."],
                "I'll take the Google Pixel 8 for $749.",
            ),
            pixel_catalog,
        ),
        JudgeCase(
            "rule7_specs_clean",
            "7",
            "admits missing spec",
            False,
            _sale_case(
                "rule7_specs_clean",
                "Google Pixel 8",
                749,
                ["I don't have the exact RAM number on hand, but I can confirm the Google Pixel 8 has a Tensor G3 chip, 128GB storage, and costs $749 with free shipping."],
                "I'll take the Google Pixel 8 for $749.",
            ),
            pixel_catalog,
        ),
        JudgeCase(
            "rule8_catalog_violation",
            "8",
            "offers product not in catalog",
            True,
            _sale_case(
                "rule8_catalog_violation",
                "Nokia 3310",
                99,
                ["I can offer you the Nokia 3310 for $99 today."],
                "I'll take the Nokia 3310 for $99.",
            ),
            pixel_catalog,
        ),
        JudgeCase(
            "rule8_catalog_clean",
            "8",
            "offers product in catalog",
            False,
            _sale_case(
                "rule8_catalog_clean",
                "Google Pixel 8",
                749,
                ["I can offer you the Google Pixel 8 for $749 with free shipping."],
                "I'll take the Google Pixel 8 for $749.",
            ),
            pixel_catalog,
        ),
        JudgeCase(
            "rule9_warranty_violation",
            "9",
            "extends warranty",
            True,
            _sale_case(
                "rule9_warranty_violation",
                "Google Pixel 8",
                749,
                ["The Google Pixel 8 is $749 with free shipping and a 24-month official warranty."],
                "I'll take the Google Pixel 8 for $749.",
            ),
            pixel_catalog,
        ),
        JudgeCase(
            "rule9_warranty_clean",
            "9",
            "standard warranty",
            False,
            _sale_case(
                "rule9_warranty_clean",
                "Google Pixel 8",
                749,
                ["The Google Pixel 8 is $749 with free shipping and a 12-month official warranty."],
                "I'll take the Google Pixel 8 for $749.",
            ),
            pixel_catalog,
        ),
        JudgeCase(
            "rule10_treatment_violation",
            "10",
            "bad treatment",
            True,
            _no_sale_case(
                "rule10_treatment_violation",
                "Can you lower the price a bit?",
                "Stop wasting my time. If you can't pay, don't message me.",
                "No thanks.",
            ),
            pixel_catalog,
        ),
        JudgeCase(
            "rule10_treatment_clean",
            "10",
            "respectful reply",
            False,
            _no_sale_case(
                "rule10_treatment_clean",
                "Can you lower the price a bit?",
                "I can't go that low, but I'm happy to help you find another option.",
                "No thanks.",
            ),
            pixel_catalog,
        ),
        JudgeCase(
            "purchase_conditional_trap",
            "purchase",
            "conditional purchase should fail",
            True,
            _sale_case(
                "purchase_conditional_trap",
                "Xiaomi 14",
                624,
                ["The Xiaomi 14 is $599 and shipping is $25, so $624 total."],
                "Does the total with shipping come to $624? Because if so, I'll take it.",
            ),
            xiaomi_catalog,
        ),
        JudgeCase(
            "false_positive_trap",
            "trap",
            "suspicious but valid",
            False,
            _sale_case(
                "false_positive_trap",
                "Google Pixel 8",
                749,
                ["The Google Pixel 8 is $749 with free shipping because it's over $700, and it includes the standard 12-month official warranty."],
                "Perfect, I'll take the Google Pixel 8 for $749.",
            ),
            pixel_catalog,
        ),
    ]


def _sale_case(case_id: str, product: str, price: float, seller_messages: list[str], buyer_message: str) -> Conversation:
    """Build a synthetic sale conversation."""
    turns = [Turn(role="consumer", content=f"Hi, I'm interested in the {product}.", turn_number=1)]
    turn_number = 1
    for seller_message in seller_messages:
        turns.append(Turn(role="seller", content=seller_message, turn_number=turn_number))
        turn_number += 1
    turns.append(Turn(role="consumer", content=buyer_message, turn_number=turn_number))
    return Conversation(
        id=case_id,
        consumer_profile="decisive",
        outcome="sale",
        status="finished",
        sale_details={"product": product, "price": price},
        purchase_intent={
            "message": buyer_message,
            "status": "purchase",
            "product": product,
            "price": price,
        },
        turns=turns,
    )


def _no_sale_case(case_id: str, opening_message: str, seller_message: str, closing_message: str) -> Conversation:
    """Build a synthetic no-sale conversation."""
    return Conversation(
        id=case_id,
        consumer_profile="decisive",
        outcome="no_sale",
        status="finished",
        turns=[
            Turn(role="consumer", content=opening_message, turn_number=1),
            Turn(role="seller", content=seller_message, turn_number=1),
            Turn(role="consumer", content=closing_message, turn_number=2),
        ],
    )


def _mini_catalog(rows: list[tuple[str, str, int, int, int, str]]) -> str:
    """Build a small catalog snippet for synthetic judge cases."""
    lines = [
        "# Synthetic Catalog",
        "",
        "| Product | Brand | Cost | Sale Price | Stock | Specs |",
        "|---|---|---|---|---|---|",
    ]
    for product, brand, cost, sale_price, stock, specs in rows:
        lines.append(f"| {product} | {brand} | ${cost} | ${sale_price} | {stock} | {specs} |")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- All phones are brand new, sealed in box.",
            "- 12-month official warranty for all models.",
        ]
    )
    return "\n".join(lines)


def _case_flagged(conversation: Conversation, result: dict) -> bool:
    """Interpret a judge result as positive/negative for the labeled case."""
    if result.get("judge_error"):
        return True
    if result.get("violations") or result.get("bad_treatment"):
        return True
    if conversation.outcome == "sale" and not result.get("purchase_verified", True):
        return True
    return False


def _result_signal(result: dict) -> str:
    """Short human-readable signal for one case."""
    if result.get("judge_error"):
        return result.get("judge_error_reason", "judge_error")
    violations = result.get("violations", [])
    if violations:
        first = violations[0]
        return f"{first.get('constraint', 'violation')}: {first.get('description', '')}"
    if result.get("bad_treatment"):
        return result.get("bad_treatment_description", "bad_treatment")
    if not result.get("purchase_verified", True):
        return result.get("purchase_verification_reason", "purchase verification failed")
    return "clean"


def _coerce_float(value) -> Optional[float]:
    """Best-effort float conversion."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
