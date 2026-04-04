"""Sales Arena CLI entry point."""

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path

import yaml

from arena.evaluation import evaluate_experiment
from arena.llm import LLMClient
from arena.simulation import run_simulation
from arena.stock import StockTracker
from arena.types import Conversation


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
    api_key = model_config.get("api_key", "not-needed")

    num_consumers = args.consumers or config.get("num_consumers", 20)
    max_turns = args.turns or config.get("max_turns", 10)

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
            api_key=consumer_model_config.get("api_key", api_key),
        )
    else:
        consumer_llm = None  # simulation will use the same llm

    # Create stock tracker
    stock = StockTracker(stock_config)

    print(f"=== Sales Arena ===")
    print(f"Modelo vendedor: {model}")
    if consumer_model_config:
        print(f"Modelo consumidores: {consumer_model_config.get('name', model)}")
    print(f"Consumidores: {num_consumers}")
    print(f"Turnos máx: {max_turns}")
    print(f"Productos en stock: {len(stock_config)}")
    print()

    # Progress callback
    def on_turn(turn_round, conv_id, role, content):
        label = "🛒" if role == "consumer" else "🏪"
        preview = content[:80].replace("\n", " ")
        print(f"  [{conv_id}] T{turn_round} {label} {preview}...")

    # Run simulation
    print("--- Simulación ---")
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
        consumer_llm=consumer_llm,
    )

    sim_tokens = llm.usage.total
    print(f"\nSimulación completa. Tokens usados: {sim_tokens}")

    # Run evaluation
    print("\n--- Evaluación ---")
    result = evaluate_experiment(
        llm=llm,
        conversations=conversations,
        catalog_text=catalog_text,
        constraints_text=constraints_text,
        cost_map=cost_map,
        seller_prompt=seller_prompt,
        model_name=model,
        model_params={"temperature": temperature, "max_tokens": max_tokens},
    )

    eval_tokens = llm.usage.total - sim_tokens
    print(f"Evaluación completa. Tokens usados: {eval_tokens}")

    # Write results
    exp_dir = EXPERIMENTS / result.experiment_id
    _write_results(exp_dir, result, seller_prompt)

    # Print summary
    print(f"\n{'='*50}")
    print(f"RESULTADOS")
    print(f"{'='*50}")
    print(f"Profit: ${result.total_profit:,.2f}")
    print(f"Revenue: ${result.total_revenue:,.2f}")
    print(f"Ventas válidas: {result.valid_sales}/{result.total_conversations}")
    print(f"Ventas inválidas: {result.invalid_sales}")
    print(f"No-ventas: {result.no_sales}")
    print(f"Violaciones: {len(result.violations)}")
    print(f"Tokens totales: {result.total_tokens}")

    if result.violations:
        print(f"\nViolaciones detectadas:")
        for v in result.violations:
            print(f"  - {v.conversation_id}: {v.constraint} — {v.description}")

    print(f"\nResultados guardados en: {exp_dir}")
    print(f"profit:{result.total_profit:.2f}")


def cmd_evaluate(args):
    """Re-evaluate a past experiment."""
    exp_dir = Path(args.experiment_dir)
    result_file = exp_dir / "result.json"

    if not result_file.exists():
        print(f"Error: {result_file} no existe.")
        sys.exit(1)

    with open(result_file) as f:
        data = json.load(f)

    print(f"Re-evaluación de {exp_dir.name}")
    print(f"Profit original: ${data.get('total_profit', 0):,.2f}")
    print(f"Ventas válidas: {data.get('valid_sales', 0)}/{data.get('total_conversations', 0)}")


def _read_file(path: Path) -> str:
    """Read a text file or exit with error."""
    if not path.exists():
        print(f"Error: {path} no existe. Ejecutá el setup primero.")
        sys.exit(1)
    return path.read_text(encoding="utf-8")


def _read_config(path: Path) -> dict:
    """Read YAML config file."""
    if not path.exists():
        print(f"Error: {path} no existe. Ejecutá el setup primero.")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


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
        f"# Conversación {conv.id}",
        f"",
        f"- **Perfil**: {conv.consumer_profile}",
        f"- **Resultado**: {conv.outcome}",
    ]
    if conv.sale_details:
        lines.append(f"- **Producto**: {conv.sale_details.get('product', '?')}")
        lines.append(f"- **Precio**: ${conv.sale_details.get('price', '?')}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for turn in conv.turns:
        role_label = "**CLIENTE**" if turn.role == "consumer" else "**VENDEDOR**"
        lines.append(f"### Turno {turn.turn_number} — {role_label}")
        lines.append("")
        lines.append(turn.content)
        lines.append("")

    return "\n".join(lines)


def _build_summary(result) -> str:
    """Build the summary.md content."""
    lines = [
        f"# Experimento {result.experiment_id}",
        "",
        "## Resultado",
        f"- **Modelo**: {result.model}",
        f"- **Profit**: ${result.total_profit:,.2f} (solo ventas válidas)",
        f"- **Revenue**: ${result.total_revenue:,.2f}",
        f"- **Ventas válidas**: {result.valid_sales}/{result.total_conversations}",
        f"- **Ventas inválidas** (violaciones): {result.invalid_sales}",
        f"- **No-ventas**: {result.no_sales}",
        f"- **Tokens totales**: {result.total_tokens}",
        "",
    ]

    if result.violations:
        lines.append("## Violaciones")
        for v in result.violations:
            lines.append(f"- **{v.conversation_id}**: {v.constraint} — {v.description}")
        lines.append("")

    lines.append("## Análisis")
    lines.append(result.analysis)
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Sales Arena — Sales agent trainer")
    subparsers = parser.add_subparsers(dest="command", help="Comando")

    # simulate
    sim_parser = subparsers.add_parser("simulate", help="Correr un experimento")
    sim_parser.add_argument(
        "--consumers", type=int, default=None, help="Cantidad de consumidores"
    )
    sim_parser.add_argument(
        "--turns", type=int, default=None, help="Turnos máximos por conversación"
    )

    # evaluate
    eval_parser = subparsers.add_parser(
        "evaluate", help="Re-evaluar un experimento pasado"
    )
    eval_parser.add_argument("experiment_dir", help="Directorio del experimento")

    args = parser.parse_args()

    if args.command == "simulate":
        cmd_simulate(args)
    elif args.command == "evaluate":
        cmd_evaluate(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
