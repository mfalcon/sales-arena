#!/usr/bin/env python3
"""Pre-compute relevant seller turns per rule for labeling hints.

For each case in the input JSONL, asks the judge LLM ONE question:
    "Which seller turns are relevant to evaluating each rule?"
It does NOT ask for verdicts. The output is purely a pointer: per rule
(and per sale_integrity item), the list of message numbers a human
labeler should focus on. Bias is toward recall — include any turn where
the rule plausibly applies.

Output: same JSONL but with a `relevant_turns` field added per case:
    {"rule_1": [4], "rule_2": [2], ..., "integrity_explicit_close": [11], ...}

Resumes from a partial output file. Skips cases that already have
`relevant_turns`. Domain-agnostic: reads constraints.md and uses whatever
rules are there.

Usage:
    uv run python evals/locator.py <input.jsonl> --out <output.jsonl>
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

from arena.llm import LLMClient, extract_json
from arena.prompts import format_rules_block, parse_rules


WORKSPACE = ROOT / "workspace"
INTEGRITY_ITEMS = ("explicit_close", "product_match", "price_match")


LOCATOR_SYSTEM = (
    "You are a TURN LOCATOR for a human labeling task, NOT a judge.\n\n"
    "For each business rule and each sale-integrity item, list the seller "
    "message numbers (Message N) in the transcript that are RELEVANT to "
    "evaluating that item — i.e. turns where the seller said or did "
    "something the item is about. Include the turn even if you think the "
    "seller respected the rule. Bias toward over-including: when in doubt, "
    "include the turn. The human labeler will decide pass/fail; you only "
    "point them at the relevant turns so they don't have to re-read the "
    "whole transcript per rule.\n\n"
    "If a rule has no relevant seller turn in this conversation, return [].\n\n"
    "BUSINESS RULES:\n{rules_block}\n\n"
    "SALE INTEGRITY ITEMS (relevant only at the closing turns of a sale):\n"
    "- explicit_close: the customer's confirmation, and the seller turn it "
    "responds to.\n"
    "- product_match: the seller turns where the product was named/agreed.\n"
    "- price_match:   the seller turns where the final price was named/agreed.\n\n"
    "Respond ONLY with a JSON object, no markdown or commentary:\n"
    "{{\n"
    '  "rule_1": [<message_numbers>], ... one entry per rule above,\n'
    '  "integrity_explicit_close": [...],\n'
    '  "integrity_product_match": [...],\n'
    '  "integrity_price_match": [...]\n'
    "}}\n"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-compute relevant turns per rule.")
    parser.add_argument("input", help="Input cases JSONL.")
    parser.add_argument("--out", required=True, help="Output JSONL with relevant_turns added.")
    args = parser.parse_args()

    cases = _read_jsonl(Path(args.input))
    if not cases:
        print(f"No cases in {args.input}", file=sys.stderr)
        return 1

    done = {c["case_id"]: c for c in _read_jsonl(Path(args.out))}
    constraints = (WORKSPACE / "constraints.md").read_text(encoding="utf-8")
    rules = parse_rules(constraints)
    rule_ids = [rid for rid, _ in rules]
    system = LOCATOR_SYSTEM.format(rules_block=format_rules_block(rules))

    config = yaml.safe_load((WORKSPACE / "config.yaml").read_text(encoding="utf-8"))
    judge_cfg = config.get("judge_model") or config.get("model", {})
    llm = LLMClient(
        base_url=judge_cfg.get("base_url", "http://localhost:1234/v1"),
        model=judge_cfg.get("name", "local-model"),
        temperature=judge_cfg.get("temperature", 0.1),
        max_tokens=judge_cfg.get("max_tokens", 800),
        api_key=os.path.expandvars(judge_cfg.get("api_key", "not-needed")),
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    new_calls = 0
    with out_path.open("a", encoding="utf-8") as f:
        for i, case in enumerate(cases, start=1):
            cid = case["case_id"]
            if cid in done and "relevant_turns" in done[cid]:
                results.append(done[cid])
                continue
            transcript = _format_transcript(case.get("transcript", []))
            user = f"=== CONVERSATION ({cid}) ===\n{transcript}"
            try:
                response = llm.send(
                    [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    json_mode=True,
                )
            except Exception as e:
                print(f"  [{i}/{len(cases)}] {cid}: locator call failed: {e}", file=sys.stderr)
                continue
            parsed = extract_json(response) or {}
            relevant = _normalize(parsed, rule_ids)
            enriched = dict(case)
            enriched["relevant_turns"] = relevant
            results.append(enriched)
            f.write(json.dumps(enriched, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            new_calls += 1
            n_hits = sum(len(v) for v in relevant.values())
            print(f"  [{i}/{len(cases)}] {cid}: {n_hits} relevant-turn hits across {len(relevant)} items")

    # Rewrite cleanly: input order, no duplicates.
    with out_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    print(f"\nLocator: {new_calls} new calls. Output: {out_path} ({len(results)} cases)")
    return 0


def _normalize(parsed: dict, rule_ids: list[int]) -> dict[str, list[int]]:
    """Keep only known keys, coerce values to int lists."""
    out: dict[str, list[int]] = {}
    keys = [f"rule_{rid}" for rid in rule_ids] + [f"integrity_{k}" for k in INTEGRITY_ITEMS]
    for k in keys:
        raw = parsed.get(k, [])
        if not isinstance(raw, list):
            raw = []
        cleaned: list[int] = []
        for v in raw:
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if n > 0 and n not in cleaned:
                cleaned.append(n)
        out[k] = cleaned
    return out


def _format_transcript(transcript: list[dict]) -> str:
    lines = []
    for turn in transcript:
        role = "CUSTOMER" if turn.get("role") == "consumer" else "SELLER"
        msg = turn.get("message", "?")
        lines.append(f"[Message {msg}] {role}: {turn.get('content', '')}")
    return "\n\n".join(lines)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


if __name__ == "__main__":
    raise SystemExit(main())
