# Sales Arena

**Train your sales agent before sending it to production.**

Sales Arena is a sales-agent trainer. You bring your seller prompt, your catalog, and your business rules, and Sales Arena puts it to sell against simulated consumers with different profiles. Then it evaluates performance and iterates automatically to optimize profit.

## Quick Start

### 1. Install

Requires [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`).

```bash
uv sync
```

### 2. Setup with your agent

Hand `program.md` to your agent (Claude Code, Codex, etc.) and let it walk you through setup. The agent will ask for your catalog, business rules, and an initial prompt.

### 3. Run

```bash
uv run python run.py simulate
```

### 4. Iterate

The agent reads the results, tweaks the prompt, and runs again. You can leave it iterating overnight.

## How it works

1. **Simulation**: 20 consumers with different profiles (decisive, bargain hunter, indecisive, demanding, rushed, browser) chat with your seller in parallel.
2. **Evaluation**: An LLM judge checks that no business rules were broken. An analyst LLM gives qualitative feedback.
3. **Metric**: Total profit from valid sales (those that didn't violate constraints).
4. **Iteration**: The agent adjusts the prompt based on results and repeats.

See [`docs/loop-macro.md`](docs/loop-macro.md) for a diagram of the optimization loop.

## Layout

```
sales-arena/
  run.py              # CLI
  program.md          # Instructions for the orchestrator agent
  arena/              # Core code
  workspace/          # Your catalog, constraints, prompt (created by the agent)
  experiments/        # Results from each experiment
  docs/               # Diagrams and notes
```

## Requirements

- Python 3.11+
- An LLM compatible with the OpenAI API (LM Studio, Ollama, OpenAI, Anthropic via proxy, etc.)

## Inspiration

- [autoresearch](https://github.com/karpathy/autoresearch) by Karpathy — the automatic iteration loop
- [The Loop Is Only as Good as the Metric](https://www.distributedthoughts.org/2026-03-16-the-loop-is-only-as-good-as-the-metric/) — the importance of clear metrics
