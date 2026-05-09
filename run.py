"""Sales Arena CLI entry point."""

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv(override=True)

from arena.evaluation import evaluate_experiment
from arena.llm import LLMClient
from arena.simulation import run_simulation
from arena.stock import StockTracker
from arena.types import Conversation, Turn


WORKSPACE = Path("workspace")
EXPERIMENTS = Path("experiments")


def cmd_simulate(args):
    """Run a simulation experiment."""
    # Read workspace files
    catalog_text = _read_file(WORKSPACE / "catalog.md")
    constraints_text = _read_file(WORKSPACE / "constraints.md")
    seller_prompt = _read_file(WORKSPACE / "seller_prompt.md")
    config = _read_config(WORKSPACE / "config.yaml")

    # Extract config
    model_config = config.get("model", {})
    base_url = model_config.get("base_url", "http://localhost:1234/v1")
    model = model_config.get("name", "local-model")
    temperature = model_config.get("temperature", 0.7)
    max_tokens = model_config.get("max_tokens", 1500)
    api_key = os.path.expandvars(model_config.get("api_key", "not-needed"))

    num_consumers = args.consumers or config.get("num_consumers", 20)
    max_turns = args.turns or config.get("max_turns", 10)
    seed = args.seed if args.seed is not None else config.get("seed")

    stock_config = config.get("stock", {})
    cost_map = config.get("cost_map", {})
    product_list = list(stock_config.keys()) if stock_config else None
    price_map = config.get("price_map", {})

    # Create LLM client for seller
    llm = LLMClient(
        base_url=base_url,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        api_key=api_key,
    )

    # Create LLM client for consumers (same model by default, configurable)
    consumer_model_config = config.get("consumer_model")
    if consumer_model_config:
        consumer_llm = LLMClient(
            base_url=consumer_model_config.get("base_url", base_url),
            model=consumer_model_config.get("name", model),
            temperature=consumer_model_config.get("temperature", temperature),
            max_tokens=consumer_model_config.get("max_tokens", max_tokens),
            api_key=os.path.expandvars(consumer_model_config.get("api_key", api_key)),
        )
    else:
        consumer_llm = None  # simulation will use the same llm

    # Create LLM client for judge (same as seller by default, configurable)
    judge_model_config = config.get("judge_model")
    if judge_model_config:
        judge_llm = LLMClient(
            base_url=judge_model_config.get("base_url", base_url),
            model=judge_model_config.get("name", model),
            temperature=judge_model_config.get("temperature", 0.1),
            max_tokens=judge_model_config.get("max_tokens", max_tokens),
            api_key=os.path.expandvars(judge_model_config.get("api_key", api_key)),
        )
    else:
        judge_llm = None  # evaluation will use the same llm

    # Create stock tracker
    stock = StockTracker(stock_config)

    print(f"=== Sales Arena ===")
    print(f"Seller model: {model}")
    if consumer_model_config:
        print(f"Consumer model: {consumer_model_config.get('name', model)}")
    if judge_model_config:
        print(f"Judge model: {judge_model_config.get('name', model)}")
    print(f"Consumers: {num_consumers}")
    print(f"Max turns: {max_turns}")
    print(f"Products in stock: {len(stock_config)}")
    if seed is not None:
        print(f"Seed: {seed}")
    print()

    # Progress callback
    def on_turn(turn_round, conv_id, role, content):
        label = "🛒" if role == "consumer" else "🏪"
        preview = content[:80].replace("\n", " ")
        print(f"  [{conv_id}] T{turn_round} {label} {preview}...")

    # Event log for detailed tracing
    event_log = []

    def on_event(event):
        event_log.append(event)
        t = event["type"]
        seq = event["seq"]
        cid = event.get("conv_id", "")
        if t == "stock_update":
            print(f"    📦 #{seq} [{cid}] STOCK: {event['product']} {event['before']} → {event['after']}")
        elif t == "status_change":
            print(f"    🏁 #{seq} [{cid}] {event['outcome'].upper()}"
                  + (f" — {event['details'].get('product', '')} @ ${event['details'].get('price', '')}" if event.get('details', {}).get('product') else ""))
        elif t == "consumer_intent" and event["status"] != "browsing":
            print(f"    🎯 #{seq} [{cid}] intent={event['status']}")

    # Run simulation
    print("--- Simulation ---")
    conversations = run_simulation(
        llm=llm,
        seller_prompt=seller_prompt,
        catalog_text=catalog_text,
        constraints_text=constraints_text,
        stock=stock,
        num_consumers=num_consumers,
        max_turns=max_turns,
        product_list=product_list,
        price_map=price_map,
        on_turn=on_turn,
        on_event=on_event,
        consumer_llm=consumer_llm,
        seed=seed,
    )

    seller_tokens = llm.usage.total
    consumer_tokens = consumer_llm.usage.total if consumer_llm else 0
    sim_tokens = seller_tokens + consumer_tokens
    print(f"\nSimulation complete. Tokens used: {sim_tokens} (seller {seller_tokens}, consumer {consumer_tokens})")

    # Run evaluation
    print("\n--- Evaluation ---")
    judge_client = judge_llm or llm
    judge_tokens_before = judge_client.usage.total
    result = evaluate_experiment(
        llm=judge_client,
        conversations=conversations,
        catalog_text=catalog_text,
        constraints_text=constraints_text,
        cost_map=cost_map,
        seller_prompt=seller_prompt,
        model_name=model,
        model_params={"temperature": temperature, "max_tokens": max_tokens},
        on_event=lambda e: event_log.append(e),
        initial_stock=stock_config,
        usage={
            "seller": seller_tokens,
            "consumer": consumer_tokens,
            "judge": judge_client.usage.total - judge_tokens_before,
        },
        models={
            "seller": model,
            "consumer": (consumer_model_config or {}).get("name", model) if consumer_llm else model,
            "judge": (judge_model_config or {}).get("name", model) if judge_llm else model,
        },
        seed=seed,
        business_rules=config.get("business_rules"),
    )

    eval_tokens = result.usage.get("judge", 0)
    print(f"Evaluation complete. Tokens used: {eval_tokens}")

    # Write results
    exp_dir = EXPERIMENTS / result.experiment_id
    _write_results(exp_dir, result, seller_prompt)

    # Write event log
    if event_log:
        with open(exp_dir / "events.json", "w", encoding="utf-8") as f:
            json.dump(event_log, f, ensure_ascii=False, indent=2, default=str)

    # Append to results.tsv
    _append_to_tsv(result)

    # Print summary
    print(f"\n{'='*50}")
    print(f"RESULTS")
    print(f"{'='*50}")
    print(f"Profit: ${result.total_profit:,.2f}")
    print(f"Revenue: ${result.total_revenue:,.2f}")
    print(f"Valid sales: {result.valid_sales}/{result.total_conversations}")
    print(f"Invalid sales: {result.invalid_sales}")
    print(f"No-sales: {result.no_sales}")
    print(f"Violations: {len(result.violations)}")
    if result.usage:
        u = result.usage
        print(f"Total tokens: {u.get('total', 0)} (seller {u.get('seller', 0)}, consumer {u.get('consumer', 0)}, judge {u.get('judge', 0)})")
    else:
        print(f"Total tokens: {result.total_tokens}")

    llm_errors = [e for e in event_log if e.get("type") == "llm_error"]
    if llm_errors:
        print(f"\n⚠ LLM errors during simulation: {len(llm_errors)}")
        for e in llm_errors[:5]:
            print(f"  - [{e['conv_id']}] {e['role']} round {e.get('round')}: {e['error'][:120]}")

    if result.violations:
        print(f"\nViolations detected:")
        for v in result.violations:
            print(f"  - {v.conversation_id}: {v.constraint} — {v.description}")

    print(f"\nResults saved to: {exp_dir}")
    print(f"profit:{result.total_profit:.2f}")


def cmd_evaluate(args):
    """Re-evaluate a past experiment by replaying its conversations through the judge."""
    exp_dir = Path(args.experiment_dir)
    result_file = exp_dir / "result.json"
    events_file = exp_dir / "events.json"

    if not result_file.exists():
        print(f"Error: {result_file} does not exist.")
        sys.exit(1)

    with open(result_file) as f:
        data = json.load(f)

    catalog_text = _read_file(WORKSPACE / "catalog.md")
    constraints_text = _read_file(WORKSPACE / "constraints.md")
    config = _read_config(WORKSPACE / "config.yaml")

    model_config = config.get("model", {})
    judge_config = config.get("judge_model") or model_config
    judge_llm = LLMClient(
        base_url=judge_config.get("base_url", "http://localhost:1234/v1"),
        model=judge_config.get("name", "local-model"),
        temperature=judge_config.get("temperature", 0.1),
        max_tokens=judge_config.get("max_tokens", 800),
        api_key=os.path.expandvars(judge_config.get("api_key", "not-needed")),
    )

    purchase_intents = {}
    if events_file.exists():
        with open(events_file) as f:
            events = json.load(f)
        for event in events:
            if event.get("type") == "consumer_intent" and event.get("status") == "purchase":
                purchase_intents[event.get("conv_id")] = event.get("raw_json", {})

    conversations = []
    for raw in data.get("conversations", []):
        conv = Conversation(
            id=raw.get("id", "?"),
            consumer_profile=raw.get("consumer_profile", "?"),
            outcome=raw.get("outcome", "pending"),
            sale_details=raw.get("sale_details"),
            purchase_intent=purchase_intents.get(raw.get("id"))
                or raw.get("purchase_intent"),
            status=raw.get("status", "finished"),
            turns=[
                Turn(
                    role=t.get("role", "?"),
                    content=t.get("content", ""),
                    turn_number=int(t.get("turn_number", 0) or 0),
                )
                for t in raw.get("turns", [])
            ],
        )
        conversations.append(conv)

    print(f"Re-evaluating {exp_dir.name} with judge={judge_config.get('name')}")
    print(f"Original profit: ${data.get('total_profit', 0):,.2f}")
    print(f"Original valid sales: {data.get('valid_sales', 0)}/{data.get('total_conversations', 0)}")
    print()

    result = evaluate_experiment(
        llm=judge_llm,
        conversations=conversations,
        catalog_text=catalog_text,
        constraints_text=constraints_text,
        cost_map=config.get("cost_map", {}),
        seller_prompt=data.get("seller_prompt", ""),
        model_name=data.get("model", ""),
        model_params=data.get("model_params", {}),
        initial_stock=config.get("stock", {}),
        models={"judge": judge_config.get("name", "")},
        business_rules=config.get("business_rules"),
    )

    out_path = exp_dir / "reeval.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(dataclasses.asdict(result), f, ensure_ascii=False, indent=2, default=str)

    print(f"\n{'='*50}")
    print(f"RE-EVALUATION RESULTS")
    print(f"{'='*50}")
    print(f"Profit: ${result.total_profit:,.2f} (original: ${data.get('total_profit', 0):,.2f})")
    print(f"Valid sales: {result.valid_sales}/{result.total_conversations} (original: {data.get('valid_sales', 0)}/{data.get('total_conversations', 0)})")
    print(f"Violations: {len(result.violations)}")
    print(f"\nSaved to: {out_path}")


def _read_file(path: Path) -> str:
    """Read a text file or exit with error."""
    if not path.exists():
        print(f"Error: {path} does not exist. Run setup first.")
        sys.exit(1)
    return path.read_text(encoding="utf-8")


def _read_config(path: Path) -> dict:
    """Read YAML config file."""
    if not path.exists():
        print(f"Error: {path} does not exist. Run setup first.")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _append_to_tsv(result):
    """Append experiment result as a row to results.tsv."""
    tsv_path = EXPERIMENTS / "results.tsv"
    header = "timestamp\tmodel\tprofit\trevenue\tvalid_sales\tinvalid_sales\tno_sales\ttotal\tviolations\ttokens\tprompt\n"

    if not tsv_path.exists():
        tsv_path.write_text(header, encoding="utf-8")

    # Escape prompt: replace newlines and tabs
    prompt_oneline = result.seller_prompt.replace("\n", "\\n").replace("\t", " ")

    row = (
        f"{result.timestamp}\t"
        f"{result.model}\t"
        f"{result.total_profit:.2f}\t"
        f"{result.total_revenue:.2f}\t"
        f"{result.valid_sales}\t"
        f"{result.invalid_sales}\t"
        f"{result.no_sales}\t"
        f"{result.total_conversations}\t"
        f"{len(result.violations)}\t"
        f"{result.total_tokens}\t"
        f"{prompt_oneline}\n"
    )

    with open(tsv_path, "a", encoding="utf-8") as f:
        f.write(row)


def _write_results(exp_dir: Path, result, seller_prompt: str):
    """Write experiment results to disk."""
    exp_dir.mkdir(parents=True, exist_ok=True)
    conv_dir = exp_dir / "conversations"
    conv_dir.mkdir(exist_ok=True)

    # Write result.json
    result_data = dataclasses.asdict(result)
    with open(exp_dir / "result.json", "w", encoding="utf-8") as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2, default=str)

    # Write seller_prompt.md
    (exp_dir / "seller_prompt.md").write_text(seller_prompt, encoding="utf-8")

    # Write conversation logs
    for conv in result.conversations:
        filename = f"{conv.id}_{conv.consumer_profile}.md"
        conv_text = _format_conversation_md(conv)
        (conv_dir / filename).write_text(conv_text, encoding="utf-8")

    # Write summary.md
    summary = _build_summary(result)
    (exp_dir / "summary.md").write_text(summary, encoding="utf-8")


def _format_conversation_md(conv: Conversation) -> str:
    """Format a conversation as readable markdown."""
    lines = [
        f"# Conversation {conv.id}",
        f"",
        f"- **Profile**: {conv.consumer_profile}",
        f"- **Outcome**: {conv.outcome}",
    ]
    if conv.sale_details:
        lines.append(f"- **Product**: {conv.sale_details.get('product', '?')}")
        lines.append(f"- **Price**: ${conv.sale_details.get('price', '?')}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for turn in conv.turns:
        role_label = "**CUSTOMER**" if turn.role == "consumer" else "**SELLER**"
        lines.append(f"### Turn {turn.turn_number} — {role_label}")
        lines.append("")
        lines.append(turn.content)
        lines.append("")

    return "\n".join(lines)


def _build_summary(result) -> str:
    """Build the summary.md content."""
    lines = [
        f"# Experiment {result.experiment_id}",
        "",
        "## Results",
        f"- **Model**: {result.model}",
        f"- **Profit**: ${result.total_profit:,.2f} (valid sales only)",
        f"- **Revenue**: ${result.total_revenue:,.2f}",
        f"- **Valid sales**: {result.valid_sales}/{result.total_conversations}",
        f"- **Invalid sales** (violations): {result.invalid_sales}",
        f"- **No-sales**: {result.no_sales}",
        f"- **Total tokens**: {result.total_tokens}",
        "",
    ]

    if result.violations:
        lines.append("## Violations")
        for v in result.violations:
            lines.append(f"- **{v.conversation_id}**: {v.constraint} — {v.description}")
        lines.append("")

    lines.append("## Analysis")
    lines.append(result.analysis)
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Sales Arena — Sales agent trainer")
    subparsers = parser.add_subparsers(dest="command", help="Command")

    # simulate
    sim_parser = subparsers.add_parser("simulate", help="Run an experiment")
    sim_parser.add_argument(
        "--consumers", type=int, default=None, help="Number of consumers"
    )
    sim_parser.add_argument(
        "--turns", type=int, default=None, help="Max turns per conversation"
    )
    sim_parser.add_argument(
        "--seed", type=int, default=None, help="Random seed for reproducibility"
    )

    # evaluate
    eval_parser = subparsers.add_parser(
        "evaluate", help="Re-evaluate a past experiment"
    )
    eval_parser.add_argument("experiment_dir", help="Experiment directory")

    args = parser.parse_args()

    if args.command == "simulate":
        cmd_simulate(args)
    elif args.command == "evaluate":
        cmd_evaluate(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
