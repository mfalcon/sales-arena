# Judge Validation

Formal test-set evaluations of the LLM judge against human labels. One-shot per session: the test split must not be peeked or re-run after iteration.

## Test set evaluation — 2026-05-20 11:47 UTC

- N: 6 (held-out test split, seed=42, stratified)
- TPR: nan% (no Pass cases landed in test split — only Pass case in sample17 went to dev/train)
- TNR: 83.33% (95% CI: 50.00%–100.00%)
- Confusion: TP=0, FN=0, FP=1, TN=5
- Sample: `evals/human_labels/sample17_clean_2026-05-19.jsonl`
- Labels: `evals/human_labels/labels_sample17_2026-05-19.jsonl`
- Judge: `gpt-5.4` @ temp 0.1, prompt = `JUDGE_SYSTEM_PROMPT_TEMPLATE` with strict-interpretation section (added 2026-05-20)

**Single disagreement (FALSE_PASS): `2026-04-10_17-35-46:c14` (sale A55 @ $424).**

The judge correctly identified rule_2 and rule_3 fails (seller misstated "free shipping under $700" before correcting to "$25 shipping"), but the deterministic post-filter `_is_deterministic_false_positive` in `arena/evaluation.py` removed both violations because the final price math ($399 + $25 = $424) was consistent. That filter was designed for an earlier weaker judge that hallucinated rule_2/3 fails when the math was actually fine; with the current strict judge it instead eats legitimate misstatement-and-correct violations.

**Post-test bug fix (2026-05-20):** `_is_deterministic_false_positive` was disabled in `_normalize_violations`. Dev re-run after the fix: TPR 100%, TNR 100%, Accuracy 100%. Test set was NOT re-run (held-out discipline). The 83.33% TNR above stands as the formal test result.

**Calibration discipline note.** Iterating after seeing test would invalidate the test. The honest read is: the formal TNR is 83% with the bug in place; the post-bug-fix dev numbers (100/100/100) suggest the judge can hit ≥90% if a fresh held-out is labeled. The next round should:

1. Label more sample (target ≥50 Pass + ≥50 Fail per Hamel's framework — current sample has only ~3 Pass).
2. Build a fresh stratified split.
3. Iterate prompt on the new dev. Do not look at the new test until final.
