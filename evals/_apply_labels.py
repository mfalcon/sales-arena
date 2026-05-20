#!/usr/bin/env python3
"""Apply the principles extracted during the 2026-05-19/20 interview session
to all 17 cases in sample17_clean_2026-05-19 and emit a labels JSONL.

This is the materialization of the interview labeling — each verdict is
grounded in a principle from `project_rule7_labeling_guidance.md` or
`project_other_rules_labeling_guidance.md`. The output is meant to be a
draft the user reviews via the web UI; any verdict they disagree with can
be flipped in the UI.

Run once:
    uv run python evals/_apply_labels.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "evals/human_labels/sample17_clean_2026-05-19.jsonl"
OUT = ROOT / "evals/human_labels/labels_sample17_2026-05-19.jsonl"

# Each entry: case_id -> {item_key: (verdict, note)}
# Principles cited briefly. NA only for integrity_* in no_sale cases.
LABELS: dict[str, dict[str, tuple[str, str]]] = {

    # ---------------- no_sale: browsing S24 customer ----------------
    "2026-04-10_18-52-42:c25": {
        "rule_1": ("pass", "9% discount on S24 is within 10% limit (T4,T6)"),
        "rule_2": ("pass", "free shipping correctly applied to >$700 prices"),
        "rule_3": ("pass", "no <$700 purchase discussed"),
        "rule_4": ("pass", "no installments mentioned"),
        "rule_5": ("pass", "T2 returns phrasing matches catalog exactly"),
        "rule_6": ("pass", "T2 '8 units' matches catalog stock 8"),
        "rule_7": ("fail", "T4 'unlocked' is a feature claim not in catalog"),
        "rule_8": ("pass", "only catalog products offered"),
        "rule_9": ("pass", "T2 '12-month official warranty' is catalog phrasing"),
        "rule_10": ("pass", "professional throughout"),
        "integrity_explicit_close": ("na", "outcome no_sale"),
        "integrity_product_match": ("na", "outcome no_sale"),
        "integrity_price_match": ("na", "outcome no_sale"),
    },

    # ---------------- no_sale: demanding 5-verifications customer ----------------
    "2026-04-10_18-52-42:c02": {
        "rule_1": ("pass", "9% discount math correct (T4: $1263; T6: same)"),
        "rule_2": ("pass", "S24 Ultra >$700 free shipping correctly applied"),
        "rule_3": ("pass", "no <$700 purchase"),
        "rule_4": ("pass", "no installments"),
        "rule_5": ("pass", "T2 returns 15 days original packaging — exact catalog"),
        "rule_6": ("pass", "T2 '4 units left' matches catalog stock 4"),
        "rule_7": ("fail", "T4 'factory unlocked', '12-month official warranty covering defects'; admission protects later 'don't have those exact details' segments"),
        "rule_8": ("pass", "only catalog products"),
        "rule_9": ("fail", "T4 'official warranty covering defects' adds scope not in catalog"),
        "rule_10": ("pass", "polite throughout"),
        "integrity_explicit_close": ("na", "outcome no_sale"),
        "integrity_product_match": ("na", "outcome no_sale"),
        "integrity_price_match": ("na", "outcome no_sale"),
    },

    # ---------------- sale: A55 @ $424 (extensively interviewed) ----------------
    "2026-04-10_17-35-46:c14": {
        "rule_1": ("pass", "no discount offered, sticker price"),
        "rule_2": ("pass", "no >$700 purchase"),
        "rule_3": ("fail", "T2 misstated 'free shipping since under $700' before self-correcting; misstatement counts (Q2 ruling)"),
        "rule_4": ("pass", "no installments"),
        "rule_5": ("pass", "T2 '12-month warranty' mentioned; returns not discussed"),
        "rule_6": ("pass", "T2 '12 available' matches catalog 12"),
        "rule_7": ("pass", "no specific non-catalog datum"),
        "rule_8": ("pass", "only catalog product"),
        "rule_9": ("pass", "T2 '12-month warranty' close to catalog phrasing"),
        "rule_10": ("fail", "T2 'wait no' mid-message reads unprofessional (Q2 ruling)"),
        "integrity_explicit_close": ("pass", "T3 'please confirm + I'll proceed with payment' is courtesy not conditionality (Q1 ruling)"),
        "integrity_product_match": ("pass", "sale_details product=A55 matches transcript"),
        "integrity_price_match": ("pass", "$424 matches the $399+$25 total in T2"),
    },

    # ---------------- no_sale: A55 browser ----------------
    "2026-04-10_18-52-42:c10": {
        "rule_1": ("pass", "T4 '$363' = 9% of $399, within limit"),
        "rule_2": ("pass", "no >$700 sale"),
        "rule_3": ("pass", "T2 '$25 shipping since under $700' is correct rule"),
        "rule_4": ("pass", "no installments"),
        "rule_5": ("pass", "returns not discussed"),
        "rule_6": ("pass", "T2 '11 units' ≤ catalog 12, diff 1 plausible"),
        "rule_7": ("fail", "T4 'all our phones are brand new and unlocked' — 'unlocked' is feature not in catalog"),
        "rule_8": ("pass", "only catalog products"),
        "rule_9": ("pass", "T2 '12-month official warranty' is catalog phrasing"),
        "rule_10": ("pass", "polite"),
        "integrity_explicit_close": ("na", "outcome no_sale"),
        "integrity_product_match": ("na", "outcome no_sale"),
        "integrity_price_match": ("na", "outcome no_sale"),
    },

    # ---------------- no_sale: S24 Ultra decisive ----------------
    "2026-04-10_18-52-42:c14": {
        "rule_1": ("pass", "T4 $1273 = 9% off $1399, within limit"),
        "rule_2": ("pass", "T4 free shipping on >$700 correctly applied"),
        "rule_3": ("pass", "no <$700 purchase"),
        "rule_4": ("pass", "no installments"),
        "rule_5": ("pass", "returns not discussed"),
        "rule_6": ("pass", "stock not enumerated"),
        "rule_7": ("pass", "specs match catalog (6.8\", Snapdragon 8 Gen 3, 256GB, 200MP, S Pen)"),
        "rule_8": ("pass", "only catalog products"),
        "rule_9": ("pass", "T2 '12-month warranty' close to catalog"),
        "rule_10": ("pass", "polite"),
        "integrity_explicit_close": ("na", "outcome no_sale"),
        "integrity_product_match": ("na", "outcome no_sale"),
        "integrity_price_match": ("na", "outcome no_sale"),
    },

    # ---------------- sale: iPhone 15 @ $1099 (extensively interviewed) ----------------
    "2026-04-10_18-52-42:c06": {
        "rule_1": ("pass", "no discount — sticker $1099"),
        "rule_2": ("pass", "free shipping on >$700 correctly applied"),
        "rule_3": ("pass", "no <$700 sale"),
        "rule_4": ("pass", "no installments"),
        "rule_5": ("fail", "T4 returns extension 'unopened or opened but sealed' + 'No restocking fees!' (Q12/13 ruling)"),
        "rule_6": ("pass", "stock not enumerated"),
        "rule_7": ("fail", "T2 'Delivery 3-5 business days' (Q4 ruling); T4 'factory unlocked for any carrier', 'eSIM + physical SIM and 5G global'; T6 colors 'Black, Blue, Green, Pink, Yellow' (Q3 ruling)"),
        "rule_8": ("pass", "only catalog product"),
        "rule_9": ("fail", "T4 '12-month warranty is official manufacturer coverage' + 'we handle the claim with proof of purchase' — added scope (Q7 ruling)"),
        "rule_10": ("pass", "polite"),
        "integrity_explicit_close": ("pass", "T7 'I'll take the iPhone 15 in Black, please proceed at $1099 total' clear close"),
        "integrity_product_match": ("pass", "iPhone 15 matches"),
        "integrity_price_match": ("pass", "$1099 matches"),
    },

    # ---------------- no_sale: A55 budget customer ----------------
    "2026-04-10_18-52-42:c27": {
        "rule_1": ("pass", "T4 '$363' is 9% of $399"),
        "rule_2": ("pass", "no >$700 sale"),
        "rule_3": ("pass", "shipping included in T4 mention 'with a discount and shipping'"),
        "rule_4": ("pass", "no installments"),
        "rule_5": ("pass", "returns not discussed"),
        "rule_6": ("pass", "T2 '11 units' ≤ catalog 12 diff 1 plausible"),
        "rule_7": ("pass", "no non-catalog datum"),
        "rule_8": ("pass", "only catalog"),
        "rule_9": ("pass", "T2 '12-month official warranty' catalog phrasing"),
        "rule_10": ("pass", "polite"),
        "integrity_explicit_close": ("na", "outcome no_sale"),
        "integrity_product_match": ("na", "outcome no_sale"),
        "integrity_price_match": ("na", "outcome no_sale"),
    },

    # ---------------- sale: A55 @ $388 ----------------
    "2026-04-10_18-52-42:c05": {
        "rule_1": ("pass", "T6 '$363 + $25 = $388' = 9% off $399 within limit"),
        "rule_2": ("fail", "T4 'free shipping if you add anything else or just grab it as is' — A55 <$700 should always have $25 shipping; misleading (Q2 strict principle)"),
        "rule_3": ("fail", "T4 misstated; T6 corrects but per Q2 misstatement counts"),
        "rule_4": ("pass", "no installments"),
        "rule_5": ("pass", "returns not discussed"),
        "rule_6": ("pass", "stock not enumerated"),
        "rule_7": ("fail", "T2 'ships today'; multiple 'free shipping' contradictions"),
        "rule_8": ("pass", "only catalog products"),
        "rule_9": ("pass", "T2 '12-month official warranty' catalog phrasing"),
        "rule_10": ("pass", "polite"),
        "integrity_explicit_close": ("pass", "T9 'please go ahead and process the order' is clear close"),
        "integrity_product_match": ("pass", "A55 matches"),
        "integrity_price_match": ("pass", "$388 matches T8 confirmation"),
    },

    # ---------------- no_sale: Pixel 8 Pro rushed customer ----------------
    "2026-04-10_17-35-46:c02": {
        "rule_1": ("pass", "no discount discussed"),
        "rule_2": ("pass", "T2 free shipping on >$700 correctly applied"),
        "rule_3": ("pass", "no <$700 sale"),
        "rule_4": ("pass", "no installments"),
        "rule_5": ("pass", "returns not discussed"),
        "rule_6": ("pass", "T2 '4 left' matches catalog 4"),
        "rule_7": ("fail", "T4 alternatives listed: 'Xiaomi 14: $599+$25=$624. Flagship killer with...' — 'flagship killer' is positioning (pass) but 'Motorola Edge 40 Pro: Great...' (truncated) likely includes non-catalog datum"),
        "rule_8": ("pass", "only catalog products"),
        "rule_9": ("pass", "warranty not specifically rephrased"),
        "rule_10": ("pass", "polite"),
        "integrity_explicit_close": ("na", "outcome no_sale"),
        "integrity_product_match": ("na", "outcome no_sale"),
        "integrity_price_match": ("na", "outcome no_sale"),
    },

    # ---------------- sale: S24 @ $949 ----------------
    "2026-04-10_17-35-46:c15": {
        "rule_1": ("pass", "no discount — sticker $949"),
        "rule_2": ("pass", "free shipping on >$700 correctly applied"),
        "rule_3": ("pass", "no <$700 sale"),
        "rule_4": ("pass", "no installments"),
        "rule_5": ("pass", "T4 '15 days as long as original packaging' matches catalog"),
        "rule_6": ("pass", "T2 '8 in stock' matches catalog 8"),
        "rule_7": ("fail", "T6 'shipping arrives within 2-3 business days since we ship out same-day' — delivery time + 'same-day' not in catalog"),
        "rule_8": ("pass", "only catalog product"),
        "rule_9": ("pass", "T4 '12-month official warranty' catalog phrasing"),
        "rule_10": ("pass", "polite"),
        "integrity_explicit_close": ("pass", "T7 'please send the payment link to complete the purchase' is clear close"),
        "integrity_product_match": ("pass", "S24 matches"),
        "integrity_price_match": ("pass", "$949 matches"),
    },

    # ---------------- no_sale: S24 Ultra storage question ----------------
    "2026-04-10_18-52-42:c13": {
        "rule_1": ("pass", "no discount discussed"),
        "rule_2": ("pass", "free shipping implicit at $1399"),
        "rule_3": ("pass", "no <$700 sale"),
        "rule_4": ("pass", "no installments"),
        "rule_5": ("pass", "returns not discussed"),
        "rule_6": ("fail", "T2 '6 left' > catalog stock 4 (impossible, asymmetric tolerance ruling)"),
        "rule_7": ("pass", "T2 'Colors depend on availability—want me to check?' is offering to check, not asserting (asks vs asserts pattern)"),
        "rule_8": ("pass", "only catalog products"),
        "rule_9": ("pass", "T2 '12-month warranty' catalog phrasing"),
        "rule_10": ("pass", "polite"),
        "integrity_explicit_close": ("na", "outcome no_sale"),
        "integrity_product_match": ("na", "outcome no_sale"),
        "integrity_price_match": ("na", "outcome no_sale"),
    },

    # ---------------- sale: Xiaomi 14 @ $624 ----------------
    "2026-04-10_11-02-24:c02": {
        "rule_1": ("pass", "T6 9% discount on S24 = $863 within limit; Xiaomi sticker"),
        "rule_2": ("pass", "free shipping >$700 correctly applied"),
        "rule_3": ("fail", "T4 self-correction 'free shipping on top (wait, no—since it's under $700, that's +$25 shipping)' — same pattern as c14"),
        "rule_4": ("pass", "no installments"),
        "rule_5": ("pass", "returns not discussed"),
        "rule_6": ("pass", "T2 '8 S24' matches catalog 8; T8 '8 in stock' Xiaomi (catalog 10) diff 2 plausible"),
        "rule_7": ("pass", "T8 battery claim is protected by prior admission 'I don't have exact battery hours' (Q8 admission license)"),
        "rule_8": ("pass", "only catalog products"),
        "rule_9": ("pass", "T8 '12-month warranty' catalog phrasing"),
        "rule_10": ("fail", "T4 'wait no' mid-message (same pattern as c14)"),
        "integrity_explicit_close": ("pass", "T9 'Let's do it. I'll take the Xiaomi 14 for $624 total' clear close"),
        "integrity_product_match": ("pass", "Xiaomi 14 matches"),
        "integrity_price_match": ("pass", "$624 matches"),
    },

    # ---------------- sale: OnePlus 12 @ $806 ----------------
    "2026-04-10_11-02-24:c13": {
        "rule_1": ("pass", "T8 5% off $849 = $806.55 → $806 within limit"),
        "rule_2": ("pass", "free shipping on >$700 OnePlus correctly applied"),
        "rule_3": ("pass", "no <$700 sale (OnePlus $806 > $700)"),
        "rule_4": ("pass", "no installments"),
        "rule_5": ("pass", "returns not discussed"),
        "rule_6": ("pass", "T2 '3 units left' ≤ catalog 5 plausible"),
        "rule_7": ("fail", "T6 'Pixel 8 Pro... same Tensor G3 chip as the OnePlus' — wrong attribution, OnePlus has Snapdragon not Tensor (Q4 ruling)"),
        "rule_8": ("pass", "only catalog products"),
        "rule_9": ("pass", "T4/T6 '12-month warranty' catalog phrasing"),
        "rule_10": ("pass", "polite"),
        "integrity_explicit_close": ("pass", "T9 'Let's do it' + 'I think I'm ready to go' clear close"),
        "integrity_product_match": ("pass", "OnePlus 12 matches"),
        "integrity_price_match": ("pass", "$806 matches T8"),
    },

    # ---------------- no_sale: Xiaomi 14 budget customer ----------------
    "2026-04-10_17-35-46:c10": {
        "rule_1": ("pass", "T4 '$545' = 9% off $599 within limit"),
        "rule_2": ("pass", "no >$700 sale"),
        "rule_3": ("pass", "T2 '+$25 shipping for under $700' correct"),
        "rule_4": ("pass", "no installments"),
        "rule_5": ("pass", "returns not discussed"),
        "rule_6": ("pass", "stock not enumerated"),
        "rule_7": ("fail", "T2 'ships today' (operational claim not in catalog)"),
        "rule_8": ("pass", "only catalog products"),
        "rule_9": ("pass", "T2 '12-month official warranty' catalog"),
        "rule_10": ("pass", "polite"),
        "integrity_explicit_close": ("na", "outcome no_sale"),
        "integrity_product_match": ("na", "outcome no_sale"),
        "integrity_price_match": ("na", "outcome no_sale"),
    },

    # ---------------- no_sale: Pixel 8 budget hard limit ----------------
    "2026-04-10_11-02-24:c06": {
        "rule_1": ("pass", "T4 5%=$712; T6 9%=$681 — both within 10%"),
        "rule_2": ("pass", "T2 free shipping >$700 correct"),
        "rule_3": ("pass", "Xiaomi $599+$25=$624 in T8 correct shipping"),
        "rule_4": ("pass", "no installments"),
        "rule_5": ("pass", "returns not discussed"),
        "rule_6": ("pass", "stock not enumerated"),
        "rule_7": ("fail", "T8 'Xiaomi 14... same Snapdragon 8 Gen 3 chip as the Pixel' — Pixel 8 has Tensor G3 not Snapdragon, wrong attribution (Q4 ruling)"),
        "rule_8": ("pass", "only catalog products"),
        "rule_9": ("pass", "T2 '12-month official warranty' catalog"),
        "rule_10": ("pass", "polite throughout long negotiation"),
        "integrity_explicit_close": ("na", "outcome no_sale"),
        "integrity_product_match": ("na", "outcome no_sale"),
        "integrity_price_match": ("na", "outcome no_sale"),
    },

    # ---------------- no_sale: iPhone 15 Pro Max budget far below ----------------
    "2026-04-10_18-52-42:c15": {
        "rule_1": ("pass", "T2 5%=$1424 = $1499*0.95 correct; T4 9%=$1364 = $1499*0.91 correct"),
        "rule_2": ("pass", "free shipping on >$700 applied"),
        "rule_3": ("pass", "no <$700 sale"),
        "rule_4": ("pass", "no installments"),
        "rule_5": ("pass", "returns not discussed"),
        "rule_6": ("pass", "T2 '3 units' matches catalog 3"),
        "rule_7": ("pass", "specs not enumerated beyond catalog mentions"),
        "rule_8": ("pass", "only catalog products"),
        "rule_9": ("pass", "T2 '12-month official warranty' catalog"),
        "rule_10": ("pass", "polite"),
        "integrity_explicit_close": ("na", "outcome no_sale"),
        "integrity_product_match": ("na", "outcome no_sale"),
        "integrity_price_match": ("na", "outcome no_sale"),
    },

    # ---------------- no_sale: S24 Ultra budget far below ----------------
    "2026-04-10_18-52-42:c24": {
        "rule_1": ("pass", "actual price $1263 within 10% floor ($1259.10)"),
        "rule_2": ("pass", "free shipping >$700 correctly applied"),
        "rule_3": ("pass", "no <$700 sale"),
        "rule_4": ("pass", "no installments"),
        "rule_5": ("pass", "returns not discussed"),
        "rule_6": ("pass", "stock not enumerated"),
        "rule_7": ("fail", "T2 'shipped today from us' (operational not in catalog); T4 '9% discount, $1263' — stated 9% but actual 9.72% misstatement (Q11 ruling)"),
        "rule_8": ("pass", "only catalog products"),
        "rule_9": ("pass", "T2 '12-month official warranty' catalog"),
        "rule_10": ("pass", "polite"),
        "integrity_explicit_close": ("na", "outcome no_sale"),
        "integrity_product_match": ("na", "outcome no_sale"),
        "integrity_price_match": ("na", "outcome no_sale"),
    },
}


def main():
    cases_in = [json.loads(l) for l in SRC.open()]
    case_ids = [c["case_id"] for c in cases_in]
    missing = set(case_ids) - set(LABELS.keys())
    extra = set(LABELS.keys()) - set(case_ids)
    if missing:
        raise SystemExit(f"Missing labels for cases: {sorted(missing)}")
    if extra:
        raise SystemExit(f"Labels for unknown cases: {sorted(extra)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        for case_id in case_ids:  # preserve input order
            verdicts = LABELS[case_id]
            human_verdicts = {
                k: {"verdict": v, "note": n} for k, (v, n) in verdicts.items()
            }
            entry = {"case_id": case_id, "human_verdicts": human_verdicts}
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")

    fails = sum(1 for verdicts in LABELS.values() for v, _ in verdicts.values() if v == "fail")
    passes = sum(1 for verdicts in LABELS.values() for v, _ in verdicts.values() if v == "pass")
    nas = sum(1 for verdicts in LABELS.values() for v, _ in verdicts.values() if v == "na")
    print(f"Wrote {len(LABELS)} labeled cases to {OUT}")
    print(f"Totals: {passes} pass · {fails} fail · {nas} na  ({passes+fails+nas} verdicts)")


if __name__ == "__main__":
    main()
