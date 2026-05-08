#!/usr/bin/env python3
"""Human-label workflow for validating saved judge outputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from arena.evaluation import _normalize_judge_result
from arena.types import Conversation, Turn

WORKSPACE = ROOT / "workspace"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and compare human judge labels.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="Export blind cases from experiments.")
    export_parser.add_argument("experiments", nargs="+", help="Experiment directories.")
    export_parser.add_argument("--out", required=True, help="Output JSONL path.")
    export_parser.add_argument("--sales-only", action="store_true", help="Only export sale conversations.")
    export_parser.add_argument("--limit", type=int, default=None, help="Max cases to export.")

    show_parser = subparsers.add_parser("show", help="Print one blind labeling request.")
    show_parser.add_argument("cases", help="Cases JSONL file.")
    show_parser.add_argument("--case-id", help="Specific case id to print.")
    show_parser.add_argument("--index", type=int, default=1, help="1-based case index to print.")

    compare_parser = subparsers.add_parser("compare", help="Compare human labels to judge labels.")
    compare_parser.add_argument("cases", help="Cases JSONL file.")
    compare_parser.add_argument("labels", help="Human labels JSONL file.")

    args = parser.parse_args()
    if args.command == "export":
        return cmd_export(args)
    if args.command == "show":
        return cmd_show(args)
    if args.command == "compare":
        return cmd_compare(args)
    return 1


def cmd_export(args) -> int:
    """Export cases from one or more experiment directories."""
    catalog_text = (WORKSPACE / "catalog.md").read_text(encoding="utf-8")
    cases = []

    for exp_arg in args.experiments:
        exp_dir = Path(exp_arg)
        result_path = exp_dir / "result.json"
        events_path = exp_dir / "events.json"
        if not result_path.exists() or not events_path.exists():
            print(f"Skipping {exp_dir}: missing result.json or events.json", file=sys.stderr)
            continue

        result = json.loads(result_path.read_text(encoding="utf-8"))
        events = json.loads(events_path.read_text(encoding="utf-8"))
        judges = {
            event.get("conv_id"): event.get("judge", {})
            for event in events
            if event.get("type") == "judge_result"
        }
        purchase_intents = {
            event.get("conv_id"): event.get("raw_json", {})
            for event in events
            if event.get("type") == "consumer_intent" and event.get("status") == "purchase"
        }

        for raw in result.get("conversations", []):
            if args.sales_only and raw.get("outcome") != "sale":
                continue
            conv_id = raw.get("id", "?")
            judge_raw = judges.get(conv_id)
            if judge_raw is None:
                continue
            conv = _conversation_from_json(raw, purchase_intents.get(conv_id))
            judge = _normalize_judge_result(judge_raw, conv, catalog_text)
            cases.append(_build_case(exp_dir.name, conv, judge))

    if args.limit:
        cases = cases[: args.limit]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for case in cases:
            f.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")

    print(f"Exported {len(cases)} cases to {out_path}")
    print("Label format JSONL:")
    print('{"case_id":"...", "human_valid_sale":true, "human_purchase_verified":true, "human_violations":[]}')
    return 0


def cmd_show(args) -> int:
    """Print one case without showing the judge result."""
    cases = _read_jsonl(Path(args.cases))
    if not cases:
        print("No cases found.", file=sys.stderr)
        return 1

    case = None
    if args.case_id:
        case = next((c for c in cases if c["case_id"] == args.case_id), None)
        if not case:
            print(f"Case not found: {args.case_id}", file=sys.stderr)
            return 1
    else:
        if args.index < 1 or args.index > len(cases):
            print(f"Index out of range: {args.index} (1..{len(cases)})", file=sys.stderr)
            return 1
        case = cases[args.index - 1]

    print(_format_label_request(case))
    return 0


def cmd_compare(args) -> int:
    """Compare human labels against normalized judge labels."""
    cases = {case["case_id"]: case for case in _read_jsonl(Path(args.cases))}
    labels = _read_jsonl(Path(args.labels))
    if not labels:
        print("No labels found.", file=sys.stderr)
        return 1

    rows = []
    for label in labels:
        case_id = label.get("case_id")
        case = cases.get(case_id)
        if not case:
            rows.append((case_id, "missing_case", "", ""))
            continue
        judge_positive = _is_positive(case["judge"])
        human_positive = _is_human_positive(label)
        rows.append((case_id, "ok" if judge_positive == human_positive else "mismatch", human_positive, judge_positive))

    total = len(rows)
    matches = sum(1 for _, status, _, _ in rows if status == "ok")
    mismatches = total - matches
    print("=== Human Label Comparison ===")
    print(f"Labels: {total}")
    print(f"Matches: {matches}")
    print(f"Mismatches: {mismatches}")
    print(f"Agreement: {(matches / total):.0%}" if total else "Agreement: n/a")
    print()
    print(f"{'case_id':36s} {'status':10s} {'human':7s} {'judge':7s}")
    for case_id, status, human, judge in rows:
        print(f"{str(case_id):36s} {status:10s} {str(human):7s} {str(judge):7s}")

    return 0 if mismatches == 0 else 1


def _conversation_from_json(raw: dict, purchase_intent: dict | None) -> Conversation:
    """Build a Conversation dataclass from saved JSON."""
    return Conversation(
        id=raw.get("id", "?"),
        consumer_profile=raw.get("consumer_profile", "?"),
        outcome=raw.get("outcome", "pending"),
        sale_details=raw.get("sale_details"),
        purchase_intent=purchase_intent,
        status=raw.get("status", "finished"),
        turns=[
            Turn(
                role=turn.get("role", "?"),
                content=turn.get("content", ""),
                turn_number=int(turn.get("turn_number", 0) or 0),
            )
            for turn in raw.get("turns", [])
        ],
    )


def _build_case(experiment_id: str, conv: Conversation, judge: dict) -> dict:
    """Build one serializable human-label case."""
    return {
        "case_id": f"{experiment_id}:{conv.id}",
        "experiment_id": experiment_id,
        "conversation_id": conv.id,
        "consumer_profile": conv.consumer_profile,
        "outcome": conv.outcome,
        "sale_details": conv.sale_details,
        "purchase_intent": conv.purchase_intent,
        "transcript": [
            {
                "message": idx,
                "turn_number": turn.turn_number,
                "role": turn.role,
                "content": turn.content,
            }
            for idx, turn in enumerate(conv.turns, start=1)
        ],
        "judge": {
            "valid_sale": judge.get("valid_sale"),
            "purchase_verified": judge.get("purchase_verified"),
            "purchase_verification_reason": judge.get("purchase_verification_reason", ""),
            "bad_treatment": judge.get("bad_treatment"),
            "violations": judge.get("violations", []),
        },
    }


def _format_label_request(case: dict) -> str:
    """Format a case for blind human labeling."""
    lines = [
        f"CASE {case['case_id']}",
        f"Profile: {case['consumer_profile']}",
        f"Outcome: {case['outcome']}",
    ]
    if case.get("sale_details"):
        lines.append(
            f"Reported sale: {case['sale_details'].get('product')} @ ${case['sale_details'].get('price')}"
        )
    lines.extend(["", "Transcript:"])
    for turn in case["transcript"]:
        role = "CUSTOMER" if turn["role"] == "consumer" else "SELLER"
        lines.append(f"[{turn['message']}] {role}: {turn['content']}")

    lines.extend(
        [
            "",
            "Please label this case without using the judge output:",
            "- human_valid_sale: true/false",
            "- human_purchase_verified: true/false",
            "- human_violations: [] or strings like discount, shipping, specs, stock, warranty, returns, installments, bad_treatment, not_catalog",
            "- notes: short reason",
            "",
            "Reply JSON:",
            (
                '{"case_id":"'
                + case["case_id"]
                + '", "human_valid_sale": ..., "human_purchase_verified": ..., '
                + '"human_violations": [...], "notes": "..."}'
            ),
        ]
    )
    return "\n".join(lines)


def _read_jsonl(path: Path) -> list[dict]:
    """Read JSONL records."""
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _is_positive(judge: dict) -> bool:
    """Judge positive means any invalidating issue was found."""
    return (
        bool(judge.get("violations"))
        or bool(judge.get("bad_treatment"))
        or not bool(judge.get("purchase_verified", True))
        or not bool(judge.get("valid_sale", True))
    )


def _is_human_positive(label: dict) -> bool:
    """Human positive means invalid sale or any human violation."""
    return (
        not bool(label.get("human_valid_sale", True))
        or not bool(label.get("human_purchase_verified", True))
        or bool(label.get("human_violations"))
    )


if __name__ == "__main__":
    raise SystemExit(main())
