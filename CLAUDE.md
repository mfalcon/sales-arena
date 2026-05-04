# CLAUDE.md

## Project Overview

Sales Arena is a sales agent trainer. It runs simulated sales conversations between a user's seller agent (defined by a prompt) and simulated consumers with various profiles, then evaluates performance and iterates to optimize profit.

## Commands

### Run a simulation
```bash
uv run python run.py simulate                    # full experiment (20 consumers, 10 turns)
uv run python run.py simulate --consumers 5      # quick test
uv run python run.py simulate --turns 3          # fewer turns
```

### Re-evaluate a past experiment
```bash
uv run python run.py evaluate experiments/<timestamp>
```

Dependencies are managed by `uv` (see `pyproject.toml`). `uv sync` installs them into `.venv`.

## Architecture

### Entry point
- **`run.py`** — CLI with `simulate` and `evaluate` commands.

### Core (`arena/`)
- **`types.py`** — Dataclasses: Turn, Conversation, Violation, ExperimentResult.
- **`llm.py`** — OpenAI-compatible LLM client + JSON parser. `LLMClient.send(messages)` takes a pre-built messages list.
- **`stock.py`** — StockTracker. Tracks product quantities, decrements on sale.
- **`prompts.py`** — All prompt templates. 6 consumer profiles, seller context builder, judge prompt, analyst prompt.
- **`simulation.py`** — Round-robin engine. Pseudo-parallel conversations (interleaved turns). Seller sees all conversation statuses + live stock.
- **`evaluation.py`** — Post-simulation evaluation. LLM judge checks constraint violations + bad treatment. Analysis is done by the orchestrator agent (not an LLM call).

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

## Autonomous Optimization Mode

When the user requests "N iteraciones" or "corré el loop", you are authorized to follow `program.md` Phase 3 + Phase 5 without per-step confirmation, including:

- Edit `workspace/seller_prompt.md`
- Run `uv run python run.py simulate` (and `--consumers`/`--turns` overrides)
- `git add` + `git commit` if profit improves over the current best
- `git reset --hard <last-winning-commit>` if profit regresses (rollback to best)
- Read `experiments/<latest>/` files

You remain forbidden from: editing files outside `workspace/`, modifying `config.yaml` parameters other than what `program.md` allows, force-pushing, deleting branches, or touching `.env`/`.gitignore`.

### Guardrails (stop and report instead of continuing)

Stop the loop and summarize for the user when ANY of these triggers fires:
1. The user-specified iteration count is reached (e.g. "10 iteraciones" → stop after 10).
2. **3 consecutive iterations** with no improvement over the current best.
3. A single iteration drops profit **>90% below the current best** (likely a broken prompt change).
4. The same error occurs in **2 consecutive runs** (per `program.md` retry rule).
5. The judge's reliability looks off (violations on ALL sales, or 0 violations across 5+ runs) — run "Judge the Judge" eval before continuing.

When stopping for guardrail 2-5, do NOT rollback the in-progress change — leave the workspace dirty so the user can inspect.

### Reporting cadence

After each iteration, output a one-line status: `iter N/M | profit: $X (best: $Y, baseline: $Z) | action: committed|rolled-back | next: <one-line plan>`. Don't print full summaries until the loop finishes or hits a guardrail.
