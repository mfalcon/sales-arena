#!/usr/bin/env python3
"""Calibrate the LLM judge against human Pass/Fail labels.

Workflow:
    1. Label cases blindly with evals/label_judge.py
    2. Split: train (15%) / dev (45%) / test (40%) — stratified, seed=42
    3. Run this script in dev mode while iterating the judge prompt
    4. Run once in final mode against the test set

The judge "label" is taken from one of two sources:
    - cached (default): the judge output stored in candidates JSONL (cheap, no API)
    - --rerun: re-runs the judge with the CURRENT prompt against the configured model

Commands:
    split      Show split sizes and class balance.
    train      Print training-set cases (for use as few-shot examples).
    dev        Score dev set, print TPR/TNR/CI/disagreements.
    final      Score test set ONCE (records to evals/JUDGE_VALIDATION.md).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from arena.evaluation import _run_judge
from arena.llm import LLMClient
from arena.types import Conversation, Turn

from evals.human_label_judge import (
    _conversation_from_json,
    _is_human_positive,
    _is_positive,
    _read_jsonl,
)
from evals.validate_judge import _build_judge_llm, _read_text, _read_yaml

WORKSPACE = ROOT / "workspace"
SEED = 42
TRAIN_FRAC = 0.15
DEV_FRAC = 0.45  # test = 1 - TRAIN_FRAC - DEV_FRAC = 0.40


@dataclass
class LabeledCase:
    case_id: str
    case: dict
    human_pass: bool


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate the LLM judge against human labels.")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("cases", help="Cases JSONL path.")
    common.add_argument("labels", help="Human labels JSONL path.")

    sub.add_parser("split", parents=[common], help="Show split sizes.")
    sub.add_parser("train", parents=[common], help="Print training set cases.")

    score = sub.add_parser("dev", parents=[common], help="Score dev set.")
    score.add_argument("--rerun", action="store_true", help="Re-run the judge (calls API).")
    score.add_argument(
        "--cache",
        default="evals/judge_cache.jsonl",
        help="Where to cache rerun outputs (so dev iterations don't repay API cost).",
    )

    final = sub.add_parser("final", parents=[common], help="Score test set (one-shot).")
    final.add_argument("--rerun", action="store_true", help="Re-run the judge (calls API).")
    final.add_argument(
        "--cache",
        default="evals/judge_cache.jsonl",
        help="Where to cache rerun outputs.",
    )
    final.add_argument(
        "--out",
        default="evals/JUDGE_VALIDATION.md",
        help="Markdown file to append the final report to.",
    )

    args = parser.parse_args()
    cases = _load_cases(Path(args.cases))
    labels = _load_labels(Path(args.labels))
    labeled = _join(cases, labels)
    if not labeled:
        print("No labeled cases found.", file=sys.stderr)
        return 1

    train, dev, test = _stratified_split(labeled, TRAIN_FRAC, DEV_FRAC, SEED)

    if args.command == "split":
        return cmd_split(labeled, train, dev, test)
    if args.command == "train":
        return cmd_train(train)
    if args.command == "dev":
        return cmd_score(dev, "dev", args.rerun, Path(args.cache))
    if args.command == "final":
        return cmd_final(test, args.rerun, Path(args.cache), Path(args.out))
    return 1


def cmd_split(all_labeled, train, dev, test) -> int:
    """Print split sizes and class balance."""
    print("=== Split ===")
    print(f"Total labeled: {len(all_labeled)}  ({_balance(all_labeled)})")
    print(f"Train ({TRAIN_FRAC:.0%}): {len(train)}  ({_balance(train)})")
    print(f"Dev   ({DEV_FRAC:.0%}): {len(dev)}  ({_balance(dev)})")
    print(f"Test  ({1 - TRAIN_FRAC - DEV_FRAC:.0%}): {len(test)}  ({_balance(test)})")
    return 0


def cmd_train(train) -> int:
    """Print training cases (for few-shot examples in the judge prompt)."""
    print(f"=== Training set ({len(train)} cases) ===")
    for lc in train:
        outcome = "PASS" if lc.human_pass else "FAIL"
        print(f"\n--- {lc.case_id} [{outcome}] ---")
        for turn in lc.case["transcript"]:
            role = "C" if turn["role"] == "consumer" else "S"
            print(f"  [{turn['message']}] {role}: {turn['content']}")
    return 0


def cmd_score(split, name: str, rerun: bool, cache_path: Path) -> int:
    """Score a split (dev mode — can be re-run repeatedly)."""
    print(f"=== Scoring {name} ({len(split)} cases) ===")
    judge_passes = _judge_predictions(split, rerun, cache_path)
    return _report(split, judge_passes, name)


def cmd_final(test, rerun: bool, cache_path: Path, out_path: Path) -> int:
    """Score test set (one-shot, appends to markdown report)."""
    print(f"=== FINAL evaluation on test ({len(test)} cases) ===")
    print("This is the held-out test set — do not iterate after seeing these results.")
    judge_passes = _judge_predictions(test, rerun, cache_path)
    rc = _report(test, judge_passes, "test")

    summary = _format_markdown_report(test, judge_passes)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as f:
        f.write(summary)
    print(f"\nReport appended to {out_path}")
    return rc


# --- Core scoring ---


def _judge_predictions(split: list[LabeledCase], rerun: bool, cache_path: Path) -> list[bool]:
    """Return judge_pass[] aligned with split. True means judge says Pass."""
    if not rerun:
        return [not _is_positive(lc.case["judge"]) for lc in split]

    cache = _load_cache(cache_path)
    config = _read_yaml(WORKSPACE / "config.yaml")
    constraints = _read_text(WORKSPACE / "constraints.md")
    catalog = _read_text(WORKSPACE / "catalog.md")
    rules = _build_rules(config)
    llm = _build_judge_llm(config)

    cache_key_prefix = _judge_signature(config)
    judge_passes = []
    new_results = 0
    for lc in split:
        key = f"{cache_key_prefix}::{lc.case_id}"
        if key in cache:
            judge = cache[key]
        else:
            conv = _conversation_from_case(lc.case)
            judge = _run_judge(llm, conv, constraints, catalog, rules=rules)
            cache[key] = judge
            _append_cache(cache_path, key, judge)
            new_results += 1
            print(f"  judged {lc.case_id} (cache miss)")
        judge_passes.append(not _is_positive(judge))
    print(f"Judge calls: {new_results} new, {len(split) - new_results} cached")
    return judge_passes


def _report(split: list[LabeledCase], judge_passes: list[bool], name: str) -> int:
    """Print TPR/TNR/CI/confusion/disagreements. Returns 0 if TPR&TNR>=0.90."""
    human_passes = [lc.human_pass for lc in split]
    tp = sum(1 for h, j in zip(human_passes, judge_passes) if h and j)
    fn = sum(1 for h, j in zip(human_passes, judge_passes) if h and not j)
    tn = sum(1 for h, j in zip(human_passes, judge_passes) if not h and not j)
    fp = sum(1 for h, j in zip(human_passes, judge_passes) if not h and j)

    tpr = tp / (tp + fn) if (tp + fn) else float("nan")
    tnr = tn / (tn + fp) if (tn + fp) else float("nan")
    accuracy = (tp + tn) / len(split)

    tpr_lo, tpr_hi = _bootstrap_rate_ci(human_passes, judge_passes, positive_class=True)
    tnr_lo, tnr_hi = _bootstrap_rate_ci(human_passes, judge_passes, positive_class=False)

    print()
    print(f"--- {name} results ---")
    print(f"  N: {len(split)}")
    print(f"  Confusion (rows=human, cols=judge):")
    print(f"             pred Pass   pred Fail")
    print(f"   Pass      {tp:>9d}   {fn:>9d}")
    print(f"   Fail      {fp:>9d}   {tn:>9d}")
    print(f"  TPR: {tpr:.0%}  (95% CI: {tpr_lo:.0%}–{tpr_hi:.0%})")
    print(f"  TNR: {tnr:.0%}  (95% CI: {tnr_lo:.0%}–{tnr_hi:.0%})")
    print(f"  Accuracy: {accuracy:.0%}")

    target_met = tpr >= 0.90 and tnr >= 0.90
    minimum_met = tpr >= 0.80 and tnr >= 0.80
    if target_met:
        print(f"  Target TPR&TNR ≥ 90%: PASS")
    elif minimum_met:
        print(f"  Target TPR&TNR ≥ 90%: BELOW (≥80% minimum met)")
    else:
        print(f"  Target TPR&TNR ≥ 80%: FAIL — judge needs work")

    disagreements = [
        (lc, j) for lc, j, h in zip(split, judge_passes, human_passes) if j != h
    ]
    if disagreements:
        print(f"\n  Disagreements ({len(disagreements)}):")
        for lc, j in disagreements:
            kind = "FALSE_PASS" if j else "FALSE_FAIL"
            note = lc.case.get("_label_notes", "")
            print(f"    {kind}  {lc.case_id}  human={'Pass' if lc.human_pass else 'Fail'}  judge={'Pass' if j else 'Fail'}  {note}")

    return 0 if target_met else 1


def _format_markdown_report(test: list[LabeledCase], judge_passes: list[bool]) -> str:
    """Format a markdown summary of the test-set results."""
    from datetime import datetime, timezone

    human_passes = [lc.human_pass for lc in test]
    tp = sum(1 for h, j in zip(human_passes, judge_passes) if h and j)
    fn = sum(1 for h, j in zip(human_passes, judge_passes) if h and not j)
    tn = sum(1 for h, j in zip(human_passes, judge_passes) if not h and not j)
    fp = sum(1 for h, j in zip(human_passes, judge_passes) if not h and j)
    tpr = tp / (tp + fn) if (tp + fn) else float("nan")
    tnr = tn / (tn + fp) if (tn + fp) else float("nan")
    tpr_lo, tpr_hi = _bootstrap_rate_ci(human_passes, judge_passes, positive_class=True)
    tnr_lo, tnr_hi = _bootstrap_rate_ci(human_passes, judge_passes, positive_class=False)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "",
        f"## Test set evaluation — {timestamp}",
        f"- N: {len(test)}",
        f"- TPR: {tpr:.2%} (95% CI: {tpr_lo:.2%}–{tpr_hi:.2%})",
        f"- TNR: {tnr:.2%} (95% CI: {tnr_lo:.2%}–{tnr_hi:.2%})",
        f"- Confusion: TP={tp}, FN={fn}, FP={fp}, TN={tn}",
        "",
    ]
    return "\n".join(lines)


# --- Bootstrap CI ---


def _bootstrap_rate_ci(
    human_passes: list[bool],
    judge_passes: list[bool],
    positive_class: bool,
    n_bootstrap: int = 2000,
) -> tuple[float, float]:
    """Bootstrap 95% CI for TPR (positive_class=True) or TNR (positive_class=False)."""
    rng = random.Random(SEED)
    indices_in_class = [
        i for i, h in enumerate(human_passes) if h == positive_class
    ]
    if not indices_in_class:
        return (float("nan"), float("nan"))

    rates = []
    n = len(indices_in_class)
    for _ in range(n_bootstrap):
        sample = [indices_in_class[rng.randrange(n)] for _ in range(n)]
        agree = sum(1 for i in sample if (judge_passes[i] == positive_class))
        rates.append(agree / n)
    rates.sort()
    lo = rates[int(0.025 * n_bootstrap)]
    hi = rates[int(0.975 * n_bootstrap) - 1]
    return (lo, hi)


# --- Splits ---


def _stratified_split(
    labeled: list[LabeledCase], train_frac: float, dev_frac: float, seed: int
) -> tuple[list[LabeledCase], list[LabeledCase], list[LabeledCase]]:
    """Stratified shuffle split by human Pass/Fail. Deterministic with seed."""
    rng = random.Random(seed)
    pos = [lc for lc in labeled if lc.human_pass]
    neg = [lc for lc in labeled if not lc.human_pass]
    rng.shuffle(pos)
    rng.shuffle(neg)

    def split_one(items: list[LabeledCase]) -> tuple[list, list, list]:
        n = len(items)
        n_train = max(1, round(n * train_frac)) if n else 0
        n_dev = max(1, round(n * dev_frac)) if n else 0
        n_train = min(n_train, n)
        n_dev = min(n_dev, n - n_train)
        train = items[:n_train]
        dev = items[n_train : n_train + n_dev]
        test = items[n_train + n_dev :]
        return train, dev, test

    p_train, p_dev, p_test = split_one(pos)
    n_train, n_dev, n_test = split_one(neg)
    train = p_train + n_train
    dev = p_dev + n_dev
    test = p_test + n_test
    rng.shuffle(train)
    rng.shuffle(dev)
    rng.shuffle(test)
    return train, dev, test


# --- I/O helpers ---


def _load_cases(path: Path) -> dict[str, dict]:
    return {case["case_id"]: case for case in _read_jsonl(path)}


def _load_labels(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {label["case_id"]: label for label in _read_jsonl(path)}


def _join(cases: dict, labels: dict) -> list[LabeledCase]:
    out = []
    for case_id, label in labels.items():
        case = cases.get(case_id)
        if case is None:
            print(f"  WARN label without case: {case_id}", file=sys.stderr)
            continue
        case_with_notes = dict(case)
        case_with_notes["_label_notes"] = label.get("notes", "")
        out.append(
            LabeledCase(
                case_id=case_id,
                case=case_with_notes,
                human_pass=not _is_human_positive(label),
            )
        )
    return out


def _balance(items: list[LabeledCase]) -> str:
    p = sum(1 for lc in items if lc.human_pass)
    f = len(items) - p
    return f"Pass={p}, Fail={f}"


def _conversation_from_case(case: dict) -> Conversation:
    return Conversation(
        id=case["conversation_id"],
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
            for turn in case["transcript"]
        ],
    )


def _build_rules(config: dict) -> dict:
    return config.get("business_rules") or {}


# --- Cache ---


def _load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    out = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            out[entry["key"]] = entry["judge"]
    return out


def _append_cache(path: Path, key: str, judge: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"key": key, "judge": judge}, ensure_ascii=False) + "\n")


def _judge_signature(config: dict) -> str:
    """Cache key prefix that bumps when judge config or normalization changes."""
    judge = config.get("judge_model", {})
    name = judge.get("name", "?")
    temp = judge.get("temperature", "?")
    hashed_paths = [ROOT / "arena" / "prompts.py", ROOT / "arena" / "evaluation.py"]
    try:
        import hashlib

        h = hashlib.sha256()
        for p in hashed_paths:
            if p.exists():
                h.update(p.read_text(encoding="utf-8").encode("utf-8"))
        sig = h.hexdigest()[:8]
    except Exception:
        sig = "?"
    return f"{name}@{temp}#{sig}"


if __name__ == "__main__":
    raise SystemExit(main())
