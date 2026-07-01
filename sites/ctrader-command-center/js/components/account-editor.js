import { UI } from '../ui.js';
import { Auth } from '../auth.js';

const CFG = window.APP_CONFIG || {};

const VOLUME_MODES = ['fixed_lots', 'percent_of_balance', 'percent_of_equity', 'fixed_currency_risk', 'percent_risk'];
const ORDER_HANDLING = ['follow_signal', 'market_only', 'limit_only'];
const TP_STRATEGY = ['tp1_only', 'all_tps', 'no_tp'];
const SL_STRATEGY = ['follow_signal', 'no_sl', 'trailing_at_breakeven'];
const EXECUTION_MODE = ['demo', 'live'];
const SECOND_UPDATE = ['full_close', 'half_close', 'ignore'];
const ENTRY_UPDATE = ['amend_pending', 'ignore', 'close_and_reopen'];
const MARKET_CONTEXT = ['strict', 'warn', 'ignore'];

function field(label, input) {
  const g = UI.el('div', 'form-group');
  g.appendChild(UI.el('label', '', [label]));
  g.appendChild(input);
  return g;
}

function textInput(value, placeholder = '') {
  const el = document.createElement('input');
  el.type = 'text';
  el.value = value ?? '';
  el.placeholder = placeholder;
  return el;
}

function numberInput(value, step = 'any') {
  const el = document.createElement('input');
  el.type = 'number';
  el.step = step;
  el.value = value ?? '';
  return el;
}

function selectInput(options, value) {
  const el = document.createElement('select');
  for (const opt of options) {
    const o = document.createElement('option');
    o.value = opt;
    o.textContent = opt;
    if (opt === value) o.selected = true;
    el.appendChild(o);
  }
  return el;
}

function checkbox(value) {
  const el = document.createElement('input');
  el.type = 'checkbox';
  el.checked = !!value;
  return el;
}

function buildForm(account) {
  const cfg = account.config || {};
  const ct = cfg.ctrader || {};
  const tr = cfg.trading || {};
  const pc = tr.partial_close || {};
  const ua = tr.update_actions || {};

  const form = UI.el('form', 'form-grid');
  const fields = {};

  const add = (label, key, input) => {
    fields[key] = input;
    form.appendChild(field(label, input));
  };

  add('Name', 'name', textInput(account.name));
  fields.name.disabled = true;
  add('Enabled', 'enabled', checkbox(account.enabled));
  add('Host Type', 'host_type', selectInput(['demo', 'live'], ct.host_type || 'demo'));

  add('Broker URL', 'broker_url', textInput(ct.broker_url || 'https://auth-ctrader.mrme0.store'));
  add('Grant ID', 'grant_id', textInput(ct.grant_id || ''));
  add('cTrader Account ID', 'account_id', textInput(ct.account_id || 0));

  add('Execution Mode', 'execution_mode', selectInput(EXECUTION_MODE, tr.execution_mode || 'demo'));
  add('Min Parse Confidence', 'min_parse_confidence', numberInput(tr.min_parse_confidence ?? 0.75, '0.01'));
  add('Max Positions', 'max_positions', numberInput(tr.max_positions ?? 3, '1'));
  add('Max Positions Per Symbol', 'max_positions_per_symbol', numberInput(tr.max_positions_per_symbol ?? 5, '1'));

  add('Default Volume', 'default_volume', numberInput(tr.default_volume ?? 0.01, '0.01'));
  add('Volume Mode', 'volume_mode', selectInput(VOLUME_MODES, tr.volume_mode || 'fixed_lots'));
  add('Volume Value', 'volume_value', numberInput(tr.volume_value ?? 0.01, '0.01'));
  add('Max Volume Lots', 'max_volume_lots', numberInput(tr.max_volume_lots, '0.01'));
  add('Min Volume Lots', 'min_volume_lots', numberInput(tr.min_volume_lots, '0.01'));

  add('Order Handling', 'order_handling', selectInput(ORDER_HANDLING, tr.order_handling || 'follow_signal'));
  add('TP Strategy', 'tp_strategy', selectInput(TP_STRATEGY, tr.tp_strategy || 'tp1_only'));
  add('SL Strategy', 'sl_strategy', selectInput(SL_STRATEGY, tr.sl_strategy || 'follow_signal'));
  add('Max SL Distance (pips)', 'max_sl_distance_pips', numberInput(tr.max_sl_distance_pips));
  add('Max Risk Per Trade %', 'max_risk_per_trade_pct', numberInput(tr.max_risk_per_trade_pct));
  add('Max Daily Risk %', 'max_daily_risk_pct', numberInput(tr.max_daily_risk_pct));
  add('Max Spread (pips)', 'max_spread_pips', numberInput(tr.max_spread_pips));
  add('Market Context Mode', 'market_context_mode', selectInput(MARKET_CONTEXT, tr.market_context_mode || 'warn'));

  add('Close on TP1 %', 'on_tp1_pct', numberInput(pc.on_tp1_pct ?? 50, '1'));
  add('Close on TP2 %', 'on_tp2_pct', numberInput(pc.on_tp2_pct ?? 25, '1'));
  add('Close on TP3 %', 'on_tp3_pct', numberInput(pc.on_tp3_pct ?? 100, '1'));
  add('Close Half %', 'on_close_half_pct', numberInput(pc.on_close_half_pct ?? 50, '1'));
  add('Second Update %', 'on_second_update_pct', numberInput(pc.on_second_update_pct ?? 100, '1'));

  add('Second Update Action', 'second_update_action', selectInput(SECOND_UPDATE, ua.second_update_action || 'full_close'));
  add('Entry Update Action', 'entry_update_action', selectInput(ENTRY_UPDATE, ua.entry_update_action || 'ignore'));

  add('Allow Same Symbol Add', 'allow_same_symbol_add', checkbox(tr.allow_same_symbol_add));
  add('Allow Opposite Direction', 'allow_opposite_direction', checkbox(tr.allow_opposite_direction));
  add('Close Stale Positions', 'close_stale_positions', checkbox(tr.close_stale_positions));
  add('Position Timeout (min)', 'position_timeout_minutes', numberInput(tr.position_timeout_minutes));
  add('Update Aggregation (ms)', 'update_aggregation_ms', numberInput(tr.update_aggregation_ms));

  add('Symbol Whitelist', 'symbols_filter', textInput((cfg.symbols_filter || []).join(', ')));

  return { form, fields };
}

function collectConfig(fields) {
  const parse = (k, fallback) => {
    const v = fields[k].value;
    if (v === '' || v === null || v === undefined) return fallback;
    const n = Number(v);
    return Number.isNaN(n) ? fallback : n;
  };
  const parseIntOr = (k, fallback) => {
    const v = fields[k].value;
    if (v === '' || v === null || v === undefined) return fallback;
    const n = parseInt(v, 10);
    return Number.isNaN(n) ? fallback : n;
  };
  return {
    name: fields.name.value,
    enabled: fields.enabled.checked,
    owner_id: 'system',
    ctrader: {
      broker_url: fields.broker_url.value,
      grant_id: fields.grant_id.value,
      client_id: '',
      client_secret: '',
      account_id: parseIntOr('account_id', 0),
      host_type: fields.host_type.value,
    },
    trading: {
      enabled: fields.enabled.checked,
      execution_mode: fields.execution_mode.value,
      min_parse_confidence: parse('min_parse_confidence', 0.75),
      max_positions: parseIntOr('max_positions', 3),
      max_positions_per_symbol: parseIntOr('max_positions_per_symbol', 5),
      default_volume: parse('default_volume', 0.01),
      volume_mode: fields.volume_mode.value,
      volume_value: parse('volume_value', 0.01),
      max_volume_lots: fields.max_volume_lots.value === '' ? null : parse('max_volume_lots'),
      min_volume_lots: fields.min_volume_lots.value === '' ? null : parse('min_volume_lots'),
      order_handling: fields.order_handling.value,
      tp_strategy: fields.tp_strategy.value,
      sl_strategy: fields.sl_strategy.value,
      max_sl_distance_pips: fields.max_sl_distance_pips.value === '' ? null : parse('max_sl_distance_pips'),
      max_risk_per_trade_pct: fields.max_risk_per_trade_pct.value === '' ? null : parse('max_risk_per_trade_pct'),
      max_daily_risk_pct: fields.max_daily_risk_pct.value === '' ? null : parse('max_daily_risk_pct'),
      max_spread_pips: fields.max_spread_pips.value === '' ? null : parse('max_spread_pips'),
      market_context_mode: fields.market_context_mode.value,
      partial_close: {
        on_tp1_pct: parse('on_tp1_pct', 50),
        on_tp2_pct: parse('on_tp2_pct', 25),
        on_tp3_pct: parse('on_tp3_pct', 100),
        on_close_half_pct: parse('on_close_half_pct', 50),
        on_second_update_pct: parse('on_second_update_pct', 100),
      },
      update_actions: {
        close_half_override_pct: null,
        close_partial_override_pct: null,
        second_update_action: fields.second_update_action.value,
        entry_update_action: fields.entry_update_action.value,
      },
      symbol_overrides: [],
      allow_same_symbol_add: fields.allow_same_symbol_add.checked,
      allow_opposite_direction: fields.allow_opposite_direction.checked,
      update_aggregation_ms: fields.update_aggregation_ms.value === '' ? null : parseIntOr('update_aggregation_ms'),
      position_timeout_minutes: fields.position_timeout_minutes.value === '' ? null : parse('position_timeout_minutes'),
      close_stale_positions: fields.close_stale_positions.checked,
    },
    symbols_filter: fields.symbols_filter.value.split(',').map(s => s.trim()).filter(Boolean),
  };
}

async function saveAccount(account, fields) {
  const db = Auth.getTablesDB();
  const cfg = collectConfig(fields);
  const row = {
    name: cfg.name,
    enabled: cfg.enabled,
    owner_id: cfg.owner_id,
    host_type: cfg.ctrader.host_type,
    config_json: JSON.stringify({
      ctrader: cfg.ctrader,
      trading: cfg.trading,
      symbols_filter: cfg.symbols_filter,
    }),
    updated_at: new Date().toISOString(),
  };
  try {
    await db.updateRow({
      databaseId: CFG.databaseId,
      tableId: CFG.accountsTable,
      rowId: cfg.name,
      data: row,
    });
    UI.toast('success', `Saved ${cfg.name}`);
    return true;
  } catch (err) {
    UI.toast('error', `Save failed: ${err.message}`);
    return false;
  }
}

export function openAccountEditor(account) {
  const overlay = UI.el('div', '', []);
  overlay.style.cssText = 'position:fixed;inset:0;z-index:60;background:rgba(5,7,10,0.92);overflow:auto;padding:24px;';

  const panel = UI.el('div', 'glass-panel section');
  panel.style.maxWidth = '960px';
  panel.style.margin = '0 auto';
  panel.innerHTML = `
    <div class="section-header">
      <h2 class="section-title">Edit Account: ${UI.esc(account.name)}</h2>
      <button class="btn secondary btn-close">Close</button>
    </div>
    <div id="editor-form-wrap"></div>
    <div style="margin-top:16px;display:flex;gap:10px;">
      <button class="btn btn-save">Save Account</button>
      <button class="btn secondary btn-close-bottom">Cancel</button>
    </div>
  `;

  const { form, fields } = buildForm(account);
  panel.querySelector('#editor-form-wrap').appendChild(form);

  const close = () => overlay.remove();
  panel.querySelector('.btn-close').addEventListener('click', close);
  panel.querySelector('.btn-close-bottom').addEventListener('click', close);
  panel.querySelector('.btn-save').addEventListener('click', async () => {
    if (await saveAccount(account, fields)) {
      close();
      const { listAccounts } = await import('../api.js').then(m => m.API);
      window.commandState.accounts = await listAccounts();
      window.commandBus.dispatchEvent(new CustomEvent('accounts'));
    }
  });

  overlay.appendChild(panel);
  document.body.appendChild(overlay);
}

window.AccountEditor = { openAccountEditor };
