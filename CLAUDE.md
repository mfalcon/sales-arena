# CLAUDE.md

## Project Overview

Sales Arena is a sales agent trainer. It runs simulated sales conversations between a user's seller agent (defined by a prompt) and simulated consumers with various profiles, then evaluates performance and iterates to optimize profit.

## Commands

### Run a simulation
```bash
python run.py simulate                    # full experiment (20 consumers, 10 turns)
python run.py simulate --consumers 5      # quick test
python run.py simulate --turns 3          # fewer turns
```

### Re-evaluate a past experiment
```bash
python run.py evaluate experiments/<timestamp>
```

## Architecture

### Entry point
- **`run.py`** — CLI with `simulate` and `evaluate` commands.

### Core (`arena/`)
- **`types.py`** — Dataclasses: Turn, Conversation, Violation, ExperimentResult.
- **`llm.py`** — OpenAI-compatible LLM client + JSON parser. `LLMClient.send(messages)` takes a pre-built messages list.
- **`stock.py`** — StockTracker. Tracks product quantities, decrements on sale.
- **`prompts.py`** — All prompt templates. 6 consumer profiles, seller context builder, judge prompt, analyst prompt.
- **`simulation.py`** — Round-robin engine. Pseudo-parallel conversations (interleaved turns). Seller sees all conversation statuses + live stock.
- **`evaluation.py`** — Post-simulation evaluation. LLM judge checks constraint violations + bad treatment. LLM analyst gives qualitative feedback.

### User workspace (`workspace/`)
- **`catalog.md`** — Product catalog (free format).
- **`constraints.md`** — Business rules (free format).
- **`seller_prompt.md`** — The seller prompt being optimized.
- **`config.yaml`** — Model config, stock, cost_map, price_map, simulation params.

### Output (`experiments/`)
Each experiment generates a timestamped directory with: `result.json`, `summary.md`, `seller_prompt.md`, and `conversations/*.md`.

## Key Patterns

- The simulation is pseudo-parallel: consumer turns are interleaved (round-robin), not sequential.
- The seller gets context of all other conversations (status summaries) + live stock on every response.
- Purchase detection uses a JSON marker (COMPRA: {...}) + keyword fallback.
- The judge uses low temperature (0.1) for consistency.
- Profit = sum(price - cost) for valid sales only (no constraint violations).

## Orchestration

This project is designed to be run by an orchestrator agent (Codex, Claude Code). See `program.md` for orchestrator instructions. The orchestrator handles:
- Setup (onboarding user, creating workspace files)
- Running the optimization loop (modify prompt → simulate → evaluate → git commit/rollback)
- Model comparison
