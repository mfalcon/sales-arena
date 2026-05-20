#!/usr/bin/env python3
"""Re-run the LLM judge on cases, updating their judge output + verdicts dict.

Reads a JSONL of cases (candidates format: case_id, conversation_id,
transcript, sale_details, ...), runs the current judge on each conversation,
and writes a new JSONL with the refreshed `judge` field (legacy schema) plus
the new `verdicts` dict (atomic per-item binary labels).

Domain-agnostic: works on whatever rules are in workspace/constraints.md
and whatever items the judge emits in `verdicts`.

Usage:
    uv run python evals/refresh_judge.py <input.jsonl> --out <output.jsonl>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from arena.evaluation import _run_judge
from arena.llm import LLMClient
from arena.types import Conversation, Turn


WORKSPACE = ROOT / "workspace"


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh judge output on cases.")
    parser.add_argument("input", help="Input cases JSONL.")
    parser.add_argument("--out", required=True, help="Output JSONL with refreshed judge.")
    args = parser.parse_args()

    constraints = (WORKSPACE / "constraints.md").read_text(encoding="utf-8")
    catalog = (WORKSPACE / "catalog.md").read_text(encoding="utf-8")
    config = yaml.safe_load((WORKSPACE / "config.yaml").read_text(encoding="utf-8"))
    judge_cfg = config.get("judge_model") or config.get("model", {})
    rules_cfg = config.get("business_rules")

    llm = LLMClient(
        base_url=judge_cfg.get("base_url", "http://localhost:1234/v1"),
        model=judge_cfg.get("name", "local-model"),
        temperature=judge_cfg.get("temperature", 0.1),
        max_tokens=judge_cfg.get("max_tokens", 800),
        api_key=os.path.expandvars(judge_cfg.get("api_key", "not-needed")),
    )

    cases = _read_jsonl(Path(args.input))
    if not cases:
        print(f"No cases in {args.input}", file=sys.stderr)
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for i, case in enumerate(cases, start=1):
            conv = _conv_from_case(case)
            result = _run_judge(llm, conv, constraints, catalog, rules=rules_cfg)
            refreshed = dict(case)
            refreshed["judge"] = {
                k: v for k, v in result.items() if k != "verdicts"
            }
            refreshed["verdicts"] = result.get("verdicts", {})
            f.write(json.dumps(refreshed, ensure_ascii=False, sort_keys=True) + "\n")
            n_fail = sum(1 for v in refreshed["verdicts"].values() if v.get("verdict") == "fail")
            print(f"  [{i}/{len(cases)}] {case['case_id']}: {n_fail} fail(s)")

    print(f"\nRefreshed {len(cases)} cases → {out_path}")
    return 0


def _conv_from_case(case: dict) -> Conversation:
    return Conversation(
        id=case.get("conversation_id", case.get("id", "?")),
        consumer_profile=case.get("consumer_profile", "?"),
        outcome=case.get("outcome", "pending"),
        sale_details=case.get("sale_details"),
        purchase_intent=case.get("purchase_intent"),
        status="finished",
        turns=[
            Turn(
                role=turn["role"],
                content=turn["content"],
                turn_number=int(turn.get("turn_number", 0) or 0),
            )
            for turn in case.get("transcript", [])
        ],
    )


def _read_jsonl(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


if __name__ == "__main__":
    raise SystemExit(main())
