#!/usr/bin/env python3
"""Browser-based annotation tool for blind per-item judge labeling.

Domain-agnostic: items rendered dynamically from the `verdicts` dict in each
case. Rule descriptions parsed from `workspace/constraints.md` at runtime.
Three universal sale-integrity items have static descriptions.

Usage:
    uv run python evals/label_judge_web.py \\
        --cases evals/human_labels/sample20_v2_2026-05-12.jsonl \\
        --out   evals/human_labels/labels_v2_2026-05-12.jsonl \\
        [--port 8765]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from threading import Lock

from flask import Flask, jsonify, request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from arena.prompts import parse_rules
from evals.spec_audit import audit_conversation, parse_catalog_specs


WORKSPACE = ROOT / "workspace"

# Universal sale-integrity descriptions. Applies to any workspace.
INTEGRITY_DESCRIPTIONS = {
    "integrity_explicit_close": "the customer explicitly confirmed the purchase (not conditional, not hedging)",
    "integrity_product_match":  "the reported product matches what was discussed and agreed",
    "integrity_price_match":    "the reported price matches the amount actually agreed",
}


# --- App state ---


class Store:
    """Thread-safe in-memory store + JSONL persistence."""

    def __init__(self, cases_path: Path, labels_path: Path):
        self.cases_path = cases_path
        self.labels_path = labels_path
        self.lock = Lock()
        self.cases: list[dict] = _read_jsonl(cases_path)
        if not self.cases:
            raise SystemExit(f"No cases in {cases_path}")
        labels = _read_jsonl(labels_path) if labels_path.exists() else []
        self.labels: dict[str, dict] = {l["case_id"]: l for l in labels}
        constraints_text = (WORKSPACE / "constraints.md").read_text(encoding="utf-8")
        self.rules_by_id = {rid: text for rid, text in parse_rules(constraints_text)}
        catalog_text = (WORKSPACE / "catalog.md").read_text(encoding="utf-8")
        self.catalog_specs = parse_catalog_specs(catalog_text)

    def all_case_summaries(self) -> list[dict]:
        return [
            {
                "case_id": c["case_id"],
                "consumer_profile": c.get("consumer_profile", "?"),
                "outcome": c.get("outcome", "?"),
                "labeled": c["case_id"] in self.labels,
            }
            for c in self.cases
        ]

    def case_by_id(self, case_id: str) -> dict | None:
        return next((c for c in self.cases if c["case_id"] == case_id), None)

    def save_label(self, case_id: str, human_verdicts: dict) -> None:
        with self.lock:
            self.labels[case_id] = {"case_id": case_id, "human_verdicts": human_verdicts}
            self._flush_locked()

    def delete_label(self, case_id: str) -> None:
        with self.lock:
            if case_id in self.labels:
                del self.labels[case_id]
                self._flush_locked()

    def _flush_locked(self) -> None:
        self.labels_path.parent.mkdir(parents=True, exist_ok=True)
        with self.labels_path.open("w", encoding="utf-8") as f:
            # Persist in case order for stable diffs
            for c in self.cases:
                if c["case_id"] in self.labels:
                    f.write(json.dumps(self.labels[c["case_id"]], ensure_ascii=False, sort_keys=True) + "\n")


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


# --- Item ordering & descriptions ---


def _item_sort_key(key: str):
    if key.startswith("rule_"):
        try:
            return (0, int(key.split("_", 1)[1]), "")
        except ValueError:
            return (0, 9999, key)
    if key.startswith("integrity_"):
        return (1, 0, key)
    return (2, 0, key)


def _item_descriptions(verdicts: dict, rules_by_id: dict[int, str]) -> list[dict]:
    """For each key in `verdicts`, return display info ordered."""
    out = []
    for key in sorted(verdicts.keys(), key=_item_sort_key):
        if key.startswith("rule_"):
            try:
                rid = int(key.split("_", 1)[1])
            except ValueError:
                short, full = key, "(unknown rule)"
            else:
                full = rules_by_id.get(rid, "(rule text not found in constraints.md)")
                short = full if len(full) <= 80 else full[:77] + "..."
        elif key in INTEGRITY_DESCRIPTIONS:
            full = INTEGRITY_DESCRIPTIONS[key].capitalize() + "."
            short = full if len(full) <= 80 else full[:77] + "..."
        else:
            short, full = key, f"(no description for {key})"
        out.append({"key": key, "short": short, "full": full})
    return out


# --- Flask app ---


def build_app(store: Store) -> Flask:
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False

    @app.route("/")
    def index():
        return INDEX_HTML

    @app.route("/api/state")
    def api_state():
        return jsonify({"cases": store.all_case_summaries()})

    @app.route("/api/case/<case_id>")
    def api_case(case_id: str):
        case = store.case_by_id(case_id)
        if case is None:
            return jsonify({"error": "case not found"}), 404
        items = _item_descriptions(case.get("verdicts", {}), store.rules_by_id)
        existing = store.labels.get(case_id, {}).get("human_verdicts", {})
        spec_audit = audit_conversation(case.get("transcript", []), store.catalog_specs)
        return jsonify(
            {
                "case_id": case_id,
                "consumer_profile": case.get("consumer_profile", "?"),
                "outcome": case.get("outcome", "?"),
                "sale_details": case.get("sale_details"),
                "purchase_intent": case.get("purchase_intent"),
                "transcript": case.get("transcript", []),
                "items": items,
                "existing_label": existing,
                "judge_verdicts": case.get("verdicts", {}),
                "spec_audit": spec_audit,
                "relevant_turns": case.get("relevant_turns") or {},
            }
        )

    @app.route("/api/label", methods=["POST"])
    def api_label():
        payload = request.get_json(silent=True) or {}
        case_id = payload.get("case_id")
        human_verdicts = payload.get("human_verdicts", {})
        if not case_id or not isinstance(human_verdicts, dict):
            return jsonify({"error": "invalid payload"}), 400
        case = store.case_by_id(case_id)
        if case is None:
            return jsonify({"error": "case not found"}), 404
        expected_keys = set(case.get("verdicts", {}).keys())
        sanitized: dict[str, dict] = {}
        for key, info in human_verdicts.items():
            if key not in expected_keys:
                continue
            verdict = str(info.get("verdict", "")).strip().lower()
            if verdict not in ("pass", "fail", "na"):
                continue
            note = str(info.get("note", "")).strip()
            sanitized[key] = {"verdict": verdict, "note": note}
        missing = expected_keys - set(sanitized.keys())
        if missing:
            return jsonify({"error": "missing items", "missing": sorted(missing)}), 400
        store.save_label(case_id, sanitized)
        return jsonify({"ok": True, "labeled_count": len(store.labels)})

    @app.route("/api/label/<case_id>", methods=["DELETE"])
    def api_delete_label(case_id: str):
        store.delete_label(case_id)
        return jsonify({"ok": True, "labeled_count": len(store.labels)})

    return app


# --- HTML / JS ---


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Judge Labeling</title>
<style>
  :root {
    --bg: #f7f7f8;
    --panel: #ffffff;
    --border: #e1e4e8;
    --muted: #6a737d;
    --text: #24292e;
    --accent: #0366d6;
    --pass: #28a745;
    --fail: #d73a49;
    --na: #6a737d;
    --customer: #0366d6;
    --seller: #b08800;
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 0; background: var(--bg); color: var(--text);
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                      Helvetica, Arial, sans-serif; font-size: 14px; }
  header { background: var(--panel); border-bottom: 1px solid var(--border);
           padding: 8px 16px; display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 16px; margin: 0; }
  header .meta { color: var(--muted); font-size: 13px; }
  header .spacer { flex: 1; }
  header .progress { color: var(--muted); font-size: 13px; }
  header .progress strong { color: var(--text); }
  main { display: flex; height: calc(100vh - 49px); }
  .left { flex: 1.2; padding: 12px 16px; overflow-y: auto; background: var(--panel);
          border-right: 1px solid var(--border); }
  .right { flex: 1; padding: 12px 16px; overflow-y: auto; }
  .casehead { padding-bottom: 8px; margin-bottom: 12px; border-bottom: 1px solid var(--border); }
  .casehead h2 { font-size: 15px; margin: 0 0 4px 0; }
  .casehead .badges span { display: inline-block; background: #eaecef; color: var(--muted);
                            padding: 2px 8px; border-radius: 10px; font-size: 12px; margin-right: 4px; }
  .casehead .sale { color: var(--text); font-weight: 600; margin-top: 4px; }
  .turn { padding: 8px 10px; margin-bottom: 6px; border-left: 4px solid; border-radius: 3px;
          background: #fafbfc; white-space: pre-wrap; word-wrap: break-word; }
  .turn.consumer { border-color: var(--customer); }
  .turn.seller { border-color: var(--seller); }
  .turn .who { font-size: 11px; color: var(--muted); font-weight: 600; text-transform: uppercase;
               letter-spacing: 0.5px; margin-bottom: 4px; }
  .turn .who .msg-num { color: var(--muted); margin-right: 4px; }
  .item { background: var(--panel); border: 1px solid var(--border); border-radius: 4px;
          padding: 10px 12px; margin-bottom: 8px; }
  .item.unanswered { border-left: 4px solid #f0b400; }
  .item.answered { border-left: 4px solid var(--pass); }
  .item .head { display: flex; gap: 8px; align-items: flex-start; }
  .item .key { font-family: ui-monospace, "SF Mono", Consolas, monospace; font-size: 12px;
               background: #eaecef; padding: 2px 6px; border-radius: 3px; flex-shrink: 0; }
  .item .desc { flex: 1; cursor: help; }
  .item .full { display: none; font-size: 13px; color: var(--muted); margin-top: 4px;
                padding: 6px 8px; background: #f6f8fa; border-radius: 3px; }
  .item.expanded .full { display: block; }
  .verdicts { display: flex; gap: 8px; margin-top: 8px; }
  .verdicts label { flex: 1; display: flex; align-items: center; justify-content: center;
                    padding: 6px 8px; border: 1px solid var(--border); border-radius: 3px;
                    cursor: pointer; user-select: none; font-weight: 500; background: #fff; }
  .verdicts input[type=radio] { display: none; }
  .verdicts label:hover { background: #f1f3f5; }
  .verdicts input[type=radio]:checked + .lbl-text { font-weight: 700; }
  .verdicts label.pass.selected { background: #dcffe4; border-color: var(--pass); color: var(--pass); }
  .verdicts label.fail.selected { background: #ffeef0; border-color: var(--fail); color: var(--fail); }
  .verdicts label.na.selected { background: #eaecef; border-color: var(--na); color: var(--na); }
  .note { margin-top: 6px; }
  .note input { width: 100%; padding: 4px 6px; border: 1px solid var(--border); border-radius: 3px;
                font-size: 13px; }
  .controls { position: sticky; bottom: 0; background: var(--panel); border-top: 1px solid var(--border);
              padding: 10px 12px; margin: 12px -16px -12px -16px; display: flex; gap: 8px; align-items: center; }
  .controls button { padding: 8px 14px; border: 1px solid var(--border); background: #fff; color: var(--text);
                     border-radius: 4px; cursor: pointer; font-size: 13px; font-weight: 500; }
  .controls button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
  .controls button:disabled { opacity: 0.5; cursor: not-allowed; }
  .controls button:hover:not(:disabled) { filter: brightness(0.95); }
  .controls .reveal-toggle { margin-left: auto; font-size: 12px; color: var(--muted); }
  .controls .reveal-toggle input { vertical-align: middle; margin-right: 4px; }
  .judge-reveal { font-size: 12px; color: var(--muted); margin-top: 4px; padding: 4px 6px;
                  background: #fffbdd; border-radius: 3px; display: none; }
  .judge-reveal.visible { display: block; }
  .nav-list { font-size: 12px; color: var(--muted); margin-bottom: 8px; }
  .nav-list a { color: var(--muted); text-decoration: none; padding: 2px 4px; border-radius: 2px; }
  .nav-list a.labeled { color: var(--pass); font-weight: 600; }
  .nav-list a.current { background: var(--accent); color: #fff; font-weight: 600; }
  .nav-list a:hover { background: #eaecef; }
  .keyhelp { font-size: 11px; color: var(--muted); margin-top: 10px; padding-top: 10px;
             border-top: 1px solid var(--border); }
  .keyhelp kbd { background: #eaecef; padding: 1px 5px; border-radius: 3px; font-family: ui-monospace,
                 monospace; font-size: 11px; }
  .spec-mention { background: #dcffe4; border-bottom: 1px dashed var(--pass); cursor: help;
                  padding: 0 2px; border-radius: 2px; }
  .spec-mention.wrong { background: #ffeef0; border-bottom-color: var(--fail); color: var(--fail);
                        font-weight: 600; }
  .spec-audit { margin-top: 8px; padding: 8px 10px; background: #f6f8fa; border-radius: 3px;
                font-size: 12px; border-left: 3px solid var(--pass); }
  .spec-audit .audit-head { font-weight: 600; color: var(--muted); margin-bottom: 6px;
                            font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
  .spec-audit .audit-turn { margin-bottom: 6px; }
  .spec-audit .audit-turn:last-child { margin-bottom: 0; }
  .spec-audit .turn-label { color: var(--muted); font-size: 11px; margin-bottom: 2px; }
  .spec-audit .audit-mention { margin: 2px 0; line-height: 1.4; }
  .spec-audit .audit-token { display: inline-block; background: #dcffe4; color: var(--pass);
                             font-family: ui-monospace, monospace; font-size: 11px;
                             padding: 1px 5px; border-radius: 2px; margin-right: 6px; font-weight: 600; }
  .spec-audit .audit-token.wrong { background: #ffeef0; color: var(--fail); }
  .spec-audit .turn-products { color: var(--muted); font-size: 11px; font-style: italic;
                               margin-bottom: 4px; }
  .spec-audit .turn-products .wrong-note { color: var(--fail); font-style: normal; font-weight: 600; }
  .spec-audit .audit-products { color: var(--muted); font-size: 11px; }
  .spec-audit .audit-products.unique { color: var(--text); font-weight: 500; }
  .spec-audit .audit-empty { color: var(--muted); font-style: italic; font-size: 11px; }
  .relevant-chips { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
  .relevant-chips .label { font-size: 11px; color: var(--muted); margin-right: 4px;
                           text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; }
  .relevant-chips .chip { display: inline-block; padding: 2px 7px; background: #ddf4ff; color: #0969da;
                          border: 1px solid #b6e3ff; border-radius: 10px; font-size: 11px;
                          font-family: ui-monospace, monospace; cursor: pointer; user-select: none; }
  .relevant-chips .chip:hover { background: #b6e3ff; }
  .relevant-chips .empty { font-size: 11px; color: var(--muted); font-style: italic; }
  .turn.flash { animation: flash 1.2s ease-out; }
  @keyframes flash {
    0% { background: #fff8c5; }
    100% { background: #fafbfc; }
  }
</style>
</head>
<body>
<header>
  <h1>Judge Labeling</h1>
  <span class="meta" id="case-meta">—</span>
  <div class="spacer"></div>
  <div class="progress" id="progress">—</div>
</header>
<main>
  <div class="left">
    <div class="casehead" id="casehead"></div>
    <div id="transcript"></div>
  </div>
  <div class="right">
    <div class="nav-list" id="nav-list"></div>
    <div id="items"></div>
    <div class="controls">
      <button id="prev-btn" type="button">◀ Prev (←)</button>
      <button id="next-btn" type="button">Next (→)</button>
      <button id="save-btn" class="primary" type="button">Save & Next (⌘↩)</button>
      <label class="reveal-toggle"><input type="checkbox" id="reveal-judge"> reveal judge after save</label>
    </div>
    <div class="keyhelp">
      <kbd>1</kbd> pass on focused item · <kbd>2</kbd> fail · <kbd>3</kbd> na ·
      <kbd>j</kbd>/<kbd>k</kbd> next/prev item · <kbd>←</kbd>/<kbd>→</kbd> prev/next case ·
      <kbd>⌘↩</kbd> save & next
    </div>
  </div>
</main>
<script>
const state = {
  cases: [],
  currentIdx: 0,
  currentCase: null,
  pendingVerdicts: {},   // { key: {verdict, note} }
  focusedItemIdx: 0,
  revealJudge: false,
};

async function loadState() {
  const r = await fetch('/api/state');
  const data = await r.json();
  state.cases = data.cases;
  // Start at first unlabeled
  const firstUnlabeled = state.cases.findIndex(c => !c.labeled);
  state.currentIdx = firstUnlabeled === -1 ? 0 : firstUnlabeled;
  updateProgress();
  await loadCurrent();
}

function updateProgress() {
  const total = state.cases.length;
  const labeled = state.cases.filter(c => c.labeled).length;
  document.getElementById('progress').innerHTML =
    `<strong>${labeled}</strong> / ${total} labeled · case ${state.currentIdx + 1} / ${total}`;
  renderNavList();
}

function renderNavList() {
  const nav = document.getElementById('nav-list');
  nav.innerHTML = state.cases.map((c, i) => {
    const cls = [];
    if (c.labeled) cls.push('labeled');
    if (i === state.currentIdx) cls.push('current');
    return `<a href="#" class="${cls.join(' ')}" data-idx="${i}" title="${c.case_id}">${i+1}</a>`;
  }).join(' ');
  nav.querySelectorAll('a').forEach(a => {
    a.addEventListener('click', e => {
      e.preventDefault();
      state.currentIdx = parseInt(a.dataset.idx);
      loadCurrent();
    });
  });
}

async function loadCurrent() {
  state.focusedItemIdx = 0;
  const c = state.cases[state.currentIdx];
  if (!c) return;
  const r = await fetch(`/api/case/${encodeURIComponent(c.case_id)}`);
  const data = await r.json();
  state.currentCase = data;
  state.pendingVerdicts = {};
  for (const item of data.items) {
    const existing = data.existing_label[item.key];
    state.pendingVerdicts[item.key] = existing ? {...existing} : {verdict: '', note: ''};
  }
  renderCase();
  updateProgress();
}

function renderCase() {
  const c = state.currentCase;
  document.getElementById('case-meta').textContent = c.case_id;
  let head = `<h2>${c.case_id}</h2><div class="badges">
    <span>profile: ${c.consumer_profile}</span>
    <span>outcome: ${c.outcome}</span></div>`;
  if (c.sale_details) {
    head += `<div class="sale">Reported sale: ${c.sale_details.product} @ $${c.sale_details.price}</div>`;
  }
  document.getElementById('casehead').innerHTML = head;

  const auditByMsg = {};
  (c.spec_audit || []).forEach(a => { auditByMsg[a.message] = a; });

  const transcript = document.getElementById('transcript');
  transcript.innerHTML = c.transcript.map(t => {
    const who = t.role === 'consumer' ? 'CUSTOMER' : 'SELLER';
    const cls = t.role === 'consumer' ? 'consumer' : 'seller';
    const audit = t.role === 'seller' ? auditByMsg[t.message] : null;
    const body = audit ? renderHighlightedContent(t.content, audit.mentions) : escapeHtml(t.content);
    return `<div class="turn ${cls}" data-msg="${t.message}">
      <div class="who"><span class="msg-num">[${t.message}]</span>${who}</div>
      <div>${body}</div></div>`;
  }).join('');

  const relevantByItem = c.relevant_turns || {};
  const items = document.getElementById('items');
  items.innerHTML = c.items.map((it, idx) => {
    const current = state.pendingVerdicts[it.key] || {verdict: '', note: ''};
    const audit = it.key === 'rule_7' ? renderSpecAuditPanel(c.spec_audit || []) : '';
    const chips = renderRelevantChips(relevantByItem[it.key]);
    return `
    <div class="item ${current.verdict ? 'answered' : 'unanswered'}" data-key="${it.key}" data-idx="${idx}">
      <div class="head">
        <span class="key">${it.key}</span>
        <span class="desc" title="click for full text">${escapeHtml(it.short)}</span>
      </div>
      <div class="full">${escapeHtml(it.full)}</div>
      ${chips}
      ${audit}
      <div class="verdicts">
        ${renderRadio(it.key, 'pass', current.verdict)}
        ${renderRadio(it.key, 'fail', current.verdict)}
        ${renderRadio(it.key, 'na', current.verdict)}
      </div>
      <div class="note">
        <input type="text" placeholder="optional note" value="${escapeAttr(current.note)}"
               data-key="${it.key}">
      </div>
      <div class="judge-reveal" data-key="${it.key}"></div>
    </div>`;
  }).join('');

  document.querySelectorAll('.relevant-chips .chip').forEach(el => {
    el.addEventListener('click', () => scrollToMessage(parseInt(el.dataset.msg)));
  });

  items.querySelectorAll('.item .desc').forEach(el => {
    el.addEventListener('click', () => el.parentElement.parentElement.classList.toggle('expanded'));
  });
  items.querySelectorAll('input[type=radio]').forEach(el => {
    el.addEventListener('change', () => {
      const key = el.name;
      const verdict = el.value;
      state.pendingVerdicts[key].verdict = verdict;
      const item = el.closest('.item');
      item.classList.remove('unanswered');
      item.classList.add('answered');
      item.querySelectorAll('.verdicts label').forEach(l => l.classList.remove('selected'));
      el.parentElement.classList.add('selected');
    });
  });
  items.querySelectorAll('.note input').forEach(el => {
    el.addEventListener('input', () => {
      state.pendingVerdicts[el.dataset.key].note = el.value;
    });
  });
  // restore selected styling
  items.querySelectorAll('.item').forEach(item => {
    const key = item.dataset.key;
    const v = state.pendingVerdicts[key]?.verdict;
    if (v) {
      const sel = item.querySelector(`input[name="${key}"][value="${v}"]`);
      if (sel) sel.parentElement.classList.add('selected');
    }
  });
  focusItem(state.focusedItemIdx);
}

function renderRelevantChips(turns) {
  if (turns === undefined) return '';
  if (!turns.length) {
    return `<div class="relevant-chips"><span class="label">look here</span><span class="empty">no relevant turns</span></div>`;
  }
  const chips = turns.map(n => `<span class="chip" data-msg="${n}">[${n}]</span>`).join('');
  return `<div class="relevant-chips"><span class="label">look here</span>${chips}</div>`;
}

function scrollToMessage(msg) {
  const el = document.querySelector(`.turn[data-msg="${msg}"]`);
  if (!el) return;
  el.scrollIntoView({block: 'center', behavior: 'smooth'});
  el.classList.remove('flash');
  void el.offsetWidth;
  el.classList.add('flash');
}

function renderHighlightedContent(content, mentions) {
  if (!mentions || !mentions.length) return escapeHtml(content);
  let html = '';
  let cursor = 0;
  for (const m of mentions) {
    const [s, e] = m.span;
    if (s < cursor) continue;
    html += escapeHtml(content.slice(cursor, s));
    const cls = m.wrong_product ? 'spec-mention wrong' : 'spec-mention';
    const prefix = m.wrong_product ? 'WRONG PRODUCT — ' : '';
    const title = `${prefix}${m.token} — in: ${m.products.join(', ')}`;
    html += `<span class="${cls}" title="${escapeAttr(title)}">${escapeHtml(content.slice(s, e))}</span>`;
    cursor = e;
  }
  html += escapeHtml(content.slice(cursor));
  return html;
}

function renderSpecAuditPanel(auditTurns) {
  if (!auditTurns.length) {
    return `<div class="spec-audit"><div class="audit-head">Spec audit</div>
            <div class="audit-empty">No catalog tokens mentioned by the seller. Read each turn to confirm no spec claims were made.</div></div>`;
  }
  const rows = auditTurns.map(a => {
    const mentions = a.mentions.map(m => {
      const tokenCls = m.wrong_product ? 'audit-token wrong' : 'audit-token';
      const productsCls = m.products.length === 1 ? 'audit-products unique' : 'audit-products';
      return `<div class="audit-mention">
        <span class="${tokenCls}">${escapeHtml(m.token)}</span>
        <span class="${productsCls}">${escapeHtml(m.products.join(', '))}</span>
      </div>`;
    }).join('');
    const products = a.products_in_turn || [];
    const wrong = a.mentions.some(m => m.wrong_product);
    let productsLine = '';
    if (products.length) {
      const note = wrong ? ' <span class="wrong-note">— mismatch above</span>' : '';
      productsLine = `<div class="turn-products">discussed in this turn: ${escapeHtml(products.join(', '))}${note}</div>`;
    }
    return `<div class="audit-turn">
      <div class="turn-label">[Message ${a.message}] seller</div>
      ${productsLine}
      ${mentions}
    </div>`;
  }).join('');
  return `<div class="spec-audit">
    <div class="audit-head">Spec audit — catalog tokens found in seller turns</div>
    ${rows}
  </div>`;
}

function renderRadio(key, val, current) {
  const checked = current === val ? 'checked' : '';
  const cls = val + (current === val ? ' selected' : '');
  return `<label class="${cls}">
    <input type="radio" name="${key}" value="${val}" ${checked}>
    <span class="lbl-text">${val}</span></label>`;
}

function focusItem(idx) {
  const items = document.querySelectorAll('.item');
  if (!items.length) return;
  idx = Math.max(0, Math.min(idx, items.length - 1));
  state.focusedItemIdx = idx;
  items.forEach((el, i) => {
    if (i === idx) {
      el.style.boxShadow = '0 0 0 2px var(--accent)';
      el.scrollIntoView({block: 'nearest', behavior: 'smooth'});
    } else {
      el.style.boxShadow = '';
    }
  });
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function escapeAttr(s) { return escapeHtml(s); }

async function saveCurrent({advance = true} = {}) {
  const verdicts = {};
  let allAnswered = true;
  for (const [key, v] of Object.entries(state.pendingVerdicts)) {
    if (!v.verdict) { allAnswered = false; }
    verdicts[key] = {verdict: v.verdict || '', note: v.note || ''};
  }
  if (!allAnswered) {
    alert('Answer all items before saving (or pick "na" if it does not apply).');
    return;
  }
  const r = await fetch('/api/label', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({case_id: state.currentCase.case_id, human_verdicts: verdicts}),
  });
  if (!r.ok) {
    const err = await r.json();
    alert('Save failed: ' + (err.error || 'unknown'));
    return;
  }
  state.cases[state.currentIdx].labeled = true;
  if (state.revealJudge) revealJudge();
  if (advance) {
    const nextUnlabeled = state.cases.findIndex((c, i) => i > state.currentIdx && !c.labeled);
    if (nextUnlabeled !== -1) {
      state.currentIdx = nextUnlabeled;
      await loadCurrent();
    } else {
      updateProgress();
      alert('All cases labeled! 🎉');
    }
  } else {
    updateProgress();
  }
}

function revealJudge() {
  const j = state.currentCase.judge_verdicts || {};
  document.querySelectorAll('.judge-reveal').forEach(el => {
    const k = el.dataset.key;
    const v = j[k];
    if (!v) return;
    el.classList.add('visible');
    el.innerHTML = `<strong>judge said:</strong> ${escapeHtml(v.verdict)} — ${escapeHtml(v.reason || '')}`;
  });
}

function setVerdictOnFocused(verdict) {
  const items = document.querySelectorAll('.item');
  const item = items[state.focusedItemIdx];
  if (!item) return;
  const radio = item.querySelector(`input[value="${verdict}"]`);
  if (radio) {
    radio.checked = true;
    radio.dispatchEvent(new Event('change'));
  }
}

document.getElementById('prev-btn').addEventListener('click', () => {
  if (state.currentIdx > 0) { state.currentIdx--; loadCurrent(); }
});
document.getElementById('next-btn').addEventListener('click', () => {
  if (state.currentIdx < state.cases.length - 1) { state.currentIdx++; loadCurrent(); }
});
document.getElementById('save-btn').addEventListener('click', () => saveCurrent());
document.getElementById('reveal-judge').addEventListener('change', e => {
  state.revealJudge = e.target.checked;
});

document.addEventListener('keydown', e => {
  // Ignore when typing in an input
  if (e.target.matches('input[type=text]')) return;
  if (e.key === 'ArrowLeft') {
    if (state.currentIdx > 0) { state.currentIdx--; loadCurrent(); }
  } else if (e.key === 'ArrowRight') {
    if (state.currentIdx < state.cases.length - 1) { state.currentIdx++; loadCurrent(); }
  } else if (e.key === '1') {
    setVerdictOnFocused('pass');
  } else if (e.key === '2') {
    setVerdictOnFocused('fail');
  } else if (e.key === '3') {
    setVerdictOnFocused('na');
  } else if (e.key === 'j') {
    focusItem(state.focusedItemIdx + 1);
  } else if (e.key === 'k') {
    focusItem(state.focusedItemIdx - 1);
  } else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
    saveCurrent();
  }
});

loadState();
</script>
</body>
</html>"""


# --- CLI entrypoint ---


def main() -> int:
    parser = argparse.ArgumentParser(description="Browser labeling tool for judge verdicts.")
    parser.add_argument("--cases", required=True, help="Cases JSONL (with `verdicts` dict).")
    parser.add_argument("--out", required=True, help="Labels JSONL output (read+write, supports resume).")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765).")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1).")
    args = parser.parse_args()

    cases_path = Path(args.cases)
    labels_path = Path(args.out)
    if not cases_path.exists():
        print(f"Cases not found: {cases_path}", file=sys.stderr)
        return 1

    store = Store(cases_path, labels_path)
    app = build_app(store)
    print(f"Loaded {len(store.cases)} cases, {len(store.labels)} already labeled.")
    print(f"Open: http://{args.host}:{args.port}/")
    print(f"Stop: Ctrl-C")
    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
