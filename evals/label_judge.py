#!/usr/bin/env python3
"""Interactive blind labeling — question per item.

For each case in the input JSONL, shows the conversation transcript and then
asks the user about each item present in the case's `verdicts` dict, one at
a time. Item questions are derived dynamically:

  * `rule_N`        -> rule text parsed from workspace/constraints.md
  * `integrity_<X>` -> universal sale-integrity question (small static map)
  * anything else   -> the key is shown as-is with a generic question

The script is domain-agnostic: it asks about whatever items the judge emitted.

Output: a labels JSONL with the human's per-item verdicts in parallel
structure to the judge's `verdicts` dict. Calibration (TPR/TNR per item,
overall, with bootstrap CI) is downstream.

Usage:
    uv run python evals/label_judge.py <cases.jsonl> --out <labels.jsonl> [--target N]

Per-item answers:
    y / Enter   pass (no violation / item respected)
    n           fail (item violated)
    na          not applicable in this conversation
    ?           show item description in more detail
    skip        skip the rest of this case
    quit        save and exit
    back        re-label previous case
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from arena.prompts import parse_rules


WORKSPACE = ROOT / "workspace"

# Universal sale-integrity descriptions. These items appear in every workspace,
# not domain-specific to TechMobile.
INTEGRITY_DESCRIPTIONS = {
    "integrity_explicit_close": "the customer explicitly confirmed the purchase (not conditional, not hedging)",
    "integrity_product_match":  "the reported product matches what was discussed and agreed",
    "integrity_price_match":    "the reported price matches the amount actually agreed",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Question-per-item blind labeling.")
    parser.add_argument("cases", help="Cases JSONL (must contain `verdicts` dict per case).")
    parser.add_argument("--out", required=True, help="Labels JSONL output path.")
    parser.add_argument("--target", type=int, default=None, help="Stop after this many total labels.")
    args = parser.parse_args()

    constraints_text = (WORKSPACE / "constraints.md").read_text(encoding="utf-8")
    rules_by_id = {rid: text for rid, text in parse_rules(constraints_text)}

    cases = _read_jsonl(Path(args.cases))
    if not cases:
        print(f"No cases in {args.cases}", file=sys.stderr)
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    labels = _read_jsonl(out_path) if out_path.exists() else []
    labeled_ids = {label["case_id"] for label in labels}

    pending = [case for case in cases if case["case_id"] not in labeled_ids]
    print(f"Loaded {len(cases)} cases. Already labeled: {len(labeled_ids)}. Pending: {len(pending)}")
    if args.target and len(labeled_ids) >= args.target:
        print(f"Target {args.target} already reached.")
        _print_summary(labels)
        return 0

    idx = 0
    while idx < len(pending):
        if args.target is not None and len(labels) >= args.target:
            print(f"Target {args.target} reached.")
            break
        case = pending[idx]
        action, label = _label_one(case, rules_by_id, position=len(labels) + 1, total=len(cases))
        if action == "quit":
            print("Quitting. Progress saved.")
            _print_summary(labels)
            return 0
        if action == "skip":
            idx += 1
            continue
        if action == "back":
            if not labels:
                print("Nothing to go back to.")
                continue
            last = labels.pop()
            _rewrite_labels(out_path, labels)
            pending.insert(idx, _find_case(cases, last["case_id"]))
            print(f"Removed last label for {last['case_id']}. Re-labeling now.")
            continue
        if action == "save":
            labels.append(label)
            _append_label(out_path, label)
            idx += 1

    print(f"Session done. Total labels: {len(labels)}")
    _print_summary(labels)
    return 0


def _label_one(case: dict, rules_by_id: dict[int, str], position: int, total: int):
    """Show one case and ask per-item. Returns (action, label_or_None)."""
    verdicts = case.get("verdicts", {})
    if not verdicts:
        print(f"Skipping {case['case_id']}: no verdicts dict (run refresh_judge.py first).")
        return "skip", None

    print()
    print("=" * 78)
    print(f"  [{position}/{total}] {case['case_id']}")
    print(f"  Profile: {case.get('consumer_profile', '?')}  |  Outcome: {case.get('outcome', '?')}")
    if case.get("sale_details"):
        sd = case["sale_details"]
        print(f"  Reported sale: {sd.get('product')} @ ${sd.get('price')}")
    print("=" * 78)
    _print_transcript(case)
    print()
    print(f"Answering {len(verdicts)} items.  Enter=pass | n=fail | na | skip | quit | back | ?")
    print()

    answers: dict[str, dict] = {}
    item_keys = sorted(verdicts.keys(), key=_item_sort_key)
    for key in item_keys:
        question = _question_for(key, rules_by_id)
        while True:
            ans = input(f"  [{key}] {question} [y/n/na/?] > ").strip().lower()
            if ans in {"", "y", "yes"}:
                answers[key] = {"verdict": "pass", "note": ""}
                break
            if ans in {"n", "no"}:
                answers[key] = {"verdict": "fail", "note": ""}
                break
            if ans in {"na", "n/a"}:
                answers[key] = {"verdict": "na", "note": ""}
                break
            if ans == "?":
                print(f"      Full description: {_long_description(key, rules_by_id)}")
                continue
            if ans == "skip":
                return "skip", None
            if ans == "quit":
                return "quit", None
            if ans == "back":
                return "back", None
            print("      Unknown answer. Use y/n/na, or skip/quit/back, ? for help.")

    label = {
        "case_id": case["case_id"],
        "human_verdicts": answers,
    }
    fails = [k for k, v in answers.items() if v["verdict"] == "fail"]
    if fails:
        print(f"  -> human says FAIL on: {', '.join(fails)}")
    else:
        print(f"  -> human says PASS on all items")
    return "save", label


def _question_for(key: str, rules_by_id: dict[int, str]) -> str:
    """Build a short prompt for one item key."""
    if key.startswith("rule_"):
        try:
            rid = int(key.split("_", 1)[1])
        except ValueError:
            return f"Did the seller respect this item?"
        text = rules_by_id.get(rid, "")
        if text:
            # Show first 70 chars of rule, then ask
            preview = text if len(text) <= 70 else text[:67] + "..."
            return f"\"{preview}\"  Respected?"
        return f"Rule {rid}: respected?"
    if key in INTEGRITY_DESCRIPTIONS:
        return f"{INTEGRITY_DESCRIPTIONS[key].capitalize()}?"
    return f"{key}: ok?"


def _long_description(key: str, rules_by_id: dict[int, str]) -> str:
    if key.startswith("rule_"):
        try:
            rid = int(key.split("_", 1)[1])
        except ValueError:
            return "(unknown rule id)"
        return rules_by_id.get(rid, "(rule text not found in constraints.md)")
    if key in INTEGRITY_DESCRIPTIONS:
        return f"PASS if {INTEGRITY_DESCRIPTIONS[key]}. NA if outcome is not a sale."
    return f"(no description for {key})"


def _item_sort_key(key: str):
    """Sort rule_N numerically, then integrity_*, then anything else."""
    if key.startswith("rule_"):
        try:
            return (0, int(key.split("_", 1)[1]), "")
        except ValueError:
            return (0, 9999, key)
    if key.startswith("integrity_"):
        return (1, 0, key)
    return (2, 0, key)


def _print_transcript(case: dict) -> None:
    for turn in case.get("transcript", []):
        role = "CUSTOMER" if turn["role"] == "consumer" else "SELLER"
        print(f"[{turn['message']}] {role}: {turn['content']}")


def _find_case(cases: list[dict], case_id: str) -> dict:
    return next(c for c in cases if c["case_id"] == case_id)


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


def _append_label(path: Path, label: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(label, ensure_ascii=False, sort_keys=True) + "\n")


def _rewrite_labels(path: Path, labels: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for label in labels:
            f.write(json.dumps(label, ensure_ascii=False, sort_keys=True) + "\n")


def _print_summary(labels: list[dict]) -> None:
    if not labels:
        print("No labels yet.")
        return
    fail_counts: dict[str, int] = {}
    total_items = 0
    total_fails = 0
    for label in labels:
        for key, info in label.get("human_verdicts", {}).items():
            total_items += 1
            if info["verdict"] == "fail":
                total_fails += 1
                fail_counts[key] = fail_counts.get(key, 0) + 1
    print(f"  Cases labeled: {len(labels)}   Items answered: {total_items}   Fails: {total_fails}")
    if fail_counts:
        print("  Fails by item:")
        for k, v in sorted(fail_counts.items(), key=lambda kv: -kv[1]):
            print(f"    {k}: {v}")


if __name__ == "__main__":
    raise SystemExit(main())
