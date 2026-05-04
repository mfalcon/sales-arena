"""Build a self-contained HTML viewer for a Sales Arena experiment."""

import json
import sys
from pathlib import Path

import yaml


def _load_experiment(exp_path: Path, config: dict):
    """Load and enrich a single experiment."""
    with open(exp_path / "result.json") as f:
        data = json.load(f)

    cost_map = config.get("cost_map", {})
    price_map = config.get("price_map", {})

    for conv in data["conversations"]:
        sd = conv.get("sale_details") or {}
        product = sd.get("product", "")
        price = sd.get("price")
        cost = cost_map.get(product, 0)
        list_price = price_map.get(product, 0)

        conv["_cost"] = cost
        conv["_list_price"] = list_price
        if price and product:
            price_f = float(price)
            conv["_profit"] = round(price_f - cost, 2)
            conv["_discount_pct"] = round((list_price - price_f) / list_price * 100, 1) if list_price else 0
        else:
            conv["_profit"] = None
            conv["_discount_pct"] = None

        conv["_violations"] = [v for v in data.get("violations", []) if v["conversation_id"] == conv["id"]]

    events_file = exp_path / "events.json"
    events = []
    if events_file.exists():
        with open(events_file) as f:
            events = json.load(f)

    return data, events


def build_viewer(exp_dir: str, output_file: str = None):
    exp_path = Path(exp_dir)

    with open("workspace/config.yaml") as f:
        config = yaml.safe_load(f)

    cost_map = config.get("cost_map", {})
    price_map = config.get("price_map", {})
    initial_stock = config.get("stock", {})

    # Discover all experiments with result.json
    experiments_root = Path("experiments")
    all_exp_dirs = sorted(
        [d for d in experiments_root.iterdir() if d.is_dir() and (d / "result.json").exists()],
        key=lambda d: d.name,
        reverse=True,
    )

    all_experiments = {}
    all_events = {}
    for d in all_exp_dirs:
        try:
            data, events = _load_experiment(d, config)
            all_experiments[d.name] = data
            all_events[d.name] = events
        except Exception:
            continue

    # Current experiment
    current_id = exp_path.name
    if current_id not in all_experiments:
        data, events = _load_experiment(exp_path, config)
        all_experiments[current_id] = data
        all_events[current_id] = events

    experiments_json = json.dumps(all_experiments, ensure_ascii=False)
    all_events_json = json.dumps(all_events, ensure_ascii=False)
    config_json = json.dumps({"cost_map": cost_map, "price_map": price_map, "initial_stock": initial_stock}, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sales Arena — Viewer</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f1117; color: #e1e4e8; }}

.header {{ background: linear-gradient(135deg, #1a1e2e 0%, #2d1b4e 100%); padding: 24px 32px; border-bottom: 1px solid #30363d; display: flex; align-items: center; justify-content: space-between; }}
.header-left h1 {{ font-size: 20px; font-weight: 600; margin-bottom: 8px; }}
.header-left .subtitle {{ color: #8b949e; font-size: 13px; }}
.exp-select {{ background: #21262d; border: 1px solid #30363d; color: #e1e4e8; border-radius: 6px; padding: 8px 12px; font-size: 13px; cursor: pointer; max-width: 300px; }}

.metrics {{ display: flex; gap: 12px; padding: 20px 32px; background: #161b22; border-bottom: 1px solid #30363d; flex-wrap: wrap; }}
.metric {{ background: #1c2128; border: 1px solid #30363d; border-radius: 8px; padding: 14px 20px; min-width: 140px; }}
.metric .value {{ font-size: 24px; font-weight: 700; }}
.metric .label {{ font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 2px; }}
.metric.profit .value {{ color: #3fb950; }}
.metric.danger .value {{ color: #f85149; }}
.metric.warn .value {{ color: #d29922; }}
.metric.info .value {{ color: #58a6ff; }}

.layout {{ display: flex; height: calc(100vh - 170px); }}

.sidebar {{ width: 380px; border-right: 1px solid #30363d; overflow-y: auto; background: #161b22; flex-shrink: 0; }}
.sidebar .filters {{ padding: 12px 16px; border-bottom: 1px solid #30363d; display: flex; gap: 8px; flex-wrap: wrap; }}
.filter-btn {{ background: #21262d; border: 1px solid #30363d; color: #c9d1d9; border-radius: 16px; padding: 4px 12px; font-size: 12px; cursor: pointer; }}
.filter-btn:hover {{ background: #30363d; }}
.filter-btn.active {{ background: #388bfd26; border-color: #388bfd; color: #58a6ff; }}

.conv-item {{ padding: 12px 16px; border-bottom: 1px solid #21262d; cursor: pointer; transition: background 0.15s; }}
.conv-item:hover {{ background: #1c2128; }}
.conv-item.selected {{ background: #1c2128; border-left: 3px solid #58a6ff; }}
.conv-item .conv-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }}
.conv-item .conv-id {{ font-weight: 600; font-size: 14px; }}
.conv-item .conv-profile {{ font-size: 11px; color: #8b949e; background: #21262d; padding: 2px 8px; border-radius: 10px; }}
.conv-item .conv-detail {{ font-size: 12px; color: #8b949e; }}
.conv-item .conv-tags {{ display: flex; gap: 6px; margin-top: 6px; }}

.tag {{ font-size: 10px; padding: 2px 8px; border-radius: 10px; font-weight: 500; }}
.tag.sale {{ background: #23863626; color: #3fb950; }}
.tag.no_sale {{ background: #f8514926; color: #f85149; }}
.tag.timeout {{ background: #d2992226; color: #d29922; }}
.tag.violation {{ background: #f8514926; color: #f85149; border: 1px solid #f85149; }}
.tag.loss {{ background: #f8514926; color: #f85149; }}
.tag.high-discount {{ background: #d2992226; color: #d29922; }}

.main-panel {{ flex: 1; overflow-y: auto; padding: 24px 32px; }}

.conv-detail-header {{ margin-bottom: 24px; }}
.conv-detail-header h2 {{ font-size: 18px; margin-bottom: 8px; }}
.conv-detail-header .meta {{ display: flex; gap: 16px; flex-wrap: wrap; }}
.conv-detail-header .meta-item {{ font-size: 13px; color: #8b949e; }}
.conv-detail-header .meta-item strong {{ color: #e1e4e8; }}

.turn {{ margin-bottom: 16px; display: flex; gap: 12px; }}
.turn .avatar {{ width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 16px; flex-shrink: 0; }}
.turn.consumer .avatar {{ background: #1f6feb33; }}
.turn.seller .avatar {{ background: #23863633; }}
.turn .bubble {{ max-width: 70%; padding: 12px 16px; border-radius: 12px; font-size: 14px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }}
.turn.consumer {{ justify-content: flex-start; }}
.turn.consumer .bubble {{ background: #1c2128; border: 1px solid #30363d; border-bottom-left-radius: 4px; }}
.turn.seller {{ justify-content: flex-end; }}
.turn.seller .bubble {{ background: #0d4429; border: 1px solid #238636; border-bottom-right-radius: 4px; }}

.turn .turn-label {{ font-size: 10px; color: #8b949e; margin-bottom: 4px; }}

.detection-box {{ margin: 8px 0 8px 48px; padding: 8px 12px; border-radius: 6px; font-size: 12px; font-family: 'SF Mono', Monaco, monospace; }}
.detection-box.purchase {{ background: #23863620; border: 1px solid #23863640; color: #3fb950; }}
.detection-box.no-buy {{ background: #f8514920; border: 1px solid #f8514940; color: #f85149; }}
.detection-box.neutral {{ background: #21262d; border: 1px solid #30363d; color: #8b949e; }}

.judge-section {{ margin-top: 24px; padding: 16px; background: #1c2128; border: 1px solid #30363d; border-radius: 8px; }}
.judge-section h3 {{ font-size: 14px; margin-bottom: 12px; color: #d2a8ff; }}
.judge-section .verdict {{ font-size: 13px; margin-bottom: 8px; }}
.judge-section .violation-item {{ background: #f8514915; border: 1px solid #f8514930; border-radius: 6px; padding: 8px 12px; margin-bottom: 8px; font-size: 12px; }}

.profit-section {{ margin-top: 16px; padding: 16px; background: #1c2128; border: 1px solid #30363d; border-radius: 8px; }}
.profit-section h3 {{ font-size: 14px; margin-bottom: 12px; color: #79c0ff; }}
.profit-calc {{ font-family: 'SF Mono', Monaco, monospace; font-size: 13px; line-height: 1.8; }}
.profit-calc .positive {{ color: #3fb950; }}
.profit-calc .negative {{ color: #f85149; }}

.empty-state {{ display: flex; align-items: center; justify-content: center; height: 100%; color: #484f58; font-size: 15px; }}

.stock-badge {{ display: inline-block; font-size: 10px; background: #21262d; color: #8b949e; padding: 1px 6px; border-radius: 4px; margin-left: 4px; }}

.annotation-section {{ margin-top: 24px; padding: 16px; background: #1c2128; border: 1px solid #30363d; border-radius: 8px; }}
.annotation-section h3 {{ font-size: 14px; margin-bottom: 12px; color: #f0883e; }}
.annotation-row {{ display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }}
.annotation-row label {{ font-size: 13px; color: #8b949e; min-width: 160px; }}
.ann-btn-group {{ display: flex; gap: 4px; }}
.ann-btn {{ background: #21262d; border: 1px solid #30363d; color: #c9d1d9; border-radius: 6px; padding: 4px 12px; font-size: 12px; cursor: pointer; }}
.ann-btn:hover {{ background: #30363d; }}
.ann-btn.selected-yes {{ background: #23863640; border-color: #238636; color: #3fb950; }}
.ann-btn.selected-no {{ background: #f8514940; border-color: #f85149; color: #f85149; }}
.ann-btn.selected-partial {{ background: #d2992240; border-color: #d29922; color: #d29922; }}
.ann-notes {{ width: 100%; background: #0d1117; border: 1px solid #30363d; border-radius: 6px; color: #e1e4e8; padding: 8px; font-size: 13px; font-family: inherit; resize: vertical; min-height: 60px; }}
.ann-notes:focus {{ outline: none; border-color: #58a6ff; }}

.export-bar {{ position: fixed; bottom: 0; left: 0; right: 0; background: #161b22; border-top: 1px solid #30363d; padding: 10px 32px; display: flex; align-items: center; gap: 16px; z-index: 100; }}
.export-btn {{ background: #238636; border: none; color: #fff; border-radius: 6px; padding: 8px 20px; font-size: 13px; cursor: pointer; font-weight: 600; }}
.export-btn:hover {{ background: #2ea043; }}
.export-btn.secondary {{ background: #21262d; border: 1px solid #30363d; color: #c9d1d9; }}
.export-btn.secondary:hover {{ background: #30363d; }}
.export-status {{ font-size: 12px; color: #8b949e; }}
.progress-text {{ font-size: 12px; color: #58a6ff; }}

.conv-item .ann-indicator {{ font-size: 10px; margin-left: 6px; }}
.ann-indicator.done {{ color: #3fb950; }}
.ann-indicator.pending {{ color: #484f58; }}
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <h1>Sales Arena — Experiment Viewer</h1>
    <div class="subtitle" id="subtitle"></div>
  </div>
  <select class="exp-select" id="exp-select" onchange="switchExperiment(this.value)"></select>
</div>

<div class="metrics" id="metrics"></div>

<div class="layout" style="height: calc(100vh - 210px);">
  <div class="sidebar">
    <div class="filters" id="filters"></div>
    <div id="conv-list"></div>
  </div>
  <div class="main-panel" id="main-panel">
    <div class="empty-state">Select a conversation from the sidebar</div>
  </div>
</div>

<div class="export-bar">
  <button class="export-btn" onclick="exportAnnotations()">Export JSON</button>
  <button class="export-btn secondary" onclick="exportTSV()">Export TSV</button>
  <span class="progress-text" id="ann-progress"></span>
  <span class="export-status" id="export-status"></span>
</div>

<script>
const ALL_EXPERIMENTS = {experiments_json};
const ALL_EVENTS = {all_events_json};
const CONFIG = {config_json};
let currentExpId = '{current_id}';
let DATA = ALL_EXPERIMENTS[currentExpId];
let EVENTS = ALL_EVENTS[currentExpId] || [];

const PROFILE_EMOJI = {{
  decisive: '🎯', bargain_hunter: '💰', indecisive: '🤔',
  demanding: '🔍', rushed: '⏰', browser: '👀'
}};

// Build lookup: events by conv_id and type
let eventsByConv = {{}};
function rebuildEventIndex() {{
  eventsByConv = {{}};
  for (const e of EVENTS) {{
    const key = e.conv_id || '';
    if (!eventsByConv[key]) eventsByConv[key] = [];
    eventsByConv[key].push(e);
  }}
}}
rebuildEventIndex();

// Get consumer intent for a specific turn (from events.json)
function getIntentForTurn(convId, turnNumber) {{
  const convEvents = eventsByConv[convId] || [];
  // Find consumer_intent event closest to this turn
  for (const e of convEvents) {{
    if (e.type === 'consumer_intent') {{
      // Match by finding the turn event just before this intent
      const turnEvents = convEvents.filter(t => t.type === 'turn' && t.role === 'consumer' && t.round === turnNumber);
      if (turnEvents.length > 0 && Math.abs(e.seq - turnEvents[0].seq) <= 2) {{
        return e;
      }}
    }}
  }}
  return null;
}}

// Get stock snapshot at a turn
function getStockAtTurn(convId, turnNumber, role) {{
  const convEvents = eventsByConv[convId] || [];
  for (const e of convEvents) {{
    if (e.type === 'turn' && e.conv_id === convId && e.round === turnNumber && e.role === role && e.stock) {{
      return e.stock;
    }}
  }}
  return null;
}}

// Get stock updates for a conversation
function getStockUpdates(convId) {{
  return (eventsByConv[convId] || []).filter(e => e.type === 'stock_update');
}}

// Init
let conversations = DATA.conversations;
let activeFilter = 'all';
let selectedConvId = null;

function renderMetrics() {{
  const el = document.getElementById('metrics');
  const m = [
    {{ value: '$' + DATA.total_profit.toLocaleString('en', {{minimumFractionDigits:2}}), label: 'Total Profit', cls: DATA.total_profit > 0 ? 'profit' : 'danger' }},
    {{ value: '$' + DATA.total_revenue.toLocaleString('en', {{minimumFractionDigits:2}}), label: 'Revenue', cls: 'info' }},
    {{ value: DATA.valid_sales + '/' + DATA.total_conversations, label: 'Valid Sales', cls: 'info' }},
    {{ value: String(DATA.invalid_sales), label: 'Invalid Sales', cls: DATA.invalid_sales > 0 ? 'danger' : 'info' }},
    {{ value: String(DATA.no_sales), label: 'No Sales', cls: DATA.no_sales > 5 ? 'warn' : 'info' }},
    {{ value: String(DATA.violations.length), label: 'Violations', cls: DATA.violations.length > 0 ? 'danger' : 'info' }},
    {{ value: DATA.total_tokens.toLocaleString(), label: 'Tokens', cls: 'info' }},
  ];
  el.innerHTML = m.map(x => `<div class="metric ${{x.cls}}"><div class="value">${{x.value}}</div><div class="label">${{x.label}}</div></div>`).join('');
  document.getElementById('subtitle').textContent = `${{DATA.experiment_id}} · Model: ${{DATA.model}} · ${{DATA.total_conversations}} conversations`;
}}

function renderFilters() {{
  const profiles = [...new Set(conversations.map(c => c.consumer_profile))].sort();
  const outcomes = [...new Set(conversations.map(c => c.outcome))];
  const filters = [
    {{ key: 'all', label: 'All (' + conversations.length + ')' }},
    ...outcomes.map(o => ({{ key: 'outcome:' + o, label: o + ' (' + conversations.filter(c => c.outcome === o).length + ')' }})),
    ...profiles.map(p => ({{ key: 'profile:' + p, label: PROFILE_EMOJI[p] + ' ' + p }})),
    {{ key: 'flag:loss', label: '🔴 Loss' }},
    {{ key: 'flag:high-discount', label: '🟡 >10% disc' }},
  ];
  const el = document.getElementById('filters');
  el.innerHTML = filters.map(f =>
    `<button class="filter-btn ${{activeFilter === f.key ? 'active' : ''}}" data-filter="${{f.key}}">${{f.label}}</button>`
  ).join('');
  el.querySelectorAll('.filter-btn').forEach(btn => {{
    btn.onclick = () => {{ activeFilter = btn.dataset.filter; renderFilters(); renderList(); }};
  }});
}}

function getConvFlags(conv) {{
  const flags = [];
  if (conv._profit !== null && conv._profit !== undefined && conv._profit < 0) flags.push('loss');
  if (conv._discount_pct !== null && conv._discount_pct > 10) flags.push('high-discount');
  if (conv._violations && conv._violations.length > 0) flags.push('violation');
  return flags;
}}

function filterConversations() {{
  return conversations.filter(c => {{
    if (activeFilter === 'all') return true;
    if (activeFilter.startsWith('outcome:')) return c.outcome === activeFilter.split(':')[1];
    if (activeFilter.startsWith('profile:')) return c.consumer_profile === activeFilter.split(':')[1];
    if (activeFilter === 'flag:loss') return c._profit !== null && c._profit < 0;
    if (activeFilter === 'flag:high-discount') return c._discount_pct !== null && c._discount_pct > 10;
    return true;
  }});
}}

function renderList() {{
  const el = document.getElementById('conv-list');
  const filtered = filterConversations();
  el.innerHTML = filtered.map(conv => {{
    const flags = getConvFlags(conv);
    const sd = conv.sale_details || {{}};
    let detail = '';
    if (conv.outcome === 'sale') {{
      detail = `${{sd.product || '?'}} @ $${{sd.price || '?'}}`;
      if (conv._profit !== null) detail += ` → profit: $${{conv._profit.toFixed(0)}}`;
    }} else {{
      detail = `${{conv.turns.length}} turns`;
    }}
    const tags = [
      `<span class="tag ${{conv.outcome}}">${{conv.outcome}}</span>`,
      ...flags.map(f => `<span class="tag ${{f}}">${{f}}</span>`)
    ].join('');
    const ann = annotations[conv.id];
    const isDone = ann && ann.valid_sale !== undefined;
    const annDot = isDone ? '<span class="ann-indicator done">✓</span>' : '<span class="ann-indicator pending">○</span>';
    return `<div class="conv-item ${{selectedConvId === conv.id ? 'selected' : ''}}" data-id="${{conv.id}}">
      <div class="conv-header">
        <span class="conv-id">${{PROFILE_EMOJI[conv.consumer_profile] || ''}} ${{conv.id}} ${{annDot}}</span>
        <span class="conv-profile">${{conv.consumer_profile}}</span>
      </div>
      <div class="conv-detail">${{detail}}</div>
      <div class="conv-tags">${{tags}}</div>
    </div>`;
  }}).join('');

  el.querySelectorAll('.conv-item').forEach(item => {{
    item.onclick = () => {{ selectedConvId = item.dataset.id; renderList(); renderDetail(item.dataset.id); }};
  }});
}}

function renderDetail(convId) {{
  const conv = conversations.find(c => c.id === convId);
  if (!conv) return;
  const panel = document.getElementById('main-panel');

  const sd = conv.sale_details || {{}};
  const metaItems = [
    `<span class="meta-item">Profile: <strong>${{conv.consumer_profile}}</strong></span>`,
    `<span class="meta-item">Outcome: <strong>${{conv.outcome}}</strong></span>`,
    `<span class="meta-item">Turns: <strong>${{conv.turns.length}}</strong></span>`,
  ];
  if (sd.product) metaItems.push(`<span class="meta-item">Product: <strong>${{sd.product}}</strong></span>`);
  if (sd.price) metaItems.push(`<span class="meta-item">Price: <strong>$${{sd.price}}</strong></span>`);

  let turnsHtml = '';
  for (const turn of conv.turns) {{
    const isConsumer = turn.role === 'consumer';
    const emoji = isConsumer ? '🛒' : '🏪';
    const roleLabel = isConsumer ? 'Consumer' : 'Seller';

    turnsHtml += `<div class="turn ${{turn.role}}">`;
    if (isConsumer) {{
      turnsHtml += `<div class="avatar">${{emoji}}</div>`;
      turnsHtml += `<div><div class="turn-label">Turn ${{turn.turn_number}} — ${{roleLabel}}</div><div class="bubble">${{escapeHtml(turn.content)}}</div></div>`;
    }} else {{
      turnsHtml += `<div style="flex:1"></div><div><div class="turn-label" style="text-align:right">Turn ${{turn.turn_number}} — ${{roleLabel}}</div><div class="bubble">${{escapeHtml(turn.content)}}</div></div>`;
      turnsHtml += `<div class="avatar">${{emoji}}</div>`;
    }}
    turnsHtml += `</div>`;

    // Detection boxes for consumer messages — use real events data
    if (isConsumer) {{
      const intent = getIntentForTurn(convId, turn.turn_number);
      if (intent) {{
        const st = intent.status || intent.raw_json?.status || 'unknown';
        if (st === 'purchase') {{
          const prod = intent.raw_json?.product || '?';
          const pr = intent.raw_json?.price || '?';
          turnsHtml += `<div class="detection-box purchase">⚡ PURCHASE: ${{prod}} @ $${{pr}}</div>`;
        }} else if (st === 'no_purchase') {{
          turnsHtml += `<div class="detection-box no-buy">🚪 NO_PURCHASE</div>`;
        }} else {{
          turnsHtml += `<div class="detection-box neutral">📝 BROWSING</div>`;
        }}
      }}
      // Show stock snapshot if available
      const stock = getStockAtTurn(convId, turn.turn_number, 'consumer');
      if (stock) {{
        const stockItems = Object.entries(stock).filter(([k,v]) => v < CONFIG.initial_stock[k]).map(([k,v]) => `${{k}}: ${{v}}`);
        if (stockItems.length > 0) {{
          turnsHtml += `<div class="detection-box neutral" style="font-size:10px;color:#6e7681">📦 Stock changes: ${{stockItems.join(', ')}}</div>`;
        }}
      }}
    }}
  }}

  // Judge section
  let judgeHtml = '';
  const violations = conv._violations || [];
  if (conv.outcome === 'sale') {{
    if (violations.length > 0) {{
      judgeHtml = `<div class="verdict" style="color:#f85149">❌ valid_sale: false</div>`;
      judgeHtml += violations.map(v => `<div class="violation-item"><strong>${{escapeHtml(v.constraint)}}</strong><br>${{escapeHtml(v.description)}}</div>`).join('');
    }} else {{
      judgeHtml = `<div class="verdict" style="color:#3fb950">✅ valid_sale: true — no violations</div>`;
    }}
  }} else {{
    judgeHtml = `<div class="verdict">Outcome: ${{conv.outcome}} — judge checked for bad_treatment only</div>`;
    if (violations.length > 0) {{
      judgeHtml += violations.map(v => `<div class="violation-item"><strong>${{escapeHtml(v.constraint)}}</strong><br>${{escapeHtml(v.description)}}</div>`).join('');
    }} else {{
      judgeHtml += `<div class="verdict" style="color:#3fb950">No bad treatment detected</div>`;
    }}
  }}

  // Profit section
  let profitHtml = '';
  if (conv.outcome === 'sale' && sd.product) {{
    const price = parseFloat(sd.price) || 0;
    const cost = conv._cost || 0;
    const listPrice = conv._list_price || 0;
    const profit = conv._profit || 0;
    const disc = conv._discount_pct || 0;
    const profitClass = profit >= 0 ? 'positive' : 'negative';
    const discWarn = disc > 10 ? ' ⚠️ EXCEEDS 10% MAX' : disc > 9 ? ' ⚠️ borderline' : '';
    profitHtml = `<div class="profit-calc">
List price: $${{listPrice.toLocaleString()}}<br>
Sale price: $${{price.toLocaleString()}}<br>
Cost: $${{cost.toLocaleString()}}<br>
Discount: ${{disc.toFixed(1)}}%${{discWarn}}<br>
<strong class="${{profitClass}}">Profit: $${{profit.toFixed(2)}}</strong>
${{violations.length > 0 ? '<br><span class="negative">⛔ INVALIDATED — not counted</span>' : ''}}
</div>`;
  }}

  // Annotation panel
  const ann = annotations[convId] || {{}};
  const isSale = conv.outcome === 'sale';

  // Get judge data from events
  const judgeEvent = EVENTS.find(e => e.type === 'judge_result' && e.conv_id === convId);
  const judgeData = judgeEvent ? judgeEvent.judge : {{}};

  function yesNoBtn(field) {{
    const current = ann[field];
    const yesClass = current === true ? 'selected-yes' : '';
    const noClass = current === false ? 'selected-no' : '';
    return `<div class="ann-btn-group">
      <button class="ann-btn ${{yesClass}}" onclick="setAnnotation('${{convId}}','${{field}}',true)">Yes</button>
      <button class="ann-btn ${{noClass}}" onclick="setAnnotation('${{convId}}','${{field}}',false)">No</button>
    </div>`;
  }}

  const judgeViolations = judgeData.violations || [];

  let annHtml = `
    <div class="annotation-row">
      <label>Is this a valid interaction?</label>
      ${{yesNoBtn('valid_sale')}}
    </div>
    <div class="annotation-row">
      <label>Was there bad treatment?</label>
      ${{yesNoBtn('bad_treatment')}}
    </div>
    <div class="annotation-row">
      <label>Did the customer confirm the purchase?</label>
      ${{yesNoBtn('purchase_verified')}}
    </div>`;

  if (judgeViolations.length > 0) {{
    annHtml += `
    <div class="annotation-row">
      <label>Are the violations real? (${{judgeViolations.length}})</label>
      ${{yesNoBtn('violations')}}
    </div>`;
  }}

  annHtml += `
    <div class="annotation-row" style="align-items:flex-start">
      <label>Notes</label>
      <textarea class="ann-notes" placeholder="Free text..." oninput="setAnnotation('${{convId}}','notes',this.value)">${{escapeHtml(ann.notes || '')}}</textarea>
    </div>`;

  panel.innerHTML = `
    <div class="conv-detail-header">
      <h2>${{PROFILE_EMOJI[conv.consumer_profile] || ''}} ${{conv.id}} — ${{conv.consumer_profile}}</h2>
      <div class="meta">${{metaItems.join('')}}</div>
    </div>
    ${{turnsHtml}}
    <div class="judge-section">
      <h3>🧑‍⚖️ Judge Evaluation</h3>
      ${{judgeHtml}}
    </div>
    ${{profitHtml ? `<div class="profit-section"><h3>💰 Profit Calculation</h3>${{profitHtml}}</div>` : ''}}
    <div class="annotation-section">
      <h3>🏷️ Human Annotation</h3>
      ${{annHtml}}
    </div>
  `;
}}

function escapeHtml(text) {{
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}}

// --- Annotations (localStorage) ---
let annotations = JSON.parse(localStorage.getItem('sales-arena-annotations-' + DATA.experiment_id) || '{{}}');

function saveAnnotations() {{
  localStorage.setItem('sales-arena-annotations-' + DATA.experiment_id, JSON.stringify(annotations));
  updateProgress();
}}

function setAnnotation(convId, field, value) {{
  if (!annotations[convId]) annotations[convId] = {{}};
  annotations[convId][field] = value;
  saveAnnotations();
  renderDetail(convId);
  renderList();
}}

function updateProgress() {{
  const total = conversations.length;
  const done = Object.keys(annotations).filter(k => annotations[k].valid_sale !== undefined).length;
  document.getElementById('ann-progress').textContent = `Annotated: ${{done}}/${{total}}`;
}}

function buildExportData() {{
  return conversations.map(c => {{
    const ann = annotations[c.id] || {{}};
    const sd = c.sale_details || {{}};
    const judgeEvent = EVENTS.find(e => e.type === 'judge_result' && e.conv_id === c.id);
    const judge = judgeEvent ? judgeEvent.judge : {{}};
    const judgeViolations = judge.violations || [];

    return {{
      conv_id: c.id,
      profile: c.consumer_profile,
      outcome: c.outcome,
      product: sd.product || '',
      price: sd.price || '',
      judge_valid_sale: judge.valid_sale ?? '',
      judge_bad_treatment: judge.bad_treatment ?? '',
      judge_purchase_verified: judge.purchase_verified ?? '',
      judge_violations_count: judgeViolations.length,
      human_valid_sale: ann.valid_sale ?? '',
      human_bad_treatment: ann.bad_treatment ?? '',
      human_purchase_verified: ann.purchase_verified ?? '',
      human_violations: ann.violations ?? '',
      notes: ann.notes || '',
    }};
  }});
}}

function exportAnnotations() {{
  const out = buildExportData();
  const blob = new Blob([JSON.stringify(out, null, 2)], {{type: 'application/json'}});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `annotations_${{DATA.experiment_id}}.json`;
  a.click();
  URL.revokeObjectURL(url);
  document.getElementById('export-status').textContent = 'JSON exported!';
}}

function exportTSV() {{
  const out = buildExportData();
  if (out.length === 0) return;
  const headers = Object.keys(out.reduce((a, b) => ({{...a, ...b}}), {{}}));
  const rows = out.map(r => headers.map(h => String(r[h] ?? '').replace(/\\t/g,' ').replace(/\\n/g,' ')).join('\\t'));
  const tsv = [headers.join('\\t'), ...rows].join('\\n');
  const blob = new Blob([tsv], {{type: 'text/tab-separated-values'}});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `annotations_${{DATA.experiment_id}}.tsv`;
  a.click();
  URL.revokeObjectURL(url);
  document.getElementById('export-status').textContent = 'TSV exported!';
}}

// --- Experiment switcher ---
function renderExpSelect() {{
  const sel = document.getElementById('exp-select');
  sel.innerHTML = Object.keys(ALL_EXPERIMENTS).map(id => {{
    const d = ALL_EXPERIMENTS[id];
    const label = `${{id}} | $$${{d.total_profit?.toLocaleString()}} | ${{d.valid_sales}}/${{d.total_conversations}} sales | ${{d.model}}`;
    return `<option value="${{id}}" ${{id === currentExpId ? 'selected' : ''}}>${{label}}</option>`;
  }}).join('');
}}

function switchExperiment(expId) {{
  currentExpId = expId;
  DATA = ALL_EXPERIMENTS[expId];
  EVENTS = ALL_EVENTS[expId] || [];
  rebuildEventIndex();
  conversations = DATA.conversations;
  selectedConvId = null;
  activeFilter = 'all';

  // Reload annotations for this experiment
  const storageKey = 'sales-arena-annotations-' + DATA.experiment_id;
  annotations = JSON.parse(localStorage.getItem(storageKey) || '{{}}');

  renderMetrics();
  renderFilters();
  renderList();
  updateProgress();
  document.getElementById('main-panel').innerHTML = '<div class="empty-state">Select a conversation from the sidebar</div>';
}}

renderExpSelect();
renderMetrics();
renderFilters();
renderList();
updateProgress();
</script>
</body>
</html>""";

    out = Path(output_file or "experiments/viewer.html")
    out.write_text(html, encoding="utf-8")
    print(f"Viewer written to {out}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python build_viewer.py <experiment_dir> [output.html]")
        sys.exit(1)
    build_viewer(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
