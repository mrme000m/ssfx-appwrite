import { UI } from '../ui.js';
import { API } from '../api.js';

const DEFAULT_SYMBOLS = ['EURUSD', 'GBPUSD', 'USDJPY', 'XAUUSD', 'US30', 'BTCUSD'];

function statCard(label, value, sub) {
  return `
    <div class="metric-card">
      <div class="metric-label">${UI.esc(label)}</div>
      <div class="metric-value">${value}</div>
      ${sub ? `<div class="metric-sub">${UI.esc(sub)}</div>` : ''}
    </div>
  `;
}

function renderPrice(container, data) {
  const wrap = container.querySelector('#market-price');
  const bid = data.bid ?? data.close ?? data.price;
  const ask = data.ask ?? (data.bid && data.spread ? data.bid + data.spread : undefined);
  const spread = data.spread ?? (bid && ask ? ask - bid : undefined);
  const digits = data.digits ?? 5;

  wrap.innerHTML = `
    ${statCard('Bid', bid !== undefined ? UI.fmtNumber(bid, digits) : '—', 'last quote')}
    ${statCard('Ask', ask !== undefined ? UI.fmtNumber(ask, digits) : '—', 'ask price')}
    ${statCard('Spread', spread !== undefined ? UI.fmtNumber(spread, digits) : '—', 'bid/ask distance')}
    ${statCard('Time', data.timestamp ? UI.fmtTime(new Date(data.timestamp).getTime()) : '—', 'UTC')}
  `;
}

function renderContext(container, data) {
  const wrap = container.querySelector('#market-context');
  const ctx = data.context || data || {};
  const indicators = ctx.indicators || {};

  const rows = Object.entries(indicators).slice(0, 8).map(([k, v]) => {
    const val = typeof v === 'object' ? JSON.stringify(v) : String(v);
    return `<div class="metric-row"><span class="metric-key">${UI.esc(k)}</span><span class="metric-val">${UI.esc(val)}</span></div>`;
  }).join('');

  wrap.innerHTML = `
    ${statCard('Trend', ctx.trend || '—', ctx.trend_strength ? `strength ${UI.fmtNumber(ctx.trend_strength)}` : '')}
    ${statCard('Volatility', ctx.volatility || '—', ctx.volatility_pct ? `${UI.fmtNumber(ctx.volatility_pct)}%` : '')}
    ${statCard('Session', ctx.session || '—', ctx.session_phase || '')}
    ${statCard('Symbol', ctx.symbol || '—', ctx.timeframe || '')}
    <div class="metric-list" style="grid-column:1/-1">
      ${rows || '<div class="metric-row"><span class="metric-key">No indicator data</span></div>'}
    </div>
  `;
}

function renderQuality(container, data) {
  const wrap = container.querySelector('#market-quality');
  const q = data.quality || data || {};
  const score = typeof q.score === 'number' ? q.score : q.quality_score;
  const reasons = q.reasons || q.blockers || [];

  wrap.innerHTML = `
    ${statCard('Quality Score', score !== undefined ? `${(score * 100).toFixed(0)}%` : '—', q.grade || '')}
    ${statCard('Status', q.tradeable ? 'TRADEABLE' : 'BLOCKED', q.tradeable ? 'conditions ok' : 'check blockers')}
    ${statCard('Regime', q.regime || '—', q.regime_confidence ? `confidence ${UI.fmtNumber(q.regime_confidence)}` : '')}
    <div class="metric-list" style="grid-column:1/-1">
      ${reasons.length ? reasons.map(r => `<div class="metric-row ${q.tradeable ? '' : 'warn'}"><span class="metric-key">${UI.esc(r)}</span></div>`).join('') : '<div class="metric-row"><span class="metric-key">No blockers</span></div>'}
    </div>
  `;
}

async function loadSymbol(symbol, container) {
  const btn = container.querySelector('#market-load');
  if (btn) btn.disabled = true;

  container.querySelector('#market-price').innerHTML = '<div class="metric-card"><div class="metric-label">Loading</div></div>';
  container.querySelector('#market-context').innerHTML = '<div class="metric-card"><div class="metric-label">Loading</div></div>';
  container.querySelector('#market-quality').innerHTML = '<div class="metric-card"><div class="metric-label">Loading</div></div>';

  try {
    const [price, context, quality] = await Promise.all([
      API.dataservice.price(symbol).catch(() => ({})),
      API.dataservice.context(symbol).catch(() => ({})),
      API.dataservice.quality(symbol).catch(() => ({})),
    ]);
    renderPrice(container, price);
    renderContext(container, context);
    renderQuality(container, quality);
  } catch (err) {
    UI.toast('error', `Market data failed: ${err.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

export function mount(container) {
  const section = UI.el('section', 'section glass-panel');
  section.innerHTML = `
    <div class="section-header">
      <h2 class="section-title">Market Context</h2>
      <span class="section-sub">Dataservice price · context · quality</span>
    </div>
    <div class="market-controls">
      <select id="market-symbol" class="market-symbol">
        ${DEFAULT_SYMBOLS.map(s => `<option value="${s}">${s}</option>`).join('')}
      </select>
      <input id="market-symbol-input" type="text" placeholder="SYMBOL" value="EURUSD" />
      <button id="market-load" class="btn">Load</button>
    </div>
    <div class="market-grid">
      <div>
        <div class="market-grid-title">Price</div>
        <div id="market-price" class="metric-grid"></div>
      </div>
      <div>
        <div class="market-grid-title">Context</div>
        <div id="market-context" class="metric-grid"></div>
      </div>
      <div>
        <div class="market-grid-title">Quality</div>
        <div id="market-quality" class="metric-grid"></div>
      </div>
    </div>
  `;
  container.appendChild(section);

  const select = section.querySelector('#market-symbol');
  const input = section.querySelector('#market-symbol-input');
  const btn = section.querySelector('#market-load');

  select.addEventListener('change', () => {
    input.value = select.value;
    loadSymbol(select.value, section);
  });

  btn.addEventListener('click', () => {
    const symbol = input.value.trim().toUpperCase();
    if (!symbol) return;
    loadSymbol(symbol, section);
  });

  loadSymbol(input.value.trim().toUpperCase(), section);
}

window.MarketContextComponent = { mount };
