/**
 * SSFX HQ — Account editor overlay (reused by fleet).
 */
window.AccountEditor = (function () {
  const VOLUME_MODES = ['fixed_lots', 'percent_of_balance', 'percent_of_equity', 'fixed_currency_risk', 'percent_risk'];
  const ORDER_HANDLING = ['follow_signal', 'market_only', 'limit_only'];
  const TP_STRATEGY = ['tp1_only', 'all_tps', 'no_tp'];
  const SL_STRATEGY = ['follow_signal', 'no_sl', 'trailing_at_breakeven'];
  const EXECUTION_MODE = ['demo', 'live'];
  const MARKET_CONTEXT = ['strict', 'warn', 'ignore'];

  function selectHtml(options, value) {
    return options.map((o) => `<option value="${o}" ${o === value ? 'selected' : ''}>${o}</option>`).join('');
  }

  function openAccountEditor(account) {
    const cfg = account.config || {};
    const ct = cfg.ctrader || {};
    const tr = cfg.trading || {};
    const pc = tr.partial_close || {};

    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="overlay-content">
        <div class="panel">
          <div class="panel-header">
            <span class="panel-title">Edit Account: ${window.UI.esc(account.name)}</span>
            <button class="btn btn-sm btn-ghost btn-close">Close</button>
          </div>
          <div class="panel-body">
            <form id="editor-form" class="form-grid">
              <div class="form-group">
                <label class="form-label">Name</label>
                <input name="name" class="form-input" value="${window.UI.esc(account.name)}" disabled />
              </div>
              <div class="form-group">
                <label class="form-label">Enabled</label>
                <div class="form-row">
                  <input type="checkbox" name="enabled" ${account.enabled ? 'checked' : ''} />
                  <label>Enabled</label>
                </div>
              </div>
              <div class="form-group">
                <label class="form-label">Host Type</label>
                <select name="host_type" class="form-select">${selectHtml(['demo', 'live'], ct.host_type || 'demo')}</select>
              </div>
              <div class="form-group">
                <label class="form-label">Broker URL</label>
                <input name="broker_url" class="form-input" value="${window.UI.esc(ct.broker_url || '')}" />
              </div>
              <div class="form-group">
                <label class="form-label">Grant ID</label>
                <input name="grant_id" class="form-input" value="${window.UI.esc(ct.grant_id || '')}" />
              </div>
              <div class="form-group">
                <label class="form-label">Account ID</label>
                <input name="account_id" class="form-input" type="number" value="${ct.account_id || 0}" />
              </div>

              <div class="form-section" style="grid-column:1/-1">
                <div class="form-section-title">Trading</div>
              </div>
              <div class="form-group">
                <label class="form-label">Execution Mode</label>
                <select name="execution_mode" class="form-select">${selectHtml(EXECUTION_MODE, tr.execution_mode || 'demo')}</select>
              </div>
              <div class="form-group">
                <label class="form-label">Min Confidence</label>
                <input name="min_parse_confidence" class="form-input" type="number" step="0.01" value="${tr.min_parse_confidence ?? 0.75}" />
              </div>
              <div class="form-group">
                <label class="form-label">Max Positions</label>
                <input name="max_positions" class="form-input" type="number" value="${tr.max_positions ?? 3}" />
              </div>
              <div class="form-group">
                <label class="form-label">Per Symbol</label>
                <input name="max_positions_per_symbol" class="form-input" type="number" value="${tr.max_positions_per_symbol ?? 5}" />
              </div>

              <div class="form-section" style="grid-column:1/-1">
                <div class="form-section-title">Volume</div>
              </div>
              <div class="form-group">
                <label class="form-label">Volume Mode</label>
                <select name="volume_mode" class="form-select">${selectHtml(VOLUME_MODES, tr.volume_mode || 'fixed_lots')}</select>
              </div>
              <div class="form-group">
                <label class="form-label">Default Volume</label>
                <input name="default_volume" class="form-input" type="number" step="0.01" value="${tr.default_volume ?? 0.01}" />
              </div>
              <div class="form-group">
                <label class="form-label">Volume Value</label>
                <input name="volume_value" class="form-input" type="number" step="0.01" value="${tr.volume_value ?? 0.01}" />
              </div>
              <div class="form-group">
                <label class="form-label">Max Volume</label>
                <input name="max_volume_lots" class="form-input" type="number" step="0.01" value="${tr.max_volume_lots ?? ''}" />
              </div>

              <div class="form-section" style="grid-column:1/-1">
                <div class="form-section-title">Risk</div>
              </div>
              <div class="form-group">
                <label class="form-label">Order Handling</label>
                <select name="order_handling" class="form-select">${selectHtml(ORDER_HANDLING, tr.order_handling || 'follow_signal')}</select>
              </div>
              <div class="form-group">
                <label class="form-label">TP Strategy</label>
                <select name="tp_strategy" class="form-select">${selectHtml(TP_STRATEGY, tr.tp_strategy || 'tp1_only')}</select>
              </div>
              <div class="form-group">
                <label class="form-label">SL Strategy</label>
                <select name="sl_strategy" class="form-select">${selectHtml(SL_STRATEGY, tr.sl_strategy || 'follow_signal')}</select>
              </div>
              <div class="form-group">
                <label class="form-label">Max Risk/Trade %</label>
                <input name="max_risk_per_trade_pct" class="form-input" type="number" step="0.1" value="${tr.max_risk_per_trade_pct ?? ''}" />
              </div>
              <div class="form-group">
                <label class="form-label">Max Daily Risk %</label>
                <input name="max_daily_risk_pct" class="form-input" type="number" step="0.1" value="${tr.max_daily_risk_pct ?? ''}" />
              </div>
              <div class="form-group">
                <label class="form-label">Market Context</label>
                <select name="market_context_mode" class="form-select">${selectHtml(MARKET_CONTEXT, tr.market_context_mode || 'warn')}</select>
              </div>

              <div class="form-section" style="grid-column:1/-1">
                <div class="form-section-title">Partial Close</div>
              </div>
              <div class="form-group">
                <label class="form-label">TP1 %</label>
                <input name="on_tp1_pct" class="form-input" type="number" value="${pc.on_tp1_pct ?? 50}" />
              </div>
              <div class="form-group">
                <label class="form-label">TP2 %</label>
                <input name="on_tp2_pct" class="form-input" type="number" value="${pc.on_tp2_pct ?? 25}" />
              </div>
              <div class="form-group">
                <label class="form-label">TP3 %</label>
                <input name="on_tp3_pct" class="form-input" type="number" value="${pc.on_tp3_pct ?? 100}" />
              </div>
              <div class="form-group">
                <label class="form-label">Half Close %</label>
                <input name="on_close_half_pct" class="form-input" type="number" value="${pc.on_close_half_pct ?? 50}" />
              </div>

              <div class="form-group" style="grid-column:1/-1">
                <label class="form-label">Symbol Whitelist</label>
                <input name="symbols_filter" class="form-input" value="${(cfg.symbols_filter || []).join(', ')}" placeholder="XAUUSD, EURUSD" />
              </div>

              <div style="grid-column:1/-1;display:flex;gap:var(--sp-2);margin-top:var(--sp-4)">
                <button type="submit" class="btn btn-primary">Save Account</button>
                <button type="button" class="btn btn-ghost btn-close-bottom">Cancel</button>
              </div>
            </form>
          </div>
        </div>
      </div>
    `;

    const close = () => overlay.remove();
    overlay.querySelector('.btn-close').addEventListener('click', close);
    overlay.querySelector('.btn-close-bottom').addEventListener('click', close);

    overlay.querySelector('#editor-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      const getNum = (k) => {
        const v = fd.get(k);
        return v === '' || v == null ? null : Number(v);
      };
      const patch = {
        enabled: fd.get('enabled') === 'on',
        config: {
          ctrader: {
            host_type: fd.get('host_type'),
            broker_url: fd.get('broker_url'),
            grant_id: fd.get('grant_id'),
            account_id: Number(fd.get('account_id')) || 0,
          },
          trading: {
            execution_mode: fd.get('execution_mode'),
            min_parse_confidence: getNum('min_parse_confidence') ?? 0.75,
            max_positions: getNum('max_positions') ?? 3,
            max_positions_per_symbol: getNum('max_positions_per_symbol') ?? 5,
            volume_mode: fd.get('volume_mode'),
            default_volume: getNum('default_volume') ?? 0.01,
            volume_value: getNum('volume_value') ?? 0.01,
            max_volume_lots: getNum('max_volume_lots'),
            order_handling: fd.get('order_handling'),
            tp_strategy: fd.get('tp_strategy'),
            sl_strategy: fd.get('sl_strategy'),
            max_risk_per_trade_pct: getNum('max_risk_per_trade_pct'),
            max_daily_risk_pct: getNum('max_daily_risk_pct'),
            market_context_mode: fd.get('market_context_mode'),
            partial_close: {
              on_tp1_pct: getNum('on_tp1_pct') ?? 50,
              on_tp2_pct: getNum('on_tp2_pct') ?? 25,
              on_tp3_pct: getNum('on_tp3_pct') ?? 100,
              on_close_half_pct: getNum('on_close_half_pct') ?? 50,
            },
          },
          symbols_filter: fd.get('symbols_filter')
            ? fd.get('symbols_filter').split(',').map((s) => s.trim()).filter(Boolean)
            : [],
        },
      };

      try {
        await window.API.V2API.updateAccount(account.name, patch);
        window.UI.toast('success', `Account ${account.name} saved`);
        close();
        await window.refreshAccounts();
      } catch (err) {
        window.UI.toast('error', `Save failed: ${err.message}`);
      }
    });

    document.body.appendChild(overlay);
  }

  return { openAccountEditor };
})();
